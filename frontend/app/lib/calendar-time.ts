// Pure, dependency-free date/time helpers for MED-005 (Appointments and
// Calendar). Every timestamp exchanged with the backend is an
// offset-aware ISO 8601 string (e.g. "2026-07-24T09:30:00+00:00") - this
// module is the single place that parses/formats those, so no page ever
// does naive string-splicing (`new Date("2024-01-01")`-style guessing)
// or renders a timestamp without an explicit target timezone.

/** Parses an offset-aware ISO 8601 timestamp. Throws instead of silently
 * returning an Invalid Date, since a malformed backend timestamp is a bug
 * worth surfacing loudly rather than rendering "Invalid Date" in the UI. */
export function parseInstant(iso: string): Date {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error(`Not a valid ISO 8601 timestamp: ${iso}`);
  }
  return parsed;
}

/** Renders an instant as a wall-clock date+time string in `timezone`
 * (an IANA name, e.g. "Europe/Sofia") - never the browser's local
 * timezone, since a clinic's calendar must always read in the clinic's
 * own timezone regardless of where the viewer happens to be. */
export function formatInstant(iso: string, timezone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(parseInstant(iso));
}

/** Just the wall-clock time-of-day portion, in `timezone`. Always
 * 24-hour (`hourCycle: "h23"`) regardless of viewer locale, so
 * `formatTimeRange` below never depends on a locale-specific AM/PM
 * convention. */
export function formatTimeOfDay(iso: string, timezone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(parseInstant(iso));
}

/** "09:30–10:00" - both ends rendered in the same timezone. */
export function formatTimeRange(startIso: string, endIso: string, timezone: string): string {
  return `${formatTimeOfDay(startIso, timezone)}–${formatTimeOfDay(endIso, timezone)}`;
}

/** The calendar date (YYYY-MM-DD) an instant falls on on the wall clock
 * in `timezone` - used to group appointments/blocks by day and to know
 * which local day a UTC instant belongs to (never the UTC calendar date,
 * which can differ from the clinic's local date near midnight). */
export function localDateString(iso: string, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(parseInstant(iso));
  const year = parts.find((p) => p.type === "year")?.value;
  const month = parts.find((p) => p.type === "month")?.value;
  const day = parts.find((p) => p.type === "day")?.value;
  if (!year || !month || !day) {
    throw new Error(`Could not derive a local date for ${iso} in ${timezone}`);
  }
  return `${year}-${month}-${day}`;
}

/** Today's date (YYYY-MM-DD) as seen on the wall clock in `timezone`. */
export function todayInTimezone(timezone: string, now: Date = new Date()): string {
  return localDateString(now.toISOString(), timezone);
}

const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Adds `days` (may be negative) to a YYYY-MM-DD date string, entirely in
 * calendar-date arithmetic - never via a `Date` constructed from the
 * string directly in the browser's local timezone, which would silently
 * shift by a day near a DST boundary or a non-UTC browser timezone. */
