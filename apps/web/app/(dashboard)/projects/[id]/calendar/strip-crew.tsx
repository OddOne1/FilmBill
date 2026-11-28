'use client'

import { TeamChip } from '@/components/shared/team-chip'
import * as React from 'react'
import Link from 'next/link'
import { AlertTriangle, Plus, UserPlus } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatMoney } from '@/lib/money'
import { cn } from '@/lib/utils'
import { datesBetween, dayLabel, weekdayLetter, type IsoDate } from '@/lib/calendar-dates'
import type {
  CalendarStrip,
  CrewAssignment,
  CrewAssignmentsCreated,
  CrewAvailability,
  CrewAvailabilityPerson,
  CrewBulkStatusResult,
  CrewClash,
  CrewRoleSuggestions,
  CrewStripCounts,
  PartyPage,
  ProjectCrewStrip,
} from '@/types'

/**
 * The strip panel's Crew section (B2c-1).
 *
 * People on the strip with their role, a status chip (scheduled / booked /
 * declined) and a clash badge; **Add crew** opens a picker that shows, for
 * each crew person, what the strip's dates look like for them (the API's
 * `/crew/availability`). Per person: the days checklist with call and wrap
 * of their own, the role, **Mark booked** with a "confirmed how?" note,
 * Decline, Cancel and Remove; **Mark selected as booked** for several.
 *
 * Clashes are the API's, with its sentence, and never stop a save.
 * Without `projects.edit` it is read-only and says why (17c). Without
 * `budgets.view` no rate is drawn and the picker has no rate filter — the
 * API sends no number anyway.
 */

export function crewCountsText(counts: CrewStripCounts | null | undefined): string {
  if (!counts) return ''
  const parts: string[] = []
  if (counts.booked) parts.push(`${counts.booked} booked`)
  if (counts.scheduled) parts.push(`${counts.scheduled} scheduled`)
  return parts.join(' · ')
}

const STATUS_CHIP: Record<string, string> = {
  scheduled: 'bg-status-warning/15 text-status-warning',
  booked: 'bg-status-success/15 text-status-success',
  declined: 'bg-bg-tertiary text-text-tertiary line-through',
  cancelled: 'bg-bg-tertiary text-text-tertiary',
}

