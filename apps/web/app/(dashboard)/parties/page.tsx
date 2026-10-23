'use client'

import * as React from 'react'
import Link from 'next/link'
import { Loader2, Plus, Search } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type { Party, PartyPage, PartyType } from '@/types'

/**
 * Parties — customers, suppliers, partners and the people in them.
 *
 * One list for all of them, because a party is one thing wearing several
 * hats: the production company that hires you this month invoices you the
 * next. The tabs are filters over one list rather than separate screens, so
 * a company that is both appears in both and is still one record.
 *
 * **The search is one field over five columns** (name, trading name, party
 * number, VAT ID, email), server-side. Somebody looking for a customer types
 * what they remember and does not know which field it was — and a client-side
 * filter would only ever search the page it had already loaded.
 */

const TABS: { key: string; label: string; role?: string; type?: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'customers', label: 'Customers', role: 'customer' },
  { key: 'suppliers', label: 'Suppliers', role: 'supplier' },
  { key: 'partners', label: 'Partners', role: 'partner' },
  { key: 'people', label: 'People', type: 'person' },
]

const TYPE_CHOICES: { value: PartyType; label: string; help: string }[] = [
  {
    value: 'organisation',
    label: 'Organisation',
    help: 'A company or institution. The top of the tree — it has no parent.',
  },
  {
    value: 'department',
    label: 'Department',
    help: 'A part of an organisation, which it needs as its parent.',
  },
  {
    value: 'person',
    label: 'Person',
    help: 'A contact inside an organisation or a department, or somebody on their own.',
  },
]

export default function PartiesPage() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const { can, loaded } = usePermissions()
  const canEdit = can('parties.edit')

  const [tab, setTab] = React.useState('all')
  const [search, setSearch] = React.useState('')
  const [includeInactive, setIncludeInactive] = React.useState(false)
  const [page, setPage] = React.useState<PartyPage | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [adding, setAdding] = React.useState(false)

  const refresh = React.useCallback(
    async (spinner = false) => {
      if (spinner) setLoading(true)
      const chosen = TABS.find((entry) => entry.key === tab)
      const params = new URLSearchParams()
      if (search.trim()) params.set('search', search.trim())
      if (chosen?.role) params.set('role', chosen.role)
      if (chosen?.type) params.set('type', chosen.type)
      if (includeInactive) params.set('include_inactive', 'true')
      try {
        setPage(await api.get<PartyPage>(`/parties?${params.toString()}`))
        setError(null)
      } catch (err) {
        setError(
          err instanceof ApiError ? err.detail : 'Could not load the parties',
        )
      } finally {
        setLoading(false)
      }
    },
    [tab, search, includeInactive],
  )

  React.useEffect(() => {
    if (!loaded) return
    // Debounced, because the search is a request per keystroke otherwise.
    const handle = setTimeout(() => void refresh(page === null), 250)
    return () => clearTimeout(handle)
    // Keyed on the active company: switching must not leave another
    // company's customers on screen.
  }, [loaded, activeCompanyId, refresh])

  if (loaded && !can('parties.view')) {
    // Rule 17c: a sentence, never a blank page or a redirect. A redirect
    // makes a permission problem look like a broken link.
    return (
      <div className="p-8">
        <h1 className="text-lg font-semibold text-text-primary">Parties</h1>
        <p className="mt-2 text-sm text-text-secondary">
          You do not have access to this company&rsquo;s customers and
          suppliers. An owner or administrator can change that under Settings
          → Company → Members.
        </p>
      </div>
    )
  }

  return (
    <div className="max-w-5xl p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Parties</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Customers, suppliers, partners — and the departments and people
            inside them.
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            data-testid="parties-new"
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
          >
            <Plus className="h-3.5 w-3.5" /> New
          </button>
        )}
      </div>

      {!canEdit && loaded && (
        <p className="mt-4 rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary">
          You can read these but not change them — adding or editing a party
          needs the parties permission.
        </p>
      )}

      <div role="tablist" aria-label="Parties" className="mt-5 flex gap-1 border-b border-border">
        {TABS.map((entry) => (
          <button
            key={entry.key}
            role="tab"
            type="button"
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
      </div>

      <div className="mt-4 flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-text-tertiary" />
          <Input
            aria-label="Search parties"
            value={search}
            placeholder="Name, number, VAT ID or email"
            onChange={(event) => setSearch(event.target.value)}
            className="pl-8"
          />
        </div>
        <label className="flex items-center gap-2 whitespace-nowrap text-xs text-text-secondary">
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(event) => setIncludeInactive(event.target.checked)}
          />
          Show archived
        </label>
      </div>

      {adding && (
        <NewPartyPanel
          onClose={() => setAdding(false)}
          onCreated={async () => {
            setAdding(false)
            await refresh()
          }}
        />
      )}

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {error}
        </p>
      )}

      {loading ? (
        <div className="mt-6 flex items-center gap-2 p-4 text-sm text-text-tertiary">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading…
        </div>
      ) : page && page.parties.length === 0 ? (
        <p
          data-testid="parties-empty"
          className="mt-6 rounded-md border border-border px-3 py-4 text-sm text-text-secondary"
        >
          {search.trim()
            ? `Nothing matches “${search.trim()}”.`
            : 'No parties yet. Add the first customer with the New button.'}
        </p>
      ) : (
        <div className="mt-4 space-y-2">
          {page?.parties.map((party) => (
            <PartyRow key={party.id} party={party} />
          ))}
          <p className="pt-2 text-xs text-text-tertiary">
            {page?.total} in total
          </p>
        </div>
      )}
    </div>
  )
}

