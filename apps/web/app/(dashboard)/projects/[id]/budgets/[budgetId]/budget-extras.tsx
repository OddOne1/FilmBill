'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import { formatAmount, NO_VALUE } from '@/lib/money'
import { glossary } from '@/lib/budget-glossary'
import type { BudgetCalculated, BudgetLine, BudgetSheet, ExtraFigures } from '@/types'
import { InfoTip } from './budget-glossary-ui'
import { toApiAmount } from './budget-tracking'

/**
 * Extras — charges agreed after the quote (B1.1 part 4).
 *
 * An extra is a line in the budget's reserved Extras section. The screen
 * lists them from `GET /budgets/{id}/calculated`'s `extras` (each one's own
 * price, cost and margin, from the engine) and the totals from its `totals`
 * (`baseline` and `with_extras`, two engine runs). Nothing is added up here.
 */

/** The sheet without the Extras section — what the Planned sheet edits. */
export function planOnly(sheet: BudgetSheet): BudgetSheet {
  return {
    ...sheet,
    sections: sheet.sections.filter((section) => !section.is_extras),
    lines: sheet.lines.filter((line) => !line.extra_status),
  }
}

/** The sheet as the forecast covers it: accepted extras only, and the
 *  Extras section only once something in it is accepted. A filter on the
 *  API's status, not a calculation. */
export function trackedOnly(sheet: BudgetSheet): BudgetSheet {
  const lines = sheet.lines.filter((line) => !line.extra_status || line.extra_status === 'accepted')
  const withLines = new Set(lines.map((line) => line.section_id))
  return {
    ...sheet,
    sections: sheet.sections.filter((section) => !section.is_extras || withLines.has(section.id)),
    lines,
  }
}

const STATUS_STYLE: Record<ExtraFigures['status'], string> = {
  proposed: 'bg-status-warning/10 text-status-warning',
  accepted: 'bg-status-success/10 text-status-success',
  declined: 'bg-bg-secondary text-text-tertiary line-through',
}

