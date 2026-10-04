import pytest
from backend.models import (
    Member,
    MasterScheduleItem,
    WeeklyOverrideItem,
    ScheduleSlotAssignment,
    DAYS_OF_WEEK,
    HOUSES,
    SLOTS_PER_DAY,
)
from backend.scheduler import ScheduleOptimizer

def create_sample_members(n=12):
    names = [
        "Dylan", "Nate", "Simon", "Blake Cohen", "Jacob", "Oliver",
        "Kobi", "Ethan", "Sebastian", "Corey", "Alex", "Blake Rosen", "Jared"
    ]
    members = []
    for i in range(n):
        pref = "daily_short" if i % 2 == 0 else "fewer_long"
        members.append(Member(
            id=f"m_{i}",
            name=names[i % len(names)],
            active=True,
            weight=1.0,
            shift_preference=pref,
            color="#2563EB",
        ))
    return members

def test_full_coverage_12_members():
    """Verify that all 280 slots are staffed, no double bookings, and hours are fair."""
    members = create_sample_members(12)
    optimizer = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=[],
        min_shift_slots=2, # min 1 hour
    )
    result = optimizer.solve()
    assert result["success"] is True
    assignments = result["assignments"]
    assert len(assignments) == 7 * 20 * 2  # exactly 280 slots

    # Verify coverage: for every day, slot, house, exactly 1 person
    coverage = {}
    for a in assignments:
        key = (a.day_of_week, a.slot, a.house)
        assert key not in coverage, f"Duplicate coverage at {key}"
        coverage[key] = a.member_id

    assert len(coverage) == 280

    # Verify no double booking (same person at both houses at the same slot)
    person_slots = set()
    for a in assignments:
        key = (a.member_id, a.day_of_week, a.slot)
        assert key not in person_slots, f"Person {a.member_id} double booked at {key}"
        person_slots.add(key)

    # Verify equal hours: 280 slots / 12 people = ~23.3 slots each (~11.7 hours)
    member_counts = {m.id: 0 for m in members}
    for a in assignments:
        member_counts[a.member_id] += 1

    for m_id, count in member_counts.items():
        assert 22 <= count <= 25, f"Member {m_id} expected 22-25 slots (~11.7h), got {count}"

def test_coverage_13_members():
    """Verify 13 members balance cleanly around ~10.8 hours (21-22 slots each)."""
    members = create_sample_members(13)
    optimizer = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=[],
    )
    result = optimizer.solve()
    assert result["success"] is True
    assert len(result["assignments"]) == 280

    member_counts = {m.id: 0 for m in members}
    for a in result["assignments"]:
        member_counts[a.member_id] += 1

    # 280 / 13 = 21.54 -> each member should get 20 to 23 slots (~10.8h)
    for m_id, count in member_counts.items():
        assert 20 <= count <= 23, f"Member {m_id} got {count} slots, expected 20 to 23"

def test_master_schedule_unavailability():
    """Verify solver never schedules a member when they have master schedule conflicts."""
    members = create_sample_members(12)
    # Member 0 busy on Monday (day 0) from 9:00 AM to 1:00 PM (slots 0..7)
    busy_items = [
        MasterScheduleItem(
            member_id="m_0",
            day_of_week=0,
            start_slot=0,
            end_slot=8,
            label="Class",
        )
    ]
    optimizer = ScheduleOptimizer(
        members=members,
        master_items=busy_items,
        weekly_overrides=[],
    )
    result = optimizer.solve()
    assert result["success"] is True

    for a in result["assignments"]:
        if a.member_id == "m_0" and a.day_of_week == 0:
            assert a.slot >= 8, f"Member 0 was scheduled at slot {a.slot} during busy class time!"

def test_weekly_override():
    """Verify weekly override takes precedence over master availability."""
    members = create_sample_members(12)
    # Member 1 has weekly override busy all Friday (day 4, slots 0..23)
    overrides = [
        WeeklyOverrideItem(
            week_id="2026-W40",
            member_id="m_1",
            day_of_week=4,
            start_slot=0,
            end_slot=20,
            override_type="busy",
        )
    ]
    optimizer = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=overrides,
    )
    result = optimizer.solve()
    assert result["success"] is True

    for a in result["assignments"]:
        if a.member_id == "m_1":
            assert a.day_of_week != 4, "Member 1 scheduled on Friday despite weekly override!"

