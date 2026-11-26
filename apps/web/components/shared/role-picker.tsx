'use client'

import * as React from 'react'
import { X } from 'lucide-react'

/**
 * Roles as chips: type one and press Enter (or a comma), or click a
 * suggestion. Free text — a role is whatever the company calls it — with the
 * roles already in use and the catalog's labor items offered first.
 *
 * Duplicates are dropped case-insensitively here as a courtesy; the API
 * normalises again, because it is the one that stores them.
 */
export function RolePicker({
  value,
  onChange,
  suggestions,
  id = 'crew-roles',
}: {
  value: string[]
  onChange: (next: string[]) => void
  suggestions: string[]
  id?: string
}) {
  const [draft, setDraft] = React.useState('')

  const add = (raw: string) => {
    const role = raw.split(/\s+/).join(' ').trim()
    if (!role) return
    if (value.some((existing) => existing.toLowerCase() === role.toLowerCase())) {
      setDraft('')
      return
    }
    onChange([...value, role])
    setDraft('')
  }

  const offered = suggestions
    .filter((role) => !value.some((chosen) => chosen.toLowerCase() === role.toLowerCase()))
    .filter((role) => !draft || role.toLowerCase().includes(draft.toLowerCase()))
    .slice(0, 12)

  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-bg-secondary px-2 py-1.5">
        {value.map((role) => (
          <span
            key={role}
            data-testid="role-chip"
            className="inline-flex items-center gap-1 rounded bg-bg-tertiary px-1.5 py-0.5 text-xs text-text-primary"
          >
            {role}
            <button
              type="button"
              aria-label={`Remove ${role}`}
              onClick={() => onChange(value.filter((entry) => entry !== role))}
              className="text-text-tertiary hover:text-text-primary"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={draft}
          onChange={(event) => {
            const next = event.target.value
            if (next.includes(',') || next.includes('|')) {
              next.split(/[,|]/).forEach(add)
              return
            }
            setDraft(next)
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              add(draft)
            } else if (event.key === 'Backspace' && !draft && value.length) {
              onChange(value.slice(0, -1))
            }
          }}
          onBlur={() => add(draft)}
          placeholder={value.length ? '' : 'DoP, Gaffer, Sound …'}
          className="min-w-[8rem] flex-1 bg-transparent text-sm text-text-primary outline-none placeholder:text-text-tertiary"
        />
      </div>
      {offered.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1" aria-label="Suggested roles">
          {offered.map((role) => (
            <button
              key={role}
              type="button"
              data-testid="role-suggestion"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => add(role)}
              className="rounded border border-dashed border-border px-1.5 py-0.5 text-xs text-text-secondary hover:border-accent hover:text-text-primary"
            >
              + {role}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
