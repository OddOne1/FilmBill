'use client'

import * as React from 'react'
import { fillFrom } from '@/lib/date-fields'
import { ChevronDown, ChevronRight, Copy, Plane, Trash2, X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { cn } from '@/lib/utils'
import { datesBetween, dayLabel, weekdayLetter, type IsoDate } from '@/lib/calendar-dates'
import type { BlockChange, BlockUpdate, CalendarDay, CalendarStrip, ProjectDayUpdate } from '@/types'
import { DAY_TYPES } from '../../projects-chrome'
import { countsSentence, dayByDate, stripName, typeColour, typeLabel, typeText } from './shared'
import { DayLocation, StripLocationSection } from './strip-locations'

/**
 * The side panel for one strip: its fields, its days as chips, and the
 * buttons that act on a selection of days — every action the grid offers by
 * dragging is here too, as a field or a button (tests and some users never
 * drag).
 *
 * Every number shown is the API's: the strip's own counts come back with
 * each response (`strip.counts`), the project's in `counts`.
 */

export interface StripActions {
  /** PATCH/PUT/POST a strip action and apply the API's answer. */
  run: (call: () => Promise<BlockChange>, failure: string) => Promise<BlockChange | null>
  /** Resize with the confirm rule (the tab owns the dialog). */
  resize: (strip: CalendarStrip, start: IsoDate, end: IsoDate) => Promise<void>
  /** Something changed that only a fresh calendar shows (a day's details). */
  reload: () => Promise<void>
  onRemoved: (change: BlockChange) => void
  onError: (message: string | null) => void
}

export function StripPanel({
  projectId,
  strip,
  focusDate = null,
  canEdit,
  actions,
  onClose,
  children,
}: {
  projectId: string
  strip: CalendarStrip
  /** The day the click on the strip landed on; it opens selected. */
  focusDate?: IsoDate | null
  canEdit: boolean
  actions: StripActions
  onClose: () => void
  /** B2c-1/B2c-2: the strip's Crew and Gear sections, drawn at the panel's end. */
  children?: React.ReactNode
}) {
  const base = `/projects/${projectId}/blocks/${strip.id}`
  const [selected, setSelected] = React.useState<Set<IsoDate>>(new Set())
  const [anchor, setAnchor] = React.useState<IsoDate | null>(null)
  const [overrideType, setOverrideType] = React.useState('wrap')
  const [moveTo, setMoveTo] = React.useState('')
  const [duplicateTo, setDuplicateTo] = React.useState('')
  const [confirmRemove, setConfirmRemove] = React.useState(false)
  const days = dayByDate(strip)
  const dates = datesBetween(strip.start_date, strip.end_date)
  const isStrip = strip.kind === 'strip'

  React.useEffect(() => {
    const start = focusDate && focusDate >= strip.start_date && focusDate <= strip.end_date ? focusDate : null
    setSelected(start ? new Set([start]) : new Set())
    setAnchor(start)
    // Only when another strip or another clicked day opens the panel — not
    // on every refresh of the same strip, which would drop a selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strip.id, focusDate])

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const patch = (body: BlockUpdate) =>
    actions.run(() => api.patch<BlockChange>(base, body), 'The strip could not be saved')

  const setTypes = (entries: { date: IsoDate; type: string | null }[]) =>
    actions.run(() => api.put<BlockChange>(`${base}/days`, { days: entries }), 'The days could not be changed')

  const chooseChip = (date: IsoDate, event: React.MouseEvent) => {
    if (event.shiftKey && anchor) {
      const [from, to] = anchor <= date ? [anchor, date] : [date, anchor]
      setSelected(new Set(datesBetween(from, to)))
      return
    }
    if (event.metaKey || event.ctrlKey) {
      const next = new Set(selected)
      if (next.has(date)) next.delete(date)
      else next.add(date)
      setSelected(next)
      setAnchor(date)
      return
    }
    setSelected(new Set([date]))
    setAnchor(date)
  }

  const markSelected = (type: string | null) =>
    setTypes(Array.from(selected).sort().map((date) => ({ date, type })))

  const single = selected.size === 1 ? days.get(Array.from(selected)[0]) ?? null : null
  const noSelection = selected.size === 0

  return (
    <aside
      data-testid="strip-panel"
      aria-label={`Strip ${stripName(strip)}`}
      className="w-full shrink-0 rounded-md border border-border bg-bg-primary p-4 text-sm lg:w-[26rem]"
    >
      <div className="flex items-start gap-2">
        <span className={cn('mt-1 h-3 w-3 shrink-0 rounded-sm', typeColour(strip.type))} />
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-medium text-text-primary">{stripName(strip)}</h2>
          <p className="text-xs text-text-secondary">
            {dayLabel(strip.start_date, true)} – {dayLabel(strip.end_date, true)}
          </p>
          <p data-testid="strip-counts" className="mt-0.5 text-xs text-text-tertiary">
            {countsSentence(strip.counts ?? {}, DAY_TYPES) || 'No days in this strip.'}
          </p>
        </div>
        <button type="button" aria-label="Close panel" onClick={onClose} className="rounded p-1 text-text-tertiary hover:text-text-primary">
          <X className="h-4 w-4" />
        </button>
      </div>

      {!isStrip && (
        <div className="mt-3 rounded bg-bg-secondary px-2 py-1.5 text-xs text-text-secondary">
          <p>This day was added on its own, outside any strip. Edit it below.</p>
          {canEdit && strip.days[0] && (
            <button
              type="button"
              onClick={async () => {
                actions.onError(null)
                try {
                  await api.post(`/projects/${projectId}/days/${strip.days[0].id}/remove`)
                  await actions.reload()
                  onClose()
                } catch (err) {
                  actions.onError(err instanceof ApiError ? err.detail : 'The day could not be removed')
                }
              }}
              className="mt-1 inline-flex items-center gap-1 text-status-error hover:underline"
            >
              <Trash2 className="h-3 w-3" /> Remove this day
            </button>
          )}
        </div>
      )}

      {isStrip && (
        <div className="mt-4 grid grid-cols-2 gap-3">
          <Field label="Label" className="col-span-2">
            <TextInput
              label="Strip label"
              value={strip.label ?? ''}
              canEdit={canEdit}
              onSave={(value) => patch({ label: value || null })}
            />
          </Field>
          <Field label="Type">
            {canEdit ? (
              <select
                aria-label="Strip type"
                value={strip.type}
                onChange={(event) => void patch({ type: event.target.value as BlockUpdate['type'] })}
                className="h-9 w-full rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
              >
                {DAY_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {typeLabel(type)}
                  </option>
                ))}
              </select>
            ) : (
              <span>{typeLabel(strip.type)}</span>
            )}
          </Field>
          <Field label="Start">
            <TextInput
              label="Strip start"
              type="date"
              value={strip.start_date}
              canEdit={canEdit}
              onSave={(value) => value && actions.resize(strip, value, strip.end_date)}
            />
          </Field>
          <Field label="End">
            <TextInput
              label="Strip end"
              type="date"
              value={strip.end_date}
              canEdit={canEdit}
              onSave={(value) => value && actions.resize(strip, strip.start_date, value)}
            />
          </Field>
          <Field label="Default start time">
            <TextInput
              label="Default start time"
              type="time"
              value={strip.default_call_time?.slice(0, 5) ?? ''}
              canEdit={canEdit}
              onSave={(value) => patch({ default_call_time: value || null })}
            />
          </Field>
          <Field label="Default end time">
            <TextInput
              label="Default end time"
              type="time"
              value={strip.default_wrap_time?.slice(0, 5) ?? ''}
              canEdit={canEdit}
              onSave={(value) => patch({ default_wrap_time: value || null })}
            />
          </Field>
          <Field label="Notes" className="col-span-2">
            <TextInput
              label="Strip notes"
              value={strip.notes ?? ''}
              canEdit={canEdit}
              onSave={(value) => patch({ notes: value || null })}
            />
          </Field>
        </div>
      )}

      {isStrip && (
        <>
          <StripLocationSection strip={strip} base={base} canEdit={canEdit} actions={actions} selected={selected} />
        </>
      )}

      <div className="mt-4">
        <p className="text-xs font-medium text-text-secondary">Days</p>
        {canEdit && isStrip && (
          <p className="text-[11px] text-text-tertiary">
            Click a day to select it, shift-click for a range, {isMac() ? '⌘' : 'Ctrl'}-click to add one.
          </p>
        )}
        <div data-testid="day-chips" role="listbox" aria-multiselectable="true" aria-label="Days of the strip" className="mt-1.5 flex flex-wrap gap-1">
          {dates.map((date) => {
            const day = days.get(date)
            const isSelected = selected.has(date)
            return (
              <button
                key={date}
                type="button"
                role="option"
                aria-selected={isSelected}
                aria-label={`${dayLabel(date, true)}, ${day ? typeLabel(day.type) : 'no day'}`}
                data-testid="day-chip"
                data-date={date}
                data-type={day?.type ?? 'gap'}
                onClick={(event) => chooseChip(date, event)}
                className={cn(
                  'flex h-11 w-10 flex-col items-center justify-center rounded text-[11px] leading-tight',
                  day ? cn(typeColour(day.type), typeText(day.type)) : 'border border-dashed border-border text-text-tertiary',
                  day?.is_override &&
                    'bg-[repeating-linear-gradient(135deg,rgba(255,255,255,0.35)_0_3px,transparent_3px_7px)]',
                  isSelected && 'ring-2 ring-text-primary ring-offset-1',
                )}
              >
                <span className="opacity-80">{weekdayLetter(date)}</span>
                <span className="font-medium tabular-nums">{Number(date.slice(8, 10))}</span>
                {day?.type === 'travel' && <Plane className="h-2.5 w-2.5" aria-hidden="true" />}
              </button>
            )
          })}
        </div>

        {canEdit && isStrip && (
          <div className="mt-3 space-y-2">
            <div className="flex flex-wrap gap-1.5">
              <ActionButton disabled={noSelection} onClick={() => void markSelected('travel')}>
                Mark as travel
              </ActionButton>
              <ActionButton disabled={noSelection} onClick={() => void markSelected('off')}>
                Mark as off
              </ActionButton>
              <ActionButton disabled={noSelection} onClick={() => void markSelected(null)}>
                Back to {typeLabel(strip.type).toLowerCase()}
              </ActionButton>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <select
                aria-label="Override type"
                value={overrideType}
                onChange={(event) => setOverrideType(event.target.value)}
                className="h-8 rounded-md border border-border bg-bg-primary px-2 text-xs text-text-primary"
              >
                {DAY_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {typeLabel(type)}
                  </option>
                ))}
              </select>
              <ActionButton disabled={noSelection} onClick={() => void markSelected(overrideType)}>
                Set selected
              </ActionButton>
            </div>
            {noSelection && (
              <p data-testid="selection-hint" className="text-[11px] text-text-tertiary">
                Select one or more days above to change their type.
              </p>
            )}
            <div className="flex flex-wrap gap-1.5">
              <ActionButton onClick={() => void setTypes([{ date: strip.start_date, type: 'travel' }])}>
                First day = travel
              </ActionButton>
              <ActionButton onClick={() => void setTypes([{ date: strip.end_date, type: 'travel' }])}>
                Last day = travel
              </ActionButton>
            </div>
          </div>
        )}

        {!single && strip.days.length > 0 && (
          <p data-testid="day-edit-hint" className="mt-2 text-[11px] text-text-tertiary">
            Click one day to edit its type, location, start and end time and notes.
          </p>
        )}
        {single && (
          <DayDetails
            key={`${single.id}-${single.updated_at}`}
            projectId={projectId}
            day={single}
            strip={strip}
            canEdit={canEdit}
            actions={actions}
          />
        )}
        {!isStrip && strip.days[0] && !single && (
          <DayDetails
            key={`${strip.days[0].id}-${strip.days[0].updated_at}`}
            projectId={projectId}
            day={strip.days[0]}
            strip={strip}
            canEdit={canEdit}
            actions={actions}
          />
        )}
      </div>

      {canEdit && isStrip && (
        <div className="mt-5 space-y-2 border-t border-border pt-4">
          <form
            className="flex flex-wrap items-end gap-1.5"
            onSubmit={(event) => {
              event.preventDefault()
              if (!moveTo) return
              void actions.run(() => api.post<BlockChange>(`${base}/move`, { start_date: moveTo }), 'The strip could not be moved')
            }}
          >
            <label className="text-xs text-text-secondary">
              Move to start on
              <Input aria-label="Move to start on" type="date" value={moveTo} onFocus={fillFrom(moveTo, strip.start_date, setMoveTo)} onChange={(event) => setMoveTo(event.target.value)} className="h-8" />
            </label>
            <ActionButton type="submit" disabled={!moveTo}>
              Move
            </ActionButton>
          </form>
          <form
            className="flex flex-wrap items-end gap-1.5"
            onSubmit={(event) => {
              event.preventDefault()
              if (!duplicateTo) return
              void actions.run(
                () => api.post<BlockChange>(`${base}/duplicate`, { start_date: duplicateTo }),
                'The strip could not be duplicated',
              )
            }}
          >
            <label className="text-xs text-text-secondary">
              Duplicate to start on
              <Input aria-label="Duplicate to start on" type="date" value={duplicateTo} onFocus={fillFrom(duplicateTo, strip.end_date, setDuplicateTo)} onChange={(event) => setDuplicateTo(event.target.value)} className="h-8" />
            </label>
            <ActionButton type="submit" disabled={!duplicateTo}>
              <Copy className="h-3 w-3" /> Duplicate
            </ActionButton>
          </form>
          {(!moveTo || !duplicateTo) && (
            <p className="text-[11px] text-text-tertiary">Pick a date to move or duplicate the strip to.</p>
          )}
          <button
            type="button"
            onClick={() => setConfirmRemove(true)}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-status-error hover:bg-bg-secondary"
          >
            <Trash2 className="h-3 w-3" /> Remove strip
          </button>
          <ConfirmDialog
            open={confirmRemove}
            onOpenChange={setConfirmRemove}
            title={`Remove ${stripName(strip)}?`}
            description={`Its ${strip.days.length} day${strip.days.length === 1 ? '' : 's'} leave the plan and stop counting in the budget. They are kept in the history, not deleted.`}
            confirmLabel="Remove strip"
            variant="danger"
            onConfirm={async () => {
              const change = await actions.run(
                () => api.post<BlockChange>(`${base}/remove`),
                'The strip could not be removed',
              )
              if (change) actions.onRemoved(change)
            }}
          />
        </div>
      )}
      {children}
    </aside>
  )
}

