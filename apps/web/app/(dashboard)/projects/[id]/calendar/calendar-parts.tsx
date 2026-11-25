'use client'

import * as React from 'react'
import { X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { dayLabel, monthLabel, shiftMonth, type IsoDate } from '@/lib/calendar-dates'
import type { BlockCreate, CalendarStrip } from '@/types'
import { DAY_TYPES } from '../../projects-chrome'
import { stripName, typeColour, typeLabel } from './shared'

// ─── Create a strip ─────────────────────────────────────────────────────────

/**
 * The small form that a drag across days, a click (and shift-click) on a
 * day, or the Add strip button opens. The dates are fields too, so the
 * whole thing works from the keyboard.
 */
export function CreateStripForm({
  initial,
  busy,
  onCreate,
  onCancel,
}: {
  initial: { from: IsoDate; to: IsoDate } | null
  busy: boolean
  onCreate: (body: BlockCreate) => void
  onCancel: () => void
}) {
  const [from, setFrom] = React.useState(initial?.from ?? '')
  const [to, setTo] = React.useState(initial?.to ?? '')
  const [type, setType] = React.useState<BlockCreate['type']>('shoot')
  const [label, setLabel] = React.useState('')
  const [location, setLocation] = React.useState('')
  const [weekendsOff, setWeekendsOff] = React.useState(false)
  const first = React.useRef<HTMLSelectElement>(null)

  React.useEffect(() => {
    setFrom(initial?.from ?? '')
    setTo(initial?.to ?? '')
  }, [initial?.from, initial?.to])

  React.useEffect(() => {
    first.current?.focus()
  }, [])

  const missing = !from
    ? 'Pick the first day.'
    : !to
      ? 'Pick the last day — the same date for one day.'
      : to < from
        ? 'The last day is before the first.'
        : null

  return (
    <form
      data-testid="create-strip-form"
      aria-label="New strip"
      onSubmit={(event) => {
        event.preventDefault()
        if (missing) return
        onCreate({
          type,
          label: label.trim() || null,
          location: location.trim() || null,
          start_date: from,
          end_date: to,
          weekends: weekendsOff ? 'off' : 'same',
        })
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onCancel()
      }}
      className="flex flex-wrap items-end gap-3 rounded-md border border-accent bg-bg-secondary p-3 shadow-sm"
    >
      <label className="text-xs text-text-secondary">
        Type
        <select
          ref={first}
          aria-label="New strip type"
          value={type}
          onChange={(event) => setType(event.target.value as BlockCreate['type'])}
          className="mt-1 block h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
        >
          {DAY_TYPES.map((value) => (
            <option key={value} value={value}>
              {typeLabel(value)}
            </option>
          ))}
        </select>
      </label>
      <label className="text-xs text-text-secondary">
        Label
        <Input aria-label="New strip label" placeholder="e.g. BTS shoot" value={label} onChange={(event) => setLabel(event.target.value)} className="h-9 w-44" />
      </label>
      <label className="text-xs text-text-secondary">
        Location
        <Input aria-label="New strip location" value={location} onChange={(event) => setLocation(event.target.value)} className="h-9 w-40" />
      </label>
      <label className="text-xs text-text-secondary">
        From
        <Input aria-label="New strip from" type="date" value={from} onChange={(event) => setFrom(event.target.value)} className="h-9" />
      </label>
      <label className="text-xs text-text-secondary">
        To
        <Input aria-label="New strip to" type="date" value={to} onChange={(event) => setTo(event.target.value)} className="h-9" />
      </label>
      <label className="flex items-center gap-1.5 pb-2 text-sm text-text-secondary">
        <input type="checkbox" checked={weekendsOff} onChange={(event) => setWeekendsOff(event.target.checked)} />
        Weekends off
      </label>
      <button
        type="submit"
        disabled={busy || missing !== null}
        className="h-9 rounded-md bg-accent px-3 text-sm font-medium text-white disabled:opacity-50"
      >
        Create
      </button>
      <button type="button" onClick={onCancel} className="h-9 rounded-md px-2 text-sm text-text-secondary hover:text-text-primary">
        Cancel
      </button>
      {missing && <span className="basis-full text-xs text-text-tertiary">{missing}</span>}
    </form>
  )
}

// ─── The whole project at a glance ──────────────────────────────────────────

/** One small bar per month from the project's first to its last day; each
 *  date tinted by the types planned on it. Click a month to jump there.
 *  Drawing only — it counts nothing. */
export function ProjectOverview({
  months,
  current,
  typesByDate,
  onPick,
}: {
  months: string[]
  current: string
  typesByDate: Map<IsoDate, string[]>
  onPick: (month: string) => void
}) {
  return (
    <div data-testid="project-overview" className="flex gap-1.5 overflow-x-auto pb-1">
      {months.map((month) => {
        const year = Number(month.slice(0, 4))
        const index = Number(month.slice(5, 7))
        const length = new Date(Date.UTC(year, index, 0)).getUTCDate()
        return (
          <button
            key={month}
            type="button"
            data-testid="overview-month"
            data-month={month}
            aria-current={month === current ? 'date' : undefined}
            aria-label={`Show ${monthLabel(month)}`}
            onClick={() => onPick(month)}
            className={cn(
              'min-w-[7.5rem] shrink-0 rounded-md border px-2 py-1.5 text-left',
              month === current ? 'border-accent bg-bg-secondary' : 'border-border hover:bg-bg-secondary',
            )}
          >
            <span className="block text-[11px] font-medium text-text-secondary">{monthLabel(month, 'short')}</span>
            <span className="mt-1 flex gap-px">
              {Array.from({ length }, (_, offset) => {
                const date = `${month}-${String(offset + 1).padStart(2, '0')}`
                const types = typesByDate.get(date) ?? []
                return (
                  <span
                    key={date}
                    data-date={types.length ? date : undefined}
                    className={cn('h-3 flex-1 rounded-[1px]', types.length ? typeColour(types[0]) : 'bg-bg-tertiary')}
                  />
                )
              })}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/** The months from `first` to `last`, at most 24. */
export function monthsBetween(first: string, last: string): string[] {
  const months: string[] = []
  for (let month = first; month <= last && months.length < 24; month = shiftMonth(month, 1)) months.push(month)
  return months
}

// ─── "+N": every strip on one day ───────────────────────────────────────────

export function DayStripsList({
  date,
  strips,
  onOpen,
  onClose,
}: {
  date: IsoDate
  strips: CalendarStrip[]
  onOpen: (strip: CalendarStrip) => void
  onClose: () => void
}) {
  return (
    <div data-testid="day-strips" role="dialog" aria-label={`Strips on ${dayLabel(date, true)}`} className="rounded-md border border-border bg-bg-primary p-3 shadow-lg">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-text-secondary">{dayLabel(date, true)}</p>
        <button type="button" aria-label="Close list" onClick={onClose} className="text-text-tertiary hover:text-text-primary">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <ul className="mt-2 space-y-1">
        {strips.map((strip) => {
          const day = strip.days.find((entry) => entry.date === date)
          return (
            <li key={strip.id}>
              <button
                type="button"
                onClick={() => onOpen(strip)}
                className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-sm hover:bg-bg-secondary"
              >
                <span className={cn('h-2.5 w-2.5 rounded-sm', typeColour(day?.type ?? strip.type))} />
                <span className="flex-1 truncate">{stripName(strip)}</span>
                <span className="text-xs text-text-tertiary">{typeLabel(day?.type ?? strip.type)}</span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
