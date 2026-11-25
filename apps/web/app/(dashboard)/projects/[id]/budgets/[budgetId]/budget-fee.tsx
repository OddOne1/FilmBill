'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import { formatAmount } from '@/lib/money'
import { ITEM_KIND_LABELS, ITEM_KINDS, kindShort } from '@/lib/item-kinds'
import type { BudgetCalculated, BudgetSheet, ItemKind, ProductionFee, ProductionFeeWrite } from '@/types'
import { toApiDecimal, trimZeros } from './budget-sheet'

/**
 * The budget's production fee, as one control at the top of the sheet (B1.2).
 *
 * It is a surcharge adjustment with a role on the server, so the engine
 * prices it like any other and this screen computes nothing: the amount
 * beside it is the engine's. The fee is this budget's own — the company
 * preset is where a NEW budget starts and what "Reset to preset" copies, and
 * `differs_from_preset` is the server's comparison, not this file's.
 */

const describe = (percent: string | null | undefined, kinds: string[]) =>
  percent == null ? 'no fee' : `${trimZeros(percent)} % · ${kinds.length ? kinds.map(kindShort).join(', ') : 'all kinds'}`

export function ProductionFeeControl({
  sheet,
  calc,
  editable,
  onChange,
  onError,
}: {
  sheet: BudgetSheet
  calc: BudgetCalculated | null
  editable: boolean
  onChange: () => Promise<void> | void
  onError: (message: string | null) => void
}) {
  const fee: ProductionFee = sheet.production_fee
  const budgetId = sheet.budget.id
  const stored = {
    enabled: fee.enabled,
    percent: fee.percent ? trimZeros(fee.percent) : '',
    kinds: (fee.kinds ?? []) as ItemKind[],
  }
  const [enabled, setEnabled] = React.useState(stored.enabled)
  const [percent, setPercent] = React.useState(stored.percent)
  const [kinds, setKinds] = React.useState<ItemKind[]>(stored.kinds)
  const [onlyKinds, setOnlyKinds] = React.useState(stored.kinds.length > 0)
  const [busy, setBusy] = React.useState(false)
  const key = `${fee.adjustment_id}-${fee.enabled}-${fee.percent}-${(fee.kinds ?? []).join(',')}`

  React.useEffect(() => {
    setEnabled(stored.enabled)
    setPercent(stored.percent)
    setKinds(stored.kinds)
    setOnlyKinds(stored.kinds.length > 0)
    // `key` is the stored fee; resetting the draft whenever it changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const sentKinds = onlyKinds ? kinds : []
  const changed =
    enabled !== stored.enabled ||
    (enabled &&
      (percent.trim() !== stored.percent ||
        onlyKinds !== stored.kinds.length > 0 ||
        sentKinds.slice().sort().join() !== stored.kinds.slice().sort().join()))
  const why = !changed
    ? 'Nothing changed.'
    : enabled && !percent.trim()
      ? 'A percent.'
      : enabled && onlyKinds && kinds.length === 0
        ? 'At least one kind, or “everything”.'
        : null

  const applied = calc?.result.adjustments.find((row) => row.label === 'Production fee')?.amount
  const preset = fee.preset_percent == null ? 'no preset' : `preset: ${describe(fee.preset_percent, fee.preset_kinds ?? [])}`

  const send = async (call: () => Promise<unknown>, failure: string) => {
    setBusy(true)
    onError(null)
    try {
      await call()
      await onChange()
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : failure)
    } finally {
      setBusy(false)
    }
  }

  const save = () => {
    const body: ProductionFeeWrite = enabled
      ? { enabled: true, percent: toApiDecimal(percent), kinds: sentKinds }
      : { enabled: false, kinds: [] }
    void send(() => api.put(`/budgets/${budgetId}/production-fee`, body), 'The production fee could not be saved')
  }

  return (
    <section data-testid="production-fee" className="mt-4 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <h2 className="font-medium text-text-primary">Production fee</h2>
        {editable ? (
          <>
            <label className="flex items-center gap-1 text-xs text-text-secondary">
              <input type="checkbox" aria-label="Production fee on" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              on
            </label>
            {enabled && (
              <>
                <input
                  aria-label="Production fee percent"
                  value={percent}
                  onChange={(e) => setPercent(e.target.value)}
                  className="w-14 rounded border border-border bg-bg-primary px-1 text-right tabular-nums"
                />
                <span className="text-xs text-text-secondary">%</span>
                <select
                  aria-label="Production fee on kinds"
                  value={onlyKinds ? 'kinds' : 'all'}
                  onChange={(e) => setOnlyKinds(e.target.value === 'kinds')}
                  className="rounded border border-border bg-bg-primary text-xs"
                >
                  <option value="all">on everything</option>
                  <option value="kinds">on certain kinds</option>
                </select>
              </>
            )}
            <button
              type="button"
              onClick={save}
              disabled={busy || why !== null}
              className="rounded bg-accent px-2 py-0.5 text-xs text-white disabled:opacity-50"
            >
              Save
            </button>
            {why && changed && <span className="text-[10px] text-text-tertiary">{why}</span>}
          </>
        ) : (
          <span data-testid="production-fee-value" className="text-text-primary">
            {fee.enabled ? describe(fee.percent, fee.kinds ?? []) : 'none'}
          </span>
        )}
        {applied && (
          <span data-testid="production-fee-amount" className="ml-auto text-xs tabular-nums text-text-secondary">
            {formatAmount(applied)}
          </span>
        )}
      </div>
      {editable && enabled && onlyKinds && (
        <div data-testid="production-fee-kinds" className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-text-secondary">
          {ITEM_KINDS.map((kind) => (
            <label key={kind} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={kinds.includes(kind)}
                onChange={() => setKinds((prev) => (prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind]))}
              />
              {ITEM_KIND_LABELS[kind]}
            </label>
          ))}
        </div>
      )}
      <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-text-tertiary">
        <span data-testid="production-fee-preset">{preset}</span>
        {fee.differs_from_preset && (
          <span data-testid="production-fee-differs" className="text-status-warning">
            This budget differs from the preset.
          </span>
        )}
        {editable && fee.differs_from_preset && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void send(() => api.post(`/budgets/${budgetId}/production-fee/reset`), 'The production fee could not be reset')
            }
            className="text-accent underline disabled:opacity-50"
          >
            Reset to preset
          </button>
        )}
      </p>
      <p className="mt-1 text-[11px] text-text-tertiary">
        Charged on the price only; the cost is not touched. Changing it here changes this budget only.
      </p>
    </section>
  )
}