export function StripCrew({
  projectId,
  strip,
  crew,
  canEdit,
  canSeeRates,
  currency,
  onChanged,
  embedded = false,
}: {
  projectId: string
  strip: CalendarStrip
  /** This strip's entry of `GET /projects/{id}/crew`; null while loading. */
  crew: ProjectCrewStrip | null
  canEdit: boolean
  canSeeRates: boolean
  currency: string
  onChanged: () => Promise<void> | void
  /** Inside a card that already names the strip (Project → Crew): no rule
   *  above it and no "Crew" heading of its own. */
  embedded?: boolean
}) {
  const [picking, setPicking] = React.useState(false)
  const [openId, setOpenId] = React.useState<string | null>(null)
  const [selected, setSelected] = React.useState<Set<string>>(new Set())
  const [bulkNote, setBulkNote] = React.useState('')
  const [warnings, setWarnings] = React.useState<CrewClash[]>([])
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [laborItems, setLaborItems] = React.useState<{ item_id: string; name: string }[]>([])

  React.useEffect(() => {
    setSelected(new Set())
    setOpenId(null)
    setWarnings([])
    setNotice(null)
    setPicking(false)
  }, [strip.id])

  React.useEffect(() => {
    api
      .get<CrewRoleSuggestions>('/crew/roles')
      .then((roles) => setLaborItems(roles.labor_items ?? []))
      .catch(() => setLaborItems([]))
  }, [])

  const assignments = crew?.assignments ?? []


  const act = async <T,>(call: () => Promise<T>, failure: string): Promise<T | null> => {
    setBusy(true)
    setError(null)
    try {
      const result = await call()
      await onChanged()
      return result
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : failure)
      return null
    } finally {
      setBusy(false)
    }
  }

  const bookSelected = async () => {
    const result = await act(
      () =>
        api.post<CrewBulkStatusResult>(`/projects/${projectId}/crew/status`, {
          assignment_ids: Array.from(selected),
          status: 'booked',
          note: bulkNote.trim() || null,
        }),
      'The people could not be marked booked. Try again.',
    )
    if (result) {
      setSelected(new Set())
      setBulkNote('')
      setNotice(
        result.skipped.length
          ? `${result.changed} marked booked. ${result.skipped.map((entry) => entry.reason).join(' ')}`
          : `${result.changed} marked booked.`,
      )
    }
  }

  return (
    <section
      data-testid="strip-crew"
      aria-label={embedded ? `Crew on ${strip.label ?? strip.type}` : 'Crew'}
      className={embedded ? 'mt-2' : 'mt-5 border-t border-border pt-4'}
    >
      <div className="flex items-center gap-2">
        {!embedded && <h3 className="text-sm font-medium text-text-primary">Crew</h3>}
        <span data-testid="strip-crew-counts" className="text-xs text-text-tertiary">
          {crewCountsText(crew?.counts) || (crew ? 'Nobody yet' : '')}
        </span>
        {canEdit && (
          <button
            type="button"
            data-testid="strip-crew-add"
            onClick={() => setPicking(!picking)}
            aria-expanded={picking}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-secondary"
          >
            <Plus className="h-3.5 w-3.5" /> Add crew
          </button>
        )}
      </div>
      {!canEdit && (
        <p data-testid="strip-crew-read-only" className="mt-1 text-xs text-text-tertiary">
          You can see who is on this strip but not change it — that needs the project edit permission.
        </p>
      )}

      {picking && canEdit && (
        <CrewPicker
          projectId={projectId}
          strip={strip}
          taken={new Set(assignments.map((a) => a.party_id))}
          laborItems={laborItems}
          canSeeRates={canSeeRates}
          currency={currency}
          onAdded={async (created) => {
            await onChanged()
            setPicking(false)
            setWarnings(created.clashes)
            setNotice(
              `${created.assignments.length} ${created.assignments.length === 1 ? 'person' : 'people'} added as scheduled.`,
            )
          }}
        />
      )}

      {notice && (
        <p role="status" data-testid="strip-crew-notice" className="mt-2 text-xs text-text-secondary">
          {notice}
        </p>
      )}
      {warnings.length > 0 && <ClashList clashes={warnings} testId="strip-crew-warnings" />}
      {error && (
        <p role="alert" className="mt-2 text-xs text-status-error">
          {error}
        </p>
      )}

      {crew && assignments.length === 0 && !picking && (
        <p className="mt-2 text-xs text-text-secondary">
          {canEdit ? 'Nobody is on this strip yet. Add crew to plan who works on these days.' : 'Nobody is on this strip yet.'}
        </p>
      )}

      <ul className="mt-2 space-y-1">
        {assignments.map((assignment) => (
          <li key={assignment.id} data-testid="strip-crew-row" data-status={assignment.status} className="rounded border border-border/60 px-2 py-1.5">
            <div className="flex items-center gap-2">
              {canEdit && (
                <input
                  type="checkbox"
                  aria-label={`Select ${assignment.party_name}`}
                  disabled={assignment.status !== 'scheduled'}
                  checked={selected.has(assignment.id)}
                  onChange={(event) => {
                    const next = new Set(selected)
                    if (event.target.checked) next.add(assignment.id)
                    else next.delete(assignment.id)
                    setSelected(next)
                  }}
                />
              )}
              <button
                type="button"
                onClick={() => setOpenId(openId === assignment.id ? null : assignment.id)}
                className="min-w-0 flex-1 truncate text-left text-sm text-text-primary hover:underline"
              >
                {assignment.party_name}
                {assignment.role_name && <span className="text-text-secondary"> · {assignment.role_name}</span>}
              </button>
              {assignment.clashes.length > 0 && (
                <span
                  data-testid="strip-crew-clash"
                  title={assignment.clashes.map((c) => c.message).join('\n')}
                  className="inline-flex items-center gap-0.5 rounded bg-status-warning/15 px-1 text-2xs text-status-warning"
                >
                  <AlertTriangle className="h-3 w-3" aria-hidden="true" /> clash
                </span>
              )}
              <span data-testid="strip-crew-status" className={cn('rounded px-1.5 py-0.5 text-2xs', STATUS_CHIP[assignment.status])}>
                {assignment.status}
              </span>
            </div>
            {assignment.hotel_visible && <CrewHotels assignment={assignment} />}
            {openId === assignment.id && (
              <AssignmentEditor
                projectId={projectId}
                strip={strip}
                assignment={assignment}
                laborItems={laborItems}
                canEdit={canEdit}
                canSeeRates={canSeeRates}
                currency={currency}
                busy={busy}
                act={act}
              />
            )}
          </li>
        ))}
      </ul>

      {canEdit && assignments.some((a) => a.status === 'scheduled') && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            data-testid="strip-crew-bulk-note"
            value={bulkNote}
            onChange={(event) => setBulkNote(event.target.value)}
            placeholder="Confirmed how? e.g. by phone"
            className="h-7 flex-1 rounded-md border border-border bg-bg-secondary px-2 text-xs"
          />
          <button
            type="button"
            data-testid="strip-crew-bulk-book"
            disabled={busy || selected.size === 0}
            onClick={() => void bookSelected()}
            className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
          >
            Mark selected as booked
          </button>
          {selected.size === 0 && (
            <span className="w-full text-2xs text-text-tertiary">Tick the scheduled people who confirmed their days.</span>
          )}
        </div>
      )}
    </section>
  )
}