export function addDays(dateString: string, days: number): string {
  const match = DATE_PATTERN.exec(dateString);
  if (!match) {
    throw new Error(`Not a YYYY-MM-DD date string: ${dateString}`);
  }
  const [, yearStr, monthStr, dayStr] = match;
  // noon UTC sidesteps any DST-transition-at-midnight edge case in the
  // arithmetic itself - only the calendar date (Y-M-D) is ever read back.
  const asUtcNoon = new Date(
    Date.UTC(Number(yearStr), Number(monthStr) - 1, Number(dayStr), 12, 0, 0),
  );
  asUtcNoon.setUTCDate(asUtcNoon.getUTCDate() + days);
  const year = asUtcNoon.getUTCFullYear().toString().padStart(4, "0");
  const month = (asUtcNoon.getUTCMonth() + 1).toString().padStart(2, "0");
  const day = asUtcNoon.getUTCDate().toString().padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** The UTC instant boundaries `[start, end)` for the given local calendar
 * date in `timezone` - used to query `date_from`/`date_to`-by-datetime
 * endpoints (appointments, calendar blocks) for exactly one local day.
 * Derived by binary-searching the UTC offset rather than assuming a
 * fixed offset, so it stays correct across a DST transition. */
export function localDayBoundsUtc(dateString: string, timezone: string): { start: Date; end: Date } {
  const start = localWallClockToUtc(dateString, "00:00", timezone);
  const end = localWallClockToUtc(addDays(dateString, 1), "00:00", timezone);
  return { start, end };
}

/** Monday-based day-of-week index for a YYYY-MM-DD calendar date string
 * (Monday=0 .. Sunday=6) - a pure calendar-date computation, deliberately
 * NOT timezone-dependent: which calendar date a Y-M-D string names is a
 * fact independent of any clock, so no `timezone` parameter is needed
 * here (unlike `localDateString`, which derives a date FROM an instant). */
function mondayBasedWeekday(dateString: string): number {
  const match = DATE_PATTERN.exec(dateString);
  if (!match) {
    throw new Error(`Not a YYYY-MM-DD date string: ${dateString}`);
  }
  const [, yearStr, monthStr, dayStr] = match;
  const sundayBasedDay = new Date(
    Date.UTC(Number(yearStr), Number(monthStr) - 1, Number(dayStr), 12, 0, 0),
  ).getUTCDay();
  return (sundayBasedDay + 6) % 7;
}

/** The Monday that starts the calendar week containing `dateString` -
 * Bulgaria (and most of Europe) uses a Monday week start, unlike the
 * Sunday-start convention `Intl`/JS `Date.getDay()` default to. */
export function startOfWeek(dateString: string): string {
  return addDays(dateString, -mondayBasedWeekday(dateString));
}

/** The 7 consecutive calendar dates (Monday..Sunday) of the week starting
 * at `weekStartDateString` (which should itself already be a Monday, e.g.
 * from `startOfWeek`). */
export function weekDates(weekStartDateString: string): string[] {
  return Array.from({ length: 7 }, (_, index) => addDays(weekStartDateString, index));
}

/** The UTC instant boundaries `[start, end)` covering all 7 local
 * calendar days of the week starting at `weekStartDateString` - one
 * combined range suitable for a single API request rather than 7
 * separate day queries. Correct across a DST transition inside the week
 * (the week may be 167h or 169h long in real elapsed time; only the
 * local calendar-day boundaries matter here, computed the same
 * DST-safe way as `localDayBoundsUtc`). */
export function localWeekBoundsUtc(
  weekStartDateString: string,
  timezone: string,
): { start: Date; end: Date } {
  const start = localDayBoundsUtc(weekStartDateString, timezone).start;
  const end = localDayBoundsUtc(addDays(weekStartDateString, 6), timezone).end;
  return { start, end };
}

const TIME_PATTERN = /^(\d{2}):(\d{2})$/;

/** Converts a local wall-clock date+time (YYYY-MM-DD, HH:MM) in
 * `timezone` to the UTC instant it represents - the general form
 * `localDayBoundsUtc` above builds on (there with a fixed "00:00").
 * Finds the instant by correcting an initial UTC guess against how far
 * that guess's OWN reading back in `timezone` drifts from the target
 * wall-clock time, so it stays correct across a DST transition without
 * hard-coding any offset. */
export function localWallClockToUtc(dateString: string, timeString: string, timezone: string): Date {
  const dateMatch = DATE_PATTERN.exec(dateString);
  const timeMatch = TIME_PATTERN.exec(timeString);
  if (!dateMatch || !timeMatch) {
    throw new Error(`Expected YYYY-MM-DD and HH:MM, got ${dateString} / ${timeString}`);
  }
  const [, yearStr, monthStr, dayStr] = dateMatch;
  const [, hourStr, minuteStr] = timeMatch;
  const targetAsUtc = Date.UTC(
    Number(yearStr),
    Number(monthStr) - 1,
    Number(dayStr),
    Number(hourStr),
    Number(minuteStr),
    0,
  );
  let guess = new Date(targetAsUtc);
  for (let i = 0; i < 4; i++) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(guess);
    const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? "0");
    const localAsUtc = Date.UTC(
      get("year"),
      get("month") - 1,
      get("day"),
      get("hour"),
      get("minute"),
      get("second"),
    );
    const driftMs = targetAsUtc - localAsUtc;
    if (driftMs === 0) {
      return guess;
    }
    guess = new Date(guess.getTime() + driftMs);
  }
  return guess;
}
