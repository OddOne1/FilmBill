/**
 * UI-1 part 4: the project calendar always shows six weeks.
 *
 * `monthGrid` is a pure function, so it is tested directly — first on the
 * three months the prompt pins, then over every month of 2026 to 2028. The
 * table's expectations are computed here from the JavaScript `Date` alone
 * (day of the week of the 1st, length of the month), not from the module's
 * own helpers, so the test does not agree with the code by sharing its
 * arithmetic.
 */
import { describe, it, expect } from 'vitest'
import { monthGrid } from '../calendar-dates'

function utc(y: number, m: number, d: number): Date {
  return new Date(Date.UTC(y, m - 1, d))
}
function isoOf(date: Date): string {
  return date.toISOString().slice(0, 10)
}
/** 0 = Monday … 6 = Sunday, straight from Date. */
function mondayIndex(date: Date): number {
  return (date.getUTCDay() + 6) % 7
}

const flat = (weeks: string[][]) => weeks.flat()

describe('the months the prompt pins', () => {
  it('March 2027 starts on a Monday, so the extra week is 22–28 February, before it', () => {
    const weeks = monthGrid(2027, 3)
    expect(weeks).toHaveLength(6)
    expect(weeks[0]).toEqual([
      '2027-02-22',
      '2027-02-23',
      '2027-02-24',
      '2027-02-25',
      '2027-02-26',
      '2027-02-27',
      '2027-02-28',
    ])
    expect(weeks[1][0]).toBe('2027-03-01')
    expect(weeks[5][6]).toBe('2027-04-04')
  })

  it('February 2027 fills exactly four rows, so it gets a week before and a week after', () => {
    const weeks = monthGrid(2027, 2)
    expect(weeks[0][0]).toBe('2027-01-25')
    expect(weeks[0][6]).toBe('2027-01-31')
    expect(weeks[1][0]).toBe('2027-02-01')
    expect(weeks[4][6]).toBe('2027-02-28')
    expect(weeks[5][0]).toBe('2027-03-01')
    expect(weeks[5][6]).toBe('2027-03-07')
  })

  it('August 2026 needs six rows by itself and is unchanged', () => {
    const weeks = monthGrid(2026, 8)
    // 1 August 2026 is a Saturday; 31 August a Monday.
    expect(weeks[0][0]).toBe('2026-07-27')
    expect(weeks[0][5]).toBe('2026-08-01')
    expect(weeks[5][0]).toBe('2026-08-31')
    expect(weeks[5][6]).toBe('2026-09-06')
  })

  it('a tie adds the week after', () => {
    // July 2026 has 31 days and starts on a Wednesday: five rows with two
    // filler days before and two after.
    expect(mondayIndex(utc(2026, 7, 1))).toBe(2)
    const weeks = monthGrid(2026, 7)
    expect(weeks[0][0]).toBe('2026-06-29')
    expect(weeks[4][6]).toBe('2026-08-02')
    expect(weeks[5][0]).toBe('2026-08-03')
    expect(weeks[5][6]).toBe('2026-08-09')
  })
})

describe('every month of 2026 to 2028', () => {
  const months: [number, number][] = []
  for (let year = 2026; year <= 2028; year += 1) for (let month = 1; month <= 12; month += 1) months.push([year, month])

  it.each(months)('%i-%i: 42 days, Monday first, the whole month, the extra-week rule', (year, month) => {
    const weeks = monthGrid(year, month)
    const days = flat(weeks)

    // Six rows of seven, 42 consecutive days, starting on a Monday.
    expect(weeks).toHaveLength(6)
    for (const week of weeks) expect(week).toHaveLength(7)
    expect(days).toHaveLength(42)
    expect(mondayIndex(new Date(`${days[0]}T00:00:00Z`))).toBe(0)
    for (let i = 1; i < 42; i += 1) {
      const step = (Date.parse(`${days[i]}T00:00:00Z`) - Date.parse(`${days[i - 1]}T00:00:00Z`)) / 86_400_000
      expect(step).toBe(1)
    }

    // Every day of the month is in it.
    const length = utc(year, month + 1, 0).getUTCDate()
    for (let d = 1; d <= length; d += 1) expect(days).toContain(isoOf(utc(year, month, d)))

    // The extra-week rule, from Date alone.
    const lead0 = mondayIndex(utc(year, month, 1))
    const rows0 = Math.ceil((lead0 + length) / 7)
    const trail0 = rows0 * 7 - lead0 - length
    const lead = days.indexOf(isoOf(utc(year, month, 1)))
    const trail = 41 - days.indexOf(isoOf(utc(year, month, length)))
    if (rows0 === 6) {
      expect([lead, trail]).toEqual([lead0, trail0])
    } else if (rows0 === 4) {
      expect([lead, trail]).toEqual([7, 7])
    } else if (lead0 < trail0) {
      expect([lead, trail]).toEqual([lead0 + 7, trail0])
    } else {
      // Fewer (or equally many) after: the week goes after.
      expect([lead, trail]).toEqual([lead0, trail0 + 7])
    }
  })

  it('covers all three cases in that range', () => {
    const rows = months.map(([year, month]) => {
      const lead0 = mondayIndex(utc(year, month, 1))
      return Math.ceil((lead0 + utc(year, month + 1, 0).getUTCDate()) / 7)
    })
    // February 2027 is the four-row month; five- and six-row months abound.
    expect(new Set(rows)).toEqual(new Set([4, 5, 6]))
  })
})
