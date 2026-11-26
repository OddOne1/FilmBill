'use client'

import * as React from 'react'
import Link from 'next/link'
import { Download, Plus, Search, Upload } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { saveBlob } from '@/lib/save-file'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type { CrewMember, CrewPage, CrewRoleSuggestions } from '@/types'
import { QuickAdd } from './quick-add'
import { CrewImport } from './crew-import'

/**
 * Crew — the people who can be booked on a production.
 *
 * Every one of them is a party with the crew flag: roles are the party's
 * tags, and the name links to the full party record, where an address, a
 * bank account or the supplier flag can be added later. This screen is the
 * fast way in for a list that exists nowhere yet: quick add, or a CSV.
 *
 * The list is the API's: search, role filter and sort are query parameters,
 * so a search finds people on pages that have not been loaded.
 */

const SORTS: { value: string; label: string }[] = [
  { value: 'name', label: 'Name A–Z' },
  { value: '-name', label: 'Name Z–A' },
  { value: 'role', label: 'Role' },
  { value: '-created', label: 'Newest first' },
  { value: 'created', label: 'Oldest first' },
]

export default function CrewListPage() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const { can, loaded } = usePermissions()
  const canEdit = can('parties.edit')

  const [search, setSearch] = React.useState('')
  const [role, setRole] = React.useState('')
  const [sort, setSort] = React.useState('name')
  const [includeInactive, setIncludeInactive] = React.useState(false)
  const [page, setPage] = React.useState<CrewPage | null>(null)
  const [roles, setRoles] = React.useState<CrewRoleSuggestions>({ used: [], catalog: [] })
  const [error, setError] = React.useState<string | null>(null)
  const [panel, setPanel] = React.useState<'add' | 'import' | null>(null)

  const refresh = React.useCallback(async () => {
    const params = new URLSearchParams()
    if (search.trim()) params.set('search', search.trim())
    if (role) params.set('role', role)
    if (sort !== 'name') params.set('sort', sort)
    if (includeInactive) params.set('include_inactive', 'true')
    try {
      const query = params.toString()
      setPage(await api.get<CrewPage>(`/crew${query ? `?${query}` : ''}`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The crew list could not be loaded. Try again.')
    }
  }, [search, role, sort, includeInactive])

  const refreshRoles = React.useCallback(async () => {
    try {
      setRoles(await api.get<CrewRoleSuggestions>('/crew/roles'))
    } catch {
      // Suggestions are a convenience; without them roles are typed in.
    }
  }, [])

  React.useEffect(() => {
    if (!loaded || !can('parties.view')) return
    const handle = setTimeout(() => void refresh(), 200)
    return () => clearTimeout(handle)
  }, [loaded, activeCompanyId, refresh, can])

  React.useEffect(() => {
    if (!loaded || !can('parties.view')) return
    void refreshRoles()
  }, [loaded, activeCompanyId, refreshRoles, can])

  if (loaded && !can('parties.view')) {
    return (
      <div className="p-8">
        <h1 className="text-lg font-semibold text-text-primary">Crew</h1>
        <p data-testid="crew-no-access" className="mt-2 text-sm text-text-secondary">
          You do not have access to this company&rsquo;s crew list. It is part of
          the contacts list, which needs permission to view contacts. An owner or
          administrator can change that under Settings → Company → Members.
        </p>
      </div>
    )
  }

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
    <div className="max-w-6xl p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Crew</h1>
          <p className="mt-1 text-sm text-text-secondary">
            The people you can book on a production. Each one is a person in
            your contacts — open a name for the full record.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canEdit && (
            <>
              <button
                type="button"
                data-testid="crew-add-open"
                onClick={() => setPanel(panel === 'add' ? null : 'add')}
                className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
              >
                <Plus className="h-3.5 w-3.5" /> Add person
              </button>
              <button
                type="button"
                data-testid="crew-import-open"
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
        </div>
      </div>

      {loaded && !canEdit && (
        <p
          data-testid="crew-read-only"
          className="mt-4 rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary"
        >
          You can read the crew list but not add to it — that needs the
          permission to edit contacts. An owner or administrator can grant it
          under Settings → Company → Members.
        </p>
      )}

      {panel === 'add' && canEdit && (
        <QuickAdd suggestions={suggestions} onAdded={changed} onClose={() => setPanel(null)} />
      )}
      {panel === 'import' && canEdit && <CrewImport onApplied={changed} />}

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <label className="relative">
          <span className="sr-only">Search crew</span>
          <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-text-tertiary" />
          <input
            data-testid="crew-search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Name, role or email"
            className="h-8 w-64 rounded-md border border-border bg-bg-secondary pl-8 pr-2 text-sm text-text-primary placeholder:text-text-tertiary"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Role{' '}
          <select
            data-testid="crew-role-filter"
            value={role}
            onChange={(event) => setRole(event.target.value)}
            className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          >
            <option value="">All roles</option>
            {roles.used.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-text-secondary">
          Sort{' '}
          <select
            data-testid="crew-sort"
            value={sort}
            onChange={(event) => setSort(event.target.value)}
            className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          >
            {SORTS.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-flex items-center gap-1.5 text-xs text-text-secondary">
          <input
            type="checkbox"
            data-testid="crew-include-inactive"
            checked={includeInactive}
            onChange={(event) => setIncludeInactive(event.target.checked)}
          />
          Show archived people
        </label>
      </div>

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {page === null ? (
        !error && <p className="mt-6 text-sm text-text-secondary">Loading the crew list…</p>
      ) : page.members.length === 0 ? (
        <p data-testid="crew-empty" className="mt-6 text-sm text-text-secondary">
          {search || role
            ? 'Nobody in the crew list matches that. Clear the search or the role filter to see everyone.'
            : canEdit
              ? 'Nobody is in the crew list yet. Add the first person, or import a CSV file.'
              : 'Nobody is in the crew list yet.'}
        </p>
      ) : (
        <>
          <p className="mt-4 text-xs text-text-tertiary" data-testid="crew-total">
            {page.total} {page.total === 1 ? 'person' : 'people'}
          </p>
          <table className="mt-2 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
                <th className="py-2 pr-3 font-medium">Name</th>
                <th className="py-2 pr-3 font-medium">Roles</th>
                <th className="py-2 pr-3 font-medium">Phone</th>
                <th className="py-2 pr-3 font-medium">Email</th>
                <th className="py-2 pr-3 font-medium">Notes</th>
              </tr>
            </thead>
            <tbody>
              {page.members.map((member) => (
                <CrewRow key={member.id} member={member} />
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

function CrewRow({ member }: { member: CrewMember }) {
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
          {(member.roles ?? []).map((entry) => (
            <span key={entry} className="rounded bg-bg-tertiary px-1.5 py-0.5 text-xs text-text-primary">
              {entry}
            </span>
          ))}
        </div>
      </td>
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