function isMac(): boolean {
  return typeof navigator !== 'undefined' && /Mac/.test(navigator.platform)
}

function Field({ label, className, children }: { label: string; className?: string; children: React.ReactNode }) {
  return (
    <div className={cn('min-w-0 text-xs text-text-secondary', className)}>
      <span className="mb-0.5 block">{label}</span>
      {children}
    </div>
  )
}

/** Saves on blur (or Enter), only when the value changed.
 *
 *  A time field ALSO saves shortly after a complete time is entered: in
 *  Chrome, Tab inside a time field moves between its hour and minute parts
 *  rather than leaving it, so a time that saved only on blur could sit
 *  unsaved while the person had moved on (found in the B2a preview). Date
 *  fields do not, because a half-typed year is a valid date and each one
 *  would resize the strip. */
function TextInput({
  label,
  value,
  canEdit,
  onSave,
  type = 'text',
  placeholder,
}: {
  label: string
  value: string
  canEdit: boolean
  onSave: (value: string) => unknown
  type?: string
  placeholder?: string
}) {
  const saved = React.useRef(value)
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  React.useEffect(() => {
    saved.current = value
  }, [value])
  React.useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])
  const commit = (next: string) => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    if (next === saved.current) return
    saved.current = next
    void onSave(next)
  }
  if (!canEdit) {
    return <span className="block truncate text-sm text-text-primary">{value || placeholder || '—'}</span>
  }
  return (
    <Input
      aria-label={label}
      type={type}
      defaultValue={value}
      placeholder={placeholder}
      key={value}
      onChange={(event) => {
        if (type !== 'time') return
        const next = event.target.value
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => commit(next), 800)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') (event.target as HTMLInputElement).blur()
      }}
      onBlur={(event) => commit(event.target.value)}
      className="h-9"
    />
  )
}