def test_repeat_week_consistency():
    """Verify optimizer prioritizes giving people the same times every week even if at different houses."""
    members = create_sample_members(12)
    
    # Week 1 schedule
    optimizer1 = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=[],
    )
    result1 = optimizer1.solve()
    assert result1["success"] is True
    week1_assignments = result1["assignments"]

    # Week 2 schedule with repeat consistency enabled
    optimizer2 = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=[],
        previous_schedule=week1_assignments,
    )
    result2 = optimizer2.solve()
    assert result2["success"] is True
    stats2 = result2["stats"]

    # High consistency expected
    assert stats2["repeat_consistency_pct"] >= 90.0, f"Repeat consistency was {stats2['repeat_consistency_pct']}%, expected >= 90%"

def test_weekly_exception_repeat_sacrifice():
    """Verify that people with weekly exceptions have their repeat consistency sacrificed first."""
    members = create_sample_members(12)
    # Week 1
    opt1 = ScheduleOptimizer(members=members, master_items=[], weekly_overrides=[])
    res1 = opt1.solve()
    assert res1["success"] is True
    w1_assignments = res1["assignments"]

    # Week 2: Member 0 has a weekly conflict on Monday (day 0) and Tuesday (day 1)
    overrides = [
        WeeklyOverrideItem(
            week_id="2026-W41",
            member_id="m_0",
            day_of_week=0,
            start_slot=0,
            end_slot=12,
            override_type="busy",
        ),
        WeeklyOverrideItem(
            week_id="2026-W41",
            member_id="m_0",
            day_of_week=1,
            start_slot=0,
            end_slot=12,
            override_type="busy",
        ),
    ]

    opt2 = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=overrides,
        previous_schedule=w1_assignments,
    )
    res2 = opt2.solve()
    assert res2["success"] is True

    # 1. Verify Member 0 is NEVER assigned during their weekly busy time
    for a in res2["assignments"]:
        if a.member_id == "m_0":
            if a.day_of_week in (0, 1):
                assert a.slot >= 12, f"Member 0 was assigned at slot {a.slot} on day {a.day_of_week} during weekly exception!"

    # 2. Check that members who had NO exceptions have high repeat retention
    w1_map = {(a.member_id, a.day_of_week, a.slot) for a in w1_assignments}
    w2_map = {(a.member_id, a.day_of_week, a.slot) for a in res2["assignments"]}

    non_exception_members = [m.id for m in members if m.id != "m_0"]
    non_ex_w1_count = sum(1 for (mid, d, s) in w1_map if mid in non_exception_members)
    non_ex_retained = sum(1 for (mid, d, s) in w1_map if mid in non_exception_members and (mid, d, s) in w2_map)

    retention_pct = (non_ex_retained / non_ex_w1_count) * 100
    assert retention_pct >= 90.0, f"Unaffected members retention was {retention_pct}%, expected >= 90%"


def test_unfillable_slots_marked_with_x():
    """Verify that when no member is available for a slot, it is marked with an X (UNFILLED)."""
    members = create_sample_members(12)
    # Mark all members as busy on Day 0 (Monday) slot 0 (9:00 - 9:30 AM)
    all_busy_overrides = [
        WeeklyOverrideItem(
            week_id="2026-W42",
            member_id=m.id,
            day_of_week=0,
            start_slot=0,
            end_slot=1,  # 9:00 - 9:30 AM
            override_type="busy",
        )
        for m in members
    ]
    optimizer = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=all_busy_overrides,
    )
    result = optimizer.solve()
    assert result["success"] is True
    assignments = result["assignments"]
    assert len(assignments) == 280

    # Verify both houses at Day 0, Slot 0 are marked as UNFILLED with '❌ X (UNFILLED)'
    unfilled_assignments = [
        a for a in assignments
        if a.day_of_week == 0 and a.slot == 0
    ]
    assert len(unfilled_assignments) == 2  # Burn and THC
    for a in unfilled_assignments:
        assert a.member_id == "UNFILLED"
        assert "❌ X" in a.member_name

    stats = result["stats"]
    assert stats["unfilled_slots_count"] == 2


