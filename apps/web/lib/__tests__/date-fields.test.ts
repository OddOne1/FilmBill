import { describe, it, expect, vi } from 'vitest'
import { fillFrom } from '../date-fields'

describe('the second date of a pair', () => {
  it('takes the first date when focused empty, so its picker opens there', () => {
    const set = vi.fn()
    fillFrom('', '2027-02-22', set)()
    expect(set).toHaveBeenCalledWith('2027-02-22')
  })

  it('leaves a date somebody already chose alone, and does nothing without a first date', () => {
    const set = vi.fn()
    fillFrom('2027-03-01', '2027-02-22', set)()
    fillFrom('', '', set)()
    fillFrom(null, null, set)()
    expect(set).not.toHaveBeenCalled()
  })
})
