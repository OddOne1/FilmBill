'use client'

import * as React from 'react'
import { ExternalLink, Plus, X } from 'lucide-react'
import { api } from '@/lib/api'
import { Input } from '@/components/ui/input'
import type { Location, LocationKind, LocationList, LocationRef } from '@/types'
import { KIND_LABEL, LocationForm } from './location-form'

/**
 * Pick a location (L1): a search over `GET /locations` limited to the kinds
 * that fit here, and **New location…**, which opens the same form as the
 * Locations page inline and selects what it created.
 *
 * The chosen location shows with the API's own map link and an "archived"
 * mark when it has been archived — it keeps showing, it just cannot be
 * picked anew (the API refuses that with a sentence).
 */
export function LocationPicker({
  label,
  value,
  kinds,
  canEdit,
  onChange,
  createName,
  onCreateNameUsed,
  testId = 'location-picker',
  clearLabel = 'Clear',
}: {
  label: string
  value: LocationRef | null | undefined
  /** Which kinds may be picked; the first is what "New location…" makes. */
  kinds: LocationKind[]
  canEdit: boolean
  onChange: (locationId: string | null) => unknown
  /** Opens the create form with this name already in it ("Create location
   *  from this text"). */
  createName?: string | null
  onCreateNameUsed?: () => void
  testId?: string
  clearLabel?: string
}) {
  const [query, setQuery] = React.useState('')
  const [open, setOpen] = React.useState(false)
  const [results, setResults] = React.useState<Location[]>([])
  const [creating, setCreating] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (createName != null) {
      setCreating(createName)
      onCreateNameUsed?.()
    }
  }, [createName, onCreateNameUsed])

  React.useEffect(() => {
    if (!open) return
    const handle = setTimeout(() => {
      const params = new URLSearchParams()
      if (query.trim()) params.set('q', query.trim())
      if (kinds.length === 1) params.set('kind', kinds[0])
      const suffix = params.toString()
      api
        .get<LocationList>(`/locations${suffix ? `?${suffix}` : ''}`)
        .then((list) => setResults(list.locations.filter((l) => kinds.includes(l.kind))))
        .catch(() => setResults([]))
    }, 150)
    return () => clearTimeout(handle)
  }, [open, query, kinds])

  return (
    <div data-testid={testId} className="text-xs">
      {value ? (
        <div data-testid={`${testId}-value`} className="flex flex-wrap items-center gap-1.5">
          <span className="text-sm text-text-primary">{value.name}</span>
          {!value.is_active && (
            <span data-testid={`${testId}-archived`} className="rounded bg-bg-tertiary px-1 text-2xs text-text-tertiary">
              archived
            </span>
          )}
          {value.map_link && (
            <a
              data-testid={`${testId}-map`}
              href={value.map_link}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-0.5 text-accent hover:underline"
            >
              Open in maps <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          )}
          {canEdit && (
            <button
              type="button"
              aria-label={`${clearLabel} ${label.toLowerCase()}`}
              onClick={() => void onChange(null)}
              className="inline-flex items-center gap-0.5 text-text-tertiary hover:text-text-primary"
            >
              <X className="h-3 w-3" /> {clearLabel}
            </button>
          )}
        </div>
      ) : (
        !canEdit && <span className="text-sm text-text-tertiary">—</span>
      )}
      {canEdit && (
        <div className="relative mt-1">
          <Input
            aria-label={label}
            placeholder={value ? 'Pick another' : 'Search locations'}
            value={query}
            onFocus={() => setOpen(true)}
            onChange={(e) => {
              setQuery(e.target.value)
              setOpen(true)
            }}
            className="h-8"
          />
          {open && (
            <ul data-testid={`${testId}-results`} className="mt-1 max-h-48 overflow-auto rounded-md border border-border bg-bg-primary">
              {results.map((location) => (
                <li key={location.id}>
                  <button
                    type="button"
                    data-testid={`${testId}-option`}
                    onClick={() => {
                      setOpen(false)
                      setQuery('')
                      void onChange(location.id)
                    }}
                    className="flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-bg-hover"
                  >
                    <span className="flex-1 truncate">{location.name}</span>
                    <span className="text-2xs text-text-tertiary">
                      {KIND_LABEL[location.kind]}
                      {location.city ? ` · ${location.city}` : ''}
                    </span>
                  </button>
                </li>
              ))}
              {results.length === 0 && (
                <li className="px-2 py-1 text-text-tertiary">
                  {query.trim() ? `No location matches “${query.trim()}”.` : 'No locations yet.'}
                </li>
              )}
              <li className="border-t border-border">
                <button
                  type="button"
                  data-testid={`${testId}-new`}
                  onClick={() => {
                    setCreating(query.trim())
                    setOpen(false)
                  }}
                  className="flex w-full items-center gap-1 px-2 py-1 text-left text-accent hover:bg-bg-hover"
                >
                  <Plus className="h-3 w-3" /> New location…
                </button>
              </li>
            </ul>
          )}
        </div>
      )}
      {creating !== null && canEdit && (
        <LocationForm
          initialName={creating}
          initialKind={kinds[0]}
          kinds={kinds}
          canEdit
          onClose={() => setCreating(null)}
          onSaved={async (location) => {
            setCreating(null)
            await onChange(location.id)
          }}
        />
      )}
    </div>
  )
}
