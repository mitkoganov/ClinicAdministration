// Focused executable check for app/lib/calendar-time.ts (MED-005). There
// is no frontend test framework in this repository - see
// check-clinic-selection.ts for the established pattern this follows.
// Run via `npm run check:calendar-time`.

import {
  addDays,
  formatTimeRange,
  localDateString,
  localDayBoundsUtc,
  localWeekBoundsUtc,
  parseInstant,
  startOfWeek,
  weekDates,
} from "../app/lib/calendar-time.ts";

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  const actualJson = JSON.stringify(actual);
  const expectedJson = JSON.stringify(expected);
  if (actualJson !== expectedJson) {
    throw new Error(`${message}: expected ${expectedJson}, got ${actualJson}`);
  }
}

function testParseInstantRejectsGarbage(): void {
  let threw = false;
  try {
    parseInstant("not-a-date");
  } catch {
    threw = true;
  }
  if (!threw) {
    throw new Error("parseInstant must throw on an unparseable string, not return Invalid Date");
  }
}

function testParseInstantAcceptsOffsetAwareIso(): void {
  const parsed = parseInstant("2026-07-24T09:30:00+00:00");
  assertEqual(parsed.toISOString(), "2026-07-24T09:30:00.000Z", "offset-aware ISO must parse to the matching UTC instant");
}

function testLocalDateStringUsesTargetTimezoneNotUtc(): void {
  // 2026-01-05T23:30:00Z is already 2026-01-06 in Europe/Sofia (UTC+2 in
  // January) - if this ever fell back to reading the UTC calendar date,
  // it would wrongly report 2026-01-05.
  const date = localDateString("2026-01-05T23:30:00+00:00", "Europe/Sofia");
  assertEqual(date, "2026-01-06", "local date must reflect the target timezone, not UTC");
}

function testFormatTimeRangeRendersBothEndsInTargetTimezone(): void {
  const range = formatTimeRange(
    "2026-07-24T09:00:00+00:00",
    "2026-07-24T09:30:00+00:00",
    "Europe/Sofia",
  );
  // Europe/Sofia is UTC+3 in July (EEST) - 09:00Z/09:30Z become 12:00/12:30.
  assertEqual(range, "12:00–12:30", "time range must be rendered in the tenant's own timezone");
}

function testAddDaysHandlesMonthBoundary(): void {
  assertEqual(addDays("2026-01-31", 1), "2026-02-01", "addDays must roll over a month boundary");
}

function testAddDaysHandlesNegativeAcrossYearBoundary(): void {
  assertEqual(addDays("2026-01-01", -1), "2025-12-31", "addDays must roll back across a year boundary");
}

function testLocalDayBoundsUtcMatchesKnownOffset(): void {
  // Europe/Sofia is UTC+2 in January (EET, no DST) - local midnight on
  // 2026-01-15 is 2026-01-14T22:00:00Z, and the next local midnight is
  // 2026-01-15T22:00:00Z.
  const bounds = localDayBoundsUtc("2026-01-15", "Europe/Sofia");
  assertEqual(bounds.start.toISOString(), "2026-01-14T22:00:00.000Z", "day start must match the known UTC+2 offset");
  assertEqual(bounds.end.toISOString(), "2026-01-15T22:00:00.000Z", "day end must be exactly 24h later at this offset");
}

function testLocalDayBoundsUtcAcrossDstTransition(): void {
  // Europe/Sofia springs forward on the last Sunday of March (2026-03-29):
  // clocks jump from 03:00 EET (UTC+2) to 04:00 EEST (UTC+3). Local
  // midnight on the DST day itself is still governed by the OLD offset
  // (UTC+2) since the jump happens at 03:00 local, not at midnight.
  const bounds = localDayBoundsUtc("2026-03-29", "Europe/Sofia");
  assertEqual(bounds.start.toISOString(), "2026-03-28T22:00:00.000Z", "start of the DST-transition day uses the pre-transition offset");
  // The FOLLOWING midnight is already under the new UTC+3 offset, so the
  // day is only 23 real hours long - the bound must reflect that, not a
  // naive +24h.
  assertEqual(bounds.end.toISOString(), "2026-03-29T21:00:00.000Z", "end of the DST-transition day reflects the shortened 23h day");
}

function testStartOfWeekIsMondayForAMidWeekDate(): void {
  // 2026-07-24 is a Friday.
  assertEqual(startOfWeek("2026-07-24"), "2026-07-20", "startOfWeek must roll back to the preceding Monday");
}

function testStartOfWeekIsIdempotentOnAMonday(): void {
  assertEqual(startOfWeek("2026-07-20"), "2026-07-20", "startOfWeek on a Monday must return that same Monday");
}

