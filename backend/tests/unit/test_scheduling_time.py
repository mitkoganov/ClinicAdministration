from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.scheduling_time import (
    clip,
    combine_local,
    duration,
    expand_with_buffers,
    is_adjacent,
    is_ambiguous_local_time,
    is_nonexistent_local_time,
    merge,
    overlaps,
    subtract,
    to_tenant_local,
)

SOFIA = ZoneInfo("Europe/Sofia")


def _dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_overlaps_true_for_intersecting_intervals():
    a = (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 11))
    b = (_dt(2026, 1, 1, 10, 30), _dt(2026, 1, 1, 11, 30))
    assert overlaps(a, b)
    assert overlaps(b, a)


def test_overlaps_false_for_half_open_touching_intervals():
    a = (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 10, 30))
    b = (_dt(2026, 1, 1, 10, 30), _dt(2026, 1, 1, 11))
    assert not overlaps(a, b)
    assert not overlaps(b, a)


def test_overlaps_false_for_disjoint_intervals():
    a = (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10))
    b = (_dt(2026, 1, 1, 11), _dt(2026, 1, 1, 12))
    assert not overlaps(a, b)


def test_is_adjacent_true_for_touching_intervals():
    a = (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 10, 30))
    b = (_dt(2026, 1, 1, 10, 30), _dt(2026, 1, 1, 11))
    assert is_adjacent(a, b)
    assert is_adjacent(b, a)


def test_is_adjacent_false_for_overlapping_intervals():
    a = (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 11))
    b = (_dt(2026, 1, 1, 10, 30), _dt(2026, 1, 1, 11, 30))
    assert not is_adjacent(a, b)


def test_duration_returns_timedelta():
    assert duration((_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 11, 30))) == timedelta(
        hours=1, minutes=30
    )


def test_clip_restricts_to_bounds():
    interval = (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 12))
    bounds = (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 11))
    assert clip(interval, bounds) == bounds


def test_clip_returns_none_when_disjoint():
    interval = (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10))
    bounds = (_dt(2026, 1, 1, 11), _dt(2026, 1, 1, 12))
    assert clip(interval, bounds) is None


def test_clip_returns_none_when_degenerate():
    interval = (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 10))
    bounds = (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 12))
    assert clip(interval, bounds) is None


def test_expand_with_buffers():
    interval = (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 10, 30))
    expanded = expand_with_buffers(interval, timedelta(minutes=10), timedelta(minutes=5))
    assert expanded == (_dt(2026, 1, 1, 9, 50), _dt(2026, 1, 1, 10, 35))


def test_subtract_removes_a_single_overlapping_interval():
    base = (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 17))
    remove = [(_dt(2026, 1, 1, 12), _dt(2026, 1, 1, 13))]
    result = subtract(base, remove)
    assert result == [
        (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 12)),
        (_dt(2026, 1, 1, 13), _dt(2026, 1, 1, 17)),
    ]


def test_subtract_removes_multiple_overlapping_intervals():
    base = (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 17))
    remove = [
        (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 11)),
        (_dt(2026, 1, 1, 14), _dt(2026, 1, 1, 15)),
    ]
    result = subtract(base, remove)
    assert result == [
        (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10)),
        (_dt(2026, 1, 1, 11), _dt(2026, 1, 1, 14)),
        (_dt(2026, 1, 1, 15), _dt(2026, 1, 1, 17)),
    ]


def test_subtract_removes_entire_base_when_fully_covered():
    base = (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 17))
    remove = [(_dt(2026, 1, 1, 8), _dt(2026, 1, 1, 18))]
    assert subtract(base, remove) == []


def test_subtract_handles_overlapping_remove_intervals():
    base = (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 17))
    remove = [
        (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 13)),
        (_dt(2026, 1, 1, 12), _dt(2026, 1, 1, 14)),
    ]
    result = subtract(base, remove)
    assert result == [
        (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10)),
        (_dt(2026, 1, 1, 14), _dt(2026, 1, 1, 17)),
    ]


def test_subtract_no_removal_when_disjoint():
    base = (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10))
    remove = [(_dt(2026, 1, 1, 11), _dt(2026, 1, 1, 12))]
    assert subtract(base, remove) == [base]