/** L1b. Where this person sleeps during this strip — their hotel stays from
 *  the project's Locations tab, read-only here — each with the API's Route
 *  link, rendered as it came. */
function CrewHotels({ assignment }: { assignment: CrewAssignment }) {
  const stays = assignment.hotel_stays ?? []
  return (
    <div data-testid="strip-crew-hotel" className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-text-secondary">
      <span>Hotel</span>
      {stays.length === 0 && (
        <span data-testid="strip-crew-hotel-none" className="text-text-tertiary">
          None yet — add guests to a hotel on the project&rsquo;s Locations tab.
        </span>
      )}
      {stays.map((stay) => (
        <span key={stay.stay_id} data-testid="strip-crew-stay" className="inline-flex items-center gap-1.5">
          <span className="text-text-primary">{stay.hotel.name}</span>
          {!stay.hotel.is_active && (
            <span data-testid="strip-crew-hotel-archived" className="rounded bg-bg-tertiary px-1 text-2xs text-text-tertiary">
              archived
            </span>
          )}
          <span className="text-text-tertiary">
            {dayLabel(stay.check_in, false)} – {dayLabel(stay.check_out, false)}
          </span>
          {stay.route_link && (
            <a
              data-testid="strip-crew-route"
              href={stay.route_link}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              Route
            </a>
          )}
        </span>
      ))}
    </div>
  )
}

export function ClashList({ clashes, testId }: { clashes: CrewClash[]; testId?: string }) {
  return (
    <ul data-testid={testId} className="mt-2 space-y-1">
      {clashes.map((clash, index) => (
        <li
          key={`${clash.party_id}-${clash.assignment_id ?? clash.unavailability_id ?? index}`}
          className="flex gap-1.5 rounded border border-status-warning/40 bg-status-warning/10 px-2 py-1 text-xs text-status-warning"
        >
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          <span>{clash.message}</span>
        </li>
      ))}
    </ul>
  )
}

