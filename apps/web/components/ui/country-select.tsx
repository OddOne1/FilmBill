'use client'

import * as React from 'react'
import { cn } from '@/lib/utils'
import { countryLabel, searchCountries } from '@/lib/countries'

/**
 * The one country picker (B1.2). Every place a country is entered uses it.
 *
 * Shows "Austria (AUT)", finds by typing a name, a two-letter or a
 * three-letter code, and hands back the TWO-letter code — what the API
 * stores and what the tax rules, region packs and VAT checks are keyed by.
 *
 * A stored code that is not in the list is shown as "XX (unknown code)" and
 * stays exactly as it is until somebody picks a country: displaying a value
 * never changes it.
 */
export interface CountrySelectProps {
  value: string | null | undefined
  onChange: (code: string | null) => void
  'aria-label': string
  id?: string
  disabled?: boolean
  placeholder?: string
  /** Offer "No country", which sends null. */
  allowEmpty?: boolean
  className?: string
}

export function CountrySelect({
  value,
  onChange,
  'aria-label': ariaLabel,
  id,
  disabled,
  placeholder = 'Type a country or its code',
  allowEmpty = false,
  className,
}: CountrySelectProps) {
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState('')
  const [active, setActive] = React.useState(0)
  const listId = React.useId()

  const matches = searchCountries(query)
  const options: { code: string | null; label: string }[] = [
    ...(allowEmpty && !query.trim() ? [{ code: null, label: 'No country' }] : []),
    ...matches.map((country) => ({ code: country.alpha2, label: `${country.name} (${country.alpha3})` })),
  ]

  const choose = (code: string | null) => {
    onChange(code)
    setOpen(false)
    setQuery('')
  }

  return (
    <div className={cn('relative', className)}>
      <input
        id={id}
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        disabled={disabled}
        placeholder={value ? undefined : placeholder}
        value={open ? query : countryLabel(value)}
        onFocus={() => {
          setOpen(true)
          setQuery('')
          setActive(0)
        }}
        onBlur={() => {
          setOpen(false)
          setQuery('')
        }}
        onChange={(event) => {
          setQuery(event.target.value)
          setOpen(true)
          setActive(0)
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault()
            setActive((index) => Math.min(index + 1, options.length - 1))
          } else if (event.key === 'ArrowUp') {
            event.preventDefault()
            setActive((index) => Math.max(index - 1, 0))
          } else if (event.key === 'Enter') {
            if (open && options[active]) {
              event.preventDefault()
              choose(options[active].code)
            }
          } else if (event.key === 'Escape') {
            setOpen(false)
            setQuery('')
          }
        }}
        className="flex h-9 w-full rounded-md border border-border bg-bg-primary px-3 py-1 text-sm text-text-primary placeholder:text-text-tertiary focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={`${ariaLabel} options`}
          className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-md border border-border bg-bg-primary py-1 text-sm shadow-lg"
        >
          {options.length === 0 && <li className="px-3 py-1.5 text-text-tertiary">No country matches “{query}”.</li>}
          {options.map((option, index) => (
            <li
              key={option.code ?? 'none'}
              role="option"
              aria-selected={index === active}
              // mousedown, not click: a click would blur the input first and
              // close the list before the choice lands.
              onMouseDown={(event) => {
                event.preventDefault()
                choose(option.code)
              }}
              onMouseEnter={() => setActive(index)}
              className={cn(
                'cursor-pointer px-3 py-1.5 text-text-primary',
                index === active && 'bg-bg-hover',
                option.code === null && 'text-text-tertiary',
              )}
            >
              {option.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