function testStartOfWeekHandlesSunday(): void {
  // 2026-07-26 is a Sunday - the LAST day of the week starting 2026-07-20.
  assertEqual(startOfWeek("2026-07-26"), "2026-07-20", "a Sunday must roll back to Monday of the SAME week, not the next one");
}

function testWeekDatesReturnsExactlySevenConsecutiveDates(): void {
  const dates = weekDates("2026-07-20");
  assertEqual(dates.length, 7, "a week must contain exactly 7 dates");
  assertEqual(dates, [
    "2026-07-20",
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
    "2026-07-24",
    "2026-07-25",
    "2026-07-26",
  ], "week dates must be the 7 consecutive Monday..Sunday calendar dates");
}

function testLocalWeekBoundsUtcSpansMondayToNextMonday(): void {
  // Europe/Sofia is UTC+2 in January (no DST) - Monday 2026-01-12
  // midnight is 2026-01-11T22:00:00Z, and the week ends at the NEXT
  // Monday's midnight, 2026-01-19T22:00:00Z (i.e. 2026-01-12..2026-01-18
  // inclusive, 7 days).
  const bounds = localWeekBoundsUtc("2026-01-12", "Europe/Sofia");
  assertEqual(bounds.start.toISOString(), "2026-01-11T22:00:00.000Z", "week start must match the Monday's local midnight");
  assertEqual(bounds.end.toISOString(), "2026-01-18T22:00:00.000Z", "week end must be the following Monday's local midnight, 7 days later");
}

function testLocalWeekBoundsUtcAcrossDstTransition(): void {
  // The week containing the 2026-03-29 Europe/Sofia spring-forward
  // transition is 23h shorter than a nominal 168h week - the bound must
  // reflect the real elapsed time, not a naive 7*24h.
  const weekStart = startOfWeek("2026-03-29");
  const bounds = localWeekBoundsUtc(weekStart, "Europe/Sofia");
  const elapsedHours = (bounds.end.getTime() - bounds.start.getTime()) / (60 * 60 * 1000);
  assertEqual(elapsedHours, 167, "a week spanning the spring-forward transition must be 167h, not a naive 168h");
}

function testLocalWeekBoundsUtcAcrossFallBackDstTransition(): void {
  // The week containing the 2026-10-25 Europe/Sofia fall-back transition
  // gains an hour - 169h, not a naive 168h.
  const weekStart = startOfWeek("2026-10-25");
  const bounds = localWeekBoundsUtc(weekStart, "Europe/Sofia");
  const elapsedHours = (bounds.end.getTime() - bounds.start.getTime()) / (60 * 60 * 1000);
  assertEqual(elapsedHours, 169, "a week spanning the fall-back transition must be 169h, not a naive 168h");
}

const checks: Array<[string, () => void]> = [
  ["parseInstant rejects garbage", testParseInstantRejectsGarbage],
  ["parseInstant accepts offset-aware ISO", testParseInstantAcceptsOffsetAwareIso],
  ["localDateString uses target timezone, not UTC", testLocalDateStringUsesTargetTimezoneNotUtc],
  ["formatTimeRange renders both ends in target timezone", testFormatTimeRangeRendersBothEndsInTargetTimezone],
  ["addDays handles month boundary", testAddDaysHandlesMonthBoundary],
  ["addDays handles negative across year boundary", testAddDaysHandlesNegativeAcrossYearBoundary],
  ["localDayBoundsUtc matches known offset", testLocalDayBoundsUtcMatchesKnownOffset],
  ["localDayBoundsUtc across a DST transition", testLocalDayBoundsUtcAcrossDstTransition],
  ["startOfWeek rolls back a mid-week date to Monday", testStartOfWeekIsMondayForAMidWeekDate],
  ["startOfWeek is idempotent on a Monday", testStartOfWeekIsIdempotentOnAMonday],
  ["startOfWeek handles Sunday correctly", testStartOfWeekHandlesSunday],
  ["weekDates returns exactly 7 consecutive dates", testWeekDatesReturnsExactlySevenConsecutiveDates],
  ["localWeekBoundsUtc spans Monday to next Monday", testLocalWeekBoundsUtcSpansMondayToNextMonday],
  ["localWeekBoundsUtc across a spring-forward DST transition", testLocalWeekBoundsUtcAcrossDstTransition],
  ["localWeekBoundsUtc across a fall-back DST transition", testLocalWeekBoundsUtcAcrossFallBackDstTransition],
];

let failures = 0;
for (const [name, check] of checks) {
  try {
    check();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

if (failures > 0) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log(`${checks.length} check(s) passed`);
