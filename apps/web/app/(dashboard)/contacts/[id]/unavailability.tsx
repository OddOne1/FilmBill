'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import { dayLabel } from '@/lib/calendar-dates'
import type { CrewUnavailabilityList } from '@/types'

/**
 * A crew person's own "not available" periods (B2c-1): a vacation, another
 * job elsewhere. They show on the calendar's crew picker and the
 * availability grid as "not available" and produce a clash warning — they
 * never stop anybody being added.
 */
export function UnavailabilitySection({ partyId, partyName, canEdit }: { partyId: string; partyName: string; canEdit: boolean }) {
  const [data, setData] = React.useState<CrewUnavailabilityList | null>(null)
  const [start, setStart] = React.useState('')
  const [end, setEnd] = React.useState('')
  const [reason, setReason] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [adding, setAdding] = React.useState(false)

  React.useEffect(() => {
    api
      .get<CrewUnavailabilityList>(`/parties/${partyId}/unavailability`)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.detail : 'The periods could not be loaded. Try again.'))
  }, [partyId])

  const run = async (call: () => Promise<CrewUnavailabilityList>, failure: string) => {
    setBusy(true)
    setError(null)
    try {
      setData(await call())
      return true
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : failure)
      return false
    } finally {
      setBusy(false)
    }
  }

  const add = async () => {
    const ok = await run(
      () =>
        api.post<CrewUnavailabilityList>(`/parties/${partyId}/unavailability`, {
          start_date: start,
          end_date: end || start,
          reason: reason.trim() || null,
        }),
      'The period could not be saved. Try again.',
    )
    if (ok) {
      setStart('')
      setEnd('')
      setReason('')
      setAdding(false)
    }
  }

  return (
    <section data-testid="crew-unavailability" aria-label="Not available" className="mt-8 border-t border-border pt-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-medium text-text-primary">Not available</h2>
        {canEdit && !adding && (
          <button type="button" data-testid="crew-unavailability-open" onClick={() => setAdding(true)} className="text-xs text-accent hover:underline">
            Add unavailable period
          </button>
        )}
      </div>
      {data && data.periods.length === 0 && (
        <p className="mt-2 text-sm text-text-secondary">No periods. {partyName} is shown as free unless booked.</p>
      )}
      <ul className="mt-2 space-y-1">
        {(data?.periods ?? []).map((period) => (
          <li key={period.id} data-testid="crew-unavailability-row" className="flex items-center gap-3 text-sm">
            <span className="text-text-primary">
              {dayLabel(period.start_date, true)}
              {period.end_date !== period.start_date && ` – ${dayLabel(period.end_date, true)}`}
            </span>
            {period.reason && <span className="text-xs text-text-secondary">{period.reason}</span>}
            {canEdit && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(
                    () => api.post<CrewUnavailabilityList>(`/parties/${partyId}/unavailability/${period.id}/remove`, {}),
                    'The period could not be removed. Try again.',
                  )
                }
                className="ml-auto text-xs text-text-tertiary hover:text-status-error"
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>
      {adding && canEdit && (
        <div className="mt-3 flex flex-wrap items-end gap-2 text-xs">
          <label className="text-text-secondary">
            From
            <input type="date" data-testid="crew-unavailability-start" value={start} onChange={(event) => setStart(event.target.value)} className="mt-1 block h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm" />
          </label>
          <label className="text-text-secondary">
            To
            <input type="date" data-testid="crew-unavailability-end" value={end} onChange={(event) => setEnd(event.target.value)} className="mt-1 block h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm" />
          </label>
          <label className="text-text-secondary">
            Why (optional)
            <input data-testid="crew-unavailability-reason" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Vacation, another job…" className="mt-1 block h-8 w-44 rounded-md border border-border bg-bg-secondary px-2 text-sm" />
          </label>
          <button type="button" data-testid="crew-unavailability-save" disabled={busy || !start} onClick={() => void add()} className="h-8 rounded-md bg-accent px-3 text-sm font-medium text-white disabled:opacity-50">
            Save
          </button>
          <button type="button" onClick={() => setAdding(false)} className="h-8 text-sm text-text-secondary">
            Cancel
          </button>
          {!start && <span className="w-full text-text-tertiary">Choose the first day; leave “To” empty for a single day.</span>}
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-status-error">
          {error}
        </p>
      )}
    </section>
  )
}
