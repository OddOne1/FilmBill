'use client'

import * as React from 'react'
import { ChevronLeft, ChevronRight, Loader2, Plus } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { cn } from '@/lib/utils'
import {
  maxDate,
  minDate,
  monthLabel,
  monthGridFor,
  shiftMonth,
  todayIso,
  type IsoDate,
} from '@/lib/calendar-dates'
import type { BlockChange, BlockCreate, CalendarStrip, Project, ProjectCalendar } from '@/types'
import { DAY_TYPES } from '../projects-chrome'
import { BookedDays, CreateStripForm, DayStripsList } from './calendar/calendar-parts'
import { MonthGrid } from './calendar/month-grid'
import { StripPanel, type StripActions } from './calendar/strip-panel'
import { countsSentence, typeColour, typeLabel } from './calendar/shared'

/**
 * The Days tab: the project calendar (B2a).
 *
 * A month grid with the project's strips drawn across their days — always
 * six weeks (`monthGrid`, UI-1), so it never changes height — the booked
 * days of the whole project in a compact strip directly under it and at its
 * width (click one to jump to its month), and a side
 * panel for the strip that is open — with the day that was clicked open for
 * editing. Every day is edited here: its type, location, start and end time
 * and notes. (The old per-day table was dropped at Mathias's request,
 * 2026-11-25: the calendar is the one place days are edited.)
 *
 * **Every count on this screen is the API's** (`counts`, `strip.counts`).
 * The budget's `days:<type>` globals are counted by the server from the
 * same rows with the distinct-date rule, and a screen that counted for
 * itself would disagree with them the first time two strips overlapped.
 */

interface PendingResize {
  strip: CalendarStrip
  start: IsoDate
  end: IsoDate
  detail: string
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
  const [calendar, setCalendar] = React.useState<ProjectCalendar | null>(null)
  const [month, setMonth] = React.useState<string | null>(null)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  /** The day a click on a strip landed on: the panel opens with it. */
  const [focusDate, setFocusDate] = React.useState<IsoDate | null>(null)
  const [pick, setPick] = React.useState<{ from: IsoDate; to: IsoDate } | null>(null)
  const [creating, setCreating] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [dayOpen, setDayOpen] = React.useState<IsoDate | null>(null)
  const [pending, setPending] = React.useState<PendingResize | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [message, setMessage] = React.useState<string | null>(null)
  const today = React.useMemo(() => todayIso(), [])
  const gridTop = React.useRef<HTMLDivElement>(null)

  const load = React.useCallback(async () => {
    try {
      const fetched = await api.get<ProjectCalendar>(`/projects/${project.id}/calendar`)
      setCalendar(fetched)
      setError(null)
      setMonth((current) => current ?? initialMonth(fetched, today))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the calendar')
    }
  }, [project.id, today])

  React.useEffect(() => {
    void load()
  }, [load])

  /** Put the API's answer on screen: the strip as it now is, the project's
   *  counts as the API counted them. Nothing is recomputed here. */
  const apply = React.useCallback((change: BlockChange) => {
    setCalendar((current) => {
      if (!current) return current
      let strips = current.strips
      if (change.strip) {
        const next = change.strip
        strips = next.is_active
          ? strips.some((strip) => strip.id === next.id)
            ? strips.map((strip) => (strip.id === next.id ? next : strip))
            : [...strips, next]
          : strips.filter((strip) => strip.id !== next.id)
      }
      return { ...current, strips, counts: change.counts ?? current.counts }
    })
    setMessage(change.detail)
  }, [])

