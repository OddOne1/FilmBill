'use client'

import * as React from 'react'
import { fillFrom } from '@/lib/date-fields'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { useCompanyStore } from '@/stores/company-store'
import type { Party, PartyPage, Project, ProjectUpdate } from '@/types'
import {
  ProjectsNoAccess,
  ProjectsReadOnlyNotice,
  STATUS_LABELS,
  useProjectAccess,
} from '../projects-chrome'
import { DaysTab } from './days-tab'
import { BudgetsTab } from './budgets-tab'

/**
 * One project: Overview · Days · Budgets.
 *
 * The status has its own control because it has its own endpoint (and its
 * own audit action): "went active on the 3rd" is a different question from
 * "somebody corrected the name".
 */

type Tab = 'overview' | 'days' | 'budgets'

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'days', label: 'Days' },
  { key: 'budgets', label: 'Budgets' },
]

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>()
  const projectId = params.id
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const access = useProjectAccess()
  const [project, setProject] = React.useState<Project | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [tab, setTab] = React.useState<Tab>('overview')

  const load = React.useCallback(async () => {
    try {
      setProject(await api.get<Project>(`/projects/${projectId}`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the project')
    }
  }, [projectId])

  React.useEffect(() => {
    if (!access.loaded || access.refused) return
    void load()
  }, [access.loaded, access.refused, activeCompanyId, load])

  if (access.refused) return <ProjectsNoAccess title="Project" />

  return (
    // The calendar takes the full width (B2a): a month of strips in a narrow
    // centred column is the screen Mathias could not work with.
    <div className={tab === 'days' ? 'p-8' : 'max-w-6xl p-8'}>
      <Link
        href="/projects"
        className="inline-flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary"
      >
        <ArrowLeft className="h-3 w-3" /> Projects
      </Link>

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {!project ? (
        !error && (
          <div className="mt-8 flex items-center gap-2 text-sm text-text-secondary">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading the project…
          </div>
        )
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-baseline gap-3">
            <h1 className="text-lg font-semibold text-text-primary">{project.name}</h1>
            <span className="font-mono text-xs text-text-tertiary">
              {project.project_number}
            </span>
            <span
              data-testid="project-status"
              className="rounded bg-bg-secondary px-2 py-0.5 text-xs text-text-secondary"
            >
              {STATUS_LABELS[project.status] ?? project.status}
            </span>
          </div>

          {access.loaded && !access.canEdit && <ProjectsReadOnlyNotice />}

          <nav aria-label="Project" className="mt-5 flex gap-1 border-b border-border">
            {TABS.map((entry) => (
              <button
                key={entry.key}
                type="button"
                role="tab"
                aria-selected={tab === entry.key}
                onClick={() => setTab(entry.key)}
                className={
                  tab === entry.key
                    ? 'border-b-2 border-accent px-3 py-2 text-sm font-medium text-text-primary'
                    : 'border-b-2 border-transparent px-3 py-2 text-sm text-text-secondary hover:text-text-primary'
                }
              >
                {entry.label}
              </button>
            ))}
          </nav>

          {tab === 'overview' && (
            <OverviewTab
              project={project}
              canEdit={access.canEdit}
              canViewParties={access.canViewParties}
              onSaved={setProject}
            />
          )}
          {tab === 'days' && (
            <DaysTab project={project} canEdit={access.canEdit} onChanged={load} />
          )}
          {tab === 'budgets' && <BudgetsTab project={project} />}
        </>
      )}
    </div>
  )
}

function OverviewTab({
  project,
  canEdit,
  canViewParties,
  onSaved,
}: {
  project: Project
  canEdit: boolean
  canViewParties: boolean
  onSaved: (project: Project) => void
}) {
  const [draft, setDraft] = React.useState<ProjectUpdate>({})
  const [clients, setClients] = React.useState<Party[]>([])
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => {
    setDraft({})
  }, [project.id, project.updated_at])

  React.useEffect(() => {
    if (!canEdit || !canViewParties) return
    void (async () => {
      try {
        const page = await api.get<PartyPage>('/parties?role=customer&limit=200')
        setClients(page?.parties ?? [])
      } catch {
        // Without the list the client stays as it is.
      }
    })()
  }, [canEdit, canViewParties])

  const value = <K extends keyof ProjectUpdate>(key: K) =>
    (key in draft ? draft[key] : (project as unknown as ProjectUpdate)[key]) ?? ''

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      onSaved(await api.patch<Project>(`/projects/${project.id}`, draft))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not save the project')
    } finally {
      setBusy(false)
    }
  }

  const changeStatus = async (status: string) => {
    setError(null)
    try {
      onSaved(await api.post<Project>(`/projects/${project.id}/status`, { status }))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not change the status')
    }
  }

  const dirty = Object.keys(draft).length > 0

  if (!canEdit) {
    return (
      <dl className="mt-5 grid max-w-2xl grid-cols-[10rem_1fr] gap-y-2 text-sm">
        <dt className="text-text-tertiary">Client</dt>
        <dd className="text-text-primary">{project.client_name ?? '—'}</dd>
        <dt className="text-text-tertiary">Dates</dt>
        <dd className="text-text-primary">
          {project.start_date}
          {project.end_date ? ` – ${project.end_date}` : ''}
        </dd>
        <dt className="text-text-tertiary">Description</dt>
        <dd className="text-text-primary">{project.description || '—'}</dd>
        <dt className="text-text-tertiary">Notes</dt>
        <dd className="whitespace-pre-wrap text-text-primary">{project.notes || '—'}</dd>
      </dl>
    )
  }

  return (
    <div className="mt-5 grid max-w-3xl gap-3 sm:grid-cols-2">
      <label className="text-xs text-text-secondary">
        Name
        <Input
          aria-label="Name"
          value={String(value('name'))}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
      </label>
      <label className="text-xs text-text-secondary">
        Status
        <select
          aria-label="Status"
          value={project.status}
          onChange={(e) => void changeStatus(e.target.value)}
          className="mt-1 block w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
        >
          {Object.keys(STATUS_LABELS).map((status) => (
            <option key={status} value={status}>
              {STATUS_LABELS[status]}
            </option>
          ))}
        </select>
      </label>
      <label className="text-xs text-text-secondary">
        Client
        {canViewParties ? (
          <select
            aria-label="Client"
            value={String(value('client_party_id'))}
            onChange={(e) => setDraft({ ...draft, client_party_id: e.target.value || null })}
            className="mt-1 block w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            <option value="">No client</option>
            {project.client_party_id &&
              !clients.some((client) => client.id === project.client_party_id) && (
                <option value={project.client_party_id}>{project.client_name}</option>
              )}
            {clients.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="mt-1 block text-sm text-text-primary">
            {project.client_name ?? '—'}{' '}
            <span className="text-xs text-text-tertiary">
              (changing it needs access to Contacts)
            </span>
          </span>
        )}
      </label>
      <div />
      <label className="text-xs text-text-secondary">
        Start date
        <Input
          aria-label="Start date"
          type="date"
          value={String(value('start_date'))}
          onChange={(e) => setDraft({ ...draft, start_date: e.target.value })}
        />
      </label>
      <label className="text-xs text-text-secondary">
        End date
        <Input
          aria-label="End date"
          type="date"
          value={String(value('end_date'))}
          min={String(value('start_date')) || undefined}
          onFocus={fillFrom(String(value('end_date')), String(value('start_date')), (next) =>
            setDraft({ ...draft, end_date: next }),
          )}
          onChange={(e) => setDraft({ ...draft, end_date: e.target.value || null })}
        />
      </label>
      <label className="text-xs text-text-secondary sm:col-span-2">
        Description
        <Input
          aria-label="Description"
          value={String(value('description'))}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
        />
      </label>
      <label className="text-xs text-text-secondary sm:col-span-2">
        Notes
        <textarea
          aria-label="Notes"
          value={String(value('notes'))}
          onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
          rows={4}
          className="mt-1 block w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
        />
      </label>
      <div className="flex items-center gap-3 sm:col-span-2">
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty || busy}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Save
        </button>
        {!dirty && <span className="text-xs text-text-tertiary">Nothing changed yet.</span>}
        {error && (
          <span role="alert" className="text-xs text-danger">
            {error}
          </span>
        )}
      </div>
    </div>
  )
}
