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
    """Verify that all 336 slots are staffed, no double bookings, and hours are fair."""
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
    assert len(assignments) == 7 * 24 * 2  # exactly 336 slots

    # Verify coverage: for every day, slot, house, exactly 1 person
    coverage = {}
    for a in assignments:
        key = (a.day_of_week, a.slot, a.house)
        assert key not in coverage, f"Duplicate coverage at {key}"
        coverage[key] = a.member_id

    assert len(coverage) == 336

    # Verify no double booking (same person at both houses at the same slot)
    person_slots = set()
    for a in assignments:
        key = (a.member_id, a.day_of_week, a.slot)
        assert key not in person_slots, f"Person {a.member_id} double booked at {key}"
        person_slots.add(key)

    # Verify equal hours: 336 slots / 12 people = 28 slots each = exactly 14.0 hours!
    member_counts = {m.id: 0 for m in members}
    for a in assignments:
        member_counts[a.member_id] += 1

    for m_id, count in member_counts.items():
        assert count == 28, f"Member {m_id} expected 28 slots (14h), got {count}"

def test_coverage_13_members():
    """Verify 13 members balance cleanly around ~13 hours (25-26 slots each)."""
    members = create_sample_members(13)
    optimizer = ScheduleOptimizer(
        members=members,
        master_items=[],
        weekly_overrides=[],
    )
    result = optimizer.solve()
    assert result["success"] is True
    assert len(result["assignments"]) == 336

    member_counts = {m.id: 0 for m in members}
    for a in result["assignments"]:
        member_counts[a.member_id] += 1

    # 336 / 13 = 25.84 -> each member should get either 25 or 26 slots
    for m_id, count in member_counts.items():
        assert 25 <= count <= 26, f"Member {m_id} got {count} slots, expected 25 or 26"

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
            end_slot=24,
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
