'use client'

import * as React from 'react'
import { Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { CompanySection, Field } from '@/components/settings/company-section'
import { useCompanyStore } from '@/stores/company-store'
import type { AuditEventPage } from '@/types'

/**
 * Settings → Company → Audit log.
 *
 * **Read-only, and read-only all the way down.** There is no write endpoint
 * on `audit_events` and there cannot be one: rows appear only as a side
 * effect of the change they describe, and the table refuses UPDATE, DELETE
 * and TRUNCATE at the database level. So this screen has no actions —
 * deliberately, and the note at the top says why, because a log with no
 * buttons otherwise reads as unfinished.
 *
 * **`audit.view`: Owner, Admin and the tax advisor** (Mathias, 2026-10-20).
 * The advisor is an outside party, which is why the API MASKS IBANs in
 * `changes` before they reach this screen (country code plus last four) —
 * reading a bank account needs `company.settings.edit`, and this must not be
 * a way round that. The masking is the server's; nothing here could undo it
 * and nothing here should try.
 *
 * **Values are rendered verbatim.** `[redacted]` is a marker the API puts
 * there, not a value — the screen shows it as what it is rather than
 * pretending a secret is blank.
 *
 * Install-level events (`company_id` NULL — the mail settings, a user
 * account) are not served here at all. A company owner has no business
 * reading what the installation administrator does.
 */

const PAGE_SIZE = 50

/** Blank means "no filter", which is not the same as `null`. */
interface Filters {
  entity_type: string
  action: string
  user_id: string
  from: string
  to: string
}

const EMPTY: Filters = {
  entity_type: '',
  action: '',
  user_id: '',
  from: '',
  to: '',
}

function queryFor(filters: Filters, offset: number): string {
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(offset),
  })
  for (const [key, value] of Object.entries(filters)) {
    // Dates only once complete: an `<input type="date">` reports partial
    // values while somebody types, and `2026-0` is a 422 that reads as a
    // broken screen.
    if (!value) continue
    if ((key === 'from' || key === 'to') && !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      continue
    }
    params.set(key, value)
  }
  return params.toString()
}

/** `{"legal_name": ["Old", "New"]}` as something readable. */
function describe(changes: Record<string, unknown>): { field: string; from: string; to: string }[] {
  return Object.entries(changes).map(([field, pair]) => {
    const [from, to] = Array.isArray(pair) ? pair : [null, pair]
    return { field, from: show(from), to: show(to) }
  })
}

function show(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—'
  return JSON.stringify(value)
}