  const run = React.useCallback<StripActions['run']>(
    async (call, failure) => {
      setError(null)
      setBusy(true)
      try {
        const change = await call()
        apply(change)
        onChanged()
        return change
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : failure)
        return null
      } finally {
        setBusy(false)
      }
    },
    [apply, onChanged],
  )

  const resize = React.useCallback(
    async (strip: CalendarStrip, start: IsoDate, end: IsoDate, confirm = false) => {
      setError(null)
      try {
        const change = await api.post<BlockChange>(`/projects/${project.id}/blocks/${strip.id}/resize`, {
          start_date: start,
          end_date: end,
          confirm,
        })
        if (change.needs_confirm) {
          setPending({ strip, start, end, detail: change.detail })
          return
        }
        apply(change)
        onChanged()
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : 'The strip could not be resized')
      }
    },
    [apply, onChanged, project.id],
  )

  const move = (strip: CalendarStrip, start: IsoDate) =>
    void run(
      () => api.post<BlockChange>(`/projects/${project.id}/blocks/${strip.id}/move`, { start_date: start }),
      'The strip could not be moved',
    )

  const create = async (body: BlockCreate) => {
    const change = await run(
      () => api.post<BlockChange>(`/projects/${project.id}/blocks`, body),
      'The strip could not be created',
    )
    if (change?.strip) {
      setCreating(false)
      setPick(null)
      setSelectedId(change.strip.id)
    }
  }

  const strips = React.useMemo(() => calendar?.strips ?? [], [calendar])
  const selected = strips.find((strip) => strip.id === selectedId) ?? null
  const shownMonth = month ?? today.slice(0, 7)

  const actions: StripActions = {
    run,
    resize: (strip, start, end) => resize(strip, start, end),
    reload: load,
    onRemoved: () => setSelectedId(null),
    onError: setError,
  }

  const typesByDate = React.useMemo(() => {
    const map = new Map<IsoDate, string[]>()
    for (const strip of strips) for (const day of strip.days) map.set(day.date, [...(map.get(day.date) ?? []), day.type])
    return map
  }, [strips])

  return (
    <div className="mt-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <p data-testid="day-counts" className="text-sm font-medium text-text-primary">
          {calendar === null ? '' : countsSentence(calendar.counts, DAY_TYPES) || 'No days planned yet.'}
        </p>
        <p className="flex flex-wrap gap-3 text-[11px] text-text-tertiary">
          {DAY_TYPES.map((type) => (
            <span key={type} className="inline-flex items-center gap-1">
              <span className={cn('h-2 w-3 rounded-sm', typeColour(type))} />
              {typeLabel(type)}
            </span>
          ))}
        </p>
      </div>

      {calendar === null ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
          {error ? null : (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Loading the calendar…
            </>
          )}
        </div>
      ) : (
        <>
          <div ref={gridTop} className="mt-3 flex flex-wrap items-center gap-2">
            <button type="button" aria-label="Previous month" onClick={() => setMonth(shiftMonth(shownMonth, -1))} className="rounded-md border border-border p-1.5 hover:bg-bg-secondary">
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button type="button" onClick={() => setMonth(today.slice(0, 7))} className="rounded-md border border-border px-2.5 py-1 text-sm hover:bg-bg-secondary">
              Today
            </button>
            <button type="button" aria-label="Next month" onClick={() => setMonth(shiftMonth(shownMonth, 1))} className="rounded-md border border-border p-1.5 hover:bg-bg-secondary">
              <ChevronRight className="h-4 w-4" />
            </button>
            <h2 data-testid="calendar-month" className="ml-1 text-base font-semibold text-text-primary">
              {monthLabel(shownMonth)}
            </h2>
            <div className="ml-auto flex items-center gap-2">
              {canEdit ? (
                <button
                  type="button"
                  onClick={() => {
                    setCreating(true)
                    setPick(null)
                  }}
                  className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
                >
                  <Plus className="h-4 w-4" /> Add strip
                </button>
              ) : (
                <span data-testid="calendar-read-only" className="text-xs text-text-tertiary">
                  You can look at the calendar but not change it — that needs the project edit permission.
                </span>
              )}
            </div>
          </div>

          {canEdit && (creating || pick) && (
            <div className="mt-3">
              <CreateStripForm
                initial={pick}
                busy={busy}
                onCreate={(body) => void create(body)}
                onCancel={() => {
                  setCreating(false)
                  setPick(null)
                }}
              />
            </div>
          )}
          {canEdit && !creating && !pick && (
            <p className="mt-2 text-[11px] text-text-tertiary">
              Drag across days to add a strip, or click a day and shift-click another. Click a day in a strip to edit it. Drag a strip to move it, its edges to change its dates.
            </p>
          )}

          {message && (
            <p data-testid="calendar-message" className="mt-2 text-sm text-text-secondary">
              {message}
            </p>
          )}
          {error && (
            <p role="alert" className="mt-2 text-sm text-status-error">
              {error}
            </p>
          )}

          <div className="mt-3 flex flex-col gap-4 lg:flex-row lg:items-start">
            {/* The calendar's column: the grid, and the Booked days strip
                under it at the grid's own width (UI-1) — not under the side
                panel as well. */}
            <div data-testid="calendar-column" className="relative min-w-0 flex-1">
              <MonthGrid
                month={shownMonth}
                weeks={monthGridFor(shownMonth)}
                strips={strips}
                today={today}
                projectStart={project.start_date}
                projectEnd={project.end_date ?? null}
                canEdit={canEdit}
                selectedStripId={selectedId}
                pick={pick}
                onPickRange={(from, to) => {
                  setPick({ from, to })
                  setSelectedId(null)
                }}
                onPickDay={(date, extend) => {
                  setSelectedId(null)
                  setPick((current) =>
                    extend && current
                      ? { from: minDate(current.from, date), to: maxDate(current.to, date) }
                      : { from: date, to: date },
                  )
                }}
                onOpenStrip={(strip, date) => {
                  setSelectedId(strip.id)
                  setFocusDate(date)
                  setDayOpen(null)
                }}
                onMove={move}
                onResize={(strip, start, end) => void resize(strip, start, end)}
                onShowDay={setDayOpen}
              />
              <div className="mt-3 min-w-0">
                <BookedDays
                  typesByDate={typesByDate}
                  currentMonth={shownMonth}
                  onPick={(date) => {
                    setMonth(date.slice(0, 7))
                    gridTop.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
                  }}
                />
              </div>
              {dayOpen && (
                <div className="absolute left-1/2 top-12 z-20 w-72 -translate-x-1/2">
                  <DayStripsList
                    date={dayOpen}
                    strips={strips.filter((strip) => strip.start_date <= dayOpen && strip.end_date >= dayOpen)}
                    onOpen={(strip) => {
                      setSelectedId(strip.id)
                      setFocusDate(dayOpen)
                      setDayOpen(null)
                    }}
                    onClose={() => setDayOpen(null)}
                  />
                </div>
              )}
            </div>
            {selected && (
              <StripPanel
                projectId={project.id}
                strip={selected}
                focusDate={focusDate}
                canEdit={canEdit}
                actions={actions}
                onClose={() => setSelectedId(null)}
              />
            )}
          </div>

        </>
      )}

      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null)
        }}
        title="Take out days that have details?"
        description={pending?.detail}
        confirmLabel="Take them out"
        variant="danger"
        onConfirm={async () => {
          if (pending) await resize(pending.strip, pending.start, pending.end, true)
        }}
      />
    </div>
  )
}

/** Today's month while the project is running; otherwise the month of its
 *  first planned day, or of its start. */
function initialMonth(calendar: ProjectCalendar, today: IsoDate): string {
  const first = calendar.strips.reduce<IsoDate | null>((acc, strip) => (acc === null ? strip.start_date : minDate(acc, strip.start_date)), null)
  const last = calendar.strips.reduce<IsoDate | null>((acc, strip) => (acc === null ? strip.end_date : maxDate(acc, strip.end_date)), null)
  if (first && last && today >= first && today <= last) return today.slice(0, 7)
  return (first ?? calendar.project_start).slice(0, 7)
}

