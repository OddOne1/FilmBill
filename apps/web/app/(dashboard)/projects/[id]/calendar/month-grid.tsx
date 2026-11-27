'use client'

import * as React from 'react'
import { AlertTriangle, Plane } from 'lucide-react'
import type { CalendarStrip, CrewStripCounts } from '@/types'
import { cn } from '@/lib/utils'
import {
  addDays,
  datesBetween,
  diffDays,
  isWeekend,
  isoWeek,
  layoutWeek,
  maxDate,
  minDate,
  type IsoDate,
} from '@/lib/calendar-dates'
import { dateAtPointer, dayByDate, stripName, typeColour, typeLabel, typeText } from './shared'
import { crewCountsText } from './strip-crew'

/**
 * The month grid: weeks as rows, Monday first, strips drawn as bars across
 * the days they cover, wrapping from one week row to the next like a
 * multi-day event.
 *
 * Pointer interactions, all of them also reachable without dragging (the
 * Add strip button, the side panel's date fields and buttons):
 *   - drag across days → create a strip over them; click a day, shift-click
 *     another → the same;
 *   - drag a strip's body → move it; drag its left or right edge → resize.
 *
 * Drags are followed on `window`, so leaving the grid mid-drag still ends
 * it; the date under the pointer is read from the element there (every
 * cell and every day of a strip carries `data-date`).
 */

/** Lanes drawn per week before a day shows "+N". */
export const MAX_LANES = 3

type Drag =
  | { kind: 'create'; from: IsoDate; to: IsoDate; moved: boolean }
  | { kind: 'move'; strip: CalendarStrip; grab: IsoDate | null; at: IsoDate | null; moved: boolean }
  | { kind: 'start' | 'end'; strip: CalendarStrip; at: IsoDate | null; moved: boolean }

export interface MonthGridProps {
  month: string
  weeks: IsoDate[][]
  strips: CalendarStrip[]
  today: IsoDate
  projectStart: IsoDate
  projectEnd: IsoDate | null
  canEdit: boolean
  selectedStripId: string | null
  pick: { from: IsoDate; to: IsoDate } | null
  onPickRange: (from: IsoDate, to: IsoDate) => void
  onPickDay: (date: IsoDate, extend: boolean) => void
  /** `date` is the day of the strip the click landed on, when known. */
  onOpenStrip: (strip: CalendarStrip, date: IsoDate | null) => void
  onMove: (strip: CalendarStrip, start: IsoDate) => void
  onResize: (strip: CalendarStrip, start: IsoDate, end: IsoDate) => void
  onShowDay: (date: IsoDate) => void
  /** B2c-1: crew per strip id, from `GET /projects/{id}/crew` — the badge
   *  "4 booked · 2 scheduled" and a marker when somebody has a clash. */
  crewCounts?: Record<string, CrewStripCounts>
}

