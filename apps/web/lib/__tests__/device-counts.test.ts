import { describe, it, expect } from 'vitest'
import { deviceCountsSentence } from '../device-counts'

/**
 * The two numbers in words. The counts are the API's; what is under test is
 * that every relation between them is SAID and none is hidden or turned
 * into an error — Mathias's three cases, and the acceptance walk.
 */

const zero = {
  owned: 0,
  recorded: 0,
  active: 0,
  needs_check: 0,
  in_repair: 0,
  in_hand: 0,
  lost: 0,
  retired: 0,
  sold: 0,
  removed: 0,
  not_yet_recorded: 0,
  beyond_quantity: 0,
}

describe('deviceCountsSentence', () => {
  it('three owned, two serials known', () => {
    expect(
      deviceCountsSentence({ ...zero, owned: 3, recorded: 2, active: 2, not_yet_recorded: 1 }),
    ).toBe('3 owned · 2 serial numbers recorded · 1 not yet recorded')
  })

  it('two owned, three serials of which one retired', () => {
    expect(
      deviceCountsSentence({ ...zero, owned: 2, recorded: 3, active: 2, retired: 1 }),
    ).toBe('2 owned · 3 serial numbers recorded · 1 retired')
  })

  it('nothing owned, nothing known', () => {
    expect(deviceCountsSentence(zero)).toBe('0 owned · no serial numbers recorded')
  })

  it('more serials in hand than owned is said, not flagged', () => {
    expect(
      deviceCountsSentence({ ...zero, owned: 1, recorded: 3, active: 2, in_repair: 1, beyond_quantity: 2 }),
    ).toBe('1 owned · 3 serial numbers recorded · 1 in repair · 2 more serial numbers than owned')
  })

  it('names every way a unit can be out of use', () => {
    expect(
      deviceCountsSentence({ ...zero, owned: 2, recorded: 6, active: 1, needs_check: 1, lost: 1, retired: 2, sold: 1 }),
    ).toBe('2 owned · 6 serial numbers recorded · 1 to be checked · 1 lost · 2 retired · 1 sold')
  })

  it('one serial number reads in the singular', () => {
    expect(deviceCountsSentence({ ...zero, owned: 1, recorded: 1, active: 1 })).toBe(
      '1 owned · 1 serial number recorded',
    )
  })
})
