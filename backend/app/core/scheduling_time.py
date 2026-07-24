"""Pure, DB-independent time/interval utilities for the MED-005
availability engine. Every function here is deterministic given its
inputs - no database access, no `datetime.now()` call hidden inside a
"helper" - so `AvailabilityService` (and this module itself) can be unit
tested with plain Python values.

Intervals are always HALF-OPEN `[start, end)` `datetime` pairs
(`tuple[datetime, datetime]`), always timezone-aware UTC once they leave
this module's local-time-combining functions - never naive.

DST policy (see tasks/current/task.md "Timezone policy" for the product
rationale):
  * a LOCAL time that does not exist on a given date (spring-forward gap)
    is SKIPPED when generating recurring-schedule occurrences (never
    silently shifted to a neighboring hour) - `combine_local` returns
    `None` for that occurrence with `on_nonexistent="skip"`.
  * a LOCAL time that occurs twice (fall-back) resolves to its FIRST
    occurrence (`fold=0`) - a documented, tested convention, not
    incidental `zoneinfo` behavior.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]


def is_nonexistent_local_time(naive_dt: datetime, tz: ZoneInfo) -> bool:
    """True if `naive_dt` falls inside a DST spring-forward gap for `tz`
    - i.e. that local wall-clock time never actually occurs. Detected by
    round-tripping naive -> aware -> UTC -> back to local and checking the
    result still matches; a gap always fails to round-trip because
    `zoneinfo` resolves the "nonexistent" naive value to SOME UTC instant
    (per PEP 495), but converting that instant back to local time never
    reproduces the original (nonexistent) wall-clock value."""
    aware = naive_dt.replace(tzinfo=tz)
    back = aware.astimezone(UTC).astimezone(tz)
    return back.replace(tzinfo=None) != naive_dt


def is_ambiguous_local_time(naive_dt: datetime, tz: ZoneInfo) -> bool:
    """True if `naive_dt` occurs twice for `tz` (DST fall-back) - the two
    `fold` values resolve to different UTC offsets."""
    fold0 = naive_dt.replace(tzinfo=tz, fold=0)
    fold1 = naive_dt.replace(tzinfo=tz, fold=1)
    return fold0.utcoffset() != fold1.utcoffset()


def combine_local(
    local_date: date,
    local_time: time,
    tz: ZoneInfo,
    *,
    on_nonexistent: str = "skip",
) -> datetime | None:
    """Combines a local calendar date + wall-clock time in `tz` into a
    concrete, timezone-aware UTC `datetime`. Ambiguous (fall-back) times
    always resolve to their first occurrence (`fold=0`). Nonexistent
    (spring-forward gap) times return `None` when `on_nonexistent="skip"`
    (the recurring-schedule-generation policy), or raise `ValueError` when
    `on_nonexistent="raise"`."""
    naive = datetime.combine(local_date, local_time)
    if is_nonexistent_local_time(naive, tz):
        if on_nonexistent == "skip":
            return None
        raise ValueError(
            f"{naive.isoformat()} does not exist in timezone {tz.key!r} (DST spring-forward gap)."
        )
    aware = naive.replace(tzinfo=tz, fold=0)
    return aware.astimezone(UTC)


def to_tenant_local(instant: datetime, tz: ZoneInfo) -> datetime:
    """UTC (or any aware) `datetime` -> the equivalent aware `datetime` in
    `tz`. Never strips the offset - callers needing a naive local value do
    that explicitly and document why."""
    return instant.astimezone(tz)


def overlaps(a: Interval, b: Interval) -> bool:
    """Half-open overlap: `[start, end)` intervals touching at a shared
    boundary (`a.end == b.start`) do NOT overlap - this is what allows an
    appointment ending at 10:30 and one starting at 10:30 to coexist."""
    return a[0] < b[1] and b[0] < a[1]


def is_adjacent(a: Interval, b: Interval) -> bool:
    """True if the two intervals share exactly one boundary and do not
    overlap (already implied, but asserted defensively)."""
    return (a[1] == b[0] or b[1] == a[0]) and not overlaps(a, b)


def duration(interval: Interval) -> timedelta:
    return interval[1] - interval[0]


def clip(interval: Interval, bounds: Interval) -> Interval | None:
    """`interval` restricted to `bounds` - `None` if the result would be
    empty (no overlap) or degenerate (`start >= end`)."""
    start = max(interval[0], bounds[0])
    end = min(interval[1], bounds[1])
    if start >= end:
        return None
    return (start, end)


def expand_with_buffers(
    interval: Interval, buffer_before: timedelta, buffer_after: timedelta
) -> Interval:
    """The full OPERATIONAL interval a booking occupies, including
    buffers - used by the availability engine to compute how much free
    time is actually required around the bookable core interval, never
    persisted as a separate DB row in this task (see task.md's chosen
    design: buffers participate in availability computation and the
    service-layer overlap check, not a second persisted interval column)."""
    return (interval[0] - buffer_before, interval[1] + buffer_after)


def subtract(base: Interval, remove: Iterable[Interval]) -> list[Interval]:
    """`base` minus every interval in `remove` (each also half-open) -
    returns zero or more disjoint remaining sub-intervals, sorted by
    start. `remove` intervals may themselves overlap each other; the
    result is still correct (equivalent to unioning `remove` first)."""
    remaining = [base]
    for cut in sorted(remove, key=lambda iv: iv[0]):
        next_remaining: list[Interval] = []
        for piece in remaining:
            if not overlaps(piece, cut):
                next_remaining.append(piece)
                continue
            if piece[0] < cut[0]:
                next_remaining.append((piece[0], cut[0]))
            if cut[1] < piece[1]:
                next_remaining.append((cut[1], piece[1]))
        remaining = next_remaining
    return sorted(remaining, key=lambda iv: iv[0])


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Sorted, disjoint intervals with any touching/overlapping pair
    combined into one - the standard interval-merge algorithm, half-open
    aware (two intervals sharing a boundary ARE merged here, unlike
    `overlaps`, since a merged free-time listing should present one
    continuous block rather than two artificially adjacent ones)."""
    ordered = sorted(intervals, key=lambda iv: iv[0])
    merged: list[Interval] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
