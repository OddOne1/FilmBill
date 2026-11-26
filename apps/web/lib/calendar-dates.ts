/**
 * Calendar arithmetic for the project calendar (B2a): ISO dates, month
 * grids, ISO week numbers and the lane layout of strips.
 *
 * Every date here is a `YYYY-MM-DD` string handled in UTC, because a
 * production day is a calendar date and not an instant — a date that moved
 * by one when the browser's timezone changed would be a shoot day on the
 * wrong day. Nothing here counts days for anyone: the counts on screen are
 * the API's (CLAUDE.md rule 2's spirit, and B2a's "the web never counts").
 */

export type IsoDate = string

const DAY_MS = 86_400_000

export function toUtc(date: IsoDate): Date {
  return new Date(`${date}T00:00:00Z`)
}

export function iso(date: Date): IsoDate {
  return date.toISOString().slice(0, 10)
}

export function addDays(date: IsoDate, days: number): IsoDate {
  return iso(new Date(toUtc(date).getTime() + days * DAY_MS))
}

/** `b − a` in whole days. */
export function diffDays(a: IsoDate, b: IsoDate): number {
  return Math.round((toUtc(b).getTime() - toUtc(a).getTime()) / DAY_MS)
}

/** 0 = Monday … 6 = Sunday. */
export function weekday(date: IsoDate): number {
  return (toUtc(date).getUTCDay() + 6) % 7
}

export function isWeekend(date: IsoDate): boolean {
  return weekday(date) >= 5
}

export function minDate(a: IsoDate, b: IsoDate): IsoDate {
  return a <= b ? a : b
}

export function maxDate(a: IsoDate, b: IsoDate): IsoDate {
  return a >= b ? a : b
}

/** Every date from `start` to `end` inclusive. */
export function datesBetween(start: IsoDate, end: IsoDate): IsoDate[] {
  const out: IsoDate[] = []
  for (let day = start; day <= end; day = addDays(day, 1)) out.push(day)
  return out
}

/** The ISO 8601 week number (weeks start on Monday; week 1 holds the
 *  year's first Thursday) — the number a European production office uses. */
export function isoWeek(date: IsoDate): number {
  const thursday = toUtc(addDays(date, 3 - weekday(date)))
  const yearStart = Date.UTC(thursday.getUTCFullYear(), 0, 1)
  return Math.floor((thursday.getTime() - yearStart) / DAY_MS / 7) + 1
}

/** `"2027-03"` → the weeks (Monday first) that hold any day of that month,
 *  each seven dates long, spilling into the neighbouring months. */
export function monthWeeks(month: string): IsoDate[][] {
  const first = `${month}-01`
  const start = addDays(first, -weekday(first))
  const weeks: IsoDate[][] = []
  for (let week = start; week.slice(0, 7) <= month; week = addDays(week, 7)) {
    weeks.push(Array.from({ length: 7 }, (_, index) => addDays(week, index)))
  }
  return weeks
}

/** Rows a month grid always has (UI-1): 6 × 7 = 42 days. */
export const GRID_WEEKS = 6

/**
 * The project calendar's grid for one month (UI-1 part 4): **always six
 * week rows, 42 days, Monday first**, so the calendar never changes height
 * as you step through the months.
 *
 * The month's own weeks (`monthWeeks`) are five or six rows, or four for a
 * February that starts on a Monday and ends on a Sunday. The missing rows
 * are added like this:
 *   - six rows: nothing changes;
 *   - five rows: one week on the side with FEWER filler days — the days of
 *     the neighbouring month in the first row (before) or the last row
 *     (after); a tie adds it after;
 *   - four rows: one week before and one after.
 * So March 2027 (no leading days, four trailing) starts with 22–28 February,
 * and February 2027 runs from 25 January to 7 March.
 *
 * `month` is 1-based (3 = March).
 */
