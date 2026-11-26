import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { findPartyWords } from '@/test/user-words'

/** The probe: the guard must be able to FAIL, or every screen that passes it
 *  proves nothing (rule 17b). */
describe('the "Parties" guard can fail', () => {
  it('finds the word in text, in every attribute it reads, and in any case', () => {
    const { container } = render(
      <div>
        <h1>Parties</h1>
        <p>Open the party record</p>
        <input aria-label="Search PARTIES" placeholder="party name" />
        <span title="a party" />
        <img alt="Party logo" />
      </div>,
    )
    expect(findPartyWords(container)).toEqual([
      'Parties',
      'Open the party record',
      'aria-label="Search PARTIES"',
      'placeholder="party name"',
      'title="a party"',
      'alt="Party logo"',
    ])
  })

  it('does not trip over words that only contain the letters', () => {
    const { container } = render(<p>Departure counterparty-free partly apartment</p>)
    expect(findPartyWords(container)).toEqual([])
  })
})
