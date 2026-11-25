import * as React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { CountrySelect } from '@/components/ui/country-select'
import { COUNTRIES, countryLabel, searchCountries } from '@/lib/countries'
import { pickCountry } from '@/test/pick-country'

/**
 * B1.2 part 3 — one country picker, stored two letters, shown three.
 */

function Harness({ initial = null as string | null, allowEmpty = false, onChange = vi.fn() }) {
  const [value, setValue] = React.useState<string | null>(initial)
  return (
    <CountrySelect
      aria-label="Country"
      allowEmpty={allowEmpty}
      value={value}
      onChange={(code) => {
        onChange(code)
        setValue(code)
      }}
    />
  )
}

function options(combobox: HTMLElement): string[] {
  const listbox = document.getElementById(combobox.getAttribute('aria-controls') ?? '')!
  return within(listbox)
    .getAllByRole('option')
    .map((option) => option.textContent ?? '')
}

describe('the list', () => {
  it('is alphabetical by English name, accents folded', () => {
    const names = COUNTRIES.map((country) => country.name)
    expect(names.slice(0, 4)).toEqual(['Afghanistan', 'Åland Islands', 'Albania', 'Algeria'])
    const sorted = [...names].sort((a, b) => new Intl.Collator('en', { sensitivity: 'base' }).compare(a, b))
    expect(names).toEqual(sorted)
  })

  it('finds by two-letter code, three-letter code and name', () => {
    expect(searchCountries('at')[0].name).toBe('Austria')
    expect(searchCountries('AUT')[0].name).toBe('Austria')
    expect(searchCountries('Aus').map((country) => country.name)).toEqual(['Australia', 'Austria'])
    expect(searchCountries('grc')[0].alpha2).toBe('GR')
    expect(searchCountries('aland')[0].alpha2).toBe('AX')
  })

  it('labels a code as its name and three-letter code; an unknown code as itself', () => {
    expect(countryLabel('AT')).toBe('Austria (AUT)')
    expect(countryLabel('gr')).toBe('Greece (GRC)')
    expect(countryLabel('XX')).toBe('XX (unknown code)')
    expect(countryLabel(null)).toBe('')
    expect(countryLabel('')).toBe('')
  })
})

describe('CountrySelect', () => {
  it('shows the stored value as “Austria (AUT)”', () => {
    render(<Harness initial="AT" />)
    expect(screen.getByRole('combobox', { name: 'Country' })).toHaveValue('Austria (AUT)')
  })

  it('stores the two-letter code, whatever was typed', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    const box = screen.getByRole('combobox', { name: 'Country' })
    pickCountry(box, 'AUT', 'Austria (AUT)')
    expect(onChange).toHaveBeenLastCalledWith('AT')
    expect(box).toHaveValue('Austria (AUT)')
    pickCountry(box, 'Deu', 'Germany (DEU)')
    expect(onChange).toHaveBeenLastCalledWith('DE')
  })

  it('opens on the whole list, alphabetical, and narrows as you type', () => {
    render(<Harness />)
    const box = screen.getByRole('combobox', { name: 'Country' })
    fireEvent.focus(box)
    expect(options(box).slice(0, 2)).toEqual(['Afghanistan (AFG)', 'Åland Islands (ALA)'])
    expect(options(box)).toHaveLength(COUNTRIES.length)
    fireEvent.change(box, { target: { value: 'Aus' } })
    expect(options(box)).toEqual(['Australia (AUS)', 'Austria (AUT)'])
  })

  it('takes Enter on the first match', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    const box = screen.getByRole('combobox', { name: 'Country' })
    fireEvent.focus(box)
    fireEvent.change(box, { target: { value: 'che' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onChange).toHaveBeenLastCalledWith('CH')
  })

  it('offers “No country” when it may be empty, and sends null', () => {
    const onChange = vi.fn()
    render(<Harness initial="AT" allowEmpty onChange={onChange} />)
    const box = screen.getByRole('combobox', { name: 'Country' })
    pickCountry(box, '', 'No country')
    expect(onChange).toHaveBeenLastCalledWith(null)
    expect(box).toHaveValue('')
  })

  it('shows an unknown stored code as itself and never changes it by showing it', () => {
    const onChange = vi.fn()
    render(<Harness initial="XX" onChange={onChange} />)
    const box = screen.getByRole('combobox', { name: 'Country' })
    expect(box).toHaveValue('XX (unknown code)')
    fireEvent.focus(box)
    fireEvent.blur(box)
    expect(onChange).not.toHaveBeenCalled()
    expect(box).toHaveValue('XX (unknown code)')
  })
})

// ─── No free-text country field anywhere ────────────────────────────────────
//
// Requested by the B1.2 prompt as a source scan with a probe. It is a guard
// against a NEW hand-made country box, not the proof that the existing ones
// work — those are the behavioural tests above and in each screen's own test
// file (party detail, tax IDs, the tax test case, the change-country dialog).
// Comments are stripped before matching, because a comment that mentions the
// old pattern has produced false results in this project before (rule 11).

const WEB_ROOT = path.resolve(__dirname, '..', '..', '..')
const SCANNED = ['app', 'components']

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry)
    if (entry === 'node_modules' || entry === '__tests__' || entry.startsWith('.')) return []
    if (statSync(full).isDirectory()) return sources(full)
    return /\.tsx?$/.test(entry) ? [full] : []
  })
}

function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
}

/** Every `<input …>` or `<Input …>` tag that names a country. */
function freeTextCountryFields(text: string): string[] {
  const tags = stripComments(text).match(/<(?:input|Input)\b[^>]*>/g) ?? []
  return tags.filter((tag) => /aria-label=["'{][^"'}]*country/i.test(tag) || /setCountry|country:\s*event/i.test(tag))
}

describe('no free-text country field', () => {
  it('the scan finds one when there is one (probe)', () => {
    const probe = `
      // <Input aria-label="Country" /> in a comment does not count
      <Input
        aria-label="Buyer country"
        value={draft.buyer_country}
        onChange={(event) => set('buyer_country', event.target.value.toUpperCase())}
      />`
    expect(freeTextCountryFields(probe)).toHaveLength(1)
    expect(freeTextCountryFields('<CountrySelect aria-label="Country" value={x} onChange={y} />')).toEqual([])
  })

  it('no screen has one (a read-only box showing countryLabel is a display, not an input)', () => {
    const offenders = SCANNED.flatMap((dir) => sources(path.join(WEB_ROOT, dir)))
      .filter((file) => !file.endsWith('country-select.tsx'))
      .flatMap((file) =>
        freeTextCountryFields(readFileSync(file, 'utf8'))
          // A disabled read-only box that shows `countryLabel(...)` is a
          // display, not an input.
          .filter((tag) => !(/readOnly/.test(tag) && /countryLabel\(/.test(tag)))
          .map((tag) => `${path.relative(WEB_ROOT, file)}: ${tag.replace(/\s+/g, ' ').slice(0, 120)}`),
      )
    expect(offenders).toEqual([])
  })

  it('scanned a real tree', () => {
    expect(SCANNED.flatMap((dir) => sources(path.join(WEB_ROOT, dir))).length).toBeGreaterThan(50)
  })
})