function ActionButton({
  children,
  disabled,
  onClick,
  type = 'button',
}: {
  children: React.ReactNode
  disabled?: boolean
  onClick?: () => void
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded-md border border-border bg-bg-secondary px-2 py-1 text-xs text-text-primary hover:bg-bg-tertiary disabled:opacity-50"
    >
      {children}
    </button>
  )
}

/** One day's own location, call, wrap and notes. Empty fields show the
 *  strip's default as a placeholder — what the day shows until it has its
 *  own. */
function DayDetails({
  projectId,
  day,
  strip,
  canEdit,
  actions,
}: {
  projectId: string
  day: CalendarDay
  strip: CalendarStrip
  canEdit: boolean
  actions: StripActions
}) {
  const [open, setOpen] = React.useState(true)
  const isStrip = strip.kind === 'strip'
  /** A strip's day changes type through the strip (one audited action that
   *  knows the main type); a day outside any strip through its own PATCH. */
  const setType = (type: string) =>
    isStrip
      ? actions.run(
          () => api.put<BlockChange>(`/projects/${projectId}/blocks/${strip.id}/days`, {
            days: [{ date: day.date, type: type === strip.type ? null : type }],
          }),
          'The day could not be changed',
        )
      : save({ type: type as ProjectDayUpdate['type'] })
  const save = async (body: ProjectDayUpdate) => {
    actions.onError(null)
    try {
      await api.patch(`/projects/${projectId}/days/${day.id}`, body)
      await actions.reload()
    } catch (err) {
      actions.onError(err instanceof ApiError ? err.detail : 'The day could not be saved')
    }
  }
  return (
    <div data-testid="day-details" className="mt-3 rounded-md border border-border p-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1 text-xs font-medium text-text-secondary"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        {dayLabel(day.date, true)} · {typeLabel(day.type)}
      </button>
      {open && (
        <div className="mt-2 grid grid-cols-2 gap-2">
          <Field label="Type" className="col-span-2">
            {canEdit ? (
              <select
                aria-label={`Type ${day.date}`}
                value={day.type}
                onChange={(event) => void setType(event.target.value)}
                className="h-9 w-full rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
              >
                {DAY_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {typeLabel(type)}
                  </option>
                ))}
              </select>
            ) : (
              <span className="block text-sm text-text-primary">{typeLabel(day.type)}</span>
            )}
          </Field>
          <Field label="Location" className="col-span-2">
            <DayLocation projectId={projectId} day={day} canEdit={canEdit} actions={actions} />
          </Field>
          <Field label="Start time">
            <TextInput
              label={`Start time ${day.date}`}
              type="time"
              value={day.call_time?.slice(0, 5) ?? ''}
              placeholder={strip.default_call_time?.slice(0, 5)}
              canEdit={canEdit}
              onSave={(value) => save({ call_time: value || null })}
            />
          </Field>
          <Field label="End time">
            <TextInput
              label={`End time ${day.date}`}
              type="time"
              value={day.wrap_time?.slice(0, 5) ?? ''}
              placeholder={strip.default_wrap_time?.slice(0, 5)}
              canEdit={canEdit}
              onSave={(value) => save({ wrap_time: value || null })}
            />
          </Field>
          <Field label="Notes" className="col-span-2">
            <TextInput
              label={`Notes ${day.date}`}
              value={day.notes ?? ''}
              canEdit={canEdit}
              onSave={(value) => save({ notes: value || null })}
            />
          </Field>
          {!day.call_time && day.effective_call_time ? (
            <p className="col-span-2 text-[11px] text-text-tertiary">Empty fields show the strip&rsquo;s default.</p>
          ) : null}
        </div>
      )}
    </div>
  )
}
