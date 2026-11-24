'use client'

import * as React from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAmount, NO_VALUE } from '@/lib/money'
import type {
  BulkFinish,
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
import {
  glossary,
  PHASE_CLICK_HINT,
  PHASE_COLUMN,
  PHASE_GLOSSARY,
  PHASE_SENTENCE,
  unsignedPercent,
  varianceWords,
} from '@/lib/budget-glossary'
import { FinishedMarker, InfoTip, Term } from './budget-glossary-ui'
import type { GlossaryId } from '@/lib/budget-glossary'

/**
 * Cost planning on the sheet (slice B1).
 *
 * **Nothing here computes money.** Ordered, spent, the forecast final cost,
 * under/over, the over-budget flag, the amounts not on a line, the forecast
 * profit — every number is read out of `GET /budgets/{id}/tracking`, keyed by
 * line and by section id, and printed. The browser does not subtract a
 * forecast from a plan to get a variance: the API's `tracking.py` does, with the same
 * rounding the engine uses, and a second subtraction here is how a row and
 * its total end up a cent apart.
 *
 * The Estimate phase is not drawn by this file at all — it is A2's sheet,
 * unchanged. The other four phases draw this read-only view over the same
 * sections and lines.
 *
 * Every word a person reads for a cost figure comes from
 * `lib/budget-glossary.ts` (B1.1); the API's field names are unchanged.
 */

export const PHASES = ['estimate', 'committed', 'actual', 'efc', 'variance'] as const
export type Phase = (typeof PHASES)[number]

export const PHASE_LABELS: Record<Phase, string> = {
  estimate: glossary(PHASE_GLOSSARY.estimate).label,
  committed: glossary(PHASE_GLOSSARY.committed).label,
  actual: glossary(PHASE_GLOSSARY.actual).label,
  efc: glossary(PHASE_GLOSSARY.efc).label,
  variance: glossary(PHASE_GLOSSARY.variance).label,
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

/** A phase's value as a person reads it: under/over in words, the rest as amounts. */
function phaseText(row: LineTracking | RollupTracking | undefined, phase: Phase): string {
  const value = phaseValue(row, phase)
  return phase === 'variance' ? varianceWords(value) : formatAmount(value)
}

export function PhaseSwitch({ phase, onChange }: { phase: Phase; onChange: (phase: Phase) => void }) {
  return (
    <div role="radiogroup" aria-label="Phase" className="inline-flex rounded-md border border-border p-0.5 text-xs">
      {PHASES.map((key) => (
        <span key={key} className="inline-flex items-center pr-1">
          <button
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
          <InfoTip id={PHASE_GLOSSARY[key]} />
        </span>
      ))}
    </div>
  )
}

function OverBudget() {
  return (
    <span
      data-testid="over-budget"
      title="Forecast to cost more than planned"
      className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-danger/10 px-1 text-[10px] font-medium text-danger"
    >
      <AlertTriangle className="h-3 w-3" /> over
    </span>
  )
}

/** What a line row says beside its name: "finished", or the amount still to
 *  spend when somebody set it by hand. */
function LineMarkers({ row }: { row: LineTracking | undefined }) {
  if (row?.finished) return <FinishedMarker />
  if (row?.remaining_rule !== 'etc') return null
  return (
    <span
      data-testid="line-has-etc"
      title="You set what is still to spend on this line yourself"
      className="ml-1.5 rounded bg-accent/10 px-1 text-[10px] font-medium text-accent"
    >
      still to spend {formatAmount(row.etc)}
    </span>
  )
}

/** The four non-estimate phases over the sheet's own sections and lines. */
export function PhaseSheet({
  sheet,
  tracking,
  phase,
  onOpenLine,
  canEdit = false,
  onChange,
}: {
  sheet: BudgetSheet
  tracking: BudgetTracking | null
  phase: Exclude<Phase, 'estimate'>
  onOpenLine: (line: BudgetLine) => void
  canEdit?: boolean
  onChange?: () => Promise<void> | void
}) {
  const [collapsed, setCollapsed] = React.useState<Set<string>>(new Set())
  const [bulk, setBulk] = React.useState<{ sectionId: string; answer: BulkFinish } | null>(null)
  const [bulkError, setBulkError] = React.useState<string | null>(null)
  const linesById = React.useMemo(() => new Map(sheet.lines.map((line) => [line.id, line])), [sheet.lines])

  const runBulk = async (section: BudgetSection, action: 'finish' | 'reopen') => {
    setBulkError(null)
    try {
      const answer = await api.post<BulkFinish>(`/budgets/${sheet.budget.id}/sections/${section.id}/${action}`)
      setBulk({ sectionId: section.id, answer })
      await onChange?.()
    } catch (err) {
      setBulk(null)
      setBulkError(err instanceof ApiError ? err.detail : 'That did not work')
    }
  }
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
              {canEdit && (
                <span className="ml-2 inline-flex gap-2 text-[11px] font-normal">
                  <button
                    type="button"
                    data-testid="section-finish-all"
                    onClick={() => void runBulk(section, 'finish')}
                    className="text-text-secondary underline-offset-2 hover:text-text-primary hover:underline"
                  >
                    Finish all lines in this section
                  </button>
                  <button
                    type="button"
                    data-testid="section-reopen-all"
                    onClick={() => void runBulk(section, 'reopen')}
                    className="text-text-secondary underline-offset-2 hover:text-text-primary hover:underline"
                  >
                    Reopen all
                  </button>
                </span>
              )}
            </div>
            {bulk?.sectionId === section.id && (
              <div data-testid="bulk-result" role="status" className="mt-1 text-xs font-normal text-text-secondary" style={{ paddingLeft: depth * 16 + 20 }}>
                {bulk.answer.message}
                {(bulk.answer.skipped ?? []).length > 0 && (
                  <ul className="mt-0.5 list-disc pl-4">
                    {(bulk.answer.skipped ?? []).map((row) => (
                      <li key={row.line_id} data-testid="bulk-skipped">
                        {linesById.get(row.line_id)?.description ?? row.description}: {formatAmount(row.committed_open)} ordered,
                        not yet invoiced
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </td>
          <td className="py-1.5 pr-2 text-right tabular-nums text-text-secondary">{formatAmount(rollup?.estimate_cost)}</td>
          <td data-testid="phase-section-value" className="py-1.5 pr-2 text-right font-medium tabular-nums">
            {phaseText(rollup, phase)}
          </td>
          <td className="py-1.5 text-right text-xs tabular-nums text-text-tertiary">
            {rollup && rollup.unallocated !== undefined && rollup.unallocated !== '0.00' ? (
              <span data-testid="phase-unallocated" title={glossary('not_on_a_line').explanation}>
                {formatAmount(rollup.unallocated)} not on a line
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
                  <LineMarkers row={row} />
                </td>
                <td className="py-1 pr-2 text-right tabular-nums text-text-secondary">{formatAmount(row?.estimate_cost)}</td>
                <td data-testid="phase-line-value" className="py-1 pr-2 text-right tabular-nums">
                  {phaseText(row, phase)}
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
        {PHASE_SENTENCE[phase]} {PHASE_CLICK_HINT}
      </p>
      {bulkError && (
        <p role="alert" className="mb-2 text-sm text-danger">
          {bulkError}
        </p>
      )}
      {!tracking && (
        <p className="mb-2 text-xs text-text-tertiary">The cost figures could not be loaded, so the columns are empty.</p>
      )}
      <table data-testid="phase-sheet" data-phase={phase} className="w-full min-w-[40rem] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-tertiary">
            <th className="py-2 pr-2 font-medium">Description</th>
            <th data-testid="phase-head-planned" className="py-2 pr-2 text-right font-medium">
              <Term id="planned" />
            </th>
            <th data-testid="phase-head-value" className="py-2 pr-2 text-right font-medium">
              <Term id={PHASE_COLUMN[phase]} />
            </th>
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
                {phaseText(tracking.budget, phase)}
              </td>
              <td />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

/** A line's own orders and invoices, what is still to spend, and its forecast. */
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
  const [confirmFinish, setConfirmFinish] = React.useState(false)
  // "Is anything still ordered?" — a string check on the API's amount, not
  // arithmetic: only an exact zero means nothing is open.
  const hasOpenOrder = !!row && !/^0+(\.0+)?$/.test(row.committed_open)

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
      setError(err instanceof ApiError ? err.detail : 'Still to spend could not be saved')
    }
  }

  const finish = async (confirmed: boolean) => {
    if (hasOpenOrder && !confirmed) {
      setConfirmFinish(true)
      return
    }
    setError(null)
    try {
      await api.post(`/budgets/${budgetId}/lines/${line.id}/finish`, { confirm_open_commitment: confirmed })
      setConfirmFinish(false)
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The line could not be marked finished')
    }
  }

  const reopen = async () => {
    setError(null)
    try {
      await api.post(`/budgets/${budgetId}/lines/${line.id}/reopen`)
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The line could not be reopened')
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
        <dt className="text-text-secondary"><Term id="planned" /></dt>
        <dd className="text-right tabular-nums">{formatAmount(row?.estimate_cost)}</dd>
        <dt className="text-text-secondary"><Term id="ordered_open" /></dt>
        <dd className="text-right tabular-nums">{formatAmount(row?.committed_open)}</dd>
        <dt className="text-text-secondary"><Term id="spent" /></dt>
        <dd className="text-right tabular-nums">{formatAmount(row?.actual)}</dd>
        <dt className="text-text-secondary"><Term id="still_to_spend" /></dt>
        <dd data-testid="drawer-remaining" className="text-right tabular-nums">{formatAmount(row?.remaining)}</dd>
        <dt className="font-medium text-text-primary"><Term id="forecast" /></dt>
        <dd data-testid="drawer-efc" className="text-right font-medium tabular-nums">
          {formatAmount(row?.efc)}
        </dd>
        <dt className="text-text-secondary"><Term id="variance" /></dt>
        <dd data-testid="drawer-variance" className={`text-right tabular-nums ${row?.over_budget ? 'text-danger' : ''}`}>
          {varianceWords(row?.variance)}
          {row?.variance_percent ? ` (${unsignedPercent(row.variance_percent)} %)` : ''}
        </dd>
      </dl>
      <p data-testid="drawer-rule" className="mt-2 text-xs text-text-tertiary">
        {row?.finished
          ? 'Finished: nothing more is expected on this line.'
          : row?.remaining_rule === 'etc'
            ? 'Still to spend is the amount you set for this line.'
            : 'Still to spend is worked out for you: what is left of the planned cost, or what is still on order if that is more.'}
      </p>

      {canEdit && row && (
        <div className="mt-3">
          {row.finished ? (
            <button
              type="button"
              data-testid="line-reopen"
              onClick={() => void reopen()}
              className="rounded-md border border-border px-3 py-1.5 text-xs"
            >
              Reopen
            </button>
          ) : (
            <button
              type="button"
              data-testid="line-finish"
              onClick={() => void finish(false)}
              className="rounded-md border border-border px-3 py-1.5 text-xs"
            >
              Mark finished
            </button>
          )}
          {!row.finished && (
            <p className="mt-1 text-[11px] text-text-tertiary">
              Marking it finished says nothing more will be spent, so its forecast final cost becomes what was spent.
            </p>
          )}
          {confirmFinish && (
            <div data-testid="finish-confirm" role="alertdialog" aria-label="Finish a line with an open order" className="mt-2 rounded-md border border-status-warning/60 p-2 text-xs">
              <p>
                {formatAmount(row.committed_open)} is still ordered and not yet invoiced on this line. Finish it anyway? Its
                forecast will no longer count that order.
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  data-testid="finish-confirm-yes"
                  onClick={() => void finish(true)}
                  className="rounded-md border border-border px-2 py-1"
                >
                  Finish anyway
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmFinish(false)}
                  className="rounded-md border border-border px-2 py-1"
                >
                  Keep it open
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {canEdit && (
        <div className="mt-4 rounded-md border border-border p-3">
          <label className="text-xs text-text-secondary" htmlFor="etc-input">
            Set still to spend yourself
          </label>
          <div className="mt-1 flex items-center gap-2">
            <input
              id="etc-input"
              aria-label={`Still to spend — ${line.description}`}
              value={etc}
              placeholder="worked out"
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
              title={row?.remaining_rule !== 'etc' ? 'Nothing is set by hand — still to spend is already worked out for you.' : undefined}
              onClick={() => void saveEtc(null)}
              className="rounded-md border border-border px-2 py-1 text-xs disabled:opacity-50"
            >
              Clear
            </button>
          </div>
          {row?.remaining_rule !== 'etc' && (
            <p className="mt-1 text-[11px] text-text-tertiary">Nothing is set by hand, so Clear has nothing to clear.</p>
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

/** The top sheet's cost half: five columns per section, the costs not on a
 *  line, total, the forecast profit beside the planned profit, and a plain
 *  bar. Every figure is the API's. */
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
  const columns: [keyof RollupTracking, GlossaryId][] = [
    ['estimate_cost', 'planned'],
    ['committed_open', 'ordered_open'],
    ['actual', 'spent'],
    ['efc', 'forecast'],
    ['variance', 'variance'],
  ]
  const cell = (key: keyof RollupTracking, row: RollupTracking | undefined) =>
    key === 'variance' ? varianceWords(row?.variance) : formatAmount(row?.[key] as string | undefined)
  return (
    <section data-testid="cost-forecast" className="mt-8 max-w-5xl">
      <h2 className="text-sm font-semibold text-text-primary">Cost forecast ({currency})</h2>
      <table className="mt-2 w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-tertiary">
            <th className="py-2 pr-3 font-medium">Code</th>
            <th className="py-2 pr-3 font-medium">Section</th>
            {columns.map(([key, id]) => (
              <th key={key} data-testid={`forecast-head-${key}`} className="py-2 pr-3 text-right font-medium">
                <Term id={id} />
              </th>
            ))}
            <th className="py-2 font-medium">Spent and owed against the plan</th>
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
                    {cell(key, row)}
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
            <td className="py-1.5 pr-3 italic">
              <Term id="not_on_a_line" suffix=" (booked on a section)" />
            </td>
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
                {cell(key, total)}
              </td>
            ))}
            <td className="py-2">
              <SpendBar row={total} />
            </td>
          </tr>
        </tbody>
      </table>

      <dl className="mt-4 ml-auto grid max-w-md grid-cols-[1fr_auto] gap-y-1 text-sm">
        <dt className="text-text-secondary"><Term id="price_agreed" /></dt>
        <dd className="text-right tabular-nums">{formatAmount(total.price)}</dd>
        <dt className="text-text-secondary"><Term id="planned_profit" /></dt>
        <dd data-testid="estimate-margin" className="text-right tabular-nums">
          {formatAmount(total.estimate_margin)}
          {total.estimate_margin_percent ? ` (${formatAmount(total.estimate_margin_percent)} %)` : ''}
        </dd>
        <dt className="font-medium text-text-primary"><Term id="forecast_profit" /></dt>
        <dd data-testid="forecast-margin" className="text-right font-medium tabular-nums">
          {formatAmount(total.forecast_margin)}
          {total.forecast_margin_percent ? ` (${formatAmount(total.forecast_margin_percent)} %)` : ''}
        </dd>
      </dl>
      <p className="mt-2 text-xs text-text-tertiary">
        Forecast profit is the agreed price minus the forecast final cost. The price does not move when costs do — that is
        what lets this number show an overrun, and what shows the money left over when a line comes in under plan.
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
      aria-label={`Spent ${formatAmount(row.actual)}, ordered not yet invoiced ${formatAmount(row.committed_open)}, planned ${formatAmount(row.estimate_cost)}`}
      className="relative h-2 w-32 overflow-hidden rounded bg-bg-secondary"
    >
      <div className="absolute inset-y-0 left-0 bg-accent" style={{ width: pct(actual) }} />
      <div className="absolute inset-y-0 bg-accent/40" style={{ left: pct(actual), width: pct(open) }} />
      {estimate > 0 && <div className="absolute inset-y-0 w-px bg-text-primary" style={{ left: pct(estimate) }} />}
    </div>
  )
}
