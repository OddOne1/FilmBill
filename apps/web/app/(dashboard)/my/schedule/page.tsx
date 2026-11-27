'use client'

import * as React from 'react'
import Link from 'next/link'
import { Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { usePermissions } from '@/hooks/use-permissions'
import type { MySchedule, MyScheduleEntry } from '@/types'

/**
 * R2. My schedule, from the user menu: my OWN booked and scheduled days,
 * upcoming first, grouped by project and strip. Read only. Nothing about
 * other crew, rates, budgets or gear — the API serves none of it.
 */

function day(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

function hhmm(value?: string | null) {
  return value ? value.slice(0, 5) : null
}

function groupByProject(entries: MyScheduleEntry[]) {
  const groups: { project: string; entries: MyScheduleEntry[] }[] = []
  for (const entry of entries) {
    const last = groups[groups.length - 1]
    if (last && last.project === entry.project_name) last.entries.push(entry)
    else groups.push({ project: entry.project_name, entries: [entry] })
  }
  return groups
}

function Entry({ entry }: { entry: MyScheduleEntry }) {
  return (
    <li data-testid="schedule-entry" className="rounded-md border border-border px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-text-primary">{entry.strip_name}</span>
        <span className="text-text-secondary">
          {entry.first_date === entry.last_date ? day(entry.first_date) : `${day(entry.first_date)} – ${day(entry.last_date)}`}
        </span>
        {entry.role_name && <span className="text-text-secondary">· {entry.role_name}</span>}
        <span
          data-testid="schedule-status"
          className={
            entry.status === 'booked'
              ? 'rounded bg-status-success/15 px-1.5 py-0.5 text-2xs text-status-success'
              : 'rounded bg-bg-tertiary px-1.5 py-0.5 text-2xs text-text-secondary'
          }
        >
          {entry.status === 'booked' ? 'Booked' : 'Scheduled'}
        </span>
      </div>
      <ul className="mt-1 space-y-0.5 text-xs text-text-secondary">
        {(entry.days ?? []).map((d) => {
          const call = hhmm(d.call_time)
          const wrap = hhmm(d.wrap_time)
          return (
            <li key={d.date} data-testid="schedule-day">
              {day(d.date)}
              {call && ` · call ${call}`}
              {wrap && ` · wrap ${wrap}`}
              {d.location && ` · ${d.location}`}
            </li>
          )
        })}
      </ul>
      {entry.note && <p className="mt-1 text-xs text-text-tertiary">Note: {entry.note}</p>}
    </li>
  )
}

function Section({ title, entries }: { title: string; entries: MyScheduleEntry[] }) {
  return (
    <section className="mt-6">
      <h2 className="text-sm font-medium text-text-primary">{title}</h2>
      {groupByProject(entries).map((group, index) => (
        <div key={`${group.project}-${index}`} className="mt-3">
          <h3 data-testid="schedule-project" className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">
            {group.project}
          </h3>
          <ul className="mt-1 space-y-2">
            {group.entries.map((entry) => (
              <Entry key={entry.assignment_id} entry={entry} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}

export default function MySchedulePage() {
  const { loaded, active } = usePermissions()
  const [schedule, setSchedule] = React.useState<MySchedule | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (!loaded) return
    let cancelled = false
    api
      .get<MySchedule>('/me/schedule')
      .then((value) => !cancelled && (setSchedule(value), setError(null)))
      .catch((err) => {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) setSchedule({ available: false, upcoming: [], past: [] })
        else setError(err instanceof ApiError ? err.detail : 'Your schedule could not be loaded. Try again.')
      })
    return () => {
      cancelled = true
    }
  }, [loaded, active?.id])

  return (
    <div className="max-w-3xl p-8">
      <h1 className="text-lg font-semibold text-text-primary">My schedule</h1>
      <p className="mt-1 text-sm text-text-secondary">
        Your own bookings in {active?.display_name ?? 'your company'}. Read only.
      </p>
      {error ? (
        <p role="alert" className="mt-4 text-sm text-status-error">
          {error}
        </p>
      ) : !schedule ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-text-tertiary">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading…
        </div>
      ) : !schedule.available ? (
        <p data-testid="schedule-unavailable" className="mt-6 text-sm text-text-secondary">
          Your schedule shows once you work as crew. Switch it on in{' '}
          <Link href="/my/crew-profile" className="text-accent hover:underline">
            My crew profile
          </Link>
          .
        </p>
      ) : (schedule.upcoming ?? []).length === 0 && (schedule.past ?? []).length === 0 ? (
        <p data-testid="schedule-empty" className="mt-6 text-sm text-text-secondary">
          No bookings yet.
        </p>
      ) : (
        <>
          {(schedule.upcoming ?? []).length > 0 ? (
            <Section title="Upcoming" entries={schedule.upcoming ?? []} />
          ) : (
            <p data-testid="schedule-no-upcoming" className="mt-6 text-sm text-text-secondary">
              No upcoming bookings.
            </p>
          )}
          {(schedule.past ?? []).length > 0 && <Section title="Earlier" entries={schedule.past ?? []} />}
        </>
      )}
    </div>
  )
}
