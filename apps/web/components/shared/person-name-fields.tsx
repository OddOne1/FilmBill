'use client'

import * as React from 'react'

/** A person's name in parts (P1b-4). The display name is built from these by
 *  the API ("First Middle Last"); the web never assembles it for saving. */
export interface PersonName {
  first_name: string
  middle_name: string
  last_name: string
}

export const EMPTY_PERSON_NAME: PersonName = {
  first_name: '',
  middle_name: '',
  last_name: '',
}

/** Whether the parts are enough to save: a first or a last name. */
export function hasPersonName(value: PersonName): boolean {
  return Boolean(value.first_name.trim() || value.last_name.trim())
}

/** The parts as the API takes them: blank → null. */
export function personNamePayload(value: PersonName): {
  first_name: string | null
  middle_name: string | null
  last_name: string | null
} {
  return {
    first_name: value.first_name.trim() || null,
    middle_name: value.middle_name.trim() || null,
    last_name: value.last_name.trim() || null,
  }
}

/** First name, middle name (optional), last name — one row on a wide
 *  screen, stacked on a narrow one. `testIdPrefix` keeps every form's test
 *  ids distinct. */
export function PersonNameFields({
  value,
  onChange,
  testIdPrefix,
  disabled = false,
  autoFocus = false,
  inputClassName = 'mt-1 h-8 w-full rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary',
}: {
  value: PersonName
  onChange: (next: PersonName) => void
  testIdPrefix: string
  disabled?: boolean
  autoFocus?: boolean
  inputClassName?: string
}) {
  const fields: [keyof PersonName, string][] = [
    ['first_name', 'First name'],
    ['middle_name', 'Middle name (optional)'],
    ['last_name', 'Last name'],
  ]
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {fields.map(([key, label], index) => (
        <label key={key} className="block text-xs font-medium text-text-secondary">
          {label}
          <input
            aria-label={label}
            data-testid={`${testIdPrefix}-${key.replace('_name', '')}`}
            value={value[key]}
            disabled={disabled}
            autoFocus={autoFocus && index === 0}
            onChange={(event) => onChange({ ...value, [key]: event.target.value })}
            className={inputClassName}
          />
        </label>
      ))}
    </div>
  )
}
