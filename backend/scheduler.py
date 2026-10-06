"""
High-performance schedule optimizer using Google OR-Tools CP-SAT.
Schedules members across Burn and THC houses from 9:00 AM to 9:00 PM (7 days/week)
with equal hour fairness, repeat-week consistency, and shift style preference matching.
"""

from typing import List, Dict, Tuple, Optional, Any
from ortools.sat.python import cp_model
from backend.models import (
    Member,
    MasterScheduleItem,
    WeeklyOverrideItem,
    ScheduleSlotAssignment,
    DAYS_OF_WEEK,
    HOUSES,
    SLOTS_PER_DAY,
    START_HOUR,
    slot_to_time_str,
)

class ScheduleOptimizer:
    def __init__(
        self,
        members: List[Member],
        master_items: List[MasterScheduleItem],
        weekly_overrides: List[WeeklyOverrideItem],
        previous_schedule: Optional[List[ScheduleSlotAssignment]] = None,
        min_shift_slots: int = 2,  # Minimum 1 hour (2 thirty-minute slots)
        max_daily_slots: int = 10, # Max 5 hours in a single day
        time_limit_seconds: float = 20.0,
    ):
        self.members = [m for m in members if m.active]
        self.num_members = len(self.members)
        self.member_id_to_idx = {m.id: idx for idx, m in enumerate(self.members)}
        self.idx_to_member = {idx: m for idx, m in enumerate(self.members)}
        
        self.master_items = master_items
        self.weekly_overrides = weekly_overrides
        self.previous_schedule = previous_schedule or []
        
        self.min_shift_slots = min_shift_slots
        self.max_daily_slots = max_daily_slots
        self.time_limit_seconds = time_limit_seconds

        self.days = len(DAYS_OF_WEEK)  # 7
        self.slots = SLOTS_PER_DAY     # 24
        self.houses = HOUSES           # ["Burn", "THC"]
        self.num_houses = len(self.houses)
        self.total_demand_slots = self.days * self.slots * self.num_houses # 280 slots (140 hours)

    def _build_availability_matrix(self) -> Dict[Tuple[int, int, int], bool]:
        """
        Returns a dict: (member_idx, day, slot) -> is_available (bool).
        Master schedule sets default availability (busy items mark false).
        Weekly overrides can add more busy blocks or carve out available blocks.
        """
        avail = {}
        for m_idx in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    avail[(m_idx, d, s)] = True

        # Apply Master Schedule (recurring busy)
        for item in self.master_items:
            m_idx = self.member_id_to_idx.get(item.member_id)
            if m_idx is None:
                continue
            d = item.day_of_week
            if 0 <= d < self.days:
                for s in range(max(0, item.start_slot), min(self.slots, item.end_slot)):
                    avail[(m_idx, d, s)] = False

        # Apply Weekly Overrides (additional busy)
        # Rule: Nobody can be assigned when they are busy in EITHER master OR weekly!
        for item in self.weekly_overrides:
            m_idx = self.member_id_to_idx.get(item.member_id)
            if m_idx is None:
                continue
            d = item.day_of_week
            if 0 <= d < self.days:
                for s in range(max(0, item.start_slot), min(self.slots, item.end_slot)):
                    if item.override_type == "busy":
                        avail[(m_idx, d, s)] = False

        return avail

    def solve(self) -> Dict[str, Any]:
        """
        Constructs and solves the scheduling CP-SAT model.
        Returns a dictionary containing:
        - success: bool
        - status: str
        - assignments: List[ScheduleSlotAssignment]
        - stats: Dict with fairness metrics, repeat consistency, hours per member
        - warnings: List[str]
        """
        if self.num_members == 0:
            return {
                "success": False,
                "status": "NO_MEMBERS",
                "assignments": [],
                "stats": {},
                "warnings": ["No active members found to schedule."],
            }

        avail = self._build_availability_matrix()
        model = cp_model.CpModel()

        # Decision Variables:
        # x[m, d, s, h] = 1 if member m works on day d at slot s in house h
        x = {}
        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    for h in range(self.num_houses):
                        x[m, d, s, h] = model.NewBoolVar(f"x_{m}_{d}_{s}_{h}")

        # Auxiliary: is_working[m, d, s] = sum_h x[m, d, s, h]
        is_working = {}
        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    is_working[m, d, s] = model.NewBoolVar(f"work_{m}_{d}_{s}")
                    model.Add(is_working[m, d, s] == sum(x[m, d, s, h] for h in range(self.num_houses)))

        # 1. Coverage Constraint: Each house at each slot must be filled by a member,
        # or marked as unfilled with an 'X' if no member is available due to conflicts.
        unfilled = {}
        unfilled_penalties = []
        burn_idx = self.houses.index("Burn") if "Burn" in self.houses else 0
        thc_idx = self.houses.index("THC") if "THC" in self.houses else 1

        for d in range(self.days):
            for s in range(self.slots):
                for h in range(self.num_houses):
                    unf = model.NewBoolVar(f"unf_{d}_{s}_{h}")
                    unfilled[d, s, h] = unf
                    model.Add(sum(x[m, d, s, h] for m in range(self.num_members)) + unf == 1)
                    # THC has higher filling priority than Burn (-105000 vs -100000)
                    penalty_weight = -105000 if h == thc_idx else -100000
                    unfilled_penalties.append(penalty_weight * unf)

                # If only one person is free for a given slot, give them to THC (Burn cannot be staffed)
                avail_at_slot = sum(1 for m in range(self.num_members) if avail[(m, d, s)])
                if avail_at_slot == 1:
                    for m in range(self.num_members):
                        model.Add(x[m, d, s, burn_idx] == 0)

        # 2. No Double Booking: At most 1 house per person at any slot
        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    model.Add(sum(x[m, d, s, h] for h in range(self.num_houses)) <= 1)

        # 3. Availability Constraints:
        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    if not avail[(m, d, s)]:
                        for h in range(self.num_houses):
                            model.Add(x[m, d, s, h] == 0)

        # 4. Target Hours & Fairness Calculation:
        # Calculate unavoidable unfilled slots (where fewer than num_houses members are available)
        unavoidable_unfilled = 0
        for d in range(self.days):
            for s in range(self.slots):
                avail_at_slot = sum(1 for m in range(self.num_members) if avail[(m, d, s)])
                if avail_at_slot < self.num_houses:
                    unavoidable_unfilled += (self.num_houses - avail_at_slot)

        effective_demand = max(0, self.total_demand_slots - unavoidable_unfilled)
        total_weight = sum(m.weight for m in self.members)
        if total_weight <= 0:
            total_weight = float(self.num_members)

        # Count total available slots for each member
        avail_count = {}
        for m_idx in range(self.num_members):
            avail_count[m_idx] = sum(1 for d in range(self.days) for s in range(self.slots) if avail[(m_idx, d, s)])

        target_slots = {}
        fair_floor = effective_demand // self.num_members
        fair_ceil = (effective_demand + self.num_members - 1) // self.num_members

        member_total_slots = {}
        slack_pos = {}
        slack_neg = {}
        fairness_penalties = []

        for m_idx, member in enumerate(self.members):
            member_total_slots[m_idx] = model.NewIntVar(0, self.total_demand_slots, f"tot_{m_idx}")
            actual_sum = sum(x[m_idx, d, s, h] for d in range(self.days) for s in range(self.slots) for h in range(self.num_houses))
            model.Add(member_total_slots[m_idx] == actual_sum)

            if all(abs(m.weight - 1.0) < 1e-4 for m in self.members):
                # Standard equal weighting
                t = round(effective_demand / self.num_members)
                t_min = max(0, fair_floor - 1)
                t_max = min(effective_demand, fair_ceil + 1)
            else:
                t = round(effective_demand * (member.weight / total_weight))
                t_min = max(0, t - 1)
                t_max = min(effective_demand, t + 1)

            target_slots[m_idx] = t

            # If member has plenty of availability, enforce fair bounds
            if avail_count[m_idx] >= t_min:
                model.Add(member_total_slots[m_idx] >= min(t_min, avail_count[m_idx]))
                model.Add(member_total_slots[m_idx] <= t_max)
            else:
                # Member is busy too much to work full share: cap at their max available
                model.Add(member_total_slots[m_idx] <= avail_count[m_idx])

            # Slack deviation for objective optimization
            sp = model.NewIntVar(0, self.total_demand_slots, f"sp_{m_idx}")
            sn = model.NewIntVar(0, self.total_demand_slots, f"sn_{m_idx}")
            slack_pos[m_idx] = sp
            slack_neg[m_idx] = sn
            model.Add(member_total_slots[m_idx] - t == sp - sn)
            fairness_penalties.append(1000 * sp + 1000 * sn)

        # 5. Anti-Fragmentation & Minimum Shift Length:
        if self.min_shift_slots >= 2:
            for m in range(self.num_members):
                for d in range(self.days):
                    # Overall working shift cannot be single 30-min isolated shifts
                    model.Add(is_working[m, d, 0] <= is_working[m, d, 1])
                    model.Add(is_working[m, d, self.slots - 1] <= is_working[m, d, self.slots - 2])
                    for s in range(1, self.slots - 1):
                        model.Add(is_working[m, d, s] <= is_working[m, d, s - 1] + is_working[m, d, s + 1])

                    # Stint at any specific house cannot be single 30-min isolated shifts
                    for h in range(self.num_houses):
                        model.Add(x[m, d, 0, h] <= x[m, d, 1, h])
                        model.Add(x[m, d, self.slots - 1, h] <= x[m, d, self.slots - 2, h])
                        for s in range(1, self.slots - 1):
                            model.Add(x[m, d, s, h] <= x[m, d, s - 1, h] + x[m, d, s + 1, h])

                    # Max continuous stint at any house <= 6 slots (3 hours)
                    for h in range(self.num_houses):
                        for s in range(self.slots - 6):
                            model.Add(sum(x[m, d, s + k, h] for k in range(7)) <= 6)

        # 6. House Switching Rules & Separation:
        # Rule A: Minimum 5-hour gap (10 slots) between working at different houses on the same day!
        # If member is at House 0 at s, they cannot be at House 1 at any slot within 9 slots before or after.
        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    window_slots = [x[m, d, s2, 1] for s2 in range(max(0, s - 9), min(self.slots, s + 10))]
                    model.Add(sum(window_slots) == 0).OnlyEnforceIf(x[m, d, s, 0])

        # Rule B: Heavily disincentivize working at two houses on the same day at all
        house_switch_penalties = []
        for m in range(self.num_members):
            for d in range(self.days):
                w_burn = model.NewBoolVar(f"w_burn_{m}_{d}")
                w_thc = model.NewBoolVar(f"w_thc_{m}_{d}")
                model.Add(sum(x[m, d, s, 0] for s in range(self.slots)) <= self.slots * w_burn)
                model.Add(sum(x[m, d, s, 1] for s in range(self.slots)) <= self.slots * w_thc)
                both_houses = model.NewBoolVar(f"both_h_{m}_{d}")
                model.Add(w_burn + w_thc - 1 <= both_houses)
                house_switch_penalties.append(-350 * both_houses)

        # 6.5 One-Sitting Priority (Avoid Leaving and Coming Back in One Day):
        # We track shift starts for each member on each day.
        # Weekends (Sat, Sun): Strictly at most 1 continuous sitting (no split shifts at all).
        # Weekdays (Mon-Fri): Cap at 2 sittings max (strictly forbids 3rd or 4th shift) and heavily penalize 2nd shift.
        split_shift_penalties = []
        for m in range(self.num_members):
            member_sec_shifts = []
            for d in range(self.days):
                starts = []
                s0 = model.NewBoolVar(f"st_{m}_{d}_0")
                model.Add(s0 == is_working[m, d, 0])
                starts.append(s0)
                for s in range(1, self.slots):
                    st = model.NewBoolVar(f"st_{m}_{d}_{s}")
                    model.Add(st >= is_working[m, d, s] - is_working[m, d, s - 1])
                    model.Add(st <= is_working[m, d, s])
                    model.Add(st <= 1 - is_working[m, d, s - 1])
                    starts.append(st)

                num_starts = sum(starts)
                if d in (5, 6):  # Saturday & Sunday: strictly at most 1 continuous sitting
                    model.Add(num_starts <= 1)
                else:  # Weekdays: cap at 2 and heavily penalize split shifts (one sitting priority)
                    model.Add(num_starts <= 2)
                    sec_shift = model.NewIntVar(0, 1, f"sec_sh_{m}_{d}")
                    model.Add(sec_shift >= num_starts - 1)
                    member_sec_shifts.append(sec_shift)
                    split_shift_penalties.append(-5000 * sec_shift)

        # 7. Repeat Consistency Across Weeks:
        # Prioritize giving people the same times every week even if at different houses!
        # If people have weekly exceptions, their repeat consistency is sacrificed first,
        # protecting the steady repeat times of members who had NO weekly exceptions!
        repeat_reward_terms = []
        prev_work_set = set()
        for prev in self.previous_schedule:
            prev_m_idx = self.member_id_to_idx.get(prev.member_id)
            if prev_m_idx is not None:
                prev_work_set.add((prev_m_idx, prev.day_of_week, prev.slot))

        # Identify members who submitted weekly exceptions for this week
        members_with_exceptions = set()
        for item in self.weekly_overrides:
            m_idx = self.member_id_to_idx.get(item.member_id)
            if m_idx is not None and item.override_type == "busy":
                members_with_exceptions.add(m_idx)

        for (prev_m_idx, d, s) in prev_work_set:
            if 0 <= d < self.days and 0 <= s < self.slots:
                # Members with NO weekly exceptions are strongly protected: high reward (6000).
                # Members who submitted weekly exceptions have their repeat times sacrificed first: lower reward (100).
                weight = 100 if prev_m_idx in members_with_exceptions else 6000
                repeat_reward_terms.append(weight * is_working[prev_m_idx, d, s])

        # 8. High-Priority 2.0 to 2.5 Hours Daily Target (4 to 5 slots):
        # Highly prioritize giving everyone 2.0 to 2.5 hours per working day.
        # Preference between 2hr everyday and longer shifts on fewer days has been removed.
        daily_target_penalties = []
        for m_idx in range(self.num_members):
            for d in range(self.days):
                day_slots = sum(x[m_idx, d, s, h] for s in range(self.slots) for h in range(self.num_houses))
                works_day = model.NewBoolVar(f"works_day_{m_idx}_{d}")
                model.Add(day_slots <= self.max_daily_slots) # 10 slots = 5 hours max
                model.Add(day_slots <= self.slots * works_day)
                model.Add(day_slots >= 1).OnlyEnforceIf(works_day)
                model.Add(day_slots == 0).OnlyEnforceIf(works_day.Not())

                # Strongly penalize working less than 4 slots (under 2 hours)
                under_4 = model.NewIntVar(0, 4, f"und_{m_idx}_{d}")
                model.Add(under_4 >= 4 * works_day - day_slots)
                daily_target_penalties.append(-600 * under_4)

                # Strongly penalize working more than 5 slots (over 2.5 hours)
                over_5 = model.NewIntVar(0, self.max_daily_slots, f"ov_{m_idx}_{d}")
                model.Add(over_5 >= day_slots - 5)
                daily_target_penalties.append(-400 * over_5)

        # Warm-start hints from previous schedule if available
        if self.previous_schedule:
            for prev in self.previous_schedule:
                prev_m_idx = self.member_id_to_idx.get(prev.member_id)
                h_idx = self.houses.index(prev.house) if prev.house in self.houses else None
                if prev_m_idx is not None and h_idx is not None:
                    if 0 <= prev.day_of_week < self.days and 0 <= prev.slot < self.slots:
                        if avail[(prev_m_idx, prev.day_of_week, prev.slot)]:
                            model.AddHint(x[prev_m_idx, prev.day_of_week, prev.slot, h_idx], 1)
                            model.AddHint(is_working[prev_m_idx, prev.day_of_week, prev.slot], 1)

        # Total Objective Function
        objective = (
            sum(unfilled_penalties)
            + sum(house_switch_penalties)
            + sum(repeat_reward_terms)
            + sum(daily_target_penalties)
            + sum(split_shift_penalties)
        )
        model.Maximize(objective)

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_seconds
        solver.parameters.num_workers = 4
        status = solver.Solve(model)

        status = solver.Solve(model)

        result = None
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            assignments, unfilled_details = self._extract_assignments(solver, x)
            stats = self._compute_stats(assignments, prev_work_set, target_slots)
            # If coverage meets or exceeds 90%, adopt primary result
            if stats["coverage_pct"] >= 90.0:
                result = {
                    "success": True,
                    "status": solver.StatusName(status),
                    "assignments": assignments,
                    "stats": stats,
                    "warnings": [],
                }

        # If primary solve was infeasible OR coverage fell below 90%:
        # Run Max-Coverage Rule-Breaking Fallback to fill as many slots as possible
        if result is None:
            result = self._solve_max_coverage_fallback(avail, prev_work_set, target_slots)

        if not result["success"]:
            return result

        # Final Mandatory Pass: Unfilled slots should TRULY only be there if no one is free!
        # If any slot is currently unfilled but someone is actually free and eligible, fill it!
        filled_gaps = self._fill_unfilled_gaps(result["assignments"], avail)
        if filled_gaps > 0:
            result["warnings"].append(f"Filled {filled_gaps} extra slot(s) by allocating available members.")

        # Recompute final stats and warnings with the completed assignments
        result["stats"] = self._compute_stats(result["assignments"], prev_work_set, target_slots)
        unfilled_count = result["stats"]["unfilled_slots_count"]
        unfilled_details = [
            f"{DAYS_OF_WEEK[a.day_of_week]} {slot_to_time_str(a.slot)} ({a.house})"
            for a in result["assignments"] if a.member_id == "UNFILLED"
        ]
        if unfilled_count > 0:
            preview = ", ".join(unfilled_details[:4])
            if len(unfilled_details) > 4:
                preview += f" (+{len(unfilled_details) - 4} more)"
            result["warnings"].append(f"{unfilled_count} slot(s) could not be filled because NO ONE on the team is available: {preview}")

        return result

    def _extract_assignments(self, solver, x):
        """Extract assignments and unfilled details from a solved CP-SAT model."""
        assignments = []
        unfilled_details = []
        for d in range(self.days):
            for s in range(self.slots):
                for h_idx, house in enumerate(self.houses):
                    assigned_m = None
                    for m_idx in range(self.num_members):
                        if solver.Value(x[m_idx, d, s, h_idx]) == 1:
                            assigned_m = m_idx
                            break

                    if assigned_m is not None:
                        member = self.idx_to_member[assigned_m]
                        assignments.append(
                            ScheduleSlotAssignment(
                                day_of_week=d,
                                slot=s,
                                house=house,
                                member_id=member.id,
                                member_name=member.name,
                                color=member.color,
                            )
                        )
                    else:
                        time_label = slot_to_time_str(s)
                        day_name = DAYS_OF_WEEK[d]
                        unfilled_details.append(f"{day_name} {time_label} ({house})")
                        assignments.append(
                            ScheduleSlotAssignment(
                                day_of_week=d,
                                slot=s,
                                house=house,
                                member_id="UNFILLED",
                                member_name="❌ X (UNFILLED)",
                                color="#DC2626",
                            )
                        )
        return assignments, unfilled_details

    def _fill_unfilled_gaps(self, assignments: List[ScheduleSlotAssignment], avail: Dict[Tuple[int, int, int], bool]) -> int:
        """
        Final safety guarantee: unfilled slots should TRULY only be there if no one is free!
        Inspects every unfilled slot and fills it if any eligible member is available and not working at the other house.
        """
        filled_count = 0
        for a in assignments:
            if a.member_id == "UNFILLED":
                d, s, h = a.day_of_week, a.slot, a.house
                other_h = "Burn" if h == "THC" else "THC"
                other_m = next((x.member_id for x in assignments if x.day_of_week == d and x.slot == s and x.house == other_h), None)

                candidates = []
                for m in self.members:
                    m_idx = self.member_id_to_idx[m.id]
                    if avail[(m_idx, d, s)] and m.id != other_m:
                        candidates.append(m)

                if candidates:
                    # Score candidates: prefer adjacent slot at same house, then lowest hours
                    best_c = None
                    best_score = -999999
                    for c in candidates:
                        score = 0
                        # Check adjacent slots at same house
                        has_adj = any(x.member_id == c.id and x.day_of_week == d and x.house == h and abs(x.slot - s) == 1 for x in assignments)
                        if has_adj:
                            score += 1000
                        # Bonus for having worked on that day
                        worked_today = sum(1 for x in assignments if x.member_id == c.id and x.day_of_week == d)
                        if worked_today < 10:
                            score += 200
                        elif worked_today >= 14:
                            score -= 2000
                        # Fairness penalty on total hours worked
                        tot = sum(1 for x in assignments if x.member_id == c.id)
                        score -= tot * 20
                        if score > best_score:
                            best_score = score
                            best_c = c

                    if best_c:
                        a.member_id = best_c.id
                        a.member_name = best_c.name
                        a.color = best_c.color
                        filled_count += 1

        return filled_count

    def _solve_max_coverage_fallback(self, avail, prev_work_set=None, target_slots=None) -> Dict[str, Any]:
        """
        Max-Coverage Rule-Breaking Solver:
        When standard rules result in poor coverage (< 90%) or infeasibility,
        this pass relaxes constraints (allowing house switching, split shifts,
        extending daily hours, and allowing available members to take extra shifts)
        to maximize house coverage.
        """
        prev_work_set = prev_work_set or set()
        target_slots = target_slots or {m: self.total_demand_slots // max(1, self.num_members) for m in range(self.num_members)}

        model = cp_model.CpModel()
        x = {}
        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    for h in range(self.num_houses):
                        x[m, d, s, h] = model.NewBoolVar(f"mc_x_{m}_{d}_{s}_{h}")

        unfilled = {}
        unfilled_penalties = []
        burn_idx = self.houses.index("Burn") if "Burn" in self.houses else 0
        thc_idx = self.houses.index("THC") if "THC" in self.houses else 1

        for d in range(self.days):
            for s in range(self.slots):
                for h in range(self.num_houses):
                    unf = model.NewBoolVar(f"mc_unf_{d}_{s}_{h}")
                    unfilled[d, s, h] = unf
                    model.Add(sum(x[m, d, s, h] for m in range(self.num_members)) + unf == 1)
                    # Astronomical penalty for unfilled slots (coverage above all rules!)
                    weight = 1050000 if h == thc_idx else 1000000
                    unfilled_penalties.append(weight * unf)

                avail_at_slot = sum(1 for m in range(self.num_members) if avail[(m, d, s)])
                if avail_at_slot == 1:
                    for m in range(self.num_members):
                        model.Add(x[m, d, s, burn_idx] == 0)

        # Basic physics constraints
        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    model.Add(sum(x[m, d, s, h] for h in range(self.num_houses)) <= 1)
                    if not avail[(m, d, s)]:
                        for h in range(self.num_houses):
                            model.Add(x[m, d, s, h] == 0)

        # Soft preferences (penalties) so the solver still produces clean schedules if possible
        soft_penalties = []
        for m in range(self.num_members):
            for d in range(self.days):
                day_slots = sum(x[m, d, s, h] for s in range(self.slots) for h in range(self.num_houses))
                # Cap at 14 slots (7 hours) to prevent physical exhaustion, but softly penalize > 10 slots
                model.Add(day_slots <= 14)
                over_10 = model.NewIntVar(0, 14, f"mc_ov10_{m}_{d}")
                model.Add(over_10 >= day_slots - 10)
                soft_penalties.append(500 * over_10)

                # Soft penalty for working both houses on same day (rule broken if needed)
                w_burn = model.NewBoolVar(f"mc_wb_{m}_{d}")
                w_thc = model.NewBoolVar(f"mc_wt_{m}_{d}")
                model.Add(sum(x[m, d, s, 0] for s in range(self.slots)) <= self.slots * w_burn)
                model.Add(sum(x[m, d, s, 1] for s in range(self.slots)) <= self.slots * w_thc)
                both_h = model.NewBoolVar(f"mc_both_{m}_{d}")
                model.Add(w_burn + w_thc - 1 <= both_h)
                soft_penalties.append(250 * both_h)

        # Soft fairness deviation
        for m in range(self.num_members):
            actual = sum(x[m, d, s, h] for d in range(self.days) for s in range(self.slots) for h in range(self.num_houses))
            t = target_slots.get(m, self.total_demand_slots // max(1, self.num_members))
            diff = model.NewIntVar(-self.total_demand_slots, self.total_demand_slots, f"mc_diff_{m}")
            model.Add(diff == actual - t)
            abs_d = model.NewIntVar(0, self.total_demand_slots, f"mc_abs_{m}")
            model.AddAbsEquality(abs_d, diff)
            soft_penalties.append(50 * abs_d)

        model.Minimize(sum(unfilled_penalties) + sum(soft_penalties))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_seconds
        solver.parameters.num_workers = 4
        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            assignments, unfilled_details = self._extract_assignments(solver, x)
            stats = self._compute_stats(assignments, prev_work_set, target_slots)
            return {
                "success": True,
                "status": f"MAX_COVERAGE_{solver.StatusName(status)}",
                "assignments": assignments,
                "stats": stats,
                "warnings": ["Generated using Max-Coverage fallback (relaxing rules to fill as many slots as possible)."],
            }
        else:
            return {
                "success": False,
                "status": "INFEASIBLE",
                "assignments": [],
                "stats": {},
                "warnings": ["Could not satisfy schedule coverage."],
            }

    def _compute_stats(
        self,
        assignments: List[ScheduleSlotAssignment],
        prev_work_set: set,
        target_slots: Dict[int, int],
    ) -> Dict[str, Any]:
        """Calculates rich metrics on fairness, repeat consistency, and shift breakdown."""
        member_hours = {m.id: {"slots": 0, "hours": 0.0, "shifts": 0, "burn_hours": 0.0, "thc_hours": 0.0} for m in self.members}
        member_days = {m.id: set() for m in self.members}
        repeat_matches = 0
        total_prev_slots = len(prev_work_set)

        # Tally slots and days
        work_slots_assigned = set()
        for a in assignments:
            if a.member_id in member_hours:
                member_hours[a.member_id]["slots"] += 1
                member_hours[a.member_id]["hours"] += 0.5
                member_days[a.member_id].add(a.day_of_week)
                if a.house == "Burn":
                    member_hours[a.member_id]["burn_hours"] += 0.5
                else:
                    member_hours[a.member_id]["thc_hours"] += 0.5

            m_idx = self.member_id_to_idx.get(a.member_id)
            if m_idx is not None:
                work_slots_assigned.add((m_idx, a.day_of_week, a.slot))

        # Check repeat consistency matches
        for item in prev_work_set:
            if item in work_slots_assigned:
                repeat_matches += 1

        repeat_consistency_pct = (
            round((repeat_matches / total_prev_slots) * 100, 1)
            if total_prev_slots > 0
            else 100.0
        )

        # Count contiguous shift blocks
        sorted_assignments = sorted(assignments, key=lambda a: (a.member_id, a.day_of_week, a.slot))
        shift_count = {m.id: 0 for m in self.members}
        current_m = None
        current_d = None
        last_s = None
        for a in sorted_assignments:
            if a.member_id != current_m or a.day_of_week != current_d or last_s is None or a.slot != last_s + 1:
                shift_count[a.member_id] = shift_count.get(a.member_id, 0) + 1
            current_m = a.member_id
            current_d = a.day_of_week
            last_s = a.slot

        # Compile member summaries
        member_summaries = []
        hours_list = []
        for m_idx, m in enumerate(self.members):
            h_data = member_hours.get(m.id, {"slots": 0, "hours": 0.0, "burn_hours": 0.0, "thc_hours": 0.0})
            target_h = target_slots.get(m_idx, round(self.total_demand_slots / max(1, self.num_members))) * 0.5
            hours_list.append(h_data["hours"])
            member_summaries.append({
                "id": m.id,
                "name": m.name,
                "color": m.color,
                "assigned_hours": h_data["hours"],
                "target_hours": target_h,
                "diff_hours": round(h_data["hours"] - target_h, 1),
                "days_worked": len(member_days.get(m.id, set())),
                "shift_count": shift_count.get(m.id, 0),
                "avg_shift_hours": round(h_data["hours"] / max(1, shift_count.get(m.id, 1)), 1),
                "burn_hours": h_data["burn_hours"],
                "thc_hours": h_data["thc_hours"],
                "preference": m.shift_preference,
            })

        # Fairness score (100% minus variance from target)
        avg_hours = sum(hours_list) / max(1, len(hours_list))
        max_dev = max(abs(h - avg_hours) for h in hours_list) if hours_list else 0.0
        fairness_score = max(0.0, round(100.0 - (max_dev * 10), 1))

        unfilled_count = sum(1 for a in assignments if a.member_id == "UNFILLED")
        filled_count = len(assignments) - unfilled_count
        coverage_pct = round((filled_count / max(1, len(assignments))) * 100, 1)

        return {
            "total_demand_hours": self.total_demand_slots * 0.5,
            "active_members_count": self.num_members,
            "avg_hours_per_member": round(avg_hours, 1),
            "fairness_score": fairness_score,
            "max_hours_deviation": round(max_dev, 1),
            "repeat_consistency_pct": repeat_consistency_pct,
            "repeat_matches": repeat_matches,
            "repeat_candidates": total_prev_slots,
            "unfilled_slots_count": unfilled_count,
            "coverage_pct": coverage_pct,
            "members": member_summaries,
        }