function PartyRow({ party }: { party: Party }) {
  return (
    <Link
      href={`/parties/${party.id}`}
      data-testid={`party-row-${party.id}`}
      className="flex items-center gap-3 rounded-md border border-border px-3 py-2.5 hover:bg-bg-hover/50"
    >
      <span className="w-20 font-mono text-xs text-text-tertiary">
        {party.party_number ?? '—'}
      </span>
      <span className="flex-1 truncate text-sm text-text-primary">
        {party.name}
        {party.trading_name && (
          <span className="ml-2 text-text-tertiary">({party.trading_name})</span>
        )}
      </span>
      <span className="w-24 text-xs text-text-tertiary">{party.type}</span>
      <span className="flex w-56 justify-end gap-1">
        {party.is_customer && <Chip>Customer</Chip>}
        {party.is_supplier && <Chip>Supplier</Chip>}
        {party.is_partner && <Chip>Partner</Chip>}
        {party.vat_verified && (
          <Chip tone="success" testId={`party-chip-verified-${party.id}`}>
            VAT checked
          </Chip>
        )}
        {!party.is_active && (
          <Chip tone="warning" testId={`party-chip-archived-${party.id}`}>
            Archived
          </Chip>
        )}
      </span>
    </Link>
  )
}

function Chip({
  children,
  tone = 'neutral',
  testId,
}: {
  children: React.ReactNode
  tone?: 'neutral' | 'success' | 'warning'
  testId?: string
}) {
  const tones = {
    neutral: 'border-border text-text-tertiary',
    success: 'border-status-success/40 text-status-success',
    warning: 'border-status-warning/40 text-status-warning',
  }
  return (
    <span
      data-testid={testId}
      className={`rounded-full border px-2 py-0.5 text-[11px] ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

function NewPartyPanel({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: () => Promise<void>
}) {
  const [type, setType] = React.useState<PartyType>('organisation')
  const [name, setName] = React.useState('')
  const [parentId, setParentId] = React.useState('')
  const [organisations, setOrganisations] = React.useState<Party[]>([])
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  // A department needs a parent and a person may have one, so the picker is
  // loaded whenever either is chosen rather than on every render.
  React.useEffect(() => {
    if (type === 'organisation') return
    void (async () => {
      try {
        const page = await api.get<PartyPage>('/parties?type=organisation')
        setOrganisations(page.parties)
      } catch {
        setOrganisations([])
      }
    })()
  }, [type])

  const choice = TYPE_CHOICES.find((entry) => entry.value === type)
  const needsParent = type === 'department'

  async function create() {
    setSaving(true)
    setError(null)
    try {
      await api.post('/parties', {
        type,
        name,
        parent_id: parentId || null,
      })
      await onCreated()
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : 'Could not add this party',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-label="New party"
      data-testid="parties-new-panel"
      className="mt-4 rounded-md border border-accent/40 bg-bg-secondary p-4"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-primary">New party</h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
        >
          Cancel
        </button>
      </div>

      <div className="mt-4 space-y-3">
        <label className="block text-xs font-medium text-text-secondary">
          What kind
          <select
            aria-label="What kind"
            value={type}
            onChange={(event) => setType(event.target.value as PartyType)}
            className="mt-1 w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm text-text-primary"
          >
            {TYPE_CHOICES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        {choice && (
          <p className="text-xs text-text-tertiary">{choice.help}</p>
        )}

        <label className="block text-xs font-medium text-text-secondary">
          Name
          <Input
            aria-label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1"
          />
        </label>

        {type !== 'organisation' && (
          <label className="block text-xs font-medium text-text-secondary">
            {needsParent ? 'Organisation (required)' : 'Organisation (optional)'}
            <select
              aria-label="Organisation"
              value={parentId}
              onChange={(event) => setParentId(event.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm text-text-primary"
            >
              <option value="">
                {needsParent ? 'Choose an organisation…' : 'None'}
              </option>
              {organisations.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {error && (
          <p
            role="alert"
            data-testid="parties-new-error"
            className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
          >
            {error}
          </p>
        )}

        <button
          type="button"
          data-testid="parties-new-submit"
          onClick={() => void create()}
          disabled={saving || !name.trim() || (needsParent && !parentId)}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
        >
          {saving
            ? 'Adding…'
            : needsParent && !parentId
              ? 'Choose an organisation first'
              : 'Add party'}
        </button>
      </div>
    </div>
  )
}
