import { describe, it, expect } from 'vitest'
import { addDays, diffDays, isoWeek, layoutWeek, monthWeeks, shiftMonth, weekday } from '../calendar-dates'

describe('calendar dates', () => {
  it('weeks start on Monday and spill into the neighbouring months', () => {
    const weeks = monthWeeks('2027-03')
    // 1 March 2027 is a Monday; 31 March a Wednesday → five rows.
    expect(weeks).toHaveLength(5)
    expect(weeks[0][0]).toBe('2027-03-01')
    expect(weeks[4][6]).toBe('2027-04-04')
    // February 2026 starts on a Sunday: the first row begins in January.
    expect(monthWeeks('2026-02')[0][0]).toBe('2026-01-26')
  })

  it('numbers weeks the ISO way, across a year boundary', () => {
    expect(isoWeek('2027-03-01')).toBe(9)
    expect(isoWeek('2026-12-31')).toBe(53)
    expect(isoWeek('2027-01-04')).toBe(1)
  })

  it('does date arithmetic in UTC', () => {
    expect(addDays('2027-03-28', 1)).toBe('2027-03-29') // across a DST change
    expect(diffDays('2027-02-22', '2027-03-01')).toBe(7)
    expect(weekday('2027-02-28')).toBe(6)
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
  })

  it('puts overlapping strips in separate lanes and reuses a free lane', () => {
    const week = Array.from({ length: 7 }, (_, i) => addDays('2027-03-01', i))
    const segments = layoutWeek(
      [
        { id: 'a', start_date: '2027-02-25', end_date: '2027-03-03' },
        { id: 'b', start_date: '2027-03-02', end_date: '2027-03-02' },
        { id: 'c', start_date: '2027-03-05', end_date: '2027-03-12' },
      ],
      week,
    )
    const byId = Object.fromEntries(segments.map((s) => [s.strip.id, s]))
    expect(byId.a).toMatchObject({ lane: 0, startCol: 0, endCol: 2, continuesLeft: true, continuesRight: false })
    expect(byId.b.lane).toBe(1)
    // Free again in lane 0 after Wednesday.
    expect(byId.c).toMatchObject({ lane: 0, startCol: 4, endCol: 6, continuesRight: true })
  })
})
