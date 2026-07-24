"""Pure availability computation - no database access, no `datetime.now()`
call - so it can be unit tested with plain Python inputs, independent of
`AvailabilityService` (the DB-backed wrapper in
app.services.availability_service). Slots are never materialized as
database rows anywhere in this module or its caller."""

from datetime import datetime, timedelta

from app.core.scheduling_time import Interval, merge, subtract

# Slot granularity defaults to the service type's own duration - i.e.
# consecutive, non-overlapping candidate slots - per tasks/current/
# task.md's own suggested default ("default: the service type's own
# duration"). A caller may pass a finer granularity (e.g. 15 minutes) to
# offer more start-time choices within the same free interval.


def compute_free_intervals(
    schedule_intervals: list[Interval], blocking_intervals: list[Interval]
) -> list[Interval]:
    """`schedule_intervals` are the provider's own working-hour windows for
    the requested range (already reduced by that schedule's own recurring
    breaks) - `blocking_intervals` are calendar blocks and existing
    blocking-status appointments. Returns the merged, disjoint free time
    across all schedule windows."""
    free: list[Interval] = []
    for window in schedule_intervals:
        free.extend(subtract(window, blocking_intervals))
    return merge(free)


def compute_bookable_slots(
    free_intervals: list[Interval],
    slot_duration: timedelta,
    granularity: timedelta,
    not_before: datetime,
) -> list[Interval]:
    """Every `[start, start + slot_duration)` slot that fits entirely
    within a free interval, stepping by `granularity`, never starting
    before `not_before` (the past-time exclusion boundary - callers pass
    the current server UTC time)."""
    if slot_duration <= timedelta(0):
        raise ValueError("slot_duration must be positive.")
    if granularity <= timedelta(0):
        raise ValueError("granularity must be positive.")

    slots: list[Interval] = []
    for start, end in free_intervals:
        cursor = max(start, not_before)
        while cursor + slot_duration <= end:
            slots.append((cursor, cursor + slot_duration))
            cursor += granularity
    return slots