export function monthGrid(year: number, month: number): IsoDate[][] {
  const key = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`
  const weeks = monthWeeks(key)
  const week = (start: IsoDate) => Array.from({ length: 7 }, (_, index) => addDays(start, index))
  const weekBefore = () => weeks.unshift(week(addDays(weeks[0][0], -7)))
  const weekAfter = () => weeks.push(week(addDays(weeks[weeks.length - 1][0], 7)))

  if (weeks.length === GRID_WEEKS - 2) {
    weekBefore()
    weekAfter()
  } else if (weeks.length === GRID_WEEKS - 1) {
    const before = weeks[0].filter((date) => date.slice(0, 7) !== key).length
    const after = weeks[weeks.length - 1].filter((date) => date.slice(0, 7) !== key).length
    if (before < after) weekBefore()
    else weekAfter()
  }
  return weeks
}

/** `"2027-03"` → `monthGrid(2027, 3)`. */
export function monthGridFor(month: string): IsoDate[][] {
  return monthGrid(Number(month.slice(0, 4)), Number(month.slice(5, 7)))
}

export function shiftMonth(month: string, by: number): string {
  const year = Number(month.slice(0, 4))
  const index = Number(month.slice(5, 7)) - 1 + by
  const date = new Date(Date.UTC(year, index, 1))
  return iso(date).slice(0, 7)
}

export function monthLabel(month: string, style: 'long' | 'short' = 'long'): string {
  return toUtc(`${month}-01`).toLocaleDateString('en-GB', {
    month: style,
    year: 'numeric',
    timeZone: 'UTC',
  })
}

/** `4 Mar` / `4 March 2027`. */
export function dayLabel(date: IsoDate, withYear = false): string {
  return toUtc(date).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: withYear ? 'long' : 'short',
    ...(withYear ? { year: 'numeric' } : {}),
    timeZone: 'UTC',
  })
}

export function weekdayLetter(date: IsoDate): string {
  return ['M', 'T', 'W', 'T', 'F', 'S', 'S'][weekday(date)]
}

export function todayIso(): IsoDate {
  const now = new Date()
  return iso(new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())))
}

// ─── Lanes ──────────────────────────────────────────────────────────────────

export interface Spanning {
  id: string
  start_date: IsoDate
  end_date: IsoDate
}

export interface Segment<T extends Spanning> {
  strip: T
  /** 0–6, Monday first, inclusive. */
  startCol: number
  endCol: number
  lane: number
  /** The strip carries on into the week before / after. */
  continuesLeft: boolean
  continuesRight: boolean
}

/**
 * Where each strip that touches this week is drawn: which columns, which
 * lane. Earlier strips first and, among those starting together, longer
 * ones first; each in the lowest lane free across its columns — the
 * arrangement a calendar app uses, so a long shoot stays on one line and a
 * one-day BTS strip slots in beside it.
 *
 * Layout only. It says nothing about how many days there are.
 */
export function layoutWeek<T extends Spanning>(strips: T[], week: IsoDate[]): Segment<T>[] {
  const first = week[0]
  const last = week[week.length - 1]
  const touching = strips
    .filter((strip) => strip.start_date <= last && strip.end_date >= first)
    .sort(
      (a, b) =>
        a.start_date.localeCompare(b.start_date) ||
        diffDays(b.start_date, b.end_date) - diffDays(a.start_date, a.end_date) ||
        a.id.localeCompare(b.id),
    )
  const taken: boolean[][] = []
  return touching.map((strip) => {
    const startCol = Math.max(0, diffDays(first, strip.start_date))
    const endCol = Math.min(6, diffDays(first, strip.end_date))
    let lane = 0
    for (;;) {
      taken[lane] ??= Array(7).fill(false)
      let free = true
      for (let col = startCol; col <= endCol; col += 1) if (taken[lane][col]) free = false
      if (free) break
      lane += 1
    }
    for (let col = startCol; col <= endCol; col += 1) taken[lane][col] = true
    return {
      strip,
      startCol,
      endCol,
      lane,
      continuesLeft: strip.start_date < first,
      continuesRight: strip.end_date > last,
    }
  })
}
