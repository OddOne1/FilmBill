'use client'

import * as React from 'react'
import { fillFrom } from '@/lib/date-fields'
import Link from 'next/link'
import { Loader2, Plus, Search } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { useCompanyStore } from '@/stores/company-store'
import type { Party, PartyPage, Project, ProjectCreate, ProjectPage } from '@/types'
import {
  DAY_TYPE_LABELS,
  ProjectsNoAccess,
  ProjectsReadOnlyNotice,
  STATUS_LABELS,
  useProjectAccess,
} from './projects-chrome'

/**
 * The project list.
 *
 * **The search and the filters are the server's**: every change goes out as a
 * query string and the list renders what comes back, because a client-side
 * filter would only ever search the page it had already loaded.
 */

const STATUSES = Object.keys(STATUS_LABELS)

/** "5 shoot · 2 prep", in the order the day types are listed. The counts are
 *  the API's (`day_counts`); nothing here adds them up. */
function dayCountText(counts: Record<string, number> | undefined): string {
  if (!counts) return ''
  return Object.keys(DAY_TYPE_LABELS)
    .filter((type) => counts[type])
    .map((type) => `${counts[type]} ${DAY_TYPE_LABELS[type].toLowerCase()}`)
    .join(' · ')
}

export default function ProjectsPage() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const { loaded, canEdit, canViewParties, refused } = useProjectAccess()

  const [search, setSearch] = React.useState('')
  const [status, setStatus] = React.useState('')
  const [clientId, setClientId] = React.useState('')
  const [includeArchived, setIncludeArchived] = React.useState(false)
  const [page, setPage] = React.useState<ProjectPage | null>(null)
  const [clients, setClients] = React.useState<Party[]>([])
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [adding, setAdding] = React.useState(false)

  const refresh = React.useCallback(async () => {
    const params = new URLSearchParams()
    if (search.trim()) params.set('search', search.trim())
    if (status) params.set('status', status)
    if (clientId) params.set('client_party_id', clientId)
    if (includeArchived) params.set('include_archived', 'true')
    params.set('limit', '100')
    try {
      setPage(await api.get<ProjectPage>(`/projects?${params.toString()}`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the projects')
    } finally {
      setLoading(false)
    }
  }, [search, status, clientId, includeArchived])

  React.useEffect(() => {
    if (!loaded || refused) return
    const handle = setTimeout(() => void refresh(), 250)
    return () => clearTimeout(handle)
  }, [loaded, refused, activeCompanyId, refresh])

  React.useEffect(() => {
    if (!loaded || !canViewParties) return
    void (async () => {
      try {
        const loadedClients = await api.get<PartyPage>('/parties?role=customer&limit=200')
        setClients(loadedClients?.parties ?? [])
      } catch {
        // The client filter and picker degrade to "no client"; the list is
        // what this screen is for.
      }
    })()
  }, [loaded, canViewParties, activeCompanyId])

  if (refused) return <ProjectsNoAccess />

  const rows = page?.projects ?? []

  return (
    <div className="max-w-6xl p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Projects</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Productions: their days, and what they are planned to cost.
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            data-testid="projects-new"
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
          >
            <Plus className="h-3.5 w-3.5" /> New project
          </button>
        )}
      </div>

      {loaded && !canEdit && <ProjectsReadOnlyNotice />}

      {adding && (
        <NewProjectForm
          clients={clients}
          onCancel={() => setAdding(false)}
          onCreated={() => {
            setAdding(false)
            void refresh()
          }}
        />
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <div className="relative min-w-[16rem] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-text-tertiary" />
          <Input
            aria-label="Search projects"
            value={search}
            placeholder="Name or project number"
            onChange={(event) => setSearch(event.target.value)}
            className="pl-8"
          />
        </div>
        <select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          className="rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
        >
          <option value="">All open statuses</option>
          {STATUSES.map((value) => (
            <option key={value} value={value}>
              {STATUS_LABELS[value]}
            </option>
          ))}
        </select>
        {canViewParties && (
          <select
            aria-label="Filter by client"
            value={clientId}
            onChange={(event) => setClientId(event.target.value)}
            className="rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            <option value="">All clients</option>
            {clients.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name}
              </option>
            ))}
          </select>
        )}
        <label className="flex items-center gap-1.5 text-sm text-text-secondary">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => setIncludeArchived(event.target.checked)}
          />
          Include archived
        </label>
      </div>

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {loading ? (
        <div className="mt-8 flex items-center gap-2 text-sm text-text-secondary">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading projects…
        </div>
      ) : rows.length === 0 ? (
        <p data-testid="projects-empty" className="mt-8 text-sm text-text-secondary">
          {search || status || clientId
            ? 'No project matches these filters.'
            : canEdit
              ? 'No projects yet. Create the first one with “New project”.'
              : 'No projects yet.'}
        </p>
      ) : (
        <table className="mt-5 w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-text-tertiary">
              <th className="py-2 pr-3 font-medium">Number</th>
              <th className="py-2 pr-3 font-medium">Name</th>
              <th className="py-2 pr-3 font-medium">Client</th>
              <th className="py-2 pr-3 font-medium">Status</th>
              <th className="py-2 pr-3 font-medium">Dates</th>
              <th className="py-2 pr-3 font-medium">Days</th>
              <th className="py-2 font-medium">Budgets</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((project: Project) => (
              <tr
                key={project.id}
                data-testid="project-row"
                className="border-b border-border/60 hover:bg-bg-secondary"
              >
                <td className="py-2 pr-3 font-mono text-xs text-text-secondary">
                  {project.project_number}
                </td>
                <td className="py-2 pr-3">
                  <Link
                    href={`/projects/${project.id}`}
                    className="font-medium text-text-primary hover:underline"
                  >
                    {project.name}
                  </Link>
                </td>
                <td className="py-2 pr-3 text-text-secondary">{project.client_name ?? '—'}</td>
                <td className="py-2 pr-3 text-text-secondary">
                  {STATUS_LABELS[project.status] ?? project.status}
                </td>
                <td className="py-2 pr-3 text-text-secondary">
                  {project.start_date}
                  {project.end_date ? ` – ${project.end_date}` : ''}
                </td>
                <td className="py-2 pr-3 text-text-secondary">
                  {dayCountText(project.day_counts) || '—'}
                </td>
                <td className="py-2 text-text-secondary">{project.budget_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function NewProjectForm({
  clients,
  onCancel,
  onCreated,
}: {
  clients: Party[]
  onCancel: () => void
  onCreated: (project: Project) => void
}) {
  const [name, setName] = React.useState('')
  const [clientId, setClientId] = React.useState('')
  const [startDate, setStartDate] = React.useState('')
  const [endDate, setEndDate] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  // Why the button is not live, said next to it (17c). Only what the server
  // would refuse anyway — a name and a start date.
  const missing = !name.trim()
    ? 'A project needs a name.'
    : !startDate
      ? 'A project needs a start date — it decides the year in its number.'
      : null

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (missing) return
    setBusy(true)
    setError(null)
    const body: ProjectCreate = {
      name: name.trim(),
      start_date: startDate,
      status: 'planning',
      client_party_id: clientId || null,
      end_date: endDate || null,
    }
    try {
      onCreated(await api.post<Project>('/projects', body))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The project could not be created')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      data-testid="project-new-form"
      onSubmit={submit}
      className="mt-5 grid gap-3 rounded-md border border-border bg-bg-secondary p-4 sm:grid-cols-2"
    >
      <label className="text-xs text-text-secondary">
        Name
        <Input aria-label="Project name" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="text-xs text-text-secondary">
        Client
        <select
          aria-label="Client"
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          className="mt-1 block w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
        >
          <option value="">No client yet</option>
          {clients.map((client) => (
            <option key={client.id} value={client.id}>
              {client.name}
            </option>
          ))}
        </select>
      </label>
      <label className="text-xs text-text-secondary">
        Start date
        <Input
          aria-label="Start date"
          type="date"
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
        />
      </label>
      <label className="text-xs text-text-secondary">
        End date (optional)
        <Input
          aria-label="End date"
          type="date"
          value={endDate}
          min={startDate || undefined}
          onFocus={fillFrom(endDate, startDate, setEndDate)}
          onChange={(e) => setEndDate(e.target.value)}
        />
      </label>
      <div className="flex items-center gap-3 sm:col-span-2">
        <button
          type="submit"
          disabled={busy || missing !== null}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Create project
        </button>
        <button type="button" onClick={onCancel} className="text-sm text-text-secondary">
          Cancel
        </button>
        {missing && (
          <span data-testid="project-new-why" className="text-xs text-text-tertiary">
            {missing}
          </span>
        )}
        {error && (
          <span role="alert" className="text-xs text-danger">
            {error}
          </span>
        )}
      </div>
    </form>
  )
}
