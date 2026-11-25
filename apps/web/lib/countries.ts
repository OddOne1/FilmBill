import data from './countries.json'

/**
 * Countries (B1.2): stored as the two-letter ISO code, shown as the English
 * name with the three-letter code — "Austria (AUT)".
 *
 * `countries.json` is a byte-identical copy of `apps/api/data/countries.json`,
 * the one list (the API's `tests/test_countries.py` fails when they differ).
 * The names are in the file rather than taken from `Intl.DisplayNames`, so a
 * test environment without full ICU data reads the same names as a browser.
 *
 * A stored code that is not in the list is shown as itself with
 * "(unknown code)" and is never changed by being displayed.
 */

export interface Country {
  alpha2: string
  alpha3: string
  name: string
}

const collator = new Intl.Collator('en', { sensitivity: 'base' })

/** Every country, alphabetical by English name. */
export const COUNTRIES: readonly Country[] = [...(data.countries as Country[])].sort((a, b) =>
  collator.compare(a.name, b.name),
)

const BY_CODE = new Map(COUNTRIES.map((country) => [country.alpha2, country]))

export function countryByCode(code: string | null | undefined): Country | undefined {
  return code ? BY_CODE.get(code.trim().toUpperCase()) : undefined
}

/** "Austria (AUT)"; "XX (unknown code)" for a code not in the list; "" for none. */
export function countryLabel(code: string | null | undefined): string {
  if (!code || !code.trim()) return ''
  const country = countryByCode(code)
  return country ? `${country.name} (${country.alpha3})` : `${code.trim()} (unknown code)`
}

/** Strip accents and case, so "aland" finds "Åland Islands". */
function fold(text: string): string {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

/**
 * The countries a typed query finds, best first: an exact two- or
 * three-letter code, then names that start with the query, then names that
 * contain it. An empty query is the whole list.
 */
export function searchCountries(query: string): Country[] {
  const q = fold(query.trim())
  if (!q) return [...COUNTRIES]
  const exact: Country[] = []
  const starts: Country[] = []
  const contains: Country[] = []
  for (const country of COUNTRIES) {
    const name = fold(country.name)
    if (q === country.alpha2.toLowerCase() || q === country.alpha3.toLowerCase()) exact.push(country)
    else if (name.startsWith(q)) starts.push(country)
    else if (name.includes(q)) contains.push(country)
  }
  return [...exact, ...starts, ...contains]
}
