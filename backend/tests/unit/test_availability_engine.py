from datetime import UTC, datetime, timedelta

from app.services.availability_engine import compute_bookable_slots, compute_free_intervals


def _dt(h, mi=0):
    return datetime(2026, 1, 5, h, mi, tzinfo=UTC)


def test_compute_free_intervals_subtracts_blocking_from_schedule():
    schedule = [(_dt(9), _dt(17))]
    blocking = [(_dt(12), _dt(13))]
    result = compute_free_intervals(schedule, blocking)
    assert result == [(_dt(9), _dt(12)), (_dt(13), _dt(17))]


def test_compute_free_intervals_merges_multiple_schedule_windows():
    schedule = [(_dt(9), _dt(12)), (_dt(12), _dt(17))]
    result = compute_free_intervals(schedule, [])
    assert result == [(_dt(9), _dt(17))]


def test_compute_free_intervals_with_no_blocking_returns_full_schedule():
    schedule = [(_dt(9), _dt(17))]
    assert compute_free_intervals(schedule, []) == [(_dt(9), _dt(17))]


def test_compute_free_intervals_fully_blocked_returns_empty():
    schedule = [(_dt(9), _dt(17))]
    blocking = [(_dt(8), _dt(18))]
    assert compute_free_intervals(schedule, blocking) == []


def test_compute_bookable_slots_generates_consecutive_slots():
    free = [(_dt(9), _dt(10))]
    slots = compute_bookable_slots(free, timedelta(minutes=30), timedelta(minutes=30), _dt(0))
    assert slots == [(_dt(9), _dt(9, 30)), (_dt(9, 30), _dt(10))]


def test_compute_bookable_slots_respects_finer_granularity():
    free = [(_dt(9), _dt(10))]
    slots = compute_bookable_slots(free, timedelta(minutes=30), timedelta(minutes=15), _dt(0))
    assert slots == [
        (_dt(9), _dt(9, 30)),
        (_dt(9, 15), _dt(9, 45)),
        (_dt(9, 30), _dt(10)),
    ]


def test_compute_bookable_slots_excludes_past_time():
    free = [(_dt(9), _dt(11))]
    slots = compute_bookable_slots(
        free, timedelta(minutes=30), timedelta(minutes=30), not_before=_dt(10)
    )
    assert slots == [(_dt(10), _dt(10, 30)), (_dt(10, 30), _dt(11))]


def test_compute_bookable_slots_no_slot_when_interval_too_short():
    free = [(_dt(9), _dt(9, 15))]
    slots = compute_bookable_slots(free, timedelta(minutes=30), timedelta(minutes=30), _dt(0))
    assert slots == []


def test_compute_bookable_slots_across_multiple_free_intervals():
    free = [(_dt(9), _dt(10)), (_dt(14), _dt(15))]
    slots = compute_bookable_slots(free, timedelta(minutes=60), timedelta(minutes=60), _dt(0))
    assert slots == [(_dt(9), _dt(10)), (_dt(14), _dt(15))]
