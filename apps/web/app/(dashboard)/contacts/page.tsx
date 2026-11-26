'use client'

import * as React from 'react'
import { Loader2, Plus, Search } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type { PartyPage } from '@/types'
import {
  ColumnPicker,
  ContactsTable,
  availableColumns,
  loadHiddenColumns,
  type ColumnKey,
} from './contacts-table'
import { NewPartyPanel } from './new-contact-panel'

/**
 * Contacts — customers, suppliers, partners, crew and the people in them.
 * The screen says Contacts; the API and the code still say party (P1b-4).
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

const TABS: {
  key: string
  label: string
  role?: string
  type?: string
  needsAttention?: boolean
}[] = [
  { key: 'all', label: 'All' },
  { key: 'customers', label: 'Customers', role: 'customer' },
  { key: 'suppliers', label: 'Suppliers', role: 'supplier' },
  { key: 'partners', label: 'Partners', role: 'partner' },
  { key: 'crew', label: 'Crew', role: 'crew' },
  { key: 'people', label: 'People', type: 'person' },
  // Last, and its own tab rather than a filter inside the others: it is a
  // worklist, not a view of the customer base. Somebody opens it to find
  // out what changed, and leaves it empty.
  { key: 'attention', label: 'Needs attention', needsAttention: true },
]

export default function ContactsPage() {
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
  // Read after mount: `localStorage` does not exist during the server render.
  const [hidden, setHidden] = React.useState<ColumnKey[]>([])
  React.useEffect(() => setHidden(loadHiddenColumns()), [])
  const allowed = availableColumns(canEdit)
  const columns = allowed.filter((column) => column.fixed || !hidden.includes(column.key))

  const refresh = React.useCallback(
    async (spinner = false) => {
      if (spinner) setLoading(true)
      const chosen = TABS.find((entry) => entry.key === tab)
      const params = new URLSearchParams()
      if (search.trim()) params.set('search', search.trim())
      if (chosen?.role) params.set('role', chosen.role)
      if (chosen?.type) params.set('type', chosen.type)
      if (includeInactive) params.set('include_inactive', 'true')
      if (chosen?.needsAttention) params.set('needs_attention', 'true')
      try {
        setPage(await api.get<PartyPage>(`/parties?${params.toString()}`))
        setError(null)
      } catch (err) {
        setError(
          err instanceof ApiError ? err.detail : 'The contacts could not be loaded. Try again.',
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
        <h1 className="text-lg font-semibold text-text-primary">Contacts</h1>
        <p className="mt-2 text-sm text-text-secondary">
          You do not have access to this company&rsquo;s customers and
          suppliers. An owner or administrator can change that under Settings
          → Company → Members.
        </p>
      </div>
    )
  }

  return (
    <div className="w-full p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Contacts</h1>
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
          You can read these but not change them — adding or editing a contact
          needs permission to edit contacts. An owner or administrator can
          grant it under Settings → Company → Members.
        </p>
      )}

      <div role="tablist" aria-label="Contacts" className="mt-5 flex gap-1 border-b border-border">
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
            aria-label="Search contacts"
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
        <ColumnPicker columns={allowed} hidden={hidden} onChange={setHidden} />
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
            : tab === 'attention'
              ? 'Nothing needs attention. Every name is split and every registry answer still says what it said last time.'
              : 'No contacts yet. Add the first one with the New button.'}
        </p>
      ) : (
        <div className="mt-4">
          <ContactsTable parties={page?.parties ?? []} columns={columns} />
          <p className="pt-2 text-xs text-text-tertiary">
            {page?.total} in total
          </p>
        </div>
      )}
    </div>
  )
}