export default function AuditLogPage() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)

  const [page, setPage] = React.useState<AuditEventPage | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [filters, setFilters] = React.useState<Filters>(EMPTY)
  const [offset, setOffset] = React.useState(0)

  React.useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .get<AuditEventPage>(`/company/audit-events?${queryFor(filters, offset)}`)
      .then((body) => {
        if (cancelled) return
        setPage(body)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setPage(null)
        setError(
          err instanceof ApiError ? err.detail : 'Could not load the audit log',
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // Keyed on the active company as well: switching must not leave another
    // company's log on screen.
  }, [activeCompanyId, filters, offset])

  function setFilter(key: keyof Filters, value: string) {
    setOffset(0)
    setFilters((current) => ({ ...current, [key]: value }))
  }

  const users = React.useMemo(() => {
    const seen = new Map<string, string>()
    for (const event of page?.events ?? []) {
      if (event.user_id) {
        seen.set(event.user_id, event.user_email ?? event.user_id)
      }
    }
    return Array.from(seen.entries()).sort((a, b) => a[1].localeCompare(b[1]))
  }, [page])

  const total = page?.total ?? 0

  return (
    <CompanySection
      title="Audit log"
      description="Every recorded change in"
      permission="audit.view"
    >
      <p className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary">
        Append-only: these entries cannot be edited or removed, by anyone using
        FilmBill. Bank account numbers are shown masked. Changes an
        installation administrator makes are not listed here.
      </p>

      <div className="mt-4 grid gap-3 rounded-lg border border-border bg-bg-secondary p-4 sm:grid-cols-3">
        <Field label="What" hint="Only the kinds this log actually holds.">
          <select
            aria-label="Entity"
            value={filters.entity_type}
            onChange={(e) => setFilter('entity_type', e.target.value)}
            className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
          >
            <option value="">Everything</option>
            {(page?.entity_types ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Action">
          <select
            aria-label="Action"
            value={filters.action}
            onChange={(e) => setFilter('action', e.target.value)}
            className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
          >
            <option value="">Everything</option>
            {(page?.actions ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Who" hint="The people on this page.">
          <select
            aria-label="User"
            value={filters.user_id}
            onChange={(e) => setFilter('user_id', e.target.value)}
            className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
          >
            <option value="">Everyone</option>
            {users.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="From">
          <Input
            type="date"
            value={filters.from}
            onChange={(e) => setFilter('from', e.target.value)}
          />
        </Field>
        <Field label="To" hint="Inclusive — the whole of that day.">
          <Input
            type="date"
            value={filters.to}
            onChange={(e) => setFilter('to', e.target.value)}
          />
        </Field>
        <div className="flex items-end">
          <button
            type="button"
            onClick={() => {
              setOffset(0)
              setFilters(EMPTY)
            }}
            className="h-9 rounded-md border border-border px-3 text-sm text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
          >
            Clear filters
          </button>
        </div>
      </div>

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {error}
        </p>
      )}

      <div className="mt-4 overflow-hidden rounded-lg border border-border">
        {loading ? (
          <div className="flex items-center gap-2 p-4 text-sm text-text-tertiary">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading…
          </div>
        ) : total === 0 ? (
          <p className="p-4 text-sm text-text-tertiary" data-testid="audit-empty">
            Nothing recorded for these filters.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-bg-secondary text-left text-xs text-text-tertiary">
              <tr>
                <th className="px-3 py-2 font-medium">When</th>
                <th className="px-3 py-2 font-medium">Who</th>
                <th className="px-3 py-2 font-medium">What</th>
                <th className="px-3 py-2 font-medium">Changed</th>
              </tr>
            </thead>
            <tbody>
              {(page?.events ?? []).map((event) => (
                <tr key={event.id} className="border-t border-border align-top">
                  <td className="whitespace-nowrap px-3 py-2 text-text-secondary">
                    {new Date(event.created_at).toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {event.user_email ?? 'system'}
                    {event.ip && (
                      <div className="text-xs text-text-tertiary">{event.ip}</div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="font-mono text-xs text-text-primary">
                      {event.action}
                    </div>
                    <div className="text-xs text-text-tertiary">
                      {event.entity_type}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {describe(event.changes).length === 0 ? (
                      <span className="text-xs text-text-tertiary">—</span>
                    ) : (
                      <ul className="space-y-0.5">
                        {describe(event.changes).map(
                          (change) => (
                            <li key={change.field} className="text-xs">
                              <span className="text-text-tertiary">
                                {change.field}:{' '}
                              </span>
                              <span className="text-text-secondary line-through">
                                {change.from}
                              </span>
                              <span className="text-text-tertiary"> → </span>
                              <span className="text-text-primary">
                                {change.to}
                              </span>
                            </li>
                          ),
                        )}
                      </ul>
                    )}
                    {event.reason && (
                      <p className="mt-1 text-xs text-text-tertiary">
                        {event.reason}
                      </p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {total > PAGE_SIZE && (
        <div className="mt-3 flex items-center gap-3 text-xs text-text-secondary">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            className="rounded-md border border-border px-3 py-1.5 transition-colors hover:bg-bg-hover disabled:opacity-40"
          >
            Newer
          </button>
          <span>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <button
            type="button"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
            className="rounded-md border border-border px-3 py-1.5 transition-colors hover:bg-bg-hover disabled:opacity-40"
          >
            Older
          </button>
        </div>
      )}
    </CompanySection>
  )
}
