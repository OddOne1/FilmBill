'use client'

import * as React from 'react'
import Link from 'next/link'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatMoney } from '@/lib/money'
import { cn } from '@/lib/utils'
import { addDays, dayLabel, todayIso, weekdayLetter, type IsoDate } from '@/lib/calendar-dates'
import type { CrewAvailability, CrewAvailabilityDay, CrewAvailabilityPerson, ProjectPage } from '@/types'

/**
 * Contacts → Crew → Availability (B2c-1): people as rows, days as columns,
 * each cell the API's state — free, scheduled, booked or unavailable — and
 * a click on a cell says what it is. Filters: role, maximum rate (only with
 * `budgets.view`) and project. Nothing is worked out here: the states and
 * the clash flag come from `GET /crew/availability`.
 */

const STATE_CELL: Record<string, string> = {
  free: 'bg-bg-secondary',
  scheduled: 'bg-status-warning/40',
  booked: 'bg-status-success/50',
  unavailable: 'bg-text-tertiary/40',
}
const STATE_WORD: Record<string, string> = {
  free: 'free',
  scheduled: 'scheduled',
  booked: 'booked',
  unavailable: 'not available',
}
const SPANS = [7, 14, 31]

export function CrewAvailabilityView({
  laborItems,
  canSeeRates,
  currency,
}: {
  laborItems: { item_id: string; name: string }[]
  canSeeRates: boolean
  currency: string
}) {
  const [from, setFrom] = React.useState<IsoDate>(() => todayIso())
  const [span, setSpan] = React.useState(7)
  const [role, setRole] = React.useState('')
  const [maxRate, setMaxRate] = React.useState('')
  const [projectId, setProjectId] = React.useState('')
  const [projects, setProjects] = React.useState<{ id: string; name: string }[]>([])
  const [data, setData] = React.useState<CrewAvailability | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [open, setOpen] = React.useState<{ person: CrewAvailabilityPerson; day: CrewAvailabilityDay } | null>(null)
  const to = addDays(from, span - 1)

  React.useEffect(() => {
    api
      .get<ProjectPage>('/projects?limit=200')
      .then((page) => setProjects(page.projects.map((p) => ({ id: p.id, name: p.name }))))
      .catch(() => setProjects([]))
  }, [])

  React.useEffect(() => {
    const params = new URLSearchParams({ from, to })
    if (role) params.set('role_item_id', role)
    if (canSeeRates && maxRate.trim()) params.set('max_rate', maxRate.trim().replace(',', '.'))
    if (projectId) params.set('project_id', projectId)
    const handle = setTimeout(() => {
      api
        .get<CrewAvailability>(`/crew/availability?${params.toString()}`)
        .then((loaded) => {
          setData(loaded)
          setError(null)
        })
        .catch((err) => setError(err instanceof ApiError ? err.detail : 'The availability could not be loaded. Try again.'))
    }, 150)
    return () => clearTimeout(handle)
  }, [from, to, role, maxRate, projectId, canSeeRates])

  return (
    <div data-testid="crew-availability" className="mt-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button type="button" aria-label="Earlier" onClick={() => setFrom(addDays(from, -span))} className="rounded border border-border p-1">
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <input
          type="date"
          aria-label="From"
          data-testid="crew-availability-from"
          value={from}
          onChange={(event) => event.target.value && setFrom(event.target.value)}
          className="h-8 rounded-md border border-border bg-bg-secondary px-2"
        />
        <button type="button" aria-label="Later" onClick={() => setFrom(addDays(from, span))} className="rounded border border-border p-1">
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
        <select aria-label="Days shown" value={span} onChange={(event) => setSpan(Number(event.target.value))} className="h-8 rounded-md border border-border bg-bg-secondary px-1">
          {SPANS.map((n) => (
            <option key={n} value={n}>
              {n} days
            </option>
          ))}
        </select>
        <select
          aria-label="Role"
          data-testid="crew-availability-role"
          value={role}
          onChange={(event) => setRole(event.target.value)}
          className="h-8 rounded-md border border-border bg-bg-secondary px-1"
        >
          <option value="">All roles</option>
          {laborItems.map((item) => (
            <option key={item.item_id} value={item.item_id}>
              {item.name}
            </option>
          ))}
        </select>
        {canSeeRates && (
          <input
            aria-label="Maximum day rate"
            data-testid="crew-availability-max-rate"
            inputMode="decimal"
            value={maxRate}
            onChange={(event) => setMaxRate(event.target.value)}
            placeholder="Max day rate"
            className="h-8 w-24 rounded-md border border-border bg-bg-secondary px-2"
          />
        )}
        <select
          aria-label="Project"
          data-testid="crew-availability-project"
          value={projectId}
          onChange={(event) => setProjectId(event.target.value)}
          className="h-8 rounded-md border border-border bg-bg-secondary px-1"
        >
          <option value="">All projects</option>
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
        <span className="ml-auto flex gap-2 text-text-tertiary">
          {Object.entries(STATE_WORD).map(([state, word]) => (
            <span key={state} className="inline-flex items-center gap-1">
              <span className={cn('h-2.5 w-3 rounded-sm', STATE_CELL[state])} />
              {word}
            </span>
          ))}
        </span>
      </div>

      {error && (
        <p role="alert" className="mt-3 text-sm text-status-error">
          {error}
        </p>
      )}
      {data && data.people.length === 0 && (
        <p data-testid="crew-availability-empty" className="mt-4 text-sm text-text-secondary">
          Nobody in the crew list matches these filters. Clear the role, rate or project to see everyone.
        </p>
      )}
      {data && data.people.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="border-collapse text-xs">
            <thead>
              <tr>
                <th className="sticky left-0 bg-bg-primary py-1 pr-3 text-left font-medium text-text-tertiary">Person</th>
                {data.rates_visible && <th className="py-1 pr-2 text-left font-medium text-text-tertiary">Day rate</th>}
                {(data.people[0]?.days ?? []).map((day) => (
                  <th key={day.date} className="w-7 px-0.5 py-1 text-center font-normal text-text-tertiary">
                    <div>{weekdayLetter(day.date)}</div>
                    <div>{Number(day.date.slice(8, 10))}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.people.map((person) => (
                <tr key={person.party_id} data-testid="crew-availability-row">
                  <td className="sticky left-0 bg-bg-primary py-0.5 pr-3">
                    <Link href={`/contacts/${person.party_id}`} className="text-text-primary hover:underline">
                      {person.name}
                    </Link>
                    {person.roles.length > 0 && <span className="ml-1 text-text-tertiary">{person.roles.join(', ')}</span>}
                  </td>
                  {data.rates_visible && (
                    <td data-testid="crew-availability-rate" className="pr-2 text-text-secondary">
                      {person.rate ? formatMoney(person.rate, currency) : '—'}
                    </td>
                  )}
                  {person.days.map((day) => (
                    <td key={day.date} className="px-0.5 py-0.5">
                      <button
                        type="button"
                        data-testid="crew-availability-cell"
                        data-state={day.state}
                        data-clash={day.clash ? 'true' : undefined}
                        aria-label={`${person.name}, ${dayLabel(day.date, true)}: ${STATE_WORD[day.state]}`}
                        onClick={() => setOpen({ person, day })}
                        className={cn(
                          'block h-5 w-6 rounded-sm',
                          STATE_CELL[day.state],
                          day.clash && 'ring-2 ring-status-warning',
                        )}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && (
        <div data-testid="crew-availability-detail" role="status" className="mt-3 rounded-md border border-border bg-bg-secondary p-2 text-xs">
          <p className="font-medium text-text-primary">
            {open.person.name}, {dayLabel(open.day.date, true)}: {STATE_WORD[open.day.state]}
          </p>
          <ul className="mt-1 space-y-0.5 text-text-secondary">
            {open.day.entries.map((entry, index) => (
              <li key={index}>
                {entry.kind === 'unavailable'
                  ? `Not available${entry.reason ? `: ${entry.reason}` : ''}`
                  : `${entry.status === 'booked' ? 'Booked' : 'Scheduled'} on ${entry.project_name}, strip “${entry.strip_name}”`}
              </li>
            ))}
            {open.day.clash && <li className="text-status-warning">More than one thing on this day: check with them.</li>}
          </ul>
        </div>
      )}
    </div>
  )
}
