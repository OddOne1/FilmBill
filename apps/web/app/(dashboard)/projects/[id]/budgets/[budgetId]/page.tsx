'use client'

import * as React from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, Copy, Download, Flag, Loader2, Lock, Unlock, Upload } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAmount, NO_VALUE } from '@/lib/money'
import { useCompanyStore } from '@/stores/company-store'
import type { Budget, BudgetCalculated, BudgetLine, BudgetSheet, BudgetTracking } from '@/types'
import {
  BudgetLockedNotice,
  BudgetsNoAccess,
  BudgetsReadOnlyNotice,
  ProjectsNoAccess,
  useProjectAccess,
} from '../../../projects-chrome'
import { BudgetSheetEditor } from './budget-sheet'
import { AdjustmentsPanel, GlobalsPanel } from './budget-panels'
import { BudgetImport } from './budget-import'
import { CostForecast, LineDrawer, PhaseSheet, PhaseSwitch, type Phase } from './budget-tracking'
import { CostsTab } from './budget-costs'
import { HowToRead, Term } from './budget-glossary-ui'
import { CloseBudgetDialog, ClosedNotice } from './budget-close'

/**
 * One budget: the Sheet, its Costs, and the Top sheet.
 *
 * Three requests, always fetched together after any write: `GET /budgets/{id}`
 * (the rows an edit PATCHes), `GET /budgets/{id}/calculated` (the engine's
 * numbers) and `GET /budgets/{id}/tracking` (ordered, spent, the forecast
 * final cost and under/over — B1). The third is optional for the screen: if it fails, the
 * estimate sheet still works and the cost columns say they are empty. A calculation the API refuses — a line pointing at a
 * global that no longer exists — is shown as the API's own sentence above
 * the sheet, and the sheet stays editable so it can be fixed.
 */

type Tab = 'sheet' | 'costs' | 'top'

