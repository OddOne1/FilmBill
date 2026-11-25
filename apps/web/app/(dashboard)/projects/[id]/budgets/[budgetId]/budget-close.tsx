'use client'

import * as React from 'react'
import { CheckCircle2, Loader2, LockKeyhole, X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAmount } from '@/lib/money'
import { varianceWords } from '@/lib/budget-glossary'
import type { Budget, CloseCheck, CloseCheckEntry, CloseCheckLine } from '@/types'

/**
 * Closing a budget at the production's end (B1.1 part 3).
 *
 * The dialog shows `GET /budgets/{id}/close-check` in plain words and
 * computes nothing: every amount, the state and the digest are the API's.
 * Closing over discrepancies sends back the digest of the list the person
 * read; if something was booked meanwhile, the API refuses and the dialog
 * shows its sentence and loads the list again.
 */

const STATE_SENTENCE: Record<CloseCheck['state'], string> = {
  clean: 'Everything adds up: no line is over its plan and nothing is left unexplained.',
  within_estimate:
    'The total stays within the estimate — some lines are over, others under, so money moved between lines. Some things below still need a look.',
  over_estimate: 'The total is over the estimate: the forecast final cost is above what was planned.',
}

function LineList({ title, rows, what }: { title: string; rows: CloseCheckLine[]; what: (row: CloseCheckLine) => string }) {
  if (rows.length === 0) return null
  return (
    <div className="mt-3">
      <h3 className="text-xs font-medium text-text-primary">{title}</h3>
      <ul className="mt-1 list-disc pl-4 text-xs text-text-secondary">
        {rows.map((row) => (
          <li key={row.line_id} data-testid="close-item" data-line={row.line_id}>
            <span className="text-text-primary">{row.description}</span> — {what(row)}
          </li>
        ))}
      </ul>
    </div>
  )
}