function AssignmentEditor({
  projectId,
  strip,
  assignment,
  laborItems,
  canEdit,
  canSeeRates,
  currency,
  busy,
  act,
}: {
  projectId: string
  strip: CalendarStrip
  assignment: CrewAssignment
  laborItems: { item_id: string; name: string }[]
  canEdit: boolean
  canSeeRates: boolean
  currency: string
  busy: boolean
  act: <T>(call: () => Promise<T>, failure: string) => Promise<T | null>
}) {
  const base = `/projects/${projectId}/crew/${assignment.id}`
  const dates = datesBetween(strip.start_date, strip.end_date)
  const stripDays = new Map(strip.days.map((day) => [day.date, day]))
  const initial = React.useMemo(
    () => new Map(assignment.days.map((day) => [day.date, { call: day.call_time?.slice(0, 5) ?? '', wrap: day.wrap_time?.slice(0, 5) ?? '' }])),
    [assignment.days],
  )
  const [days, setDays] = React.useState(initial)
  const [note, setNote] = React.useState('')
  React.useEffect(() => setDays(initial), [initial])

  const toggle = (date: IsoDate, on: boolean) => {
    const next = new Map(days)
    if (on) next.set(date, { call: '', wrap: '' })
    else next.delete(date)
    setDays(next)
  }
  const setTime = (date: IsoDate, key: 'call' | 'wrap', value: string) => {
    const next = new Map(days)
    next.set(date, { ...(next.get(date) ?? { call: '', wrap: '' }), [key]: value })
    setDays(next)
  }
  const saveDays = () =>
    act(
      () =>
        api.patch<CrewAssignment>(base, {
          // Only days inside the strip: after a strip was shortened, a day
          // left outside it is dropped on the next save, not resent.
          days: Array.from(days.entries())
            .filter(([date]) => date >= strip.start_date && date <= strip.end_date)
            .sort(([a], [b]) => (a < b ? -1 : 1))
            .map(([date, t]) => ({ date, call_time: t.call || null, wrap_time: t.wrap || null })),
        }),
      'The days could not be saved. Try again.',
    )
  const status = (wanted: string) =>
    act(() => api.post<CrewAssignment>(`${base}/status`, { status: wanted, note: note.trim() || null }), 'The status could not be changed. Try again.')

  const editable = canEdit && assignment.status !== 'cancelled'

  return (
    <div data-testid="strip-crew-editor" className="mt-2 space-y-2 border-t border-border/60 pt-2 text-xs">
      {assignment.clashes.length > 0 && <ClashList clashes={assignment.clashes} testId="strip-crew-row-clashes" />}
      {assignment.status_note && (
        <p className="text-text-secondary">
          {assignment.status === 'booked' ? 'Confirmed: ' : 'Note: '}
          {assignment.status_note}
          {assignment.booked_by_name ? ` (${assignment.booked_by_name})` : ''}
        </p>
      )}
      {canSeeRates && assignment.day_rate && (
        <p data-testid="strip-crew-rate" className="text-text-secondary">
          Day rate {formatMoney(assignment.day_rate, currency)}
        </p>
      )}
      <label className="block text-text-secondary">
        Role{' '}
        <select
          data-testid="strip-crew-role"
          disabled={!editable || busy}
          value={assignment.role_item_id ?? ''}
          onChange={(event) =>
            void act(
              () => api.patch<CrewAssignment>(base, { role_item_id: event.target.value || null }),
              'The role could not be changed. Try again.',
            )
          }
          className="ml-1 h-7 rounded-md border border-border bg-bg-secondary px-1"
        >
          <option value="">No role</option>
          {laborItems.map((item) => (
            <option key={item.item_id} value={item.item_id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>

      <fieldset data-testid="strip-crew-days">
        <legend className="text-text-secondary">Days</legend>
        <ul className="mt-1 space-y-0.5">
          {dates.map((date) => {
            const day = stripDays.get(date)
            const on = days.has(date)
            return (
              <li key={date} className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  aria-label={`Works on ${dayLabel(date, true)}`}
                  data-testid="strip-crew-day"
                  data-date={date}
                  disabled={!editable}
                  checked={on}
                  onChange={(event) => toggle(date, event.target.checked)}
                />
                <span className="w-20 text-text-primary">
                  {weekdayLetter(date)} {dayLabel(date)}
                </span>
                <span className="w-12 text-text-tertiary">{day ? day.type : 'no day'}</span>
                {on && editable && (
                  <>
                    <input
                      type="time"
                      aria-label={`Call on ${dayLabel(date, true)}`}
                      value={days.get(date)?.call ?? ''}
                      onChange={(event) => setTime(date, 'call', event.target.value)}
                      className="h-6 w-20 rounded border border-border bg-bg-secondary px-1"
                    />
                    <input
                      type="time"
                      aria-label={`Wrap on ${dayLabel(date, true)}`}
                      value={days.get(date)?.wrap ?? ''}
                      onChange={(event) => setTime(date, 'wrap', event.target.value)}
                      className="h-6 w-20 rounded border border-border bg-bg-secondary px-1"
                    />
                  </>
                )}
              </li>
            )
          })}
        </ul>
        {editable && (
          <button
            type="button"
            data-testid="strip-crew-save-days"
            disabled={busy}
            onClick={() => void saveDays()}
            className="mt-1 rounded border border-border px-2 py-0.5"
          >
            Save days
          </button>
        )}
      </fieldset>

      {editable && (
        <div className="flex flex-wrap items-center gap-1.5">
          <input
            data-testid="strip-crew-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder={assignment.status === 'booked' ? 'Why reopen?' : 'Confirmed how? e.g. by phone'}
            className="h-7 min-w-[10rem] flex-1 rounded-md border border-border bg-bg-secondary px-2"
          />
          {assignment.status === 'scheduled' && (
            <>
              <button type="button" data-testid="strip-crew-book" disabled={busy} onClick={() => void status('booked')} className="rounded bg-accent px-2 py-1 font-medium text-white">
                Mark booked
              </button>
              <button type="button" data-testid="strip-crew-decline" disabled={busy} onClick={() => void status('declined')} className="rounded border border-border px-2 py-1">
                Decline
              </button>
            </>
          )}
          {assignment.status === 'booked' && (
            <>
              <button
                type="button"
                data-testid="strip-crew-reopen"
                disabled={busy || !note.trim()}
                onClick={() => void status('scheduled')}
                className="rounded border border-border px-2 py-1 disabled:opacity-50"
              >
                Reopen
              </button>
              <button type="button" data-testid="strip-crew-cancel" disabled={busy} onClick={() => void status('cancelled')} className="rounded border border-border px-2 py-1">
                Cancel booking
              </button>
              {!note.trim() && <span className="w-full text-2xs text-text-tertiary">Reopening needs a note saying why.</span>}
            </>
          )}
          <button
            type="button"
            data-testid="strip-crew-remove"
            disabled={busy}
            onClick={() => void act(() => api.post<CrewAssignment>(`${base}/remove`, {}), 'The person could not be removed. Try again.')}
            className="ml-auto text-text-tertiary hover:text-status-error"
          >
            Remove
          </button>
        </div>
      )}
    </div>
  )
}

/** What the strip's dates look like for one person, in words. */
export function availabilityText(person: CrewAvailabilityPerson): string {
  const busy = person.days.filter((day) => day.state !== 'free')
  if (busy.length === 0) return 'free'
  const seen = new Map<string, number>()
  for (const day of busy) {
    for (const entry of day.entries) {
      const key =
        entry.kind === 'unavailable'
          ? `unavailable${entry.reason ? ` (${entry.reason})` : ''}`
          : `${entry.status} on ${entry.project_name} · ${entry.strip_name}`
      seen.set(key, (seen.get(key) ?? 0) + 1)
    }
  }
  return Array.from(seen.entries())
    .map(([key, count]) => `${key}, ${count} ${count === 1 ? 'day' : 'days'}`)
    .join('; ')
}

function CrewPicker({
  projectId,
  strip,
  taken,
  laborItems,
  canSeeRates,
  currency,
  onAdded,
}: {
  projectId: string
  strip: CalendarStrip
  taken: Set<string>
  laborItems: { item_id: string; name: string }[]
  canSeeRates: boolean
  currency: string
  onAdded: (created: CrewAssignmentsCreated) => Promise<void> | void
}) {
  const [search, setSearch] = React.useState('')
  const [role, setRole] = React.useState('')
  const [maxRate, setMaxRate] = React.useState('')
  const [availableOnly, setAvailableOnly] = React.useState(false)
  const [data, setData] = React.useState<CrewAvailability | null>(null)
  const [picked, setPicked] = React.useState<Set<string>>(new Set())
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [others, setOthers] = React.useState<PartyPage['parties'] | null>(null)

  const load = React.useCallback(async () => {
    const params = new URLSearchParams({ from: strip.start_date, to: strip.end_date })
    if (role) params.set('role_item_id', role)
    if (canSeeRates && maxRate.trim()) params.set('max_rate', maxRate.trim().replace(',', '.'))
    if (search.trim()) params.set('search', search.trim())
    if (availableOnly) params.set('available_only', 'true')
    try {
      setData(await api.get<CrewAvailability>(`/crew/availability?${params.toString()}`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The crew list could not be loaded. Try again.')
    }
  }, [strip.start_date, strip.end_date, role, maxRate, search, availableOnly, canSeeRates])

  React.useEffect(() => {
    const handle = setTimeout(() => void load(), 150)
    return () => clearTimeout(handle)
  }, [load])

  const add = async () => {
    setBusy(true)
    setError(null)
    try {
      const created = await api.post<CrewAssignmentsCreated>(`/projects/${projectId}/blocks/${strip.id}/crew`, {
        people: Array.from(picked).map((party_id) => (role ? { party_id, role_item_id: role } : { party_id })),
      })
      setPicked(new Set())
      await onAdded(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The people could not be added. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const findOthers = async () => {
    if (!search.trim()) return
    try {
      const page = await api.get<PartyPage>(`/parties?search=${encodeURIComponent(search.trim())}&type=person`)
      setOthers(page.parties.filter((party) => !party.is_crew))
    } catch {
      setOthers([])
    }
  }

  const markCrew = async (partyId: string) => {
    try {
      await api.patch(`/parties/${partyId}`, { is_crew: true })
      setOthers((current) => (current ?? []).filter((party) => party.id !== partyId))
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'This contact could not be marked as crew. Try again.')
    }
  }

  return (
    <div data-testid="crew-picker" className="mt-2 rounded-md border border-border bg-bg-secondary p-2 text-xs">
      <div className="flex flex-wrap gap-2">
        <input
          data-testid="crew-picker-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search by name"
          className="h-7 flex-1 rounded-md border border-border bg-bg-primary px-2"
        />
        <select
          data-testid="crew-picker-role"
          value={role}
          onChange={(event) => setRole(event.target.value)}
          className="h-7 rounded-md border border-border bg-bg-primary px-1"
        >
          <option value="">Any role</option>
          {laborItems.map((item) => (
            <option key={item.item_id} value={item.item_id}>
              {item.name}
            </option>
          ))}
        </select>
        {canSeeRates && (
          <input
            data-testid="crew-picker-max-rate"
            inputMode="decimal"
            value={maxRate}
            onChange={(event) => setMaxRate(event.target.value)}
            placeholder="Max day rate"
            className="h-7 w-24 rounded-md border border-border bg-bg-primary px-2"
          />
        )}
        <label className="inline-flex items-center gap-1 text-text-secondary">
          <input
            type="checkbox"
            data-testid="crew-picker-available"
            checked={availableOnly}
            onChange={(event) => setAvailableOnly(event.target.checked)}
          />
          Available only
        </label>
      </div>

      {error && (
        <p role="alert" className="mt-2 text-status-error">
          {error}
        </p>
      )}
      {data === null ? (
        !error && <p className="mt-2 text-text-secondary">Loading the crew…</p>
      ) : data.people.length === 0 ? (
        <p data-testid="crew-picker-empty" className="mt-2 text-text-secondary">
          Nobody in the crew list matches. Change the filters, or look for the person among all contacts below.
        </p>
      ) : (
        <ul className="mt-2 max-h-64 space-y-0.5 overflow-y-auto">
          {data.people.map((person) => {
            const already = taken.has(person.party_id)
            const free = person.days.every((day) => day.state === 'free')
            return (
              <li key={person.party_id} data-testid="crew-picker-row" className="flex items-center gap-2">
                <input
                  type="checkbox"
                  aria-label={`Pick ${person.name}`}
                  disabled={already}
                  checked={picked.has(person.party_id)}
                  onChange={(event) => {
                    const next = new Set(picked)
                    if (event.target.checked) next.add(person.party_id)
                    else next.delete(person.party_id)
                    setPicked(next)
                  }}
                />
                <span className="w-28 truncate text-text-primary">{person.name}</span>
                {person.is_team_member && <TeamChip />}
                <span className="w-20 truncate text-text-tertiary">{person.roles.join(', ')}</span>
                {data.rates_visible && (
                  <span data-testid="crew-picker-rate" className="w-20 text-text-secondary">
                    {person.rate ? formatMoney(person.rate, currency) : '—'}
                  </span>
                )}
                <span
                  data-testid="crew-picker-availability"
                  data-state={already ? 'taken' : free ? 'free' : 'busy'}
                  className={cn('flex-1 truncate', already ? 'text-text-tertiary' : free ? 'text-status-success' : 'text-status-warning')}
                >
                  {already ? 'already on this strip' : availabilityText(person)}
                </span>
              </li>
            )
          })}
        </ul>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="crew-picker-add"
          disabled={busy || picked.size === 0}
          onClick={() => void add()}
          className="rounded-md bg-accent px-2 py-1 font-medium text-white disabled:opacity-50"
        >
          Add {picked.size || ''} {picked.size === 1 ? 'person' : 'people'}
        </button>
        {picked.size === 0 && <span className="text-text-tertiary">Tick the people to add. They start as scheduled.</span>}
        <button
          type="button"
          data-testid="crew-picker-others"
          disabled={!search.trim()}
          onClick={() => void findOthers()}
          className="ml-auto inline-flex items-center gap-1 text-text-secondary hover:text-text-primary disabled:opacity-50"
          title={search.trim() ? undefined : 'Type a name first'}
        >
          <UserPlus className="h-3.5 w-3.5" /> Not in the crew list?
        </button>
      </div>
      {others !== null && (
        <div data-testid="crew-picker-not-crew" className="mt-2 border-t border-border pt-2">
          {others.length === 0 ? (
            <p className="text-text-secondary">No other contact by that name.</p>
          ) : (
            <ul className="space-y-0.5">
              {others.map((party) => (
                <li key={party.id} className="flex items-center gap-2">
                  <Link href={`/contacts/${party.id}`} className="text-text-primary hover:underline">
                    {party.name}
                  </Link>
                  <span className="text-text-tertiary">is not marked as crew.</span>
                  <button type="button" onClick={() => void markCrew(party.id)} className="rounded border border-border px-1.5 py-0.5">
                    Mark as crew
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
