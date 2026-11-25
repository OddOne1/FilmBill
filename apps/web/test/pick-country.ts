import { fireEvent, within } from '@testing-library/react'

/**
 * Choose a country in a `CountrySelect`: focus the combobox, type the query
 * (a name, a two- or three-letter code), and click the first option whose
 * text is `option` — "Germany (DEU)". What the user does, so a test that
 * uses it exercises the real search and the real stored value.
 */
export function pickCountry(combobox: HTMLElement, query: string, option: string) {
  fireEvent.focus(combobox)
  fireEvent.change(combobox, { target: { value: query } })
  const listbox = document.getElementById(combobox.getAttribute('aria-controls') ?? '')
  if (!listbox) throw new Error('the country list did not open')
  fireEvent.mouseDown(within(listbox).getByRole('option', { name: option }))
}
