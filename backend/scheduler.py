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
        max_daily_slots: int = 12, # Max 6 hours in a single day
        time_limit_seconds: float = 12.0,
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
        self.total_demand_slots = self.days * self.slots * self.num_houses # 336 slots (168 hours)

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

        # Apply Weekly Overrides
        for item in self.weekly_overrides:
            m_idx = self.member_id_to_idx.get(item.member_id)
            if m_idx is None:
                continue
            d = item.day_of_week
            if 0 <= d < self.days:
                for s in range(max(0, item.start_slot), min(self.slots, item.end_slot)):
                    if item.override_type == "busy":
                        avail[(m_idx, d, s)] = False
                    elif item.override_type == "available":
                        avail[(m_idx, d, s)] = True

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

        # 1. Coverage Constraint: Exactly 1 person at each house for each slot
        for d in range(self.days):
            for s in range(self.slots):
                for h in range(self.num_houses):
                    model.Add(sum(x[m, d, s, h] for m in range(self.num_members)) == 1)

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
        total_weight = sum(m.weight for m in self.members)
        if total_weight <= 0:
            total_weight = float(self.num_members)

        # Count total available slots for each member
        avail_count = {}
        for m_idx in range(self.num_members):
            avail_count[m_idx] = sum(1 for d in range(self.days) for s in range(self.slots) if avail[(m_idx, d, s)])

        target_slots = {}
        fair_floor = self.total_demand_slots // self.num_members
        fair_ceil = (self.total_demand_slots + self.num_members - 1) // self.num_members

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
                t = round(self.total_demand_slots / self.num_members)
                t_min = fair_floor
                t_max = fair_ceil
            else:
                t = round(self.total_demand_slots * (member.weight / total_weight))
                t_min = max(0, t - 1)
                t_max = min(self.total_demand_slots, t + 1)

            target_slots[m_idx] = t

            # If member has plenty of availability, enforce fair bounds strictly
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
                    # Day boundaries cannot be single 30-min isolated shifts
                    model.Add(is_working[m, d, 0] <= is_working[m, d, 1])
                    model.Add(is_working[m, d, self.slots - 1] <= is_working[m, d, self.slots - 2])
                    # Interior slots cannot be single 30-min isolated shifts
                    for s in range(1, self.slots - 1):
                        model.Add(is_working[m, d, s] <= is_working[m, d, s - 1] + is_working[m, d, s + 1])

        # 6. House Switching Penalties:
        # Discourage switching houses mid-continuous-shift
        continuity_terms = []
        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(1, self.slots):
                    switch_house = model.NewBoolVar(f"sw_h_{m}_{d}_{s}")
                    model.Add(switch_house >= x[m, d, s - 1, 0] + x[m, d, s, 1] - 1)
                    model.Add(switch_house >= x[m, d, s - 1, 1] + x[m, d, s, 0] - 1)
                    continuity_terms.append(-20 * switch_house)

        # 7. Repeat Consistency Across Weeks:
        # Prioritize giving people the same times every week even if at different houses!
        repeat_reward_terms = []
        prev_work_set = set()
        for prev in self.previous_schedule:
            prev_m_idx = self.member_id_to_idx.get(prev.member_id)
            if prev_m_idx is not None:
                prev_work_set.add((prev_m_idx, prev.day_of_week, prev.slot))

        for (prev_m_idx, d, s) in prev_work_set:
            if 0 <= d < self.days and 0 <= s < self.slots:
                # Big reward for repeating the same slot (working at either Burn or THC)
                repeat_reward_terms.append(150 * is_working[prev_m_idx, d, s])

        # 8. Shift Preferences:
        # Daily indicator: works_day[m, d]
        preference_terms = []
        for m_idx, member in enumerate(self.members):
            for d in range(self.days):
                day_slots = sum(x[m_idx, d, s, h] for s in range(self.slots) for h in range(self.num_houses))
                works_day = model.NewBoolVar(f"works_day_{m_idx}_{d}")
                model.Add(day_slots <= self.max_daily_slots)
                model.Add(day_slots <= self.slots * works_day)

                if member.shift_preference == "daily_short":
                    # Prefers ~2 hours (4 slots) everyday
                    preference_terms.append(15 * works_day)
                    excess_daily = model.NewIntVar(0, self.slots, f"ex_day_{m_idx}_{d}")
                    model.Add(excess_daily >= day_slots - 4)
                    preference_terms.append(-10 * excess_daily)

                elif member.shift_preference == "fewer_long":
                    # Prefers fewer days, longer shifts (e.g. 3-5 hours = 6-10 slots)
                    preference_terms.append(-15 * works_day)

        # Total Objective Function
        objective = (
            sum(repeat_reward_terms)
            + sum(preference_terms)
            + sum(continuity_terms)
        )
        model.Maximize(objective)

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_seconds
        solver.parameters.num_workers = 4
        status = solver.Solve(model)
        print(f"Primary solver status: {solver.StatusName(status)}, WallTime: {solver.WallTime()}")

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # If constrained too tightly (e.g. strict min shift or strict availability), try fallback relaxation
            return self._solve_relaxed_fallback(avail)

        # Extract Results
        assignments = []
        for d in range(self.days):
            for s in range(self.slots):
                for h_idx, house in enumerate(self.houses):
                    for m_idx in range(self.num_members):
                        if solver.Value(x[m_idx, d, s, h_idx]) == 1:
                            member = self.idx_to_member[m_idx]
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

        # Compute Statistics
        stats = self._compute_stats(assignments, prev_work_set, target_slots)

        return {
            "success": True,
            "status": solver.StatusName(status),
            "assignments": assignments,
            "stats": stats,
            "warnings": [],
        }

    def _solve_relaxed_fallback(self, avail) -> Dict[str, Any]:
        """Fallback solver with relaxed constraints if availability conflicts are very tight."""
        model = cp_model.CpModel()
        x = {}
        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    for h in range(self.num_houses):
                        x[m, d, s, h] = model.NewBoolVar(f"rel_x_{m}_{d}_{s}_{h}")

        for d in range(self.days):
            for s in range(self.slots):
                for h in range(self.num_houses):
                    model.Add(sum(x[m, d, s, h] for m in range(self.num_members)) == 1)

        for m in range(self.num_members):
            for d in range(self.days):
                for s in range(self.slots):
                    model.Add(sum(x[m, d, s, h] for h in range(self.num_houses)) <= 1)
                    if not avail[(m, d, s)]:
                        for h in range(self.num_houses):
                            model.Add(x[m, d, s, h] == 0)

        # Strictly bounded fair share in fallback solver
        fair_floor = self.total_demand_slots // self.num_members
        fair_ceil = (self.total_demand_slots + self.num_members - 1) // self.num_members
        avail_count = {
            m: sum(1 for d in range(self.days) for s in range(self.slots) if avail[(m, d, s)])
            for m in range(self.num_members)
        }
        dev_terms = []
        for m in range(self.num_members):
            actual = sum(x[m, d, s, h] for d in range(self.days) for s in range(self.slots) for h in range(self.num_houses))
            if avail_count[m] >= fair_floor:
                model.Add(actual >= min(fair_floor, avail_count[m]))
                model.Add(actual <= fair_ceil)
            diff = model.NewIntVar(-self.total_demand_slots, self.total_demand_slots, f"diff_{m}")
            model.Add(diff == actual - fair_floor)
            abs_diff = model.NewIntVar(0, self.total_demand_slots, f"abs_{m}")
            model.AddAbsEquality(abs_diff, diff)
            dev_terms.append(abs_diff)

        model.Minimize(sum(dev_terms))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_seconds
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {
                "success": False,
                "status": "INFEASIBLE",
                "assignments": [],
                "stats": {},
                "warnings": ["Could not satisfy schedule coverage due to severe member schedule conflicts."],
            }

        assignments = []
        for d in range(self.days):
            for s in range(self.slots):
                for h_idx, house in enumerate(self.houses):
                    for m_idx in range(self.num_members):
                        if solver.Value(x[m_idx, d, s, h_idx]) == 1:
                            member = self.idx_to_member[m_idx]
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

        stats = self._compute_stats(assignments, set(), {m: fair_floor for m in range(self.num_members)})
        return {
            "success": True,
            "status": "RELAXED_FEASIBLE",
            "assignments": assignments,
            "stats": stats,
            "warnings": ["Generated with relaxed constraints to resolve tight availability conflicts."],
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
            target_h = target_slots.get(m_idx, 28) * 0.5
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

        return {
            "total_demand_hours": 168.0,
            "active_members_count": self.num_members,
            "avg_hours_per_member": round(avg_hours, 1),
            "fairness_score": fairness_score,
            "max_hours_deviation": round(max_dev, 1),
            "repeat_consistency_pct": repeat_consistency_pct,
            "repeat_matches": repeat_matches,
            "repeat_candidates": total_prev_slots,
            "members": member_summaries,
        }
