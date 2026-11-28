'use client'

import * as React from 'react'
import { Plus, Search } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import { cn } from '@/lib/utils'
import type { Location, LocationKind, LocationList } from '@/types'
import { ALL_KINDS, KIND_LABEL, LocationForm } from './location-form'

/**
 * Locations (L1): where days are shot, where crew sleep, the emergency
 * hospital. A list with search and kind chips; a row opens the location in
 * the same panel that creates one.
 *
 * Hotels say where crew sleep, so they are only in the list for somebody who
 * may see crew — the API leaves them out otherwise (`hotels_visible`), and
 * the Hotel chip is not drawn.
 */
export default function LocationsPage() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const { can, loaded } = usePermissions()
  const canView = can('locations.view')
  const canEdit = can('locations.edit')
  const canSeeContacts = can('parties.view')

  const [search, setSearch] = React.useState('')
  const [kind, setKind] = React.useState<LocationKind | null>(null)
  const [archived, setArchived] = React.useState(false)
  const [list, setList] = React.useState<LocationList | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [open, setOpen] = React.useState<Location | 'new' | null>(null)

  const refresh = React.useCallback(async () => {
    const params = new URLSearchParams()
    if (search.trim()) params.set('q', search.trim())
    if (kind) params.set('kind', kind)
    if (archived) params.set('include_archived', 'true')
    const query = params.toString()
    try {
      setList(await api.get<LocationList>(`/locations${query ? `?${query}` : ''}`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The locations could not be loaded. Try again.')
    }
  }, [search, kind, archived])

  React.useEffect(() => {
    if (!loaded || !canView) return
    const handle = setTimeout(() => void refresh(), 150)
    return () => clearTimeout(handle)
  }, [loaded, canView, activeCompanyId, refresh])

  if (loaded && !canView) {
    return (
      <div className="p-6">
        <h1 className="text-lg font-semibold text-text-primary">Locations</h1>
        <p data-testid="locations-no-access" className="mt-2 text-sm text-text-secondary">
          Your role in this company does not include locations. Ask an owner or admin if you need them.
        </p>
      </div>
    )
  }

  const kinds = ALL_KINDS.filter((k) => k !== 'hotel' || list?.hotels_visible)

  return (
    <div className="p-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold text-text-primary">Locations</h1>
        {canEdit && (
          <button
            type="button"
            data-testid="locations-new"
            onClick={() => setOpen('new')}
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white"
          >
            <Plus className="h-3.5 w-3.5" /> New location
          </button>
        )}
      </div>
      {!canEdit && loaded && (
        <p data-testid="locations-read-only" className="mt-1 text-xs text-text-tertiary">
          You can see the locations but not change them — that needs the location edit permission.
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-text-tertiary" />
          <input
            aria-label="Search locations"
            placeholder="Name, city, street"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 w-64 rounded-md border border-border bg-bg-primary pl-8 pr-2 text-sm"
          />
        </div>
        <div role="group" aria-label="Kind" className="flex gap-1">
          {kinds.map((k) => (
            <button
              key={k}
              type="button"
              data-testid="locations-kind-chip"
              aria-pressed={kind === k}
              onClick={() => setKind(kind === k ? null : k)}
              className={cn(
                'rounded-full border px-2.5 py-0.5 text-xs',
                kind === k ? 'border-accent bg-accent/10 text-text-primary' : 'border-border text-text-secondary',
              )}
            >
              {KIND_LABEL[k]}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-xs text-text-secondary">
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Show archived
        </label>
      </div>

      {error && (
        <p role="alert" className="mt-3 text-sm text-status-error">
          {error}
        </p>
      )}

      {open && (
        <LocationForm
          key={open === 'new' ? 'new' : open.id}
          location={open === 'new' ? null : open}
          canEdit={canEdit}
          canSeeContacts={canSeeContacts}
          kinds={kinds}
          onClose={() => setOpen(null)}
          onSaved={async (location) => {
            setOpen(location)
            await refresh()
          }}
        />
      )}

      {list && list.locations.length === 0 && (
        <p data-testid="locations-empty" className="mt-6 text-sm text-text-secondary">
          {search.trim() || kind
            ? 'No location matches. Clear the search or the kind to see all of them.'
            : canEdit
              ? 'No locations yet. Add the studios, hotels and the nearest hospital you work with.'
              : 'No locations yet.'}
        </p>
      )}

      {list && list.locations.length > 0 && (
        <table data-testid="locations-table" className="mt-4 w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-text-tertiary">
              <th className="py-1.5 font-medium">Name</th>
              <th className="py-1.5 font-medium">Kind</th>
              <th className="py-1.5 font-medium">City</th>
              <th className="py-1.5 font-medium">Phone</th>
              <th className="py-1.5 font-medium">Coordinates</th>
              <th className="py-1.5 text-right font-medium">In use</th>
            </tr>
          </thead>
          <tbody>
            {list.locations.map((location) => (
              <tr
                key={location.id}
                data-testid="locations-row"
                data-kind={location.kind}
                className={cn('cursor-pointer border-b border-border/60 hover:bg-bg-hover', !location.is_active && 'text-text-tertiary')}
                onClick={() => setOpen(location)}
              >
                <td className="py-1.5">
                  {location.name}
                  {!location.is_active && <span className="ml-1 text-2xs">(archived)</span>}
                  {location.kind === 'hospital' && location.verified && (
                    <span className="ml-1 rounded bg-status-success/15 px-1 text-2xs text-status-success">verified</span>
                  )}
                </td>
                <td className="py-1.5">{KIND_LABEL[location.kind]}</td>
                <td className="py-1.5">{location.city ?? '—'}</td>
                <td className="py-1.5">{location.phone ?? '—'}</td>
                <td className="py-1.5">{location.latitude ? 'Yes' : 'No'}</td>
                <td className="py-1.5 text-right tabular-nums">{location.in_use}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
