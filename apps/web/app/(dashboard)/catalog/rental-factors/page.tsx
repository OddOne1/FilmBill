'use client'

import * as React from 'react'
import { Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import type { FactorLookup, RentalFactorGroup } from '@/types'
import {
  CatalogNoAccess,
  CatalogReadOnlyNotice,
  CatalogTabs,
  useCatalogAccess,
} from '../catalog-chrome'

/**
 * Rental factor tables — "3 days of camera cost 2× the day rate, not 3×".
 *
 * **The lookup is the server's, every time.** The table is right there on
 * screen and it is tempting to read "6 days" off it in the browser; that
 * would be this app deciding the rule, and the rule is a decision
 * (SCOPE D71, flagged `[Guessing]`): 6 days with rows at 5 and 7 takes the
 * **5-day** factor, because rounding up would silently overcharge. So the
 * "what would N days cost" box calls
 * `GET /catalog/rental-factor-groups/{id}/factor?days=N` and shows the
 * server's factor, the row it came from, and the rule it applied.
 */

/** What the API's `rule` tokens mean, in words. The tokens themselves are
 *  `FACTOR_RULES` in `billing/catalog/factors.py`. */
const RULE_TEXT: Record<string, string> = {
  exact: 'an exact row for that many days',
  next_lower: 'the next row down — never up, so a gap never overcharges',
  highest_listed: 'the highest row in the table; longer rentals do not grow',
  below_lowest:
    'shorter than the shortest row, so the factor is 1 — the table has said nothing about a rental this short',
  no_table: 'the table has no rows at all, so the factor is 1',
}

export default function RentalFactorsPage() {
  const { loaded, canEdit, refused } = useCatalogAccess()
  const [groups, setGroups] = React.useState<RentalFactorGroup[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [newName, setNewName] = React.useState('')
  const [creating, setCreating] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setGroups(
        await api.get<RentalFactorGroup[]>(
          '/catalog/rental-factor-groups?include_inactive=true',
        ),
      )
      setError(null)
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : 'Could not load the factor tables',
      )
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    if (!loaded) return
    void load()
  }, [loaded, load])

  if (refused) return <CatalogNoAccess title="Rental factors" />

  return (
    <div className="max-w-4xl p-8">
      <h1 className="text-lg font-semibold text-text-primary">
        Rental factors
      </h1>
      <p className="mt-1 text-sm text-text-secondary">
        A multi-day rental is not the day rate times the days. A factor table
        says what it is instead.
      </p>

      {loaded && !canEdit && <CatalogReadOnlyNotice />}
      <CatalogTabs />

      {canEdit && (
        <form
          data-testid="factor-group-new"
          onSubmit={async (event) => {
            event.preventDefault()
            setCreating(true)
            try {
              await api.post('/catalog/rental-factor-groups', {
                name: newName.trim(),
              })
              setNewName('')
              await load()
            } catch (err) {
              setError(
                err instanceof ApiError ? err.detail : 'Could not add the table',
              )
            } finally {
              setCreating(false)
            }
          }}
          className="mt-5 flex items-end gap-3"
        >
          <label className="text-xs text-text-secondary">
            New table
            <Input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="Camera & lenses"
              className="mt-1 w-56"
            />
          </label>
          <button
            type="submit"
            disabled={creating || !newName.trim()}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Add
          </button>
        </form>
      )}

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {loading ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading&hellip;
        </div>
      ) : groups.length === 0 ? (
        <p className="mt-6 text-sm text-text-secondary">
          No factor tables yet. Without one, a rental is billed per unit.
        </p>
      ) : (
        <div className="mt-6 space-y-6">
          {groups.map((group) => (
            <FactorGroupCard
              key={group.id}
              group={group}
              canEdit={canEdit}
              onChanged={load}
              onError={setError}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function FactorGroupCard({
  group,
  canEdit,
  onChanged,
  onError,
}: {
  group: RentalFactorGroup
  canEdit: boolean
  onChanged: () => Promise<void>
  onError: (message: string) => void
}) {
  const [rows, setRows] = React.useState(
    // `?? []` for the same reason as elsewhere: a list with a server-side
    // default comes out optional in the generated types.
    (group.factors ?? []).map((factor) => ({
      days: String(factor.days),
      factor: factor.factor,
    })),
  )
  const [busy, setBusy] = React.useState(false)

  return (
    <section
      data-testid="factor-group"
      className="rounded-md border border-border p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-text-primary">
          {group.name}
          {group.is_active ? '' : ' · off'}
        </h2>
        {canEdit && (
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await api.patch(`/catalog/rental-factor-groups/${group.id}`, {
                  name: group.name,
                  notes: group.notes,
                  is_active: !group.is_active,
                })
                await onChanged()
              } catch (err) {
                onError(
                  err instanceof ApiError
                    ? err.detail
                    : 'Could not change the table',
                )
              } finally {
                setBusy(false)
              }
            }}
            className="text-xs text-text-secondary hover:text-accent disabled:opacity-50"
          >
            {group.is_active ? 'Switch off' : 'Switch on'}
          </button>
        )}
      </div>

      <table className="mt-3 w-full max-w-sm border-collapse text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
            <th className="py-1.5 pr-3 font-medium">Days</th>
            <th className="py-1.5 pr-3 font-medium">Factor</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} data-testid="factor-row" className="border-b border-border/60">
              <td className="py-1.5 pr-3">
                {canEdit ? (
                  <Input
                    aria-label="Days"
                    value={row.days}
                    onChange={(event) =>
                      setRows(
                        rows.map((current, i) =>
                          i === index
                            ? { ...current, days: event.target.value }
                            : current,
                        ),
                      )
                    }
                    className="h-8 w-20 font-mono"
                  />
                ) : (
                  <span className="font-mono text-xs">{row.days}</span>
                )}
              </td>
              <td className="py-1.5 pr-3">
                {canEdit ? (
                  <Input
                    aria-label="Factor"
                    value={row.factor}
                    onChange={(event) =>
                      setRows(
                        rows.map((current, i) =>
                          i === index
                            ? { ...current, factor: event.target.value }
                            : current,
                        ),
                      )
                    }
                    className="h-8 w-24 font-mono"
                  />
                ) : (
                  <span className="font-mono text-xs">{row.factor}</span>
                )}
              </td>
              <td className="py-1.5 text-right">
                {canEdit && (
                  <button
                    type="button"
                    aria-label="Remove row"
                    onClick={() => setRows(rows.filter((_, i) => i !== index))}
                    className="text-xs text-text-secondary hover:text-danger"
                  >
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {canEdit && (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={() => setRows([...rows, { days: '', factor: '' }])}
            className="rounded-md border border-border px-3 py-1.5 text-xs text-text-secondary"
          >
            Add a row
          </button>
          <button
            type="button"
            data-testid="factors-save"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                // The whole table, as typed. `days` goes as a number because
                // it is a count; `factor` goes as the string it was typed as
                // because it is a decimal and this app does not parse those.
                await api.put(
                  `/catalog/rental-factor-groups/${group.id}/factors`,
                  rows.map((row) => ({
                    days: Number(row.days),
                    factor: row.factor.trim(),
                  })),
                )
                await onChanged()
              } catch (err) {
                onError(
                  err instanceof ApiError ? err.detail : 'Could not save the table',
                )
              } finally {
                setBusy(false)
              }
            }}
            className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          >
            {busy ? 'Saving…' : 'Save table'}
          </button>
        </div>
      )}

      <FactorProbe groupId={group.id} />
    </section>
  )
}