function StatusChip({ status }: { status: ExtraFigures['status'] }) {
  return (
    <span data-testid="extra-status" className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${STATUS_STYLE[status]}`}>
      {status}
    </span>
  )
}

interface Draft {
  description: string
  unit_price: string
  unit_cost: string
  note: string
  /** B1.2: does the budget's production fee fall on this extra? Ticked by
   *  default (Mathias, 2026-11-25); the API prices it either way. */
  apply_production_fee: boolean
}

const EMPTY: Draft = { description: '', unit_price: '', unit_cost: '', note: '', apply_production_fee: true }

function ExtraForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: Draft
  submitLabel: string
  onSubmit: (draft: Draft) => Promise<void>
  onCancel: () => void
}) {
  const [draft, setDraft] = React.useState<Draft>(initial)
  const missing: string[] = []
  if (!draft.description.trim()) missing.push('a description')
  if (!draft.unit_price.trim()) missing.push('a price')
  const field = (key: 'description' | 'unit_price' | 'unit_cost' | 'note', label: string, wide = false) => (
    <label className={`flex flex-col text-xs text-text-secondary ${wide ? 'flex-1' : 'w-32'}`}>
      {label}
      <input
        aria-label={label}
        value={draft[key]}
        onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
        className="mt-0.5 rounded border border-border bg-transparent px-2 py-1 text-sm text-text-primary"
      />
    </label>
  )
  return (
    <form
      data-testid="extra-form"
      onSubmit={(event) => {
        event.preventDefault()
        if (missing.length === 0) void onSubmit(draft)
      }}
      className="mt-3 space-y-2 rounded-md border border-border p-3"
    >
      <div className="flex flex-wrap gap-2">
        {field('description', 'What it is', true)}
        {field('unit_price', 'Price (net)')}
        {field('unit_cost', 'Cost (net)')}
      </div>
      {field('note', 'What the customer was told', true)}
      <label className="flex items-center gap-1.5 text-xs text-text-secondary">
        <input
          type="checkbox"
          aria-label="Add production fee"
          checked={draft.apply_production_fee}
          onChange={(event) => setDraft({ ...draft, apply_production_fee: event.target.checked })}
        />
        Add production fee — untick to bill this extra at its own price
      </label>
      <div className="flex items-center gap-2">
        <button
          type="submit"
          data-testid="extra-save"
          disabled={missing.length > 0}
          className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
        >
          {submitLabel}
        </button>
        <button type="button" onClick={onCancel} className="rounded-md border border-border px-3 py-1.5 text-xs">
          Cancel
        </button>
        {missing.length > 0 && (
          <span data-testid="extra-missing" className="text-xs text-text-tertiary">
            Needs {missing.join(' and ')}.
          </span>
        )}
      </div>
    </form>
  )
}

export function ExtrasTab({
  sheet,
  calc,
  canEdit,
  closed,
  onChange,
}: {
  sheet: BudgetSheet
  calc: BudgetCalculated | null
  canEdit: boolean
  closed: boolean
  onChange: () => Promise<void> | void
}) {
  const budgetId = sheet.budget.id
  const [adding, setAdding] = React.useState(false)
  const [editing, setEditing] = React.useState<string | null>(null)
  const [declining, setDeclining] = React.useState<string | null>(null)
  const [declineNote, setDeclineNote] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const lines = new Map(sheet.lines.map((line) => [line.id, line]))
  const extras = calc?.extras ?? []
  const writable = canEdit && !closed

  const run = async (call: () => Promise<unknown>, failure: string) => {
    setError(null)
    try {
      await call()
      await onChange()
      return true
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : failure)
      return false
    }
  }

  const body = (draft: Draft) => ({
    description: draft.description.trim(),
    unit_price: toApiAmount(draft.unit_price),
    unit_cost: draft.unit_cost.trim() ? toApiAmount(draft.unit_cost) : '0.00',
    note: draft.note.trim() || null,
    apply_production_fee: draft.apply_production_fee,
  })

  const draftOf = (line: BudgetLine | undefined): Draft =>
    line
      ? {
          description: line.description,
          unit_price: line.unit_price,
          unit_cost: line.unit_cost,
          note: line.extra_note ?? '',
          apply_production_fee: line.apply_production_fee ?? true,
        }
      : EMPTY

  return (
    <section data-testid="extras-tab" className="mt-4 max-w-4xl">
      <p className="text-xs text-text-secondary">
        Extra charges agreed with the client after the quote. A proposed or declined extra counts in no total; an accepted one
        is added to the budget, and costs can be booked on it. Extras stay editable on a locked budget.
      </p>
      {!canEdit && (
        <p data-testid="extras-read-only" className="mt-2 text-xs text-text-secondary">
          You can read the extras but not propose, accept or decline them — that needs the budget edit permission.
        </p>
      )}
      {canEdit && closed && (
        <p data-testid="extras-closed" className="mt-2 text-xs text-text-secondary">
          This budget is closed, so no extra can be proposed, changed, accepted or declined. Reopen it first.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}

      {extras.length === 0 ? (
        <p data-testid="extras-empty" className="mt-4 text-sm text-text-tertiary">
          No extras yet.
        </p>
      ) : (
        <table data-testid="extras-table" className="mt-3 w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-text-tertiary">
              <th className="py-2 pr-3 font-medium">Extra</th>
              <th className="py-2 pr-3 font-medium">Status</th>
              <th className="py-2 pr-3 text-right font-medium">Price</th>
              <th className="py-2 pr-3 text-right font-medium">Cost</th>
              <th className="py-2 pr-3 text-right font-medium">Margin</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {extras.map((row) => (
              <React.Fragment key={row.line_id}>
                <tr data-testid="extra-row" data-line={row.line_id} className="border-b border-border/60 align-top">
                  <td className="py-1.5 pr-3">
                    <div className="text-text-primary">{row.description}</div>
                    {row.note && <div className="text-xs text-text-tertiary">{row.note}</div>}
                    {lines.get(row.line_id)?.apply_production_fee === false && (
                      <div data-testid="extra-no-fee" className="text-[11px] text-text-tertiary">
                        no production fee
                      </div>
                    )}
                    {row.decided_on && (
                      <div className="text-[11px] text-text-tertiary">
                        {row.status} on {row.decided_on}
                      </div>
                    )}
                  </td>
                  <td className="py-1.5 pr-3">
                    <StatusChip status={row.status} />
                  </td>
                  <td data-testid="extra-price" className="py-1.5 pr-3 text-right tabular-nums">
                    {formatAmount(row.price)}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{formatAmount(row.cost)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{formatAmount(row.margin)}</td>
                  <td className="py-1.5 text-right text-xs whitespace-nowrap">
                    {writable && row.status !== 'declined' && (
                      <span className="flex justify-end gap-2">
                        {row.status === 'proposed' && (
                          <button
                            type="button"
                            data-testid="extra-accept"
                            onClick={() =>
                              void run(() => api.post(`/budgets/${budgetId}/extras/${row.line_id}/accept`, {}), 'Could not accept it')
                            }
                            className="hover:underline"
                          >
                            Accept
                          </button>
                        )}
                        <button type="button" data-testid="extra-edit" onClick={() => setEditing(row.line_id)} className="hover:underline">
                          Edit
                        </button>
                        <button
                          type="button"
                          data-testid="extra-decline"
                          onClick={() => {
                            setDeclining(row.line_id)
                            setDeclineNote('')
                          }}
                          className="text-danger hover:underline"
                        >
                          Decline
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
                {declining === row.line_id && (
                  <tr>
                    <td colSpan={6} className="pb-2">
                      <div data-testid="decline-form" className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2 text-xs">
                        <input
                          aria-label={`Why “${row.description}” is declined`}
                          data-testid="decline-note"
                          value={declineNote}
                          placeholder="What the customer said, or why it is withdrawn"
                          onChange={(event) => setDeclineNote(event.target.value)}
                          className="w-80 rounded border border-border bg-transparent px-2 py-1"
                        />
                        <button
                          type="button"
                          data-testid="decline-submit"
                          disabled={!declineNote.trim()}
                          onClick={() =>
                            void run(
                              () => api.post(`/budgets/${budgetId}/extras/${row.line_id}/decline`, { note: declineNote.trim() }),
                              'Could not decline it',
                            ).then((ok) => ok && setDeclining(null))
                          }
                          className="rounded-md border border-border px-2 py-1 disabled:opacity-50"
                        >
                          Decline
                        </button>
                        <button type="button" onClick={() => setDeclining(null)} className="rounded-md border border-border px-2 py-1">
                          Cancel
                        </button>
                        {!declineNote.trim() && <span className="text-text-tertiary">Declining needs a note.</span>}
                      </div>
                    </td>
                  </tr>
                )}
                {editing === row.line_id && (
                  <tr>
                    <td colSpan={6}>
                      <ExtraForm
                        initial={draftOf(lines.get(row.line_id))}
                        submitLabel={row.status === 'accepted' ? 'Save (recorded as changed after acceptance)' : 'Save'}
                        onCancel={() => setEditing(null)}
                        onSubmit={async (draft) => {
                          const ok = await run(
                            () => api.patch(`/budgets/${budgetId}/extras/${row.line_id}`, body(draft)),
                            'Could not save the extra',
                          )
                          if (ok) setEditing(null)
                        }}
                      />
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      )}

      {writable &&
        (adding ? (
          <ExtraForm
            initial={EMPTY}
            submitLabel="Propose"
            onCancel={() => setAdding(false)}
            onSubmit={async (draft) => {
              const ok = await run(() => api.post(`/budgets/${budgetId}/extras`, body(draft)), 'Could not propose the extra')
              if (ok) setAdding(false)
            }}
          />
        ) : (
          <button
            type="button"
            data-testid="extra-add"
            onClick={() => setAdding(true)}
            className="mt-3 rounded-md border border-border px-3 py-1.5 text-xs"
          >
            + Propose an extra
          </button>
        ))}
    </section>
  )
}

/** The top sheet's two totals and the accepted extras between them. Every
 *  figure is the API's: the Extras row is the engine's subtotal for the
 *  Extras section, the totals are two engine runs. */
export function ExtrasTotals({ sheet, calc }: { sheet: BudgetSheet; calc: BudgetCalculated }) {
  const extrasSection = sheet.sections.find((section) => section.is_extras)
  const accepted = extrasSection ? calc.top_sheet.find((row) => row.section_id === extrasSection.id) : undefined
  return (
    <dl data-testid="extras-totals" className="mt-4 ml-auto grid max-w-sm grid-cols-[1fr_auto] gap-y-1 border-t border-border pt-2 text-sm">
      <dt className="text-text-secondary">
        {glossary('total_quote').label}
        <InfoTip id="total_quote" />
      </dt>
      <dd data-testid="total-baseline" className="text-right tabular-nums">
        {formatAmount(calc.totals.baseline.net)}
      </dd>
      <dt className="text-text-secondary">
        {glossary('extras_accepted').label}
        <InfoTip id="extras_accepted" />
      </dt>
      <dd data-testid="total-extras-accepted" className="text-right tabular-nums">
        {accepted ? formatAmount(accepted.price) : NO_VALUE}
      </dd>
      <dt className="font-medium text-text-primary">
        {glossary('total_with_extras').label}
        <InfoTip id="total_with_extras" />
      </dt>
      <dd data-testid="total-with-extras" className="text-right font-medium tabular-nums">
        {formatAmount(calc.totals.with_extras.net)}
      </dd>
    </dl>
  )
}
