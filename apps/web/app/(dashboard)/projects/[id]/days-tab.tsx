'use client'

import * as React from 'react'
import { Loader2, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import type {
  Project,
  ProjectDay,
  ProjectDayList,
  ProjectDayRangeResponse,
  ProjectDayUpdate,
} from '@/types'
import { DAY_TYPE_LABELS, DAY_TYPES } from '../projects-chrome'

/**
 * A project's production days: add a range, edit a day in place, see the
 * month at a glance.
 *
 * The counts at the top are the API's (`counts`), because the Budget's
 * `days:<type>` globals are counted by the server from the same rows and the
 * two must never disagree.
 */

const TYPE_COLOURS: Record<string, string> = {
  prep: 'bg-sky-500/70',
  shoot: 'bg-accent',
  travel: 'bg-amber-500/70',
  wrap: 'bg-violet-500/70',
  post: 'bg-emerald-500/70',
  office: 'bg-slate-400/70',
  off: 'bg-bg-tertiary',
}

export function DaysTab({
  project,
  canEdit,
  onChanged,
}: {
  project: Project
  canEdit: boolean
  onChanged: () => void
}) {
  const [list, setList] = React.useState<ProjectDayList | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [message, setMessage] = React.useState<string | null>(null)

  const load = React.useCallback(async () => {
    try {
      setList(await api.get<ProjectDayList>(`/projects/${project.id}/days`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the days')
    }
  }, [project.id])

  React.useEffect(() => {
    void load()
  }, [load])

  const changed = async () => {
    await load()
    onChanged()
  }

  const days = (list?.days ?? []).filter((day) => day.is_active)

  return (
    <div className="mt-5">
      <p data-testid="day-counts" className="text-sm text-text-secondary">
        {list === null
          ? ''
          : DAY_TYPES.filter((type) => list.counts[type])
              .map((type) => `${list.counts[type]} ${DAY_TYPE_LABELS[type].toLowerCase()}`)
              .join(' · ') || 'No days planned yet.'}
      </p>

      {canEdit && (
        <RangeForm
          project={project}
          onDone={(detail) => {
            setMessage(detail)
            void changed()
          }}
          onError={setError}
        />
      )}

      {message && (
        <p data-testid="days-range-result" className="mt-3 text-sm text-text-secondary">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      {list === null ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading days…
        </div>
      ) : (
        <>
          <MonthStrip project={project} days={days} />
          {days.length > 0 && (
            <table className="mt-6 w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-text-tertiary">
                  <th className="py-2 pr-3 font-medium">Date</th>
                  <th className="py-2 pr-3 font-medium">Type</th>
                  <th className="py-2 pr-3 font-medium">Location</th>
                  <th className="py-2 pr-3 font-medium">Call</th>
                  <th className="py-2 pr-3 font-medium">Wrap</th>
                  <th className="py-2 pr-3 font-medium">Notes</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {days.map((day) => (
                  <DayRow
                    key={day.id}
                    day={day}
                    canEdit={canEdit}
                    onSaved={() => void changed()}
                    onError={setError}
                  />
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}

function RangeForm({
  project,
  onDone,
  onError,
}: {
  project: Project
  onDone: (detail: string) => void
  onError: (message: string | null) => void
}) {
  const [from, setFrom] = React.useState('')
  const [to, setTo] = React.useState('')
  const [type, setType] = React.useState('shoot')
  const [skipWeekends, setSkipWeekends] = React.useState(false)
  const [busy, setBusy] = React.useState(false)

  const missing = !from ? 'Pick the first day.' : !to ? 'Pick the last day — the same date for one day.' : null

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (missing) return
    setBusy(true)
    onError(null)
    try {
      const result = await api.post<ProjectDayRangeResponse>(
        `/projects/${project.id}/days/range`,
        { from, to, type, skip_weekends: skipWeekends },
      )
      onDone(result.detail)
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : 'The days could not be added')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      data-testid="days-range-form"
      onSubmit={submit}
      className="mt-4 flex flex-wrap items-end gap-3 rounded-md border border-border bg-bg-secondary p-3"
    >
      <label className="text-xs text-text-secondary">
        From
        <Input aria-label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
      </label>
      <label className="text-xs text-text-secondary">
        To
        <Input aria-label="To" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
      </label>
      <label className="text-xs text-text-secondary">
        Type
        <select
          aria-label="Day type"
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="mt-1 block rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
        >
          {DAY_TYPES.map((value) => (
            <option key={value} value={value}>
              {DAY_TYPE_LABELS[value]}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5 pb-2 text-sm text-text-secondary">
        <input
          type="checkbox"
          checked={skipWeekends}
          onChange={(e) => setSkipWeekends(e.target.checked)}
        />
        Skip weekends
      </label>
      <button
        type="submit"
        disabled={busy || missing !== null}
        className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        Add days
      </button>
      {missing && <span className="pb-2 text-xs text-text-tertiary">{missing}</span>}
    </form>
  )
}

/** One editable day. Each field saves on its own when it loses focus, so
 *  a slip of one day is one PATCH and one audit event. */
function DayRow({
  day,
  canEdit,
  onSaved,
  onError,
}: {
  day: ProjectDay
  canEdit: boolean
  onSaved: () => void
  onError: (message: string | null) => void
}) {
  const save = async (body: ProjectDayUpdate) => {
    onError(null)
    try {
      await api.patch(`/projects/${day.project_id}/days/${day.id}`, body)
      onSaved()
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : 'The day could not be saved')
    }
  }

  const remove = async () => {
    onError(null)
    try {
      await api.post(`/projects/${day.project_id}/days/${day.id}/remove`)
      onSaved()
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : 'The day could not be removed')
    }
  }

  if (!canEdit) {
    return (
      <tr data-testid="day-row" className="border-b border-border/60">
        <td className="py-1.5 pr-3">{day.date}</td>
        <td className="py-1.5 pr-3">{DAY_TYPE_LABELS[day.type] ?? day.type}</td>
        <td className="py-1.5 pr-3">{day.location ?? ''}</td>
        <td className="py-1.5 pr-3">{day.call_time?.slice(0, 5) ?? ''}</td>
        <td className="py-1.5 pr-3">{day.wrap_time?.slice(0, 5) ?? ''}</td>
        <td className="py-1.5 pr-3">{day.notes ?? ''}</td>
        <td />
      </tr>
    )
  }

  /** Saves a text field on blur, only when it changed. */
  const textCell = (
    field: 'location' | 'notes' | 'call_time' | 'wrap_time' | 'date',
    label: string,
    type = 'text',
  ) => {
    const stored = (day[field] ?? '') as string
    const shown = field.endsWith('_time') ? stored.slice(0, 5) : stored
    return (
      <Input
        aria-label={`${label} ${day.date}`}
        type={type}
        defaultValue={shown}
        key={`${field}-${day.updated_at}`}
        onBlur={(event) => {
          const next = event.target.value
          if (next === shown) return
          void save({ [field]: next === '' ? null : next } as ProjectDayUpdate)
        }}
        className="h-8"
      />
    )
  }

  return (
    <tr data-testid="day-row" className="border-b border-border/60">
      <td className="py-1 pr-2">{textCell('date', 'Date', 'date')}</td>
      <td className="py-1 pr-2">
        <select
          aria-label={`Type ${day.date}`}
          value={day.type}
          onChange={(e) => void save({ type: e.target.value as ProjectDayUpdate['type'] })}
          className="h-8 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
        >
          {DAY_TYPES.map((value) => (
            <option key={value} value={value}>
              {DAY_TYPE_LABELS[value]}
            </option>
          ))}
        </select>
      </td>
      <td className="py-1 pr-2">{textCell('location', 'Location')}</td>
      <td className="py-1 pr-2">{textCell('call_time', 'Call', 'time')}</td>
      <td className="py-1 pr-2">{textCell('wrap_time', 'Wrap', 'time')}</td>
      <td className="py-1 pr-2">{textCell('notes', 'Notes')}</td>
      <td className="py-1 text-right">
        <button
          type="button"
          aria-label={`Remove ${day.date}`}
          onClick={() => void remove()}
          className="rounded p-1 text-text-tertiary hover:text-danger"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </td>
    </tr>
  )
}

/** `YYYY-MM` of every month from the project's start (or its first day) to
 *  its end (or its last day), at most twelve. Calendar arithmetic on dates,
 *  in UTC — nothing to do with money. */
function monthsToShow(project: Project, days: ProjectDay[]): string[] {
  const dates = days.map((day) => day.date).sort()
  const first = [project.start_date, dates[0]].filter(Boolean).sort()[0] as string
  const last = [project.end_date ?? project.start_date, dates[dates.length - 1]]
    .filter(Boolean)
    .sort()
    .reverse()[0] as string
  const months: string[] = []
  let year = Number(first.slice(0, 4))
  let month = Number(first.slice(5, 7))
  const end = last.slice(0, 7)
  while (months.length < 12) {
    const key = `${year}-${String(month).padStart(2, '0')}`
    months.push(key)
    if (key >= end) break
    month += 1
    if (month > 12) {
      month = 1
      year += 1
    }
  }
  return months
}

function MonthStrip({ project, days }: { project: Project; days: ProjectDay[] }) {
  const byDate = new Map<string, ProjectDay[]>()
  for (const day of days) {
    byDate.set(day.date, [...(byDate.get(day.date) ?? []), day])
  }

  return (
    <div data-testid="month-strip" className="mt-6 space-y-3">
      {monthsToShow(project, days).map((key) => {
        const year = Number(key.slice(0, 4))
        const month = Number(key.slice(5, 7))
        const length = new Date(Date.UTC(year, month, 0)).getUTCDate()
        const label = new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString(undefined, {
          month: 'long',
          year: 'numeric',
          timeZone: 'UTC',
        })
        return (
          <div key={key}>
            <p className="text-xs font-medium text-text-secondary">{label}</p>
            <div className="mt-1 flex flex-wrap gap-0.5">
              {Array.from({ length }, (_, index) => {
                const date = `${key}-${String(index + 1).padStart(2, '0')}`
                const planned = byDate.get(date) ?? []
                const weekday = new Date(`${date}T00:00:00Z`).getUTCDay()
                const weekend = weekday === 0 || weekday === 6
                return (
                  <div
                    key={date}
                    data-testid={planned.length ? 'month-day-planned' : undefined}
                    data-date={date}
                    title={
                      planned.length
                        ? `${date}: ${planned.map((day) => DAY_TYPE_LABELS[day.type] ?? day.type).join(', ')}`
                        : date
                    }
                    className={`flex h-7 w-7 flex-col items-center justify-center rounded text-[10px] ${
                      weekend ? 'text-text-tertiary' : 'text-text-secondary'
                    } ${planned.length ? '' : 'bg-bg-secondary'}`}
                  >
                    <span>{index + 1}</span>
                    <span className="flex gap-px">
                      {planned.map((day) => (
                        <span
                          key={day.id}
                          className={`h-1 w-2 rounded-sm ${TYPE_COLOURS[day.type] ?? 'bg-text-tertiary'}`}
                        />
                      ))}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
      <p className="flex flex-wrap gap-3 text-[11px] text-text-tertiary">
        {DAY_TYPES.map((type) => (
          <span key={type} className="inline-flex items-center gap-1">
            <span className={`h-1.5 w-3 rounded-sm ${TYPE_COLOURS[type]}`} />
            {DAY_TYPE_LABELS[type]}
          </span>
        ))}
      </p>
    </div>
  )
}
