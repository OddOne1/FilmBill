'use client'

import * as React from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAmount, NO_VALUE } from '@/lib/money'
import type {
  BudgetActual,
  BudgetCommitment,
  BudgetLedger,
  BudgetLine,
  BudgetSection,
  BudgetSheet,
  BudgetTracking,
  LineTracking,
  RollupTracking,
} from '@/types'
import { EntryForm, type EntryKind } from './budget-costs'

/**
 * Cost planning on the sheet (slice B1).
 *
 * **Nothing here computes money.** Committed, actual, EFC, variance, the
 * over-budget flag, the unallocated amounts, the forecast margin — every
 * number is read out of `GET /budgets/{id}/tracking`, keyed by line and by
 * section id, and printed. The browser does not subtract an EFC from an
 * estimate to get a variance: the API's `tracking.py` does, with the same
 * rounding the engine uses, and a second subtraction here is how a row and
 * its total end up a cent apart.
 *
 * The Estimate phase is not drawn by this file at all — it is A2's sheet,
 * unchanged. The other four phases draw this read-only view over the same
 * sections and lines.
 */

export const PHASES = ['estimate', 'committed', 'actual', 'efc', 'variance'] as const
export type Phase = (typeof PHASES)[number]

export const PHASE_LABELS: Record<Phase, string> = {
  estimate: 'Estimate',
  committed: 'Committed',
  actual: 'Actual',
  efc: 'EFC',
  variance: 'Variance',
}

/** What each phase reads off a tracking row. A lookup, not arithmetic. */
function phaseValue(row: LineTracking | RollupTracking | undefined, phase: Phase): string | null | undefined {
  if (!row) return undefined
  switch (phase) {
    case 'committed':
      return row.committed_open
    case 'actual':
      return row.actual
    case 'efc':
      return row.efc
    case 'variance':
      return row.variance
    default:
      return row.estimate_cost
  }
}

const PHASE_EXPLAINED: Record<Exclude<Phase, 'estimate'>, string> = {
  committed:
    'What is still owed on open orders: each commitment less the invoices linked to it, never below zero.',
  actual: 'What has been invoiced or paid — every non-voided actual.',
  efc:
    'Estimate at completion: actual plus what is still to be spent — your ETC where you set one, otherwise the larger of what is left of the estimate and what is on order.',
  variance: 'Estimate cost minus EFC. Positive is under budget; negative is over.',
}

export function PhaseSwitch({ phase, onChange }: { phase: Phase; onChange: (phase: Phase) => void }) {
  return (
    <div role="radiogroup" aria-label="Phase" className="inline-flex rounded-md border border-border p-0.5 text-xs">
      {PHASES.map((key) => (
        <button
          key={key}
          type="button"
          role="radio"
          aria-checked={phase === key}
          data-testid={`phase-${key}`}
          onClick={() => onChange(key)}
          className={
            phase === key
              ? 'rounded bg-accent px-2.5 py-1 font-medium text-white'
              : 'rounded px-2.5 py-1 text-text-secondary hover:text-text-primary'
          }
        >
          {PHASE_LABELS[key]}
        </button>
      ))}
    </div>
  )
}

function OverBudget() {
  return (
    <span
      data-testid="over-budget"
      title="Forecast to cost more than its estimate"
      className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-danger/10 px-1 text-[10px] font-medium text-danger"
    >
      <AlertTriangle className="h-3 w-3" /> over
    </span>
  )
}

