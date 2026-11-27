'use client'

import * as React from 'react'
import Link from 'next/link'
import { Download, Plus, Upload } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { saveBlob } from '@/lib/save-file'
import { formatMoney } from '@/lib/money'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type { CrewMember, CrewPage, CrewRoleSuggestions } from '@/types'
import { QuickAdd } from './quick-add'
import { CrewImport } from './crew-import'
import { CrewAvailabilityView } from './crew-availability'

/**
 * Contacts → Crew: the people who can be booked on a production (UI-1
 * part 3, where the separate Crew page and menu item went away).
 *
 * Crew are people in the contacts list with the crew flag; their roles are
 * the contact's tags. Everything the old Crew page offered lives here, in
 * the tab's own toolbar: **Quick add crew**, **Import CSV**, **Export CSV**,
 * the role filter with the roles in use, and the sort. The search box and
 * the "Show archived" toggle are the Contacts page's own, passed in, and on
 * this tab the search also finds a role.
 *
 * The list is the crew endpoint's: search, role and sort are query
 * parameters, so a search finds people on pages that have not been loaded.
 *
 * R1: the role filter offers the catalog's labor items (`role_item_id`) and
 * the tags in use (`role`). With `budgets.view` there is a rate range and a
 * rate sort — both on the EFFECTIVE rate, own else typical, applied by the
 * API — and a Day rate column; without it none of the three is drawn.
 */

const SORTS: { value: string; label: string }[] = [
  { value: 'name', label: 'Name A–Z' },
  { value: '-name', label: 'Name Z–A' },
  { value: 'role', label: 'Role' },
  { value: '-created', label: 'Newest first' },
  { value: 'created', label: 'Oldest first' },
]
const RATE_SORTS: { value: string; label: string }[] = [
  { value: 'rate', label: 'Day rate, lowest first' },
  { value: '-rate', label: 'Day rate, highest first' },
]
/** A role-filter value naming a labor item rather than a tag. */
const ITEM = 'item:'

