'use client'

import * as React from 'react'
import { Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAmount } from '@/lib/money'
import type {
  BudgetAdjustment,
  BudgetAdjustmentCreate,
  BudgetCalculated,
  BudgetSheet,
} from '@/types'
import { toApiDecimal, trimZeros } from './budget-sheet'

/**
 * The two side panels of the sheet: Globals and Adjustments.
 *
 * Globals are a closed vocabulary (D74): a number somebody typed, or a count
 * of the project's days (`days:<type>`) that this screen shows read-only.
 * A line points at one by typing `=key` into its quantity or period.
 *
 * Adjustments change the **price only** — the engine's contract. A
 * contingency that must raise the cost side is a line of kind `fee` (D78),
 * and the panel says so rather than letting somebody find out from the
 * margin.
 */

interface PanelProps {
  sheet: BudgetSheet
  calc: BudgetCalculated | null
  editable: boolean
  onChange: () => Promise<void> | void
  onError: (message: string | null) => void
}

async function run(
  call: () => Promise<unknown>,
  failure: string,
  onChange: PanelProps['onChange'],
  onError: PanelProps['onError'],
) {
  onError(null)
  try {
    await call()
    await onChange()
    return true
  } catch (err) {
    onError(err instanceof ApiError ? err.detail : failure)
    return false
  }
}

export function GlobalsPanel({ sheet, editable, onChange, onError }: PanelProps) {
  const [key, setKey] = React.useState('')
  const [label, setLabel] = React.useState('')
  const [value, setValue] = React.useState('')
  const budgetId = sheet.budget.id
  const missing = !key.trim() ? 'A key, e.g. crew_size.' : !label.trim() ? 'A label.' : !value.trim() ? 'A value.' : null

  return (
    <section data-testid="globals-panel" className="rounded-md border border-border p-3">
      <h2 className="text-sm font-medium text-text-primary">Globals</h2>
      <p className="mt-1 text-xs text-text-tertiary">
        Type <code>=key</code> into a line&rsquo;s quantity or period to use one.
      </p>
      <ul className="mt-2 space-y-1.5">
        {sheet.globals.map((row) => (
          <li key={row.id} data-testid="global-row" className="flex items-center gap-2 text-sm">
            <code className="w-28 shrink-0 truncate text-xs text-text-secondary">{row.key}</code>
            <span className="flex-1 truncate text-text-primary">{row.label}</span>
            {row.is_computed || !editable ? (
              <span
                className="w-16 text-right tabular-nums"
                title={row.is_computed ? `Counted from the project's ${row.source.slice(5)} days — change it on the Days tab.` : undefined}
              >
                {trimZeros(row.value)}
              </span>
            ) : (
              <input
                aria-label={`Value of ${row.key}`}
                defaultValue={trimZeros(row.value)}
                key={`${row.id}-${row.value}`}
                onBlur={(event) => {
                  if (event.target.value === trimZeros(row.value)) return
                  void run(
                    () => api.patch(`/budgets/${budgetId}/globals/${row.id}`, { value: toApiDecimal(event.target.value) }),
                    'The global could not be saved',
                    onChange,
                    onError,
                  )
                }}
                className="w-16 rounded border border-border bg-bg-primary px-1 text-right tabular-nums"
              />
            )}
            {row.is_computed && <span className="text-[10px] text-text-tertiary">from Days</span>}
            {editable &&
              (row.referenced_by > 0 ? (
                // Not a dead button: the reason, where the button would be (17c).
                <span data-testid="global-in-use" className="text-[10px] text-text-tertiary">
                  used by {row.referenced_by} line{row.referenced_by === 1 ? '' : 's'}
                </span>
              ) : (
                <button
                  type="button"
                  aria-label={`Remove ${row.key}`}
                  onClick={() =>
                    void run(
                      () => api.post(`/budgets/${budgetId}/globals/${row.id}/remove`),
                      'The global could not be removed',
                      onChange,
                      onError,
                    )
                  }
                  className="text-text-tertiary hover:text-danger"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              ))}
          </li>
        ))}
        {sheet.globals.length === 0 && <li className="text-xs text-text-tertiary">None yet.</li>}
      </ul>
      {editable && (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (missing) return
            void run(
              () =>
                api.post(`/budgets/${budgetId}/globals`, {
                  key: key.trim(),
                  label: label.trim(),
                  value: toApiDecimal(value),
                  source: 'manual',
                }),
              'The global could not be added',
              onChange,
              onError,
            ).then((ok) => {
              if (ok) {
                setKey('')
                setLabel('')
                setValue('')
              }
            })
          }}
          className="mt-3 flex flex-wrap items-center gap-1.5"
        >
          <input aria-label="New global key" placeholder="key" value={key} onChange={(e) => setKey(e.target.value)} className="w-24 rounded border border-border bg-bg-primary px-1 text-xs" />
          <input aria-label="New global label" placeholder="Label" value={label} onChange={(e) => setLabel(e.target.value)} className="w-28 rounded border border-border bg-bg-primary px-1 text-xs" />
          <input aria-label="New global value" placeholder="0" value={value} onChange={(e) => setValue(e.target.value)} className="w-14 rounded border border-border bg-bg-primary px-1 text-right text-xs" />
          <button type="submit" disabled={missing !== null} className="rounded bg-accent px-2 py-0.5 text-xs text-white disabled:opacity-50">
            Add
          </button>
          {missing && <span className="text-[10px] text-text-tertiary">{missing}</span>}
        </form>
      )}
    </section>
  )
}