export function MonthGrid(props: MonthGridProps) {
  const { month, weeks, strips, canEdit } = props
  const [drag, setDrag] = React.useState<Drag | null>(null)
  const dragRef = React.useRef<Drag | null>(null)
  // The click that follows a finished drag is not a click on what it ended
  // over: without this, dropping a moved strip would also open its panel.
  const suppressClick = React.useRef(false)
  const handlers = React.useRef(props)
  handlers.current = props

  const update = (next: Drag | null) => {
    dragRef.current = next
    setDrag(next)
  }

  React.useEffect(() => {
    if (!drag) return
    const onMove = (event: PointerEvent) => {
      const current = dragRef.current
      const date = dateAtPointer(event)
      if (!current || !date) return
      if (current.kind === 'create') {
        if (date !== current.to) update({ ...current, to: date, moved: current.moved || date !== current.from })
      } else if (current.kind === 'move') {
        const grab = current.grab ?? date
        if (date !== current.at || current.grab === null) {
          update({ ...current, grab, at: date, moved: current.moved || date !== grab })
        }
      } else if (date !== current.at) {
        update({ ...current, at: date, moved: true })
      }
    }
    const onUp = () => {
      const current = dragRef.current
      update(null)
      if (!current || !current.moved) return
      suppressClick.current = true
      const p = handlers.current
      if (current.kind === 'create') {
        p.onPickRange(minDate(current.from, current.to), maxDate(current.from, current.to))
      } else if (current.kind === 'move' && current.grab && current.at) {
        const offset = diffDays(current.grab, current.at)
        if (offset !== 0) p.onMove(current.strip, addDays(current.strip.start_date, offset))
      } else if (current.kind === 'start' && current.at) {
        p.onResize(current.strip, minDate(current.at, current.strip.end_date), current.strip.end_date)
      } else if (current.kind === 'end' && current.at) {
        p.onResize(current.strip, current.strip.start_date, maxDate(current.at, current.strip.start_date))
      }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    // Re-subscribing on every drag change would drop events between renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag !== null])

  /** The strips as they would be if the current drag were dropped now. */
  const drawn = React.useMemo(() => {
    if (!drag || drag.kind === 'create' || !drag.moved || !drag.at) return strips
    return strips.map((strip) => {
      if (strip.id !== drag.strip.id) return strip
      if (drag.kind === 'move' && drag.grab) {
        const offset = diffDays(drag.grab, drag.at!)
        return { ...strip, start_date: addDays(strip.start_date, offset), end_date: addDays(strip.end_date, offset) }
      }
      if (drag.kind === 'start') return { ...strip, start_date: minDate(drag.at!, strip.end_date) }
      if (drag.kind === 'end') return { ...strip, end_date: maxDate(drag.at!, strip.start_date) }
      return strip
    })
  }, [drag, strips])

  const pickFrom = drag?.kind === 'create' ? minDate(drag.from, drag.to) : props.pick?.from
  const pickTo = drag?.kind === 'create' ? maxDate(drag.from, drag.to) : props.pick?.to
  const draggingId = drag && drag.kind !== 'create' && drag.moved ? drag.strip.id : null

  return (
    <div data-testid="month-grid" role="grid" aria-label="Calendar" className="select-none overflow-hidden rounded-md border border-border">
      <div role="row" className="grid grid-cols-[2.75rem_repeat(7,minmax(0,1fr))] border-b border-border bg-bg-secondary text-[11px] font-medium text-text-tertiary">
        <div role="columnheader" className="px-1 py-1.5 text-center">Wk</div>
        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((name, index) => (
          <div key={name} role="columnheader" className={cn('px-2 py-1.5', index >= 5 && 'text-text-tertiary')}>
            {name}
          </div>
        ))}
      </div>
      {weeks.map((week) => {
        const segments = layoutWeek(drawn, week)
        const hidden = new Map<string, number>()
        for (const segment of segments) {
          if (segment.lane < MAX_LANES) continue
          for (let col = segment.startCol; col <= segment.endCol; col += 1) {
            hidden.set(week[col], (hidden.get(week[col]) ?? 0) + 1)
          }
        }
        return (
          <div
            key={week[0]}
            role="row"
            data-testid="calendar-week"
            className="grid grid-cols-[2.75rem_repeat(7,minmax(0,1fr))] border-b border-border last:border-b-0"
            style={{ gridTemplateRows: `1.625rem repeat(${MAX_LANES}, 1.5rem) 1.375rem` }}
          >
            <div
              className="flex items-start justify-center border-r border-border pt-1.5 text-[11px] tabular-nums text-text-tertiary"
              style={{ gridColumn: 1, gridRow: '1 / -1' }}
            >
              {isoWeek(week[0])}
            </div>
            {week.map((date, col) => {
              const inMonth = date.slice(0, 7) === month
              const picked = pickFrom && pickTo && date >= pickFrom && date <= pickTo
              const more = hidden.get(date) ?? 0
              return (
                <div
                  key={date}
                  role="gridcell"
                  data-testid="calendar-day"
                  data-date={date}
                  aria-label={date}
                  aria-selected={picked ? true : undefined}
                  onPointerDown={(event) => {
                    if (!canEdit || event.button !== 0) return
                    update({ kind: 'create', from: date, to: date, moved: false })
                  }}
                  onClick={(event) => {
                    if (suppressClick.current) {
                      suppressClick.current = false
                      return
                    }
                    if (canEdit) props.onPickDay(date, event.shiftKey)
                  }}
                  className={cn(
                    'relative border-r border-border last:border-r-0',
                    isWeekend(date) && 'bg-bg-secondary',
                    picked && 'bg-accent-muted',
                    canEdit && 'cursor-cell',
                  )}
                  style={{ gridColumn: col + 2, gridRow: '1 / -1' }}
                >
                  <div className="flex items-center gap-1 px-1.5 pt-1 text-xs">
                    <span
                      data-testid={date === props.today ? 'calendar-today' : undefined}
                      className={cn(
                        'inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1 tabular-nums',
                        inMonth ? 'text-text-primary' : 'text-text-tertiary',
                        date === props.today && 'bg-accent font-semibold text-white',
                      )}
                    >
                      {Number(date.slice(8, 10))}
                    </span>
                    {date === props.projectStart && (
                      <span data-testid="project-start-mark" className="rounded bg-bg-tertiary px-1 text-[10px] text-text-secondary">
                        Start
                      </span>
                    )}
                    {props.projectEnd && date === props.projectEnd && (
                      <span data-testid="project-end-mark" className="rounded bg-bg-tertiary px-1 text-[10px] text-text-secondary">
                        End
                      </span>
                    )}
                  </div>
                  {more > 0 && (
                    <button
                      type="button"
                      data-testid="day-more"
                      onPointerDown={(event) => event.stopPropagation()}
                      onClick={(event) => {
                        event.stopPropagation()
                        props.onShowDay(date)
                      }}
                      className="absolute bottom-0.5 left-1.5 rounded px-1 text-[11px] font-medium text-text-secondary hover:bg-bg-tertiary"
                    >
                      +{more}
                    </button>
                  )}
                </div>
              )
            })}
            {segments
              .filter((segment) => segment.lane < MAX_LANES)
              .map((segment) => (
                <StripBar
                  key={`${segment.strip.id}-${week[0]}`}
                  segment={segment}
                  week={week}
                  canEdit={canEdit && segment.strip.kind === 'strip'}
                  selected={props.selectedStripId === segment.strip.id}
                  crew={props.crewCounts?.[segment.strip.id] ?? null}
                  dragging={draggingId === segment.strip.id}
                  onPointerDown={(event, kind) => {
                    if (event.button !== 0) return
                    event.stopPropagation()
                    if (!canEdit || segment.strip.kind !== 'strip') return
                    const original = strips.find((strip) => strip.id === segment.strip.id) ?? segment.strip
                    if (kind === 'move') {
                      update({ kind: 'move', strip: original, grab: dateAtPointer(event), at: null, moved: false })
                    } else {
                      update({ kind, strip: original, at: null, moved: false })
                    }
                  }}
                  onOpen={(date) => {
                    if (suppressClick.current) {
                      suppressClick.current = false
                      return
                    }
                    const original = strips.find((strip) => strip.id === segment.strip.id) ?? segment.strip
                    props.onOpenStrip(original, date)
                  }}
                />
              ))}
          </div>
        )
      })}
    </div>
  )
}

function StripBar({
  segment,
  week,
  canEdit,
  selected,
  crew,
  dragging,
  onPointerDown,
  onOpen,
}: {
  segment: ReturnType<typeof layoutWeek<CalendarStrip>>[number]
  week: IsoDate[]
  canEdit: boolean
  selected: boolean
  crew: CrewStripCounts | null
  dragging: boolean
  onPointerDown: (event: React.PointerEvent, kind: 'move' | 'start' | 'end') => void
  onOpen: (date: IsoDate | null) => void
}) {
  const { strip, startCol, endCol, lane, continuesLeft, continuesRight } = segment
  const days = dayByDate(strip)
  const dates = datesBetween(week[startCol], week[endCol])
  const name = stripName(strip)
  const where = strip.location ? ` · ${strip.location}` : ''
  const showLabel = !continuesLeft || startCol === 0

  return (
    <div
      role="button"
      tabIndex={0}
      data-testid="calendar-strip"
      data-strip-id={strip.id}
      data-kind={strip.kind}
      data-lane={lane}
      aria-label={`${name}${where}, ${strip.start_date} to ${strip.end_date}`}
      aria-pressed={selected}
      onPointerDown={(event) => onPointerDown(event, 'move')}
      onClick={(event) => {
        event.stopPropagation()
        const target = event.target instanceof Element ? event.target.closest('[data-date]') : null
        onOpen(target?.getAttribute('data-date') ?? null)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen(null)
        }
      }}
      title={`${name}${where}`}
      className={cn(
        'relative z-10 mx-0.5 my-[1px] flex overflow-hidden text-[11px] font-medium shadow-sm outline-none',
        'focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1',
        !continuesLeft && 'rounded-l-md',
        !continuesRight && 'rounded-r-md',
        selected && 'ring-2 ring-text-primary',
        dragging && 'opacity-70',
        canEdit && 'cursor-grab active:cursor-grabbing',
      )}
      style={{ gridColumn: `${startCol + 2} / ${endCol + 3}`, gridRow: lane + 2 }}
    >
      {dates.map((date) => {
        const day = days.get(date)
        const type = day?.type ?? strip.type
        return (
          <span
            key={date}
            data-testid="strip-day"
            data-date={date}
            data-type={day ? day.type : 'gap'}
            data-override={day?.is_override ? 'true' : undefined}
            className={cn(
              'relative flex-1',
              day ? typeColour(type) : 'bg-bg-tertiary opacity-60',
              day?.is_override &&
                'bg-[repeating-linear-gradient(135deg,rgba(255,255,255,0.35)_0_3px,transparent_3px_7px)]',
            )}
          >
            {day?.type === 'travel' && (
              <Plane aria-label="Travel day" className="absolute right-0.5 top-1/2 h-3 w-3 -translate-y-1/2 text-white" />
            )}
          </span>
        )
      })}
      {showLabel && (
        <span className={cn('pointer-events-none absolute inset-y-0 left-0 right-4 flex items-center truncate px-1.5 [text-shadow:0_1px_2px_rgba(0,0,0,0.45)]', typeText(strip.type))}>
          <span className="truncate">
            {name}
            {strip.location && <span className="font-normal opacity-90">{where}</span>}
          </span>
          {strip.kind === 'day' && <span className="sr-only"> ({typeLabel(strip.type)} day)</span>}
          {crew && (crew.booked > 0 || crew.scheduled > 0) && (
            <span data-testid="strip-crew-badge" className="ml-1.5 shrink-0 rounded bg-black/25 px-1 font-normal">
              {crewCountsText(crew)}
            </span>
          )}
          {crew && crew.with_clashes > 0 && (
            <AlertTriangle data-testid="strip-crew-clash-marker" aria-label="Crew clash" className="ml-1 h-3 w-3 shrink-0" />
          )}
        </span>
      )}
      {canEdit && !continuesLeft && (
        <span
          data-testid="strip-handle-start"
          aria-hidden="true"
          onPointerDown={(event) => {
            event.stopPropagation()
            onPointerDown(event, 'start')
          }}
          className="absolute inset-y-0 left-0 w-1.5 cursor-ew-resize bg-black/10 hover:bg-black/25"
        />
      )}
      {canEdit && !continuesRight && (
        <span
          data-testid="strip-handle-end"
          aria-hidden="true"
          onPointerDown={(event) => {
            event.stopPropagation()
            onPointerDown(event, 'end')
          }}
          className="absolute inset-y-0 right-0 w-1.5 cursor-ew-resize bg-black/10 hover:bg-black/25"
        />
      )}
    </div>
  )
}