export function CrewTab({ search, includeInactive }: { search: string; includeInactive: boolean }) {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const { can, loaded, active } = usePermissions()
  const canView = can('parties.view')
  const canEdit = can('parties.edit')
  const canSeeRates = can('budgets.view')
  const currency = active?.default_currency ?? 'EUR'
  // B2c-1: the list, or the availability grid (needs projects.view too).
  const canSeeAvailability = can('projects.view')
  const [view, setView] = React.useState<'people' | 'availability'>('people')
  const [minRate, setMinRate] = React.useState('')
  const [maxRate, setMaxRate] = React.useState('')

  const [role, setRole] = React.useState('')
  const [sort, setSort] = React.useState('name')
  const [page, setPage] = React.useState<CrewPage | null>(null)
  const [roles, setRoles] = React.useState<CrewRoleSuggestions>({ used: [], catalog: [], labor_items: [] })
  const [error, setError] = React.useState<string | null>(null)
  const [panel, setPanel] = React.useState<'add' | 'import' | null>(null)

  const refresh = React.useCallback(async () => {
    const params = new URLSearchParams()
    if (search.trim()) params.set('search', search.trim())
    if (role.startsWith(ITEM)) params.set('role_item_id', role.slice(ITEM.length))
    else if (role) params.set('role', role)
    if (canSeeRates && minRate.trim()) params.set('min_rate', minRate.trim().replace(',', '.'))
    if (canSeeRates && maxRate.trim()) params.set('max_rate', maxRate.trim().replace(',', '.'))
    if (sort !== 'name' && (canSeeRates || !sort.endsWith('rate'))) params.set('sort', sort)
    if (includeInactive) params.set('include_inactive', 'true')
    try {
      const query = params.toString()
      setPage(await api.get<CrewPage>(`/crew${query ? `?${query}` : ''}`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The crew list could not be loaded. Try again.')
    }
  }, [search, role, sort, includeInactive, minRate, maxRate, canSeeRates])

  const refreshRoles = React.useCallback(async () => {
    try {
      setRoles(await api.get<CrewRoleSuggestions>('/crew/roles'))
    } catch {
      // Suggestions are a convenience; without them roles are typed in.
    }
  }, [])

  React.useEffect(() => {
    if (!loaded || !canView) return
    const handle = setTimeout(() => void refresh(), 200)
    return () => clearTimeout(handle)
  }, [loaded, activeCompanyId, refresh, canView])

  React.useEffect(() => {
    if (!loaded || !canView) return
    void refreshRoles()
  }, [loaded, activeCompanyId, refreshRoles, canView])

  const exportCsv = async () => {
    setError(null)
    try {
      const query = includeInactive ? '?include_inactive=true' : ''
      saveBlob(await api.download(`/crew/export.csv${query}`), 'crew.csv')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The export failed. Try again.')
    }
  }

  const changed = () => {
    void refresh()
    void refreshRoles()
  }

  const suggestions = [...roles.used, ...roles.catalog]

  return (
    <div data-testid="crew-tab">
      {canSeeAvailability && (
        <div role="tablist" aria-label="Crew view" className="mt-4 inline-flex rounded-md border border-border p-0.5 text-xs">
          {(['people', 'availability'] as const).map((entry) => (
            <button
              key={entry}
              type="button"
              role="tab"
              aria-selected={view === entry}
              data-testid={`crew-view-${entry}`}
              onClick={() => setView(entry)}
              className={view === entry ? 'rounded bg-bg-tertiary px-2 py-1 text-text-primary' : 'px-2 py-1 text-text-secondary'}
            >
              {entry === 'people' ? 'People' : 'Availability'}
            </button>
          ))}
        </div>
      )}

      {view === 'availability' && canSeeAvailability ? (
        <CrewAvailabilityView laborItems={roles.labor_items ?? []} canSeeRates={canSeeRates} currency={currency} />
      ) : (
      <>
      <div role="toolbar" aria-label="Crew" className="mt-4 flex flex-wrap items-center gap-2">
        {canEdit && (
          <>
            <button
              type="button"
              data-testid="crew-add-open"
              aria-expanded={panel === 'add'}
              onClick={() => setPanel(panel === 'add' ? null : 'add')}
              className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
            >
              <Plus className="h-3.5 w-3.5" /> Quick add crew
            </button>
            <button
              type="button"
              data-testid="crew-import-open"
              aria-expanded={panel === 'import'}
              onClick={() => setPanel(panel === 'import' ? null : 'import')}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-text-primary"
            >
              <Upload className="h-3.5 w-3.5" /> Import CSV
            </button>
          </>
        )}
        <button
          type="button"
          data-testid="crew-export"
          onClick={() => void exportCsv()}
          className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-text-primary"
        >
          <Download className="h-3.5 w-3.5" /> Export CSV
        </button>
        <span className="flex-1" />
        <label className="text-xs text-text-secondary">
          Role{' '}
          <select
            data-testid="crew-role-filter"
            value={role}
            onChange={(event) => setRole(event.target.value)}
            className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          >
            <option value="">All roles</option>
            {(roles.labor_items ?? []).length > 0 && (
              <optgroup label="Roles (catalog)">
                {(roles.labor_items ?? []).map((entry) => (
                  <option key={entry.item_id} value={`${ITEM}${entry.item_id}`}>
                    {entry.name}
                  </option>
                ))}
              </optgroup>
            )}
            {roles.used.length > 0 && (
              <optgroup label="Tags">
                {roles.used.map((entry) => (
                  <option key={entry} value={entry}>
                    {entry}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        {canSeeRates && (
          <>
            <label className="text-xs text-text-secondary">
              Day rate from{' '}
              <input
                data-testid="crew-rate-min"
                inputMode="decimal"
                value={minRate}
                onChange={(event) => setMinRate(event.target.value)}
                className="ml-1 h-8 w-20 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
              />
            </label>
            <label className="text-xs text-text-secondary">
              to{' '}
              <input
                data-testid="crew-rate-max"
                inputMode="decimal"
                value={maxRate}
                onChange={(event) => setMaxRate(event.target.value)}
                className="ml-1 h-8 w-20 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
              />
            </label>
          </>
        )}
        <label className="text-xs text-text-secondary">
          Sort{' '}
          <select
            data-testid="crew-sort"
            value={sort}
            onChange={(event) => setSort(event.target.value)}
            className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          >
            {[...SORTS, ...(canSeeRates ? RATE_SORTS : [])].map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {canEdit && (
        <p className="mt-2 text-xs text-text-tertiary">
          Roles are the catalog&apos;s labor items, and their cost per day is the typical rate.{' '}
          <Link href="/catalog?kind=labor" data-testid="crew-typical-rates-link" className="text-accent hover:underline">
            Edit typical rates
          </Link>
        </p>
      )}

      {panel === 'add' && canEdit && (
        <QuickAdd suggestions={suggestions} onAdded={changed} onClose={() => setPanel(null)} />
      )}
      {panel === 'import' && canEdit && <CrewImport onApplied={changed} />}

      {error && (
        <p role="alert" className="mt-4 text-sm text-status-error">
          {error}
        </p>
      )}

      {page === null ? (
        !error && <p className="mt-6 text-sm text-text-secondary">Loading the crew list…</p>
      ) : page.members.length === 0 ? (
        <p data-testid="crew-empty" className="mt-6 rounded-md border border-border px-3 py-4 text-sm text-text-secondary">
          {search.trim() || role || minRate.trim() || maxRate.trim()
            ? 'Nobody in the crew list matches that. Clear the search, the role or the rate filter to see everyone.'
            : canEdit
              ? 'Nobody is in the crew list yet. Add the first person with Quick add crew, or import a CSV file.'
              : 'Nobody is in the crew list yet.'}
        </p>
      ) : (
        <>
          <table className="mt-4 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
                <th className="py-2 pr-3 font-medium">Name</th>
                <th className="py-2 pr-3 font-medium">Roles</th>
                {canSeeRates && <th className="py-2 pr-3 font-medium">Day rate</th>}
                <th className="py-2 pr-3 font-medium">Phone</th>
                <th className="py-2 pr-3 font-medium">Email</th>
                <th className="py-2 pr-3 font-medium">Notes</th>
              </tr>
            </thead>
            <tbody>
              {page.members.map((member) => (
                <CrewRow
                  key={member.id}
                  member={member}
                  showRate={canSeeRates}
                  roleSelected={role.startsWith(ITEM)}
                  currency={currency}
                />
              ))}
            </tbody>
          </table>
          <p className="pt-2 text-xs text-text-tertiary" data-testid="crew-total">
            {page.total} {page.total === 1 ? 'person' : 'people'}
          </p>
        </>
      )}
      </>
      )}
    </div>
  )
}

/** The rate cell: the API's effective rate for the selected role with a
 *  marker for own/typical, or the person's lowest to highest. */
function rateCell(member: CrewMember, roleSelected: boolean, currency: string): React.ReactNode {
  if (!member.rate_low) return '—'
  if (roleSelected) {
    return (
      <>
        {formatMoney(member.rate_low, currency)}
        {member.rate_source && member.rate_source !== 'none' && (
          <span data-testid="crew-rate-source" className="ml-1 rounded bg-bg-tertiary px-1 text-2xs text-text-secondary">
            {member.rate_source}
          </span>
        )}
      </>
    )
  }
  return member.rate_high && member.rate_high !== member.rate_low
    ? `${formatMoney(member.rate_low, currency)} – ${formatMoney(member.rate_high, currency)}`
    : formatMoney(member.rate_low, currency)
}

function CrewRow({
  member,
  showRate,
  roleSelected,
  currency,
}: {
  member: CrewMember
  showRate: boolean
  roleSelected: boolean
  currency: string
}) {
  return (
    <tr data-testid="crew-row" className="border-b border-border/60 align-top">
      <td className="py-2 pr-3">
        <Link href={`/contacts/${member.id}`} className="font-medium text-text-primary hover:underline">
          {member.name}
        </Link>
        {!member.is_active && (
          <span className="ml-2 rounded bg-bg-tertiary px-1.5 py-0.5 text-2xs text-text-secondary">
            archived
          </span>
        )}
        {member.is_supplier && (
          <span className="ml-2 rounded bg-bg-tertiary px-1.5 py-0.5 text-2xs text-text-secondary">
            also a supplier
          </span>
        )}
      </td>
      <td className="py-2 pr-3">
        <div className="flex flex-wrap gap-1">
          {(member.crew_roles ?? []).map((entry) => (
            <span key={entry.id} data-testid="crew-row-role" className="rounded bg-accent/10 px-1.5 py-0.5 text-xs text-text-primary">
              {entry.name}
            </span>
          ))}
          {(member.roles ?? []).map((entry) => (
            <span key={entry} className="rounded bg-bg-tertiary px-1.5 py-0.5 text-xs text-text-primary">
              {entry}
            </span>
          ))}
        </div>
      </td>
      {showRate && (
        <td data-testid="crew-row-rate" className="whitespace-nowrap py-2 pr-3 text-text-primary">
          {rateCell(member, roleSelected, currency)}
        </td>
      )}
      <td className="py-2 pr-3 text-text-secondary">
        {member.phone ? <a href={`tel:${member.phone}`}>{member.phone}</a> : '—'}
      </td>
      <td className="py-2 pr-3 text-text-secondary">
        {member.email ? <a href={`mailto:${member.email}`}>{member.email}</a> : '—'}
      </td>
      <td className="py-2 pr-3 text-xs text-text-secondary">{member.notes ?? ''}</td>
    </tr>
  )
}