export function AdjustmentsPanel({ sheet, calc, editable, onChange, onError }: PanelProps) {
  const budgetId = sheet.budget.id
  const [label, setLabel] = React.useState('')
  const [type, setType] = React.useState<BudgetAdjustmentCreate['type']>('discount')
  const [mode, setMode] = React.useState<BudgetAdjustmentCreate['mode']>('percent')
  const [value, setValue] = React.useState('')
  const [sectionId, setSectionId] = React.useState('')
  const sectionName = (id: string | null | undefined) =>
    id ? (sheet.sections.find((section) => section.id === id)?.name ?? '—') : 'Whole budget'
  const missing = !label.trim() ? 'A label.' : !value.trim() ? 'A value.' : null

  /** The engine's applied amount for a whole-budget adjustment, matched by
   *  label — read, never derived. */
  const appliedAmount = (row: BudgetAdjustment) =>
    row.section_id ? undefined : calc?.result.adjustments.find((applied) => applied.label === row.label)?.amount

  return (
    <section data-testid="adjustments-panel" className="rounded-md border border-border p-3">
      <h2 className="text-sm font-medium text-text-primary">Adjustments</h2>
      <p className="mt-1 text-xs text-text-tertiary">
        Discounts and surcharges move the price, never the cost. A contingency
        or fee that is a real cost belongs in the sheet as a line of kind
        &ldquo;fee&rdquo;.
      </p>
      <ul className="mt-2 space-y-1.5">
        {sheet.adjustments.map((row) => (
          <li key={row.id} data-testid="adjustment-row" className="flex items-center gap-2 text-sm">
            <span className="flex-1 truncate text-text-primary">{row.label}</span>
            <span className="text-xs text-text-tertiary">{sectionName(row.section_id)}</span>
            <span className="text-xs text-text-secondary">{row.type === 'discount' ? '−' : '+'}</span>
            {editable ? (
              <input
                aria-label={`Value of ${row.label}`}
                defaultValue={trimZeros(row.value)}
                key={`${row.id}-${row.value}`}
                onBlur={(event) => {
                  if (event.target.value === trimZeros(row.value)) return
                  void run(
                    () => api.patch(`/budgets/${budgetId}/adjustments/${row.id}`, { value: toApiDecimal(event.target.value) }),
                    'The adjustment could not be saved',
                    onChange,
                    onError,
                  )
                }}
                className="w-14 rounded border border-border bg-bg-primary px-1 text-right tabular-nums"
              />
            ) : (
              <span className="tabular-nums">{trimZeros(row.value)}</span>
            )}
            <span className="text-xs text-text-secondary">{row.mode === 'percent' ? '%' : sheet.budget.currency}</span>
            {appliedAmount(row) && (
              <span className="w-20 text-right text-xs tabular-nums text-text-secondary">{formatAmount(appliedAmount(row))}</span>
            )}
            {editable && (
              <button
                type="button"
                aria-label={`Remove ${row.label}`}
                onClick={() =>
                  void run(
                    () => api.post(`/budgets/${budgetId}/adjustments/${row.id}/remove`),
                    'The adjustment could not be removed',
                    onChange,
                    onError,
                  )
                }
                className="text-text-tertiary hover:text-danger"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            )}
          </li>
        ))}
        {sheet.adjustments.length === 0 && <li className="text-xs text-text-tertiary">None.</li>}
      </ul>
      {editable && (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (missing) return
            const body: BudgetAdjustmentCreate = {
              label: label.trim(),
              type,
              mode,
              value: toApiDecimal(value),
              base: 'subtotal',
              section_id: sectionId || null,
            }
            void run(() => api.post(`/budgets/${budgetId}/adjustments`, body), 'The adjustment could not be added', onChange, onError).then(
              (ok) => {
                if (ok) {
                  setLabel('')
                  setValue('')
                }
              },
            )
          }}
          className="mt-3 flex flex-wrap items-center gap-1.5"
        >
          <input aria-label="New adjustment label" placeholder="Label" value={label} onChange={(e) => setLabel(e.target.value)} className="w-28 rounded border border-border bg-bg-primary px-1 text-xs" />
          <select aria-label="New adjustment type" value={type} onChange={(e) => setType(e.target.value as BudgetAdjustmentCreate['type'])} className="rounded border border-border bg-bg-primary text-xs">
            <option value="discount">Discount</option>
            <option value="surcharge">Surcharge</option>
          </select>
          <input aria-label="New adjustment value" placeholder="0" value={value} onChange={(e) => setValue(e.target.value)} className="w-12 rounded border border-border bg-bg-primary px-1 text-right text-xs" />
          <select aria-label="New adjustment mode" value={mode} onChange={(e) => setMode(e.target.value as BudgetAdjustmentCreate['mode'])} className="rounded border border-border bg-bg-primary text-xs">
            <option value="percent">%</option>
            <option value="amount">{sheet.budget.currency}</option>
          </select>
          <select aria-label="New adjustment applies to" value={sectionId} onChange={(e) => setSectionId(e.target.value)} className="rounded border border-border bg-bg-primary text-xs">
            <option value="">Whole budget</option>
            {sheet.sections.map((section) => (
              <option key={section.id} value={section.id}>
                {section.name}
              </option>
            ))}
          </select>
          <button type="submit" disabled={missing !== null} className="rounded bg-accent px-2 py-0.5 text-xs text-white disabled:opacity-50">
            Add
          </button>
          {missing && <span className="text-[10px] text-text-tertiary">{missing}</span>}
        </form>
      )}
    </section>
  )
}