function EntryList({ title, rows, total }: { title: string; rows: CloseCheckEntry[]; total: string }) {
  if (rows.length === 0) return null
  return (
    <div className="mt-3">
      <h3 className="text-xs font-medium text-text-primary">
        {title}: {formatAmount(total)}
      </h3>
      <ul className="mt-1 list-disc pl-4 text-xs text-text-secondary">
        {rows.map((row) => (
          <li key={row.id} data-testid="close-item" data-entry={row.id}>
            {row.kind === 'commitment' ? 'Order' : 'Invoice'} {row.reference ?? row.description ?? ''}:{' '}
            {formatAmount(row.amount)}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function CloseBudgetDialog({
  budget,
  onClose,
  onClosed,
  onAddExtra,
}: {
  budget: Budget
  onClose: () => void
  onClosed: () => Promise<void> | void
  /** Switch to the Extras tab, offered when something is over (Part 4). */
  onAddExtra?: () => void
}) {
  const [check, setCheck] = React.useState<CloseCheck | null>(null)
  const [approve, setApprove] = React.useState(false)
  const [note, setNote] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setCheck(await api.get<CloseCheck>(`/budgets/${budget.id}/close-check`))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The check could not be loaded')
    }
  }, [budget.id])

  React.useEffect(() => {
    void load()
  }, [load])

  const clean = check?.state === 'clean'
  const noteWords = note.trim().split(/\s+/).filter(Boolean).length
  // 17c: a disabled Close button always says why.
  const missing: string[] = []
  if (check && !clean) {
    if (!approve) missing.push('tick that you approve closing it as it is')
    if (noteWords < 3) missing.push('write a note of at least one sentence')
  }

  const submit = async () => {
    if (!check) return
    setBusy(true)
    setError(null)
    try {
      await api.post(
        `/budgets/${budget.id}/close`,
        clean ? {} : { acknowledge: approve, digest: check.digest, note: note.trim() },
      )
      await onClosed()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The budget could not be closed')
      // The list may have changed (a stale digest): show the current one.
      await load()
    } finally {
      setBusy(false)
    }
  }

  const somethingOver = !!check && (check.lines_over.length > 0 || check.state === 'over_estimate')

  return (
    <div role="dialog" aria-modal="true" aria-label="Close budget" data-testid="close-dialog" className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-8">
      <div className="w-full max-w-xl rounded-lg border border-border bg-bg-primary p-5 shadow-xl">
        <div className="flex items-start justify-between">
          <h2 className="text-base font-semibold text-text-primary">Close {budget.name}</h2>
          <button type="button" aria-label="Cancel" onClick={onClose} className="text-text-tertiary hover:text-text-primary">
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mt-1 text-xs text-text-secondary">
          Closing marks the end of the production. Orders, invoices, still to spend and finishing lines are then refused
          until the budget is reopened.
        </p>

        {!check ? (
          error ? (
            <p role="alert" className="mt-4 text-sm text-danger">
              {error}
            </p>
          ) : (
            <p className="mt-4 flex items-center gap-2 text-sm text-text-secondary">
              <Loader2 className="h-4 w-4 animate-spin" /> Checking what does not add up…
            </p>
          )
        ) : (
          <>
            <p data-testid="close-state" data-state={check.state} className="mt-4 text-sm font-medium text-text-primary">
              {clean && <CheckCircle2 className="mr-1 inline h-4 w-4 text-status-success" />}
              {STATE_SENTENCE[check.state]}
            </p>
            <p className="mt-1 text-xs text-text-secondary">
              Planned {formatAmount(check.estimate_cost)} · forecast final cost {formatAmount(check.efc)} ·{' '}
              {varianceWords(check.variance)}
            </p>

            <LineList
              title="Lines over their plan"
              rows={check.lines_over}
              what={(row) => `${varianceWords(row.variance)} (planned ${formatAmount(row.estimate_cost)}, forecast ${formatAmount(row.efc)})`}
            />
            <LineList
              title="Lines under their plan and not marked finished"
              rows={check.lines_under_not_finished}
              what={(row) => `spent ${formatAmount(row.actual)} of ${formatAmount(row.estimate_cost)} planned; mark it finished if nothing more will come`}
            />
            <LineList
              title="Lines marked finished with an order still open"
              rows={check.finished_with_open_commitment}
              what={(row) => `${formatAmount(row.committed_open)} still ordered, not yet invoiced`}
            />
            <EntryList title="Ordered, not yet invoiced" rows={check.open_commitments} total={check.open_commitments_total} />
            <EntryList title="Costs not on a line" rows={check.unallocated} total={check.unallocated_total} />

            {somethingOver && onAddExtra && (
              <p className="mt-3 text-xs text-text-secondary">
                Something is over. If the client agreed to pay for it,{' '}
                <button type="button" data-testid="close-add-extra" onClick={onAddExtra} className="text-accent underline">
                  add an extra charge
                </button>{' '}
                before closing.
              </p>
            )}

            {!clean && (
              <div className="mt-4 space-y-2 border-t border-border pt-3">
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    data-testid="close-approve"
                    checked={approve}
                    onChange={(event) => setApprove(event.target.checked)}
                    className="mt-0.5"
                  />
                  I have read this list and approve closing the budget as it is.
                </label>
                <label className="block text-xs text-text-secondary" htmlFor="close-note">
                  Note — why it is fine to close (who approved it, what happens to the difference)
                </label>
                <textarea
                  id="close-note"
                  data-testid="close-note"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  rows={3}
                  className="w-full rounded border border-border bg-transparent px-2 py-1 text-sm"
                />
              </div>
            )}

            {error && (
              <p role="alert" data-testid="close-error" className="mt-3 text-sm text-danger">
                {error}
              </p>
            )}

            <div className="mt-4 flex items-center justify-end gap-2">
              {missing.length > 0 && (
                <span data-testid="close-missing" className="mr-auto text-xs text-text-tertiary">
                  To close: {missing.join(' and ')}.
                </span>
              )}
              <button type="button" onClick={onClose} className="rounded-md border border-border px-3 py-1.5 text-sm">
                Cancel
              </button>
              <button
                type="button"
                data-testid="close-submit"
                disabled={busy || missing.length > 0}
                onClick={() => void submit()}
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                Close budget
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/** The banner on a closed budget, with Reopen for those who may. */
export function ClosedNotice({
  budget,
  canEdit,
  onReopened,
}: {
  budget: Budget
  canEdit: boolean
  onReopened: () => Promise<void> | void
}) {
  const [reopening, setReopening] = React.useState(false)
  const [reason, setReason] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)

  const reopen = async () => {
    setError(null)
    try {
      await api.post(`/budgets/${budget.id}/reopen`, { reason: reason.trim() })
      setReopening(false)
      setReason('')
      await onReopened()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The budget could not be reopened')
    }
  }

  return (
    <div data-testid="budget-closed" role="status" className="mt-3 rounded-md border border-border bg-bg-secondary px-3 py-2 text-sm">
      <p className="flex items-center gap-1.5 font-medium text-text-primary">
        <LockKeyhole className="h-3.5 w-3.5" /> This budget is closed
        {budget.closed_at ? ` since ${budget.closed_at.slice(0, 10)}` : ''}.
      </p>
      <p className="mt-0.5 text-xs text-text-secondary">
        Orders, invoices, still to spend and finishing lines are refused until it is reopened.
        {budget.close_note ? ` Note: “${budget.close_note}”` : ''}
      </p>
      {canEdit &&
        (reopening ? (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              aria-label="Why it is being reopened"
              data-testid="reopen-reason"
              value={reason}
              placeholder="Why it is being reopened"
              onChange={(event) => setReason(event.target.value)}
              className="w-72 rounded border border-border bg-transparent px-2 py-1 text-sm"
            />
            <button
              type="button"
              data-testid="reopen-submit"
              disabled={!reason.trim()}
              onClick={() => void reopen()}
              className="rounded-md border border-border px-3 py-1 text-xs disabled:opacity-50"
            >
              Reopen
            </button>
            {!reason.trim() && <span className="text-xs text-text-tertiary">Reopening needs a reason.</span>}
          </div>
        ) : (
          <button
            type="button"
            data-testid="budget-reopen"
            onClick={() => setReopening(true)}
            className="mt-2 rounded-md border border-border px-3 py-1 text-xs"
          >
            Reopen…
          </button>
        ))}
      {error && (
        <p role="alert" className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  )
}