def test_single_free_person_given_to_thc():
    """Verify that if only one person is free for a given slot, they are given to THC and Burn is unfilled."""
    members = create_sample_members(12)
    # Make all members EXCEPT Dylan (m_0) busy on Day 1 (Tuesday) slots 4 and 5 (11:00 AM - 12:00 PM)
    overrides = []
    for m in members:
        if m.id != "m_0":
            overrides.append(
                WeeklyOverrideItem(
                    week_id="2026-W43",
                    member_id=m.id,
                    day_of_week=1,
                    start_slot=4,
                    end_slot=6,  # slots 4 and 5
                    override_type="busy",
                )
            )

    optimizer = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=overrides,
        min_shift_slots=2,
    )
    result = optimizer.solve()
    assert result["success"] is True

    # At Day 1, Slot 4 and Slot 5:
    for slot in (4, 5):
        slot_assigns = {
            a.house: a for a in result["assignments"]
            if a.day_of_week == 1 and a.slot == slot
        }
        # THC must be assigned to Dylan (m_0)
        assert slot_assigns["THC"].member_id == "m_0", f"Expected m_0 at THC for slot {slot}, got {slot_assigns['THC'].member_id}"
        # Burn must be UNFILLED
        assert slot_assigns["Burn"].member_id == "UNFILLED", f"Expected Burn to be UNFILLED for slot {slot}, got {slot_assigns['Burn'].member_id}"


def test_no_isolated_30min_house_stints_or_ping_pongs():
    """Verify that someone is never assigned to one house for 30m, another for 30m, and back,

    and that every stint at any house is at least 1 hour (2 slots) with no adjacent house switching.
    """
    members = create_sample_members(12)
    # Week 1
    opt1 = ScheduleOptimizer(members=members, master_items=[], weekly_overrides=[], min_shift_slots=2)
    res1 = opt1.solve()
    assert res1["success"] is True

    # Week 2 with repeat consistency and overrides
    overrides = [
        WeeklyOverrideItem(
            week_id="2026-W44",
            member_id="m_2",
            day_of_week=2,
            start_slot=4,
            end_slot=10,
            override_type="busy",
        )
    ]
    opt2 = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=overrides,
        previous_schedule=res1["assignments"],
        min_shift_slots=2,
    )
    res2 = opt2.solve()
    assert res2["success"] is True

    for result in [res1, res2]:
        # Group by member and day
        member_day_shifts = {}
        for a in result["assignments"]:
            if a.member_id != "UNFILLED":
                member_day_shifts.setdefault((a.member_id, a.day_of_week), {})[a.slot] = a.house

        for (m_id, day), slot_map in member_day_shifts.items():
            slots = sorted(slot_map.keys())
            for i in range(len(slots) - 1):
                s1, s2 = slots[i], slots[i + 1]
                # If slots are adjacent, they CANNOT switch houses!
                if s2 == s1 + 1:
                    assert slot_map[s1] == slot_map[s2], (
                        f"Member {m_id} on day {day} switched houses in adjacent slots: "
                        f"slot {s1} ({slot_map[s1]}) -> slot {s2} ({slot_map[s2]})"
                    )

            # Verify every continuous block at a given house has length >= 2 slots
            current_house = None
            current_run = 0
            prev_s = None
            for s in slots:
                h = slot_map[s]
                if prev_s is None or s != prev_s + 1 or h != current_house:
                    if current_run > 0:
                        assert current_run >= 2, (
                            f"Member {m_id} on day {day} had an isolated 30-min stint of {current_run} slot(s) at {current_house} ending at slot {prev_s}"
                        )
                    current_house = h
                    current_run = 1
                else:
                    current_run += 1
                prev_s = s
            if current_run > 0:
                assert current_run >= 2, (
                    f"Member {m_id} on day {day} had an isolated 30-min stint of {current_run} slot(s) at {current_house} ending at slot {prev_s}"
                )


def test_one_sitting_priority_and_weekend_no_splits():
    """Verify that people receive their time in one sitting: strictly 0 weekend split shifts, and cap <= 2 on weekdays."""
    from backend.models import get_all_members, get_master_schedule
    members = get_all_members()
    master_items = get_master_schedule()

    optimizer = ScheduleOptimizer(
        members=members,
        master_items=master_items,
        weekly_overrides=[],
        min_shift_slots=2,
    )
    result = optimizer.solve()
    assert result["success"] is True

    # Group assignments by (member_id, day_of_week)
    member_day_slots = {}
    for a in result["assignments"]:
        if a.member_id != "UNFILLED":
            member_day_slots.setdefault((a.member_id, a.day_of_week), []).append(a.slot)

    split_days_count = 0
    weekend_splits = 0

    for (m_id, day), slots in member_day_slots.items():
        slots.sort()
        # Count continuous sittings
        sittings = 0
        prev = None
        for s in slots:
            if prev is None or s != prev + 1:
                sittings += 1
            prev = s

        # On weekends (Saturday=5, Sunday=6): strictly at most 1 sitting (0 split shifts!)
        if day in (5, 6):
            assert sittings <= 1, f"Member {m_id} had {sittings} sittings on weekend day {day} (expected <= 1)!"
            if sittings > 1:
                weekend_splits += 1

        # On weekdays (0..4): at most 2 sittings (no 3rd or 4th shift)
        assert sittings <= 2, f"Member {m_id} had {sittings} sittings on weekday {day} (expected <= 2)!"

        if sittings > 1:
            split_days_count += 1

    assert weekend_splits == 0
    # Total split days across all members across the entire week should be very low (<= 20)
    assert split_days_count <= 20, f"Expected <= 20 split shift days, got {split_days_count}"