/** "What would N days cost?" — answered by the API, never by this table. */
function FactorProbe({ groupId }: { groupId: string }) {
  const [days, setDays] = React.useState('')
  const [result, setResult] = React.useState<FactorLookup | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    const parsed = Number(days)
    if (!days.trim() || !Number.isInteger(parsed) || parsed < 1) {
      setResult(null)
      setError(null)
      return
    }
    let cancelled = false
    const handle = setTimeout(() => {
      void (async () => {
        try {
          const answer = await api.get<FactorLookup>(
            `/catalog/rental-factor-groups/${groupId}/factor?days=${parsed}`,
          )
          if (!cancelled) {
            setResult(answer)
            setError(null)
          }
        } catch (err) {
          if (!cancelled) {
            setResult(null)
            setError(err instanceof ApiError ? err.detail : 'Lookup failed')
          }
        }
      })()
    }, 250)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [days, groupId])

  return (
    <div className="mt-4 border-t border-border pt-3">
      <label className="text-xs text-text-secondary">
        What does this table charge for
        <Input
          aria-label="Rental days"
          value={days}
          onChange={(event) => setDays(event.target.value)}
          placeholder="6"
          className="mx-2 inline-block h-7 w-16 font-mono"
        />
        days?
      </label>
      {error && (
        <p role="alert" className="mt-2 text-xs text-danger">
          {error}
        </p>
      )}
      {result && (
        <p data-testid="factor-answer" className="mt-2 text-xs text-text-primary">
          <span className="font-mono">{result.factor}</span> × the unit rate
          {result.matched_days !== null && result.matched_days !== undefined
            ? ` — from the ${result.matched_days}-day row`
            : ''}
          .{' '}
          <span className="text-text-secondary">
            {RULE_TEXT[result.rule] ?? result.rule}
          </span>
        </p>
      )}
    </div>
  )
}