/** The four non-estimate phases over the sheet's own sections and lines. */
export function PhaseSheet({
  sheet,
  tracking,
  phase,
  onOpenLine,
}: {
  sheet: BudgetSheet
  tracking: BudgetTracking | null
  phase: Exclude<Phase, 'estimate'>
  onOpenLine: (line: BudgetLine) => void
}) {
  const [collapsed, setCollapsed] = React.useState<Set<string>>(new Set())
  const sections = [...sheet.sections].sort((a, b) => a.position - b.position)
  const topLevel = sections.filter((section) => !section.parent_id)
  const childrenOf = (id: string) => sections.filter((section) => section.parent_id === id)
  const linesOf = (id: string) =>
    sheet.lines.filter((line) => line.section_id === id).sort((a, b) => a.position - b.position)

  const lineRow = (id: string) => tracking?.lines?.[id]
  const sectionRow = (id: string) => tracking?.sections?.[id]

  const renderSection = (section: BudgetSection, depth: number): React.ReactNode => {
    const rollup = sectionRow(section.id)
    const isCollapsed = collapsed.has(section.id)
    return (
      <React.Fragment key={section.id}>
        <tr data-testid="phase-section-row" data-section={section.id} className="border-b border-border bg-bg-secondary">
          <td className="py-1.5 pr-2">
            <div className="flex items-center gap-1.5" style={{ paddingLeft: depth * 16 }}>
              <button
                type="button"
                aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${section.name}`}
                onClick={() =>
                  setCollapsed((previous) => {
                    const next = new Set(previous)
                    if (next.has(section.id)) next.delete(section.id)
                    else next.add(section.id)
                    return next
                  })
                }
                className="text-text-tertiary"
              >
                {isCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              </button>
              {section.code && <span className="font-mono text-xs text-text-tertiary">{section.code}</span>}
              <span className="text-sm font-medium text-text-primary">{section.name}</span>
              {rollup?.over_budget && <OverBudget />}
            </div>
          </td>
          <td className="py-1.5 pr-2 text-right tabular-nums text-text-secondary">{formatAmount(rollup?.estimate_cost)}</td>
          <td data-testid="phase-section-value" className="py-1.5 pr-2 text-right font-medium tabular-nums">
            {formatAmount(phaseValue(rollup, phase))}
          </td>
          <td className="py-1.5 text-right text-xs tabular-nums text-text-tertiary">
            {rollup && rollup.unallocated !== undefined && rollup.unallocated !== '0.00' ? (
              <span data-testid="phase-unallocated" title="Costs booked on this section with no line">
                {formatAmount(rollup.unallocated)} unallocated
              </span>
            ) : null}
          </td>
        </tr>
        {!isCollapsed &&
          linesOf(section.id).map((line) => {
            const row = lineRow(line.id)
            return (
              <tr
                key={line.id}
                data-testid="phase-line-row"
                data-line={line.id}
                onClick={() => onOpenLine(line)}
                className="cursor-pointer border-b border-border/60 hover:bg-bg-secondary/60"
              >
                <td className="py-1 pr-2" style={{ paddingLeft: depth * 16 + 24 }}>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation()
                      onOpenLine(line)
                    }}
                    className="text-left text-text-primary hover:underline"
                  >
                    {line.description}
                  </button>
                  {row?.over_budget && <OverBudget />}
                  {row?.remaining_rule === 'etc' && (
                    <span
                      data-testid="line-has-etc"
                      title="You set an estimate to complete for this line"
                      className="ml-1.5 rounded bg-accent/10 px-1 text-[10px] font-medium text-accent"
                    >
                      ETC {formatAmount(row.etc)}
                    </span>
                  )}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums text-text-secondary">{formatAmount(row?.estimate_cost)}</td>
                <td data-testid="phase-line-value" className="py-1 pr-2 text-right tabular-nums">
                  {formatAmount(phaseValue(row, phase))}
                </td>
                <td />
              </tr>
            )
          })}
        {!isCollapsed && childrenOf(section.id).map((child) => renderSection(child, depth + 1))}
      </React.Fragment>
    )
  }

  return (
    <div className="mt-4 overflow-x-auto">
      <p data-testid="phase-explained" className="mb-2 text-xs text-text-secondary">
        {PHASE_EXPLAINED[phase]} Click a line to see its orders and invoices.
      </p>
      {!tracking && (
        <p className="mb-2 text-xs text-text-tertiary">The cost figures could not be loaded, so the columns are empty.</p>
      )}
      <table data-testid="phase-sheet" data-phase={phase} className="w-full min-w-[40rem] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-tertiary">
            <th className="py-2 pr-2 font-medium">Description</th>
            <th className="py-2 pr-2 text-right font-medium">Estimate cost</th>
            <th className="py-2 pr-2 text-right font-medium">{PHASE_LABELS[phase]}</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {topLevel.map((section) => renderSection(section, 0))}
          {tracking?.budget && (
            <tr className="border-t-2 border-border font-medium">
              <td className="py-2 pr-2">
                Total{tracking.budget.over_budget && <OverBudget />}
              </td>
              <td className="py-2 pr-2 text-right tabular-nums">{formatAmount(tracking.budget.estimate_cost)}</td>
              <td data-testid="phase-total" className="py-2 pr-2 text-right tabular-nums">
                {formatAmount(phaseValue(tracking.budget, phase))}
              </td>
              <td />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

/** A line's own orders and invoices, its ETC, and its forecast. */
export function LineDrawer({
  sheet,
  line,
  tracking,
  canEdit,
  onClose,
  onChange,
}: {
  sheet: BudgetSheet
  line: BudgetLine
  tracking: BudgetTracking | null
  canEdit: boolean
  onClose: () => void
  onChange: () => Promise<void> | void
}) {
  const budgetId = sheet.budget.id
  const row = tracking?.lines?.[line.id]
  const [ledger, setLedger] = React.useState<BudgetLedger | null>(null)
  const [showVoided, setShowVoided] = React.useState(false)
  const [adding, setAdding] = React.useState<EntryKind | null>(null)
  const [etc, setEtc] = React.useState<string>(row?.etc ?? '')
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => setEtc(row?.etc ?? ''), [row?.etc])

  const loadLedger = React.useCallback(async () => {
    try {
      const query = new URLSearchParams({ line_id: line.id })
      if (showVoided) query.set('include_voided', 'true')
      setLedger(await api.get<BudgetLedger>(`/budgets/${budgetId}/ledger?${query.toString()}`))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load this line’s costs')
    }
  }, [budgetId, line.id, showVoided])

  React.useEffect(() => {
    void loadLedger()
  }, [loadLedger])

  const refresh = async () => {
    await onChange()
    await loadLedger()
  }

  const saveEtc = async (value: string | null) => {
    setError(null)
    try {
      await api.put(`/budgets/${budgetId}/lines/${line.id}/etc`, { etc: value })
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The ETC could not be saved')
    }
  }

  const voidEntry = async (kind: EntryKind, id: string) => {
    const reason = window.prompt('Why is this entry being voided? It stays in the ledger with this reason.')
    if (!reason || !reason.trim()) return
    setError(null)
    try {
      await api.post(`/budgets/${budgetId}/${kind === 'commitment' ? 'commitments' : 'actuals'}/${id}/void`, {
        reason: reason.trim(),
      })
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not void the entry')
    }
  }

  return (
    <aside
      data-testid="line-drawer"
      aria-label={`Costs for ${line.description}`}
      className="fixed inset-y-0 right-0 z-40 w-full max-w-md overflow-y-auto border-l border-border bg-bg-primary p-5 shadow-xl"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-text-primary">{line.description}</h2>
          {line.catalog_difference && (
            <p data-testid="drawer-catalog-difference" className="mt-1 text-xs text-status-warning">
              Its rates differ from the catalog item “{line.catalog_difference.item_name}” — see the Estimate sheet.
            </p>
          )}
        </div>
        <button type="button" aria-label="Close" onClick={onClose} className="text-text-tertiary hover:text-text-primary">
          <X className="h-4 w-4" />
        </button>
      </div>

      <dl className="mt-4 grid grid-cols-[1fr_auto] gap-y-1 text-sm">
        <dt className="text-text-secondary">Estimate cost</dt>
        <dd className="text-right tabular-nums">{formatAmount(row?.estimate_cost)}</dd>
        <dt className="text-text-secondary">Committed (open)</dt>
        <dd className="text-right tabular-nums">{formatAmount(row?.committed_open)}</dd>
        <dt className="text-text-secondary">Actual</dt>
        <dd className="text-right tabular-nums">{formatAmount(row?.actual)}</dd>
        <dt className="text-text-secondary">Still to spend</dt>
        <dd className="text-right tabular-nums">{formatAmount(row?.remaining)}</dd>
        <dt className="font-medium text-text-primary">EFC</dt>
        <dd data-testid="drawer-efc" className="text-right font-medium tabular-nums">
          {formatAmount(row?.efc)}
        </dd>
        <dt className="text-text-secondary">Variance</dt>
        <dd data-testid="drawer-variance" className={`text-right tabular-nums ${row?.over_budget ? 'text-danger' : ''}`}>
          {formatAmount(row?.variance)}
          {row?.variance_percent ? ` (${formatAmount(row.variance_percent)} %)` : ''}
        </dd>
      </dl>
      <p data-testid="drawer-rule" className="mt-2 text-xs text-text-tertiary">
        {row?.remaining_rule === 'etc'
          ? 'Still to spend is the ETC you set.'
          : 'Still to spend follows the default rule: what is left of the estimate, or what is on order if that is more. This rule is provisional (SCOPE D83).'}
      </p>

      {canEdit && (
        <div className="mt-4 rounded-md border border-border p-3">
          <label className="text-xs text-text-secondary" htmlFor="etc-input">
            Estimate to complete
          </label>
          <div className="mt-1 flex items-center gap-2">
            <input
              id="etc-input"
              aria-label={`ETC — ${line.description}`}
              value={etc}
              placeholder="default rule"
              onChange={(event) => setEtc(event.target.value)}
              className="w-32 rounded border border-border bg-transparent px-2 py-1 text-right text-sm tabular-nums"
            />
            <button
              type="button"
              data-testid="etc-save"
              disabled={!etc.trim()}
              onClick={() => void saveEtc(toApiAmount(etc))}
              className="rounded-md border border-border px-2 py-1 text-xs disabled:opacity-50"
            >
              Set
            </button>
            <button
              type="button"
              data-testid="etc-clear"
              disabled={row?.remaining_rule !== 'etc'}
              title={row?.remaining_rule !== 'etc' ? 'No ETC is set — the default rule is already in use.' : undefined}
              onClick={() => void saveEtc(null)}
              className="rounded-md border border-border px-2 py-1 text-xs disabled:opacity-50"
            >
              Clear
            </button>
          </div>
          {row?.remaining_rule !== 'etc' && (
            <p className="mt-1 text-[11px] text-text-tertiary">No ETC set, so Clear has nothing to clear. 0 means the line is finished.</p>
          )}
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      <div className="mt-5 flex items-center justify-between">
        <h3 className="text-sm font-medium text-text-primary">Orders and invoices</h3>
        <label className="flex items-center gap-1 text-xs text-text-secondary">
          <input type="checkbox" checked={showVoided} onChange={(event) => setShowVoided(event.target.checked)} />
          Show voided
        </label>
      </div>
      <EntryList ledger={ledger} canEdit={canEdit} onVoid={voidEntry} />

      {canEdit ? (
        adding ? (
          <div className="mt-3">
            <EntryForm
              kind={adding}
              sheet={sheet}
              defaults={{ section_id: line.section_id, line_id: line.id }}
              openCommitments={(ledger?.commitments ?? []).filter((row) => row.status === 'open' && !row.is_voided)}
              onSaved={async () => {
                setAdding(null)
                await refresh()
              }}
              onCancel={() => setAdding(null)}
            />
          </div>
        ) : (
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              data-testid="drawer-add-commitment"
              onClick={() => setAdding('commitment')}
              className="rounded-md border border-border px-3 py-1.5 text-xs"
            >
              + Commitment
            </button>
            <button
              type="button"
              data-testid="drawer-add-actual"
              onClick={() => setAdding('actual')}
              className="rounded-md border border-border px-3 py-1.5 text-xs"
            >
              + Actual
            </button>
          </div>
        )
      ) : (
        <p data-testid="drawer-read-only" className="mt-3 text-xs text-text-tertiary">
          Adding or voiding costs needs the budget edit permission.
        </p>
      )}
    </aside>
  )
}

/** "1.234,56" → "1234.56". String handling for the API's decimal form, not arithmetic. */
export function toApiAmount(typed: string): string {
  const text = typed.trim().replace(/\s/g, '')
  if (text.includes(',')) return text.replace(/\./g, '').replace(',', '.')
  return text
}

/** Commitments then actuals, as the API sent them. */
export function EntryList({
  ledger,
  canEdit,
  onVoid,
  onClose,
  onCancel,
}: {
  ledger: BudgetLedger | null
  canEdit: boolean
  onVoid?: (kind: EntryKind, id: string) => void
  onClose?: (row: BudgetCommitment) => void
  onCancel?: (row: BudgetCommitment) => void
}) {
  if (!ledger) return <p className="mt-2 text-xs text-text-tertiary">Loading…</p>
  const rows: { kind: EntryKind; row: BudgetCommitment | BudgetActual; date: string }[] = [
    ...(ledger.commitments ?? []).map((row) => ({ kind: 'commitment' as const, row, date: row.commitment_date })),
    ...(ledger.actuals ?? []).map((row) => ({ kind: 'actual' as const, row, date: row.incurred_on })),
  ].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0))
  if (rows.length === 0) return <p className="mt-2 text-xs text-text-tertiary">Nothing booked yet.</p>
  return (
    <table data-testid="ledger" className="mt-2 w-full text-xs">
      <thead>
        <tr className="border-b border-border text-left text-text-tertiary">
          <th className="py-1 pr-2 font-medium">Date</th>
          <th className="py-1 pr-2 font-medium">Kind</th>
          <th className="py-1 pr-2 font-medium">Where</th>
          <th className="py-1 pr-2 font-medium">Payee / reference</th>
          <th className="py-1 pr-2 text-right font-medium">Amount</th>
          <th className="py-1 pr-2 font-medium">Status</th>
          <th className="py-1" />
        </tr>
      </thead>
      <tbody>
        {rows.map(({ kind, row, date }) => {
          const commitment = kind === 'commitment' ? (row as BudgetCommitment) : null
          return (
            <tr
              key={`${kind}-${row.id}`}
              data-testid="ledger-row"
              data-kind={kind}
              data-voided={row.is_voided ? 'true' : 'false'}
              className={`border-b border-border/60 ${row.is_voided ? 'text-text-tertiary line-through' : ''}`}
            >
              <td className="py-1 pr-2 tabular-nums">{date}</td>
              <td className="py-1 pr-2">{kind === 'commitment' ? 'Order' : 'Invoice'}</td>
              <td className="py-1 pr-2">
                <span className="font-mono">{row.section_code ?? ''}</span> {row.line_description ?? <em>section only</em>}
              </td>
              <td className="py-1 pr-2">
                {row.payee ?? NO_VALUE}
                {row.reference ? ` · ${row.reference}` : ''}
              </td>
              <td className="py-1 pr-2 text-right tabular-nums">
                {formatAmount(row.amount)}
                {commitment && commitment.status === 'open' && !row.is_voided && (
                  <span className="block text-[10px] text-text-tertiary">open {formatAmount(commitment.open_amount)}</span>
                )}
              </td>
              <td className="py-1 pr-2">
                {row.is_voided ? (
                  <span title={row.void_reason ?? undefined}>voided</span>
                ) : commitment ? (
                  commitment.status
                ) : (row as BudgetActual).paid_on ? (
                  'paid'
                ) : (
                  'unpaid'
                )}
              </td>
              <td className="py-1 text-right whitespace-nowrap">
                {canEdit && !row.is_voided && (
                  <span className="flex justify-end gap-2">
                    {commitment && commitment.status === 'open' && onClose && (
                      <button type="button" onClick={() => onClose(commitment)} className="hover:underline">
                        Close
                      </button>
                    )}
                    {commitment && commitment.status === 'open' && onCancel && (
                      <button type="button" onClick={() => onCancel(commitment)} className="hover:underline">
                        Cancel
                      </button>
                    )}
                    {onVoid && (
                      <button
                        type="button"
                        aria-label={`Void ${kind} ${row.reference ?? row.id}`}
                        onClick={() => onVoid(kind, row.id)}
                        className="text-danger hover:underline"
                      >
                        Void
                      </button>
                    )}
                  </span>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** The top sheet's cost half: five columns per section, unallocated, total,
 *  the forecast margin beside the estimate margin, and a plain bar. Every
 *  figure is the API's. */
export function CostForecast({
  sheet,
  tracking,
  currency,
}: {
  sheet: BudgetSheet
  tracking: BudgetTracking | null
  currency: string
}) {
  if (!tracking?.budget) {
    return <p className="mt-6 text-sm text-text-secondary">The cost forecast could not be loaded.</p>
  }
  const total = tracking.budget
  const sections = [...sheet.sections].filter((section) => !section.parent_id).sort((a, b) => a.position - b.position)
  const columns: [keyof RollupTracking, string][] = [
    ['estimate_cost', 'Estimate cost'],
    ['committed_open', 'Committed (open)'],
    ['actual', 'Actual'],
    ['efc', 'EFC'],
    ['variance', 'Variance'],
  ]
  return (
    <section data-testid="cost-forecast" className="mt-8 max-w-5xl">
      <h2 className="text-sm font-semibold text-text-primary">Cost forecast ({currency})</h2>
      <table className="mt-2 w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-tertiary">
            <th className="py-2 pr-3 font-medium">Code</th>
            <th className="py-2 pr-3 font-medium">Section</th>
            {columns.map(([, label]) => (
              <th key={label} className="py-2 pr-3 text-right font-medium">
                {label}
              </th>
            ))}
            <th className="py-2 font-medium">Spent and owed against the estimate</th>
          </tr>
        </thead>
        <tbody>
          {sections.map((section) => {
            const row = tracking.sections?.[section.id]
            return (
              <tr key={section.id} data-testid="forecast-row" data-section={section.id} className="border-b border-border/60">
                <td className="py-1.5 pr-3 font-mono text-xs text-text-tertiary">{section.code ?? ''}</td>
                <td className="py-1.5 pr-3">
                  {section.name}
                  {row?.over_budget && <OverBudget />}
                </td>
                {columns.map(([key]) => (
                  <td
                    key={key}
                    data-testid={`forecast-${key}`}
                    className={`py-1.5 pr-3 text-right tabular-nums ${key === 'variance' && row?.over_budget ? 'text-danger' : ''}`}
                  >
                    {formatAmount(row?.[key] as string | undefined)}
                  </td>
                ))}
                <td className="py-1.5">
                  <SpendBar row={row} />
                </td>
              </tr>
            )
          })}
          <tr data-testid="forecast-unallocated" className="border-b border-border/60 text-text-secondary">
            <td />
            <td className="py-1.5 pr-3 italic">Unallocated (on a section, no line)</td>
            <td className="py-1.5 pr-3 text-right">{NO_VALUE}</td>
            <td className="py-1.5 pr-3 text-right tabular-nums">{formatAmount(total.unallocated_committed_open)}</td>
            <td className="py-1.5 pr-3 text-right tabular-nums">{formatAmount(total.unallocated_actual)}</td>
            <td data-testid="forecast-unallocated-total" className="py-1.5 pr-3 text-right tabular-nums">
              {formatAmount(total.unallocated)}
            </td>
            <td />
            <td />
          </tr>
          <tr data-testid="forecast-total" className="font-medium">
            <td />
            <td className="py-2 pr-3">
              Total{total.over_budget && <OverBudget />}
            </td>
            {columns.map(([key]) => (
              <td key={key} data-testid={`forecast-total-${key}`} className="py-2 pr-3 text-right tabular-nums">
                {formatAmount(total[key] as string | undefined)}
              </td>
            ))}
            <td className="py-2">
              <SpendBar row={total} />
            </td>
          </tr>
        </tbody>
      </table>

      <dl className="mt-4 ml-auto grid max-w-md grid-cols-[1fr_auto] gap-y-1 text-sm">
        <dt className="text-text-secondary">Price (estimate)</dt>
        <dd className="text-right tabular-nums">{formatAmount(total.price)}</dd>
        <dt className="text-text-secondary">Estimate margin</dt>
        <dd data-testid="estimate-margin" className="text-right tabular-nums">
          {formatAmount(total.estimate_margin)}
          {total.estimate_margin_percent ? ` (${formatAmount(total.estimate_margin_percent)} %)` : ''}
        </dd>
        <dt className="font-medium text-text-primary">Forecast margin</dt>
        <dd data-testid="forecast-margin" className="text-right font-medium tabular-nums">
          {formatAmount(total.forecast_margin)}
          {total.forecast_margin_percent ? ` (${formatAmount(total.forecast_margin_percent)} %)` : ''}
        </dd>
      </dl>
      <p className="mt-2 text-xs text-text-tertiary">
        The forecast margin is the estimate price minus the EFC. The price is the one the estimate was agreed at and does
        not move when costs do — that is what lets this number show an overrun.
      </p>
    </section>
  )
}

/**
 * Actual and open commitments against the estimate cost, as a bar.
 *
 * The widths are a picture of the API's numbers, not a calculation the
 * screen reports: the bar has no number on it, every figure beside it is the
 * API's, and the scale is capped at 100 % so an overrun reads as a full bar
 * with the over-budget marker rather than as a bar that leaves the cell.
 */
function SpendBar({ row }: { row: RollupTracking | undefined }) {
  if (!row) return null
  const estimate = Number(row.estimate_cost)
  const actual = Number(row.actual)
  const open = Number(row.committed_open)
  const scale = Math.max(estimate, actual + open, 0)
  if (!scale) return <div className="h-2 w-32 rounded bg-bg-secondary" />
  const pct = (value: number) => `${Math.min(100, (value / scale) * 100)}%`
  return (
    <div
      data-testid="spend-bar"
      role="img"
      aria-label={`Actual ${formatAmount(row.actual)}, open ${formatAmount(row.committed_open)}, estimate ${formatAmount(row.estimate_cost)}`}
      className="relative h-2 w-32 overflow-hidden rounded bg-bg-secondary"
    >
      <div className="absolute inset-y-0 left-0 bg-accent" style={{ width: pct(actual) }} />
      <div className="absolute inset-y-0 bg-accent/40" style={{ left: pct(actual), width: pct(open) }} />
      {estimate > 0 && <div className="absolute inset-y-0 w-px bg-text-primary" style={{ left: pct(estimate) }} />}
    </div>
  )
}
