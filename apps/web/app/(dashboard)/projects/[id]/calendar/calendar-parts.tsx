'use client'

import * as React from 'react'
import { fillFrom } from '@/lib/date-fields'
import { X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { dayLabel, monthLabel, type IsoDate } from '@/lib/calendar-dates'
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
        <Input
          aria-label="New strip from"
          type="date"
          value={from}
          onChange={(event) => {
            const next = event.target.value
            setFrom(next)
            // The last day follows: a strip of one day until it is moved on.
            if (next && (!to || to < next)) setTo(next)
          }}
          className="h-9"
        />
      </label>
      <label className="text-xs text-text-secondary">
        To
        <Input aria-label="New strip to" type="date" value={to} min={from || undefined} onFocus={fillFrom(to, from, setTo)} onChange={(event) => setTo(event.target.value)} className="h-9" />
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

// ─── The whole project at a glance: only the booked days ────────────────────

/**
 * Every date that has a planned day, and only those, as small blocks in date
 * order — grouped by month with a gap between months, the short date under
 * each block. A date with days of several types (two strips on one date)
 * shows each type's colour. Click a block and the calendar moves to its
 * month (Mathias, 2026-11-25: glance at the whole production without
 * paging through months).
 *
 * Drawing only — it counts nothing; the counts are the API's.
 */
export function BookedDays({
  typesByDate,
  currentMonth,
  onPick,
}: {
  typesByDate: Map<IsoDate, string[]>
  currentMonth: string
  onPick: (date: IsoDate) => void
}) {
  const dates = Array.from(typesByDate.keys()).sort()
  if (dates.length === 0) return null
  const months: { month: string; dates: IsoDate[] }[] = []
  for (const date of dates) {
    const month = date.slice(0, 7)
    const last = months[months.length - 1]
    if (last && last.month === month) last.dates.push(date)
    else months.push({ month, dates: [date] })
  }
  return (
    <nav data-testid="booked-days" aria-label="Booked days" className="rounded-md border border-border p-3">
      <p className="mb-2 text-xs font-medium text-text-secondary">Booked days</p>
      <div className="flex flex-wrap items-end gap-x-5 gap-y-3">
        {months.map(({ month, dates: inMonth }) => (
          <div key={month} data-testid="booked-month" data-month={month}>
            <p className={cn('mb-1 text-[11px]', month === currentMonth ? 'font-semibold text-text-primary' : 'text-text-tertiary')}>
              {monthLabel(month, 'short')}
            </p>
            <div className="flex gap-0.5">
              {inMonth.map((date) => {
                const types = Array.from(new Set(typesByDate.get(date) ?? []))
                return (
                  <button
                    key={date}
                    type="button"
                    data-testid="booked-day"
                    data-date={date}
                    aria-label={`${dayLabel(date, true)}: ${types.map(typeLabel).join(', ')}. Show in the calendar`}
                    title={`${dayLabel(date, true)} · ${types.map(typeLabel).join(', ')}`}
                    onClick={() => onPick(date)}
                    className="group flex w-8 flex-col items-center gap-0.5 rounded px-0.5 pb-0.5 pt-1 hover:bg-bg-secondary"
                  >
                    <span className="flex h-3.5 w-full flex-col overflow-hidden rounded-sm">
                      {types.map((type) => (
                        <span key={type} className={cn('w-full flex-1', typeColour(type))} />
                      ))}
                    </span>
                    <span className="text-[10px] tabular-nums leading-none text-text-secondary group-hover:text-text-primary">
                      {`${date.slice(8, 10)}.${date.slice(5, 7)}.`}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </nav>
  )
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
