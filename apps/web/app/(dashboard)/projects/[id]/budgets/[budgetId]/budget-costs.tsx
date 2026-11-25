'use client'

import * as React from 'react'
import { Download, Upload } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAmount } from '@/lib/money'
import type {
  ActualImportPreview,
  ActualImportResult,
  BudgetActualCreated,
  BudgetCommitment,
  BudgetCommitmentCreated,
  BudgetLedger,
  BudgetSheet,
  CostEntryDuplicate,
  Party,
  PartyPage,
} from '@/types'
import { EntryList, toApiAmount } from './budget-tracking'

/**
 * The Costs tab (slice B1): one ledger of orders and invoices, the Add
 * dialogs, close / cancel / void, and the actuals CSV.
 *
 * **Nothing here computes money.** An entry's open amount is the API's
 * `open_amount`; the totals live on the sheet and the top sheet, from
 * `GET /tracking`. The one transformation is reading a German decimal comma
 * back into the API's form — string handling, not arithmetic.
 *
 * **A duplicate is a warning, never a refusal.** Before saving, the form asks
 * `GET /budgets/{id}/duplicates` — the same comparison the server runs on
 * create — and if anything comes back it shows the matches and offers "Save
 * anyway". A supplier really can invoice the same amount twice in a month.
 */

export type EntryKind = 'commitment' | 'actual'

const today = () => new Date().toISOString().slice(0, 10)

interface Place {
  value: string
  label: string
  section_id: string
  line_id: string | null
}

/** Every place a cost can be booked: each section on its own, and each line
 *  inside it. The section-only choice is the unbudgeted cost. */
function placesOf(sheet: BudgetSheet): Place[] {
  const sections = [...sheet.sections].sort((a, b) => a.position - b.position)
  const ordered = [
    ...sections.filter((section) => !section.parent_id).flatMap((parent) => [
      parent,
      ...sections.filter((section) => section.parent_id === parent.id),
    ]),
  ]
  const places: Place[] = []
  for (const section of ordered) {
    const prefix = `${section.code ? `${section.code} ` : ''}${section.name}`
    places.push({
      value: `${section.id}|`,
      label: `${prefix} — section only (unbudgeted)`,
      section_id: section.id,
      line_id: null,
    })
    for (const line of sheet.lines
      .filter((row) => row.section_id === section.id)
      .sort((a, b) => a.position - b.position)) {
      places.push({
        value: `${section.id}|${line.id}`,
        label: `${prefix} › ${line.description}`,
        section_id: section.id,
        line_id: line.id,
      })
    }
  }
  return places
}

function useSuppliers(enabled: boolean) {
  const [suppliers, setSuppliers] = React.useState<Party[]>([])
  React.useEffect(() => {
    if (!enabled) return
    let cancelled = false
    api
      .get<PartyPage>('/parties?role=supplier&limit=200')
      .then((page) => {
        if (!cancelled && page && Array.isArray(page.parties)) setSuppliers(page.parties)
      })
      .catch(() => {
        // The picker falls back to typed text; a failed lookup must not
        // block recording a cost (rule 17c — an unknown value never blocks).
      })
    return () => {
      cancelled = true
    }
  }, [enabled])
  return suppliers
}