def test_merge_combines_touching_intervals():
    intervals = [
        (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10)),
        (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 11)),
    ]
    assert merge(intervals) == [(_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 11))]


def test_merge_combines_overlapping_intervals():
    intervals = [
        (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10, 30)),
        (_dt(2026, 1, 1, 10), _dt(2026, 1, 1, 11)),
    ]
    assert merge(intervals) == [(_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 11))]


def test_merge_keeps_disjoint_intervals_separate():
    intervals = [
        (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10)),
        (_dt(2026, 1, 1, 12), _dt(2026, 1, 1, 13)),
    ]
    assert merge(intervals) == intervals


def test_merge_sorts_out_of_order_input():
    intervals = [
        (_dt(2026, 1, 1, 12), _dt(2026, 1, 1, 13)),
        (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10)),
    ]
    assert merge(intervals) == [
        (_dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10)),
        (_dt(2026, 1, 1, 12), _dt(2026, 1, 1, 13)),
    ]


# --- Timezone / DST ------------------------------------------------------


def test_combine_local_normal_day_produces_correct_utc():
    # Europe/Sofia is UTC+2 in winter (EET, no DST).
    result = combine_local(date(2026, 1, 15), time(9, 0), SOFIA)
    assert result == _dt(2026, 1, 15, 7, 0)


def test_combine_local_summer_time_produces_correct_utc_offset():
    # Europe/Sofia is UTC+3 in summer (EEST).
    result = combine_local(date(2026, 7, 15), time(9, 0), SOFIA)
    assert result == _dt(2026, 7, 15, 6, 0)


def test_to_tenant_local_round_trips():
    utc_instant = _dt(2026, 1, 15, 7, 0)
    local = to_tenant_local(utc_instant, SOFIA)
    assert local.hour == 9
    assert local.utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize(
    ("year", "naive_hour_minute"),
    [
        (2026, (3, 30)),  # EU spring-forward 2026: 03:00->04:00 on the last Sunday of March
    ],
)
def test_is_nonexistent_local_time_detects_spring_forward_gap(year, naive_hour_minute):
    # 2026's EU spring-forward transition is 2026-03-29 (last Sunday of March).
    naive = datetime(year, 3, 29, *naive_hour_minute)
    assert is_nonexistent_local_time(naive, SOFIA) is True


def test_is_nonexistent_local_time_false_for_a_normal_time():
    naive = datetime(2026, 3, 29, 9, 0)
    assert is_nonexistent_local_time(naive, SOFIA) is False


def test_combine_local_skips_nonexistent_time_by_default():
    result = combine_local(date(2026, 3, 29), time(3, 30), SOFIA)
    assert result is None


def test_combine_local_raises_for_nonexistent_time_when_requested():
    with pytest.raises(ValueError, match="does not exist"):
        combine_local(date(2026, 3, 29), time(3, 30), SOFIA, on_nonexistent="raise")


def test_is_ambiguous_local_time_detects_fall_back_gap():
    # 2026's EU fall-back transition is 2026-10-25 (last Sunday of October):
    # 04:00 EEST -> 03:00 EET, so local times in [03:00, 04:00) occur twice.
    naive = datetime(2026, 10, 25, 3, 30)
    assert is_ambiguous_local_time(naive, SOFIA) is True


def test_is_ambiguous_local_time_false_for_a_normal_time():
    naive = datetime(2026, 10, 25, 9, 0)
    assert is_ambiguous_local_time(naive, SOFIA) is False


def test_combine_local_resolves_ambiguous_time_to_first_occurrence():
    # The first (fold=0) occurrence of 03:30 on the fall-back day is still
    # in EEST (UTC+3) - i.e. UTC 00:30, not the second occurrence's 01:30.
    result = combine_local(date(2026, 10, 25), time(3, 30), SOFIA)
    assert result == _dt(2026, 10, 25, 0, 30)


def test_combine_local_utc_timezone_has_no_dst_edge_cases():
    result = combine_local(date(2026, 3, 29), time(3, 30), ZoneInfo("UTC"))
    assert result == _dt(2026, 3, 29, 3, 30)