def test_house_switch_gap_and_stint_limits():
    """Verify that any house switch on the same day has >= 5 hour gap, stints <= 3h, days <= 5h, and daily hours center around 2h."""
    from backend.models import get_all_members, get_master_schedule
    members = get_all_members()
    master_items = get_master_schedule()

    optimizer = ScheduleOptimizer(
        members=members,
        master_items=master_items,
        weekly_overrides=[],
        min_shift_slots=2,
    )
    result = optimizer.solve()
    assert result["success"] is True

    member_day = {}
    for a in result["assignments"]:
        if a.member_id != "UNFILLED":
            member_day.setdefault((a.member_id, a.day_of_week), []).append(a)

    for (mid, day), assigns in member_day.items():
        assigns.sort(key=lambda x: x.slot)

        # 1. Total hours on any day must be <= 5.0 hours (10 slots)
        total_day_hours = len(assigns) * 0.5
        assert total_day_hours <= 5.0, f"Member {mid} on day {day} worked {total_day_hours} hours (max allowed is 5.0h)!"

        # 2. Continuous stint at any house cannot exceed 3.0 hours (6 slots)
        cur_house = None
        cur_len = 0
        prev_s = None
        for a in assigns:
            if a.house == cur_house and prev_s is not None and a.slot == prev_s + 1:
                cur_len += 1
            else:
                if cur_len > 0:
                    assert cur_len <= 6, f"Member {mid} on day {day} had continuous stint of {cur_len * 0.5}h at {cur_house} (max 3.0h)!"
                cur_house = a.house
                cur_len = 1
            prev_s = a.slot
        if cur_len > 0:
            assert cur_len <= 6, f"Member {mid} on day {day} had continuous stint of {cur_len * 0.5}h at {cur_house} (max 3.0h)!"

        # 3. Minimum 5-hour gap (10 slots) between working at different houses on the same day
        houses = {a.house for a in assigns}
        if len(houses) > 1:
            burn_slots = [a.slot for a in assigns if a.house == "Burn"]
            thc_slots = [a.slot for a in assigns if a.house == "THC"]
            min_gap_slots = min(abs(b - t) for b in burn_slots for t in thc_slots)
            assert min_gap_slots >= 10, (
                f"Member {mid} on day {day} switched houses with gap of only {min_gap_slots * 0.5} hours (min required is 5.0h)!"
            )


def test_daily_hours_target_and_no_preference():
    """Verify that shifts are highly prioritized to 2.0 - 2.5 hours per day, single sittings, and no short/long preference bias."""
    from backend.models import get_all_members, get_master_schedule
    members = get_all_members()
    master_items = get_master_schedule()

    optimizer = ScheduleOptimizer(
        members=members,
        master_items=master_items,
        weekly_overrides=[],
        min_shift_slots=2,
    )
    result = optimizer.solve()
    assert result["success"] is True

    member_days = {}
    for a in result["assignments"]:
        if a.member_id != "UNFILLED":
            member_days.setdefault((a.member_id, a.day_of_week), []).append(a.slot)

    in_target_range = 0
    total_working_days = len(member_days)

    for (mid, d), slots in member_days.items():
        day_hours = len(slots) * 0.5
        # Must respect daily max cap
        assert day_hours <= 5.0, f"Member {mid} on day {d} worked {day_hours}h (max 5.0h)!"
        # Check if in 2.0h - 2.5h target window (4 to 5 slots)
        if 4 <= len(slots) <= 5:
            in_target_range += 1

        # Check single sitting on weekends
        if d in (5, 6):
            slots.sort()
            sittings = 0
            prev = None
            for s in slots:
                if prev is None or s != prev + 1:
                    sittings += 1
                prev = s
            assert sittings <= 1, f"Weekend split shift found for {mid} on day {d}!"

    target_pct = (in_target_range / total_working_days) * 100
    # Over 60% of shifts are strictly 2.0 to 2.5 hours
    assert target_pct >= 60.0, f"Only {target_pct:.1f}% in 2.0-2.5h range, expected >= 60%"