export function EntryForm({
  kind,
  sheet,
  defaults,
  openCommitments,
  canPickParty = true,
  onSaved,
  onCancel,
}: {
  kind: EntryKind
  sheet: BudgetSheet
  defaults?: { section_id?: string; line_id?: string | null }
  openCommitments: BudgetCommitment[]
  canPickParty?: boolean
  onSaved: (duplicates: CostEntryDuplicate[]) => Promise<void> | void
  onCancel: () => void
}) {
  const budgetId = sheet.budget.id
  const places = React.useMemo(() => placesOf(sheet), [sheet])
  const suppliers = useSuppliers(canPickParty)
  const initialPlace = defaults?.section_id ? `${defaults.section_id}|${defaults.line_id ?? ''}` : places[0]?.value ?? ''
  const [place, setPlace] = React.useState(initialPlace)
  const [search, setSearch] = React.useState('')
  const [partyId, setPartyId] = React.useState('')
  const [payeeText, setPayeeText] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [reference, setReference] = React.useState('')
  const [amount, setAmount] = React.useState('')
  const [date, setDate] = React.useState(today())
  const [paidOn, setPaidOn] = React.useState('')
  const [commitmentId, setCommitmentId] = React.useState('')
  const [warnings, setWarnings] = React.useState<CostEntryDuplicate[] | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  const shown = search.trim()
    ? places.filter((row) => row.label.toLowerCase().includes(search.trim().toLowerCase()) || row.value === place)
    : places
  const chosen = places.find((row) => row.value === place)
  const linkable = openCommitments.filter((row) => !chosen || row.section_id === chosen.section_id)

  // Why Save is not available, said in a sentence (rule 17c).
  const missing: string[] = []
  if (!chosen) missing.push('where the cost belongs')
  if (!amount.trim()) missing.push('an amount')
  if (!date) missing.push(kind === 'commitment' ? 'the order date' : 'the invoice date')

  const body = () => ({
    section_id: chosen!.section_id,
    line_id: chosen!.line_id,
    party_id: partyId || null,
    payee_text: partyId ? null : payeeText.trim() || null,
    description: description.trim() || null,
    reference: reference.trim() || null,
    amount: toApiAmount(amount),
    ...(kind === 'commitment'
      ? { commitment_date: date }
      : { incurred_on: date, paid_on: paidOn || null, commitment_id: commitmentId || null }),
  })

  const create = async () => {
    const path = `/budgets/${budgetId}/${kind === 'commitment' ? 'commitments' : 'actuals'}`
    const created = await api.post<BudgetCommitmentCreated | BudgetActualCreated>(path, body())
    await onSaved(created.duplicates ?? [])
  }

  const submit = async (anyway: boolean) => {
    if (missing.length) return
    setBusy(true)
    setError(null)
    try {
      if (!anyway) {
        const query = new URLSearchParams({ amount: toApiAmount(amount) })
        if (partyId) query.set('party_id', partyId)
        else if (payeeText.trim()) query.set('payee_text', payeeText.trim())
        if (reference.trim()) query.set('reference', reference.trim())
        const found = await api.get<CostEntryDuplicate[]>(`/budgets/${budgetId}/duplicates?${query.toString()}`)
        if (Array.isArray(found) && found.length > 0) {
          setWarnings(found)
          return
        }
      }
      await create()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The entry could not be saved')
    } finally {
      setBusy(false)
    }
  }

  const label = kind === 'commitment' ? 'commitment' : 'actual'

  return (
    <form
      data-testid={`entry-form-${kind}`}
      onSubmit={(event) => {
        event.preventDefault()
        void submit(false)
      }}
      className="space-y-2 rounded-md border border-border bg-bg-secondary p-3 text-sm"
    >
      <p className="text-xs font-medium text-text-primary">
        {kind === 'commitment' ? 'New commitment (an order, booking or agreement)' : 'New actual (an invoice or receipt)'}
      </p>
      <div>
        <input
          aria-label="Search the budget"
          placeholder="Search sections and lines…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="w-full rounded border border-border bg-transparent px-2 py-1 text-xs"
        />
        <select
          aria-label="Where it belongs"
          value={place}
          onChange={(event) => setPlace(event.target.value)}
          className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1 text-xs"
        >
          {shown.map((row) => (
            <option key={row.value} value={row.value}>
              {row.label}
            </option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {canPickParty && suppliers.length > 0 && (
          <select
            aria-label="Supplier"
            value={partyId}
            onChange={(event) => {
              setPartyId(event.target.value)
              if (event.target.value) setPayeeText('')
            }}
            className="rounded border border-border bg-transparent px-2 py-1 text-xs"
          >
            <option value="">Supplier: type a name instead</option>
            {suppliers.map((party) => (
              <option key={party.id} value={party.id}>
                {party.name}
              </option>
            ))}
          </select>
        )}
        <input
          aria-label="Payee (free text)"
          placeholder={partyId ? 'Using the supplier picked' : 'Payee, e.g. Parkgarage'}
          disabled={!!partyId}
          title={partyId ? 'A supplier is picked; clear it to type a name instead.' : undefined}
          value={payeeText}
          onChange={(event) => setPayeeText(event.target.value)}
          className="rounded border border-border bg-transparent px-2 py-1 text-xs disabled:opacity-50"
        />
        <input
          aria-label="Description"
          placeholder="Description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          className="rounded border border-border bg-transparent px-2 py-1 text-xs"
        />
        <input
          aria-label="Reference"
          placeholder={kind === 'commitment' ? 'PO / agreement no.' : 'Supplier invoice no.'}
          value={reference}
          onChange={(event) => setReference(event.target.value)}
          className="rounded border border-border bg-transparent px-2 py-1 text-xs"
        />
        <input
          aria-label="Amount (net)"
          placeholder="Amount, net"
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
          className="rounded border border-border bg-transparent px-2 py-1 text-right text-xs tabular-nums"
        />
        <input
          aria-label={kind === 'commitment' ? 'Order date' : 'Invoice date'}
          type="date"
          value={date}
          onChange={(event) => setDate(event.target.value)}
          className="rounded border border-border bg-transparent px-2 py-1 text-xs"
        />
        {kind === 'actual' && (
          <>
            <input
              aria-label="Paid on"
              type="date"
              value={paidOn}
              onChange={(event) => setPaidOn(event.target.value)}
              className="rounded border border-border bg-transparent px-2 py-1 text-xs"
            />
            <select
              aria-label="Settles commitment"
              value={commitmentId}
              onChange={(event) => setCommitmentId(event.target.value)}
              className="rounded border border-border bg-transparent px-2 py-1 text-xs"
            >
              <option value="">Not linked to an order</option>
              {linkable.map((row) => (
                <option key={row.id} value={row.id}>
                  {(row.reference || row.description || row.payee || 'Order') +
                    ` — open ${formatAmount(row.open_amount)}`}
                </option>
              ))}
            </select>
          </>
        )}
      </div>
      <p className="text-[11px] text-text-tertiary">Net, in the company currency. A correction is a new entry plus a void.</p>

      {warnings && warnings.length > 0 && (
        <div data-testid="duplicate-warning" role="alert" className="rounded border border-status-warning/50 p-2 text-xs">
          <p className="font-medium text-status-warning">
            This looks like {warnings.length === 1 ? 'an entry' : `${warnings.length} entries`} already in this budget —
            same payee, reference and amount:
          </p>
          <ul className="mt-1 list-disc pl-4 text-text-secondary">
            {warnings.map((row) => (
              <li key={row.id}>
                {row.kind === 'commitment' ? 'Order' : 'Invoice'} {row.date} · {row.payee ?? 'no payee'}
                {row.reference ? ` · ${row.reference}` : ''} · {formatAmount(row.amount)}
              </li>
            ))}
          </ul>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              data-testid="save-anyway"
              disabled={busy}
              onClick={() => void submit(true)}
              className="rounded-md bg-accent px-2 py-1 font-medium text-white disabled:opacity-50"
            >
              Save anyway
            </button>
            <button type="button" onClick={() => setWarnings(null)} className="rounded-md border border-border px-2 py-1">
              Change it
            </button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}

      {!warnings && (
        <div className="flex items-center gap-2">
          <button
            type="submit"
            data-testid={`save-${kind}`}
            disabled={busy || missing.length > 0}
            className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
          >
            Save {label}
          </button>
          <button type="button" onClick={onCancel} className="text-xs text-text-secondary">
            Cancel
          </button>
          {missing.length > 0 && (
            <span data-testid="entry-missing" className="text-[11px] text-text-tertiary">
              Needs {missing.join(', ')}.
            </span>
          )}
        </div>
      )}
    </form>
  )
}

export function CostsTab({
  sheet,
  canEdit,
  closed = false,
  canPickParty,
  onChange,
}: {
  sheet: BudgetSheet
  canEdit: boolean
  closed?: boolean
  canPickParty: boolean
  onChange: () => Promise<void> | void
}) {
  const budgetId = sheet.budget.id
  const [ledger, setLedger] = React.useState<BudgetLedger | null>(null)
  const [filters, setFilters] = React.useState({
    section_id: '',
    line_id: '',
    payee: '',
    status: '',
    date_from: '',
    date_to: '',
    include_voided: false,
  })
  const [adding, setAdding] = React.useState<EntryKind | null>(null)
  const [importing, setImporting] = React.useState(false)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  const load = React.useCallback(async () => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (key === 'include_voided') {
        if (value) query.set(key, 'true')
      } else if (value) {
        query.set(key, String(value))
      }
    }
    try {
      setLedger(await api.get<BudgetLedger>(`/budgets/${budgetId}/ledger?${query.toString()}`))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the costs')
    }
  }, [budgetId, filters])

  React.useEffect(() => {
    void load()
  }, [load])

  const refresh = async () => {
    await onChange()
    await load()
  }

  const act = async (call: () => Promise<unknown>, failure: string) => {
    setError(null)
    try {
      await call()
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : failure)
    }
  }

  const ask = (question: string) => {
    const answer = window.prompt(question)
    return answer && answer.trim() ? answer.trim() : null
  }

  const exportCsv = async () => {
    setError(null)
    try {
      const blob = await api.download(`/budgets/${budgetId}/actuals/export.csv`)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `actuals-${sheet.budget.name}.csv`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The export failed')
    }
  }

  const sections = [...sheet.sections].sort((a, b) => a.position - b.position)
  const lines = sheet.lines.filter((line) => !filters.section_id || line.section_id === filters.section_id)

  return (
    <div data-testid="costs-tab" className="mt-4">
      <div className="flex flex-wrap items-center gap-2">
        {canEdit && (
          <>
            <button
              type="button"
              data-testid="add-commitment"
              onClick={() => setAdding('commitment')}
              className="rounded-md border border-border px-3 py-1.5 text-sm"
            >
              Add commitment
            </button>
            <button
              type="button"
              data-testid="add-actual"
              onClick={() => setAdding('actual')}
              className="rounded-md border border-border px-3 py-1.5 text-sm"
            >
              Add actual
            </button>
            <button
              type="button"
              data-testid="import-actuals"
              onClick={() => setImporting(!importing)}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm"
            >
              <Upload className="h-3.5 w-3.5" /> Import CSV
            </button>
          </>
        )}
        <button
          type="button"
          data-testid="export-actuals"
          onClick={() => void exportCsv()}
          className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm"
        >
          <Download className="h-3.5 w-3.5" /> Export CSV
        </button>
      </div>
      {!canEdit &&
        (closed ? (
          <p data-testid="costs-closed" className="mt-2 text-xs text-text-secondary">
            This budget is closed, so no orders or invoices can be added, closed or voided. Reopen it first.
          </p>
        ) : (
          <p data-testid="costs-read-only" className="mt-2 text-xs text-text-secondary">
            You can read the costs but not add, close or void them — that needs the budget edit permission. An owner or
            administrator can grant it under Settings → Company → Members.
          </p>
        ))}

      {adding && (
        <div className="mt-3 max-w-2xl">
          <EntryForm
            kind={adding}
            sheet={sheet}
            canPickParty={canPickParty}
            openCommitments={(ledger?.commitments ?? []).filter((row) => row.status === 'open' && !row.is_voided)}
            onSaved={async (duplicates) => {
              setAdding(null)
              setNotice(
                duplicates.length
                  ? `Saved. It matches ${duplicates.length} other entr${duplicates.length === 1 ? 'y' : 'ies'} — check the ledger.`
                  : 'Saved.',
              )
              await refresh()
            }}
            onCancel={() => setAdding(null)}
          />
        </div>
      )}
      {importing && canEdit && (
        <ActualsImport budgetId={budgetId} onApplied={refresh} onClose={() => setImporting(false)} />
      )}

      <div className="mt-4 flex flex-wrap items-end gap-2 text-xs">
        <select
          aria-label="Filter by section"
          value={filters.section_id}
          onChange={(event) => setFilters({ ...filters, section_id: event.target.value, line_id: '' })}
          className="rounded border border-border bg-transparent px-2 py-1"
        >
          <option value="">All sections</option>
          {sections.map((section) => (
            <option key={section.id} value={section.id}>
              {section.code ? `${section.code} ` : ''}
              {section.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by line"
          value={filters.line_id}
          onChange={(event) => setFilters({ ...filters, line_id: event.target.value })}
          className="rounded border border-border bg-transparent px-2 py-1"
        >
          <option value="">All lines</option>
          {lines.map((line) => (
            <option key={line.id} value={line.id}>
              {line.description}
            </option>
          ))}
        </select>
        <input
          aria-label="Filter by payee"
          placeholder="Payee"
          value={filters.payee}
          onChange={(event) => setFilters({ ...filters, payee: event.target.value })}
          className="rounded border border-border bg-transparent px-2 py-1"
        />
        <select
          aria-label="Filter by status"
          value={filters.status}
          onChange={(event) => setFilters({ ...filters, status: event.target.value })}
          className="rounded border border-border bg-transparent px-2 py-1"
        >
          <option value="">Any order status</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <input
          aria-label="From date"
          type="date"
          value={filters.date_from}
          onChange={(event) => setFilters({ ...filters, date_from: event.target.value })}
          className="rounded border border-border bg-transparent px-2 py-1"
        />
        <input
          aria-label="To date"
          type="date"
          value={filters.date_to}
          onChange={(event) => setFilters({ ...filters, date_to: event.target.value })}
          className="rounded border border-border bg-transparent px-2 py-1"
        />
        <label className="flex items-center gap-1 text-text-secondary">
          <input
            type="checkbox"
            data-testid="show-voided"
            checked={filters.include_voided}
            onChange={(event) => setFilters({ ...filters, include_voided: event.target.checked })}
          />
          Show voided
        </label>
      </div>

      {notice && (
        <p data-testid="costs-notice" className="mt-3 text-xs text-text-secondary">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      <EntryList
        ledger={ledger}
        canEdit={canEdit}
        onVoid={(kind, id) => {
          const reason = ask('Why is this entry being voided? It stays in the ledger with this reason.')
          if (!reason) return
          void act(
            () => api.post(`/budgets/${budgetId}/${kind === 'commitment' ? 'commitments' : 'actuals'}/${id}/void`, { reason }),
            'Could not void the entry',
          )
        }}
        onClose={(row) => {
          const reason = ask('Close this order? Anything not yet invoiced leaves the open amount. Why?')
          if (!reason) return
          void act(() => api.post(`/budgets/${budgetId}/commitments/${row.id}/close`, { reason }), 'Could not close it')
        }}
        onCancel={(row) => {
          const reason = ask('Cancel this order? It leaves the committed totals entirely. Why?')
          if (!reason) return
          void act(() => api.post(`/budgets/${budgetId}/commitments/${row.id}/cancel`, { reason }), 'Could not cancel it')
        }}
      />
    </div>
  )
}

const IMPORT_LABELS: Record<string, string> = {
  create: 'New actual',
  duplicate: 'Looks like a duplicate — held back',
  error: 'Refused',
}

/** Preview, tick any duplicates to import anyway, then apply with the digest. */
export function ActualsImport({
  budgetId,
  onApplied,
  onClose,
}: {
  budgetId: string
  onApplied: () => Promise<void> | void
  onClose: () => void
}) {
  const [file, setFile] = React.useState<File | null>(null)
  const [preview, setPreview] = React.useState<ActualImportPreview | null>(null)
  const [applied, setApplied] = React.useState<ActualImportResult | null>(null)
  const [ticked, setTicked] = React.useState<Set<number>>(new Set())
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  const runPreview = async (chosen: File) => {
    setBusy(true)
    setPreview(null)
    setApplied(null)
    setTicked(new Set())
    setError(null)
    const form = new FormData()
    form.append('file', chosen)
    try {
      setPreview(await api.upload<ActualImportPreview>(`/budgets/${budgetId}/actuals/import/preview`, form))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not read that file')
    } finally {
      setBusy(false)
    }
  }

  const apply = async () => {
    if (!file || !preview) return
    setBusy(true)
    setError(null)
    const form = new FormData()
    form.append('file', file)
    const query = new URLSearchParams({ expected_digest: preview.digest })
    ticked.forEach((line) => query.append('include_duplicates', String(line)))
    try {
      setApplied(await api.upload<ActualImportResult>(`/budgets/${budgetId}/actuals/import?${query.toString()}`, form))
      await onApplied()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The import was refused')
    } finally {
      setBusy(false)
    }
  }

  const result = applied ?? preview
  const actionable = preview ? preview.creates + ticked.size : 0

  return (
    <section data-testid="actuals-import" className="mt-4 rounded-md border border-border bg-bg-secondary p-4">
      <div className="flex items-start justify-between gap-3">
        <label className="text-xs text-text-secondary">
          Import actuals from a CSV
          <input
            type="file"
            accept=".csv,text/csv,text/plain"
            data-testid="actuals-import-file"
            onChange={(event) => {
              const chosen = event.target.files?.[0] ?? null
              setFile(chosen)
              if (chosen) void runPreview(chosen)
            }}
            className="mt-2 block text-sm text-text-primary"
          />
        </label>
        <button type="button" onClick={onClose} className="text-xs text-text-secondary">
          Close
        </button>
      </div>
      <p className="mt-2 text-xs text-text-tertiary">
        Columns: <code>date;description;payee;reference;amount;section_code;line_description;paid_on</code>. This only
        adds costs — it never changes or voids one already here. A row that looks like an existing entry is held back
        unless you tick it. An unknown section code is refused, never invented. <code>10.000</code> is refused because it
        could mean ten or ten thousand.
      </p>
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
      {result && (
        <>
          <p data-testid="actuals-import-summary" className="mt-3 text-sm text-text-primary">
            {applied
              ? `Applied: ${applied.created} added.`
              : `This file would add ${result.creates}, hold back ${result.duplicates} as duplicates, and refuse ${result.errors}.`}
          </p>
          <table className="mt-2 w-full text-xs">
            <thead>
              <tr className="border-b border-border text-left text-text-tertiary">
                <th className="py-1 pr-2 font-medium">Row</th>
                <th className="py-1 pr-2 font-medium">Section</th>
                <th className="py-1 pr-2 font-medium">Description</th>
                <th className="py-1 pr-2 font-medium">What happens</th>
                <th className="py-1 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row) => (
                <tr key={row.line_number} data-testid="actuals-import-row" className="border-b border-border/60">
                  <td className="py-1 pr-2">{row.line_number}</td>
                  <td className="py-1 pr-2 font-mono">{row.section_code}</td>
                  <td className="py-1 pr-2">{row.description}</td>
                  <td className={`py-1 pr-2 ${row.action === 'error' ? 'text-danger' : ''}`}>
                    {row.action === 'duplicate' && !applied ? (
                      <label className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          aria-label={`Import row ${row.line_number} anyway`}
                          checked={ticked.has(row.line_number)}
                          onChange={(event) =>
                            setTicked((previous) => {
                              const next = new Set(previous)
                              if (event.target.checked) next.add(row.line_number)
                              else next.delete(row.line_number)
                              return next
                            })
                          }
                        />
                        Import anyway
                      </label>
                    ) : (
                      IMPORT_LABELS[row.action] ?? row.action
                    )}
                  </td>
                  <td className="py-1 text-text-secondary">{row.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!applied && (
            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                data-testid="actuals-import-apply"
                disabled={busy || actionable === 0}
                onClick={() => void apply()}
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                Add {actionable} cost{actionable === 1 ? '' : 's'}
              </button>
              {actionable === 0 && (
                <span className="text-xs text-text-tertiary">
                  Nothing in this file would be added — tick a held-back row to import it anyway.
                </span>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