export default function BudgetPage() {
  const params = useParams<{ id: string; budgetId: string }>()
  const router = useRouter()
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const access = useProjectAccess()
  const [sheet, setSheet] = React.useState<BudgetSheet | null>(null)
  const [calc, setCalc] = React.useState<BudgetCalculated | null>(null)
  const [calcError, setCalcError] = React.useState<string | null>(null)
  const [tracking, setTracking] = React.useState<BudgetTracking | null>(null)
  const [phase, setPhase] = React.useState<Phase>('estimate')
  const [drawerLine, setDrawerLine] = React.useState<BudgetLine | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [tab, setTab] = React.useState<Tab>('sheet')
  const [importing, setImporting] = React.useState(false)
  const [closing, setClosing] = React.useState(false)
  const [busy, setBusy] = React.useState(false)

  const budgetId = params.budgetId

  const reload = React.useCallback(async () => {
    try {
      setSheet(await api.get<BudgetSheet>(`/budgets/${budgetId}`))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the budget')
      return
    }
    try {
      setCalc(await api.get<BudgetCalculated>(`/budgets/${budgetId}/calculated`))
      setCalcError(null)
    } catch (err) {
      setCalc(null)
      setCalcError(err instanceof ApiError ? err.detail : 'The budget could not be calculated')
    }
    try {
      const answer = await api.get<BudgetTracking>(`/budgets/${budgetId}/tracking`)
      // Only a real tracking answer is kept — anything without a `budget`
      // rollup is treated as "not available" rather than drawn as zeros.
      setTracking(answer && (answer as BudgetTracking).budget ? answer : null)
    } catch {
      setTracking(null)
    }
  }, [budgetId])

  React.useEffect(() => {
    if (!access.loaded || !access.canViewBudgets) return
    void reload()
  }, [access.loaded, access.canViewBudgets, activeCompanyId, reload])

  if (access.refused) return <ProjectsNoAccess title="Budget" />
  if (access.budgetsRefused) {
    return (
      <div className="max-w-3xl p-8">
        <h1 className="text-lg font-semibold text-text-primary">Budget</h1>
        <BudgetsNoAccess />
      </div>
    )
  }

  const budget: Budget | undefined = sheet?.budget
  // The API's own `finished` flag per line, collected for the estimate
  // sheet's marker. A lookup, not a calculation.
  const finishedLines = new Set(
    Object.values(tracking?.lines ?? {})
      .filter((row) => row.finished)
      .map((row) => row.id),
  )
  const editable = access.canEditBudgets && !!budget && !budget.is_locked
  // Costs, still to spend and finishing lines: open on a locked budget
  // (D85), closed on a closed one (B1.1) — the API refuses them too.
  const closed = !!budget?.is_closed
  const costsEditable = access.canEditBudgets && !closed
  const currency = budget?.currency ?? ''

  const act = async (call: () => Promise<unknown>, failure: string) => {
    setBusy(true)
    setError(null)
    try {
      await call()
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : failure)
    } finally {
      setBusy(false)
    }
  }

  const exportCsv = async () => {
    setError(null)
    try {
      const blob = await api.download(`/budgets/${budgetId}/export.csv`)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${budget?.name ?? 'budget'}.csv`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The export failed')
    }
  }

  return (
    <div className="p-8">
      <Link
        href={`/projects/${params.id}`}
        className="inline-flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary"
      >
        <ArrowLeft className="h-3 w-3" /> Project
      </Link>

      {!sheet || !budget ? (
        error ? (
          <p role="alert" className="mt-4 text-sm text-danger">
            {error}
          </p>
        ) : (
          <div className="mt-8 flex items-center gap-2 text-sm text-text-secondary">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading the budget…
          </div>
        )
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-baseline gap-3">
              {access.canEditBudgets ? (
                <input
                  aria-label="Budget name"
                  defaultValue={budget.name}
                  key={budget.name}
                  onBlur={(event) => {
                    const name = event.target.value.trim()
                    if (!name || name === budget.name) return
                    void act(() => api.patch(`/budgets/${budget.id}`, { name }), 'Could not rename the budget')
                  }}
                  className="rounded border border-transparent bg-transparent px-1 text-lg font-semibold text-text-primary hover:border-border"
                />
              ) : (
                <h1 className="text-lg font-semibold text-text-primary">{budget.name}</h1>
              )}
              <span data-testid="budget-net" className="text-sm text-text-secondary">
                Net {calc ? `${formatAmount(calc.result.net)} ${currency}` : NO_VALUE}
              </span>
            </div>
            {access.canEditBudgets && (
              <div className="flex flex-wrap items-center gap-2">
                {budget.is_locked && !closed && (
                  <button
                    type="button"
                    data-testid="budget-close"
                    disabled={busy}
                    onClick={() => setClosing(true)}
                    className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm"
                  >
                    <Flag className="h-3.5 w-3.5" /> Close budget
                  </button>
                )}
                {closed ? null : budget.is_locked ? (
                  <button
                    type="button"
                    data-testid="budget-unlock"
                    disabled={busy}
                    onClick={() => void act(() => api.post(`/budgets/${budget.id}/unlock`, {}), 'Could not unlock')}
                    className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm"
                  >
                    <Unlock className="h-3.5 w-3.5" /> Unlock
                  </button>
                ) : (
                  <button
                    type="button"
                    data-testid="budget-lock"
                    disabled={busy}
                    onClick={() => void act(() => api.post(`/budgets/${budget.id}/lock`, {}), 'Could not lock')}
                    className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm"
                  >
                    <Lock className="h-3.5 w-3.5" /> Lock
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void (async () => {
                      setError(null)
                      try {
                        const copy = await api.post<Budget>(`/budgets/${budget.id}/duplicate`, {})
                        router.push(`/projects/${params.id}/budgets/${copy.id}`)
                      } catch (err) {
                        setError(err instanceof ApiError ? err.detail : 'Could not duplicate')
                      }
                    })()
                  }
                  className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm"
                >
                  <Copy className="h-3.5 w-3.5" /> Duplicate
                </button>
                {!budget.is_locked && (
                  <button
                    type="button"
                    onClick={() => setImporting(!importing)}
                    className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm"
                  >
                    <Upload className="h-3.5 w-3.5" /> Import CSV
                  </button>
                )}
              </div>
            )}
            <button
              type="button"
              onClick={() => void exportCsv()}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm"
            >
              <Download className="h-3.5 w-3.5" /> Export CSV
            </button>
          </div>

          {access.loaded && !access.canEditBudgets && <BudgetsReadOnlyNotice />}
          {closed ? (
            <ClosedNotice budget={budget} canEdit={access.canEditBudgets} onReopened={reload} />
          ) : (
            access.canEditBudgets && budget.is_locked && <BudgetLockedNotice lockedAt={budget.locked_at} />
          )}
          {closing && (
            <CloseBudgetDialog budget={budget} onClose={() => setClosing(false)} onClosed={reload} />
          )}

          {importing && editable && (
            <BudgetImport budgetId={budget.id} onApplied={reload} onClose={() => setImporting(false)} />
          )}

          {error && (
            <p role="alert" className="mt-3 text-sm text-danger">
              {error}
            </p>
          )}
          {calcError && (
            <p data-testid="budget-calc-error" role="alert" className="mt-3 rounded-md border border-danger/40 px-3 py-2 text-sm text-danger">
              The budget cannot be calculated: {calcError}
            </p>
          )}
          {(calc?.result.warnings ?? [])
            .filter((warning) => !(warning.where ?? '').startsWith('group '))
            .map((warning, index) => (
              <p key={index} data-testid="sheet-warning" className="mt-2 text-xs text-status-warning">
                ⚠ {warning.message}
              </p>
            ))}

          <nav aria-label="Budget" className="mt-5 flex gap-1 border-b border-border">
            {(
              [
                ['sheet', 'Sheet'],
                ['costs', 'Costs'],
                ['top', 'Top sheet'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
                className={
                  tab === key
                    ? 'border-b-2 border-accent px-3 py-2 text-sm font-medium text-text-primary'
                    : 'border-b-2 border-transparent px-3 py-2 text-sm text-text-secondary hover:text-text-primary'
                }
              >
                {label}
              </button>
            ))}
          </nav>

          {tab === 'sheet' && (
            <>
              <div className="mt-4 flex items-center gap-3">
                <PhaseSwitch phase={phase} onChange={setPhase} />
              </div>
              <HowToRead />
              {phase === 'estimate' ? (
                <div className="grid gap-4 xl:grid-cols-[1fr_20rem]">
                  <BudgetSheetEditor
                    sheet={sheet}
                    calc={calc}
                    editable={editable}
                    onChange={reload}
                    onError={setError}
                    finishedLines={finishedLines}
                  />
                  <div className="mt-4 space-y-4">
                    <GlobalsPanel sheet={sheet} calc={calc} editable={editable} onChange={reload} onError={setError} />
                    <AdjustmentsPanel sheet={sheet} calc={calc} editable={editable} onChange={reload} onError={setError} />
                  </div>
                </div>
              ) : (
                <PhaseSheet
                  sheet={sheet}
                  tracking={tracking}
                  phase={phase}
                  onOpenLine={setDrawerLine}
                  canEdit={costsEditable}
                  onChange={reload}
                />
              )}
            </>
          )}
          {tab === 'costs' && (
            <CostsTab
              sheet={sheet}
              canEdit={costsEditable}
              closed={closed}
              canPickParty={access.canViewParties}
              onChange={reload}
            />
          )}
          {tab === 'top' && (
            <>
              <TopSheet calc={calc} currency={currency} />
              <CostForecast sheet={sheet} tracking={tracking} currency={currency} />
            </>
          )}
          {drawerLine && (
            <LineDrawer
              key={drawerLine.id}
              sheet={sheet}
              line={drawerLine}
              tracking={tracking}
              canEdit={costsEditable}
              closed={closed}
              onClose={() => setDrawerLine(null)}
              onChange={reload}
            />
          )}
        </>
      )}
    </div>
  )
}

/** The section summary, entirely the API's: price, cost, margin and the
 *  share of the subtotal come from `top_sheet`; the totals from `result`. */
function TopSheet({ calc, currency }: { calc: BudgetCalculated | null; currency: string }) {
  if (!calc) return <p className="mt-4 text-sm text-text-secondary">No calculation to show.</p>
  const result = calc.result
  return (
    <div className="mt-4 max-w-4xl">
      <table data-testid="top-sheet" className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-tertiary">
            <th className="py-2 pr-3 font-medium">Code</th>
            <th className="py-2 pr-3 font-medium">Section</th>
            <th className="py-2 pr-3 text-right font-medium">
              <Term id="price" suffix={` (${currency})`} />
            </th>
            <th className="py-2 pr-3 text-right font-medium">
              <Term id="cost" />
            </th>
            <th className="py-2 pr-3 text-right font-medium">
              <Term id="margin" />
            </th>
            <th className="py-2 pr-3 text-right font-medium">
              <Term id="margin_percent" />
            </th>
            <th className="py-2 text-right font-medium">
              <Term id="share" />
            </th>
          </tr>
        </thead>
        <tbody>
          {calc.top_sheet.map((row) => (
            <tr key={row.section_id} data-testid="top-sheet-row" className="border-b border-border/60">
              <td className="py-1.5 pr-3 font-mono text-xs text-text-tertiary">{row.code ?? ''}</td>
              <td className="py-1.5 pr-3">{row.name}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{formatAmount(row.price)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{formatAmount(row.cost)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{formatAmount(row.margin)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">
                {row.margin_percent ? `${formatAmount(row.margin_percent)} %` : NO_VALUE}
              </td>
              <td data-testid="top-sheet-share" className="py-1.5 text-right tabular-nums">
                {row.share_of_subtotal ? `${formatAmount(row.share_of_subtotal)} %` : NO_VALUE}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="mt-4 ml-auto grid max-w-sm grid-cols-[1fr_auto] gap-y-1 text-sm">
        <dt className="text-text-secondary">Subtotal</dt>
        <dd className="text-right tabular-nums">{formatAmount(result.subtotal)}</dd>
        {result.adjustments.map((adjustment, index) => (
          <React.Fragment key={index}>
            <dt className="text-text-secondary">{adjustment.label}</dt>
            <dd className="text-right tabular-nums">{formatAmount(adjustment.amount)}</dd>
          </React.Fragment>
        ))}
        <dt className="font-medium text-text-primary">Net</dt>
        <dd data-testid="top-sheet-net" className="text-right font-medium tabular-nums">
          {formatAmount(result.net)}
        </dd>
        <dt className="text-text-secondary">Cost</dt>
        <dd className="text-right tabular-nums">{formatAmount(result.cost)}</dd>
        <dt className="text-text-secondary">Margin</dt>
        <dd className="text-right tabular-nums">
          {formatAmount(result.margin)}
          {result.margin_percent ? ` (${formatAmount(result.margin_percent)} %)` : ''}
        </dd>
        {Object.entries(result.net_by_tax_ref).map(([ref, net]) => (
          <React.Fragment key={ref}>
            <dt className="text-xs text-text-tertiary">Net {ref || '(no tax code)'}</dt>
            <dd className="text-right text-xs tabular-nums text-text-tertiary">{formatAmount(net)}</dd>
          </React.Fragment>
        ))}
      </dl>
    </div>
  )
}
