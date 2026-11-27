'use client'

import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { AlertTriangle, Plus } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { datesBetween, dayLabel, weekdayLetter, type IsoDate } from '@/lib/calendar-dates'
import type {
  CalendarStrip,
  DeviceAvailability,
  GearAvailability,
  GearBooking,
  GearBookingsCreated,
  GearBookingsPreview,
  GearBookingUpdated,
  GearBulkStatusResult,
  GearRemoveResult,
  GearStripCounts,
  GearSuggestion,
  GearWarning,
  ProjectGearStrip,
} from '@/types'
import { DeviceThumb } from '../../../gear/gear-chrome'

/**
 * The strip panel's Gear section (B2c-2), below Crew.
 *
 * The strip's bookings with thumbnail, device, quantity, a status chip
 * (scheduled / booked) and a marker when overbooked. **Add gear** opens a
 * picker that shows, for each device, the strip's numbers in SCOPE's words —
 * **owned · available · booked · scheduled**, the lowest day governing, the
 * day-by-day numbers on hover — all of it the API's
 * (`GET /gear/availability`); nothing is counted here.
 *
 * Choosing a device and a quantity asks the API what that adds
 * (`…/gear/preview`): when the device has `ask` dependencies, a checkbox
 * popup lists them with their pre-calculated quantities (unticked by
 * default, editable) and the `always` ones as "added automatically". A save
 * that comes back with an overbooking opens the overbooking popup: the
 * shortage, who holds the device, the alternatives with their own
 * availability and **Swap**, **Book anyway** with a note, or **Cancel**.
 *
 * Without `projects.edit` it is read-only and says why (17c).
 */

export function gearCountsText(counts: GearStripCounts | null | undefined): string {
  if (!counts) return ''
  const parts: string[] = []
  if (counts.booked) parts.push(`${counts.booked} booked`)
  if (counts.scheduled) parts.push(`${counts.scheduled} scheduled`)
  return parts.join(' · ')
}

/** SCOPE §4.4.7's words, the API's numbers: the lowest day governs. */
export function availabilityLine(device: DeviceAvailability): string {
  return `${device.owned} owned · ${device.min_available} available · ${device.booked} booked · ${device.scheduled} scheduled`
}

/** The hover: one line per day, as the API counted it. */
export function dayBreakdown(device: DeviceAvailability): string {
  return device.days
    .map(
      (day) =>
        `${weekdayLetter(day.date)} ${dayLabel(day.date)}: ${day.available} available (${day.booked} booked, ${day.scheduled} scheduled)`,
    )
    .join('\n')
}

const STATUS_CHIP: Record<string, string> = {
  scheduled: 'bg-status-warning/15 text-status-warning',
  booked: 'bg-status-success/15 text-status-success',
  cancelled: 'bg-bg-tertiary text-text-tertiary',
}

type Act = <T>(call: () => Promise<T>, failure: string) => Promise<T | null>

export function StripGear({
  projectId,
  strip,
  gear,
  canEdit,
  onChanged,
  embedded = false,
}: {
  projectId: string
  strip: CalendarStrip
  /** This strip's entry of `GET /projects/{id}/gear`; null while loading. */
  gear: ProjectGearStrip | null
  canEdit: boolean
  onChanged: () => Promise<void> | void
  /** Inside a card that already names the strip (Project → Gear). */
  embedded?: boolean
}) {
  const [picking, setPicking] = React.useState(false)
  const [openId, setOpenId] = React.useState<string | null>(null)
  const [selected, setSelected] = React.useState<Set<string>>(new Set())
  const [bulkNote, setBulkNote] = React.useState('')
  const [warnings, setWarnings] = React.useState<GearWarning[]>([])
  const [notice, setNotice] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => {
    setSelected(new Set())
    setOpenId(null)
    setWarnings([])
    setNotice(null)
    setPicking(false)
  }, [strip.id])

  const bookings = gear?.bookings ?? []

  const act: Act = async (call, failure) => {
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
        api.post<GearBulkStatusResult>(`/projects/${projectId}/gear/status`, {
          booking_ids: Array.from(selected),
          status: 'booked',
          note: bulkNote.trim() || null,
        }),
      'The gear could not be marked booked. Try again.',
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
      data-testid="strip-gear"
      aria-label={embedded ? `Gear on ${strip.label ?? strip.type}` : 'Gear'}
      className={embedded ? 'mt-2' : 'mt-5 border-t border-border pt-4'}
    >
      <div className="flex items-center gap-2">
        {!embedded && <h3 className="text-sm font-medium text-text-primary">Gear</h3>}
        <span data-testid="strip-gear-counts" className="text-xs text-text-tertiary">
          {gearCountsText(gear?.counts) || (gear ? 'Nothing yet' : '')}
        </span>
        {canEdit && (
          <button
            type="button"
            data-testid="strip-gear-add"
            onClick={() => setPicking(!picking)}
            aria-expanded={picking}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-secondary"
          >
            <Plus className="h-3.5 w-3.5" /> Add gear
          </button>
        )}
      </div>
      {!canEdit && (
        <p data-testid="strip-gear-read-only" className="mt-1 text-xs text-text-tertiary">
          You can see the gear on this strip but not change it — that needs the project edit permission.
        </p>
      )}

      {picking && canEdit && (
        <GearPicker
          projectId={projectId}
          strip={strip}
          onAdded={async (created) => {
            await onChanged()
            setPicking(false)
            setWarnings(created.warnings)
            const [first, ...rest] = created.bookings
            setNotice(
              first
                ? `${first.quantity} × ${first.device_name} added as scheduled${
                    rest.length ? `, with ${rest.map((b) => `${b.quantity} × ${b.device_name}`).join(', ')}` : ''
                  }.`
                : null,
            )
          }}
        />
      )}

      {notice && (
        <p role="status" data-testid="strip-gear-notice" className="mt-2 text-xs text-text-secondary">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-xs text-status-error">
          {error}
        </p>
      )}

      {gear && bookings.length === 0 && !picking && (
        <p className="mt-2 text-xs text-text-secondary">
          {canEdit ? 'No gear on this strip yet. Add gear to plan what goes out on these days.' : 'No gear on this strip yet.'}
        </p>
      )}

      <ul className="mt-2 space-y-1">
        {bookings.map((booking) => (
          <li
            key={booking.id}
            data-testid="strip-gear-row"
            data-status={booking.status}
            className={cn('rounded border border-border/60 px-2 py-1.5', booking.added_via_booking_id && 'ml-4')}
          >
            <div className="flex items-center gap-2">
              {canEdit && (
                <input
                  type="checkbox"
                  aria-label={`Select ${booking.device_name}`}
                  disabled={booking.status !== 'scheduled'}
                  title={booking.status !== 'scheduled' ? 'Only scheduled gear can be marked booked.' : undefined}
                  checked={selected.has(booking.id)}
                  onChange={(event) => {
                    const next = new Set(selected)
                    if (event.target.checked) next.add(booking.id)
                    else next.delete(booking.id)
                    setSelected(next)
                  }}
                />
              )}
              <DeviceThumb url={booking.thumbnail_url} name={booking.device_name} size={24} />
              <button
                type="button"
                onClick={() => setOpenId(openId === booking.id ? null : booking.id)}
                className="min-w-0 flex-1 truncate text-left text-sm text-text-primary hover:underline"
              >
                <span data-testid="strip-gear-quantity">{booking.quantity}</span> × {booking.device_name}
                {booking.added_via_device_name && (
                  <span className="text-text-tertiary"> · with {booking.added_via_device_name}</span>
                )}
              </button>
              {booking.warning && (
                <span
                  data-testid="strip-gear-overbooked"
                  title={booking.warning.message}
                  className="inline-flex items-center gap-0.5 rounded bg-status-error/15 px-1 text-2xs text-status-error"
                >
                  <AlertTriangle className="h-3 w-3" aria-hidden="true" /> overbooked
                </span>
              )}
              <span data-testid="strip-gear-status" className={cn('rounded px-1.5 py-0.5 text-2xs', STATUS_CHIP[booking.status])}>
                {booking.status}
              </span>
            </div>
            {openId === booking.id && (
              <BookingEditor
                projectId={projectId}
                strip={strip}
                booking={booking}
                canEdit={canEdit}
                busy={busy}
                act={act}
                onWarnings={setWarnings}
                onNotice={setNotice}
              />
            )}
          </li>
        ))}
      </ul>

      {canEdit && bookings.some((b) => b.status === 'scheduled') && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            data-testid="strip-gear-bulk-note"
            value={bulkNote}
            onChange={(event) => setBulkNote(event.target.value)}
            placeholder="Confirmed how? e.g. by phone"
            className="h-7 flex-1 rounded-md border border-border bg-bg-secondary px-2 text-xs"
          />
          <button
            type="button"
            data-testid="strip-gear-bulk-book"
            disabled={busy || selected.size === 0}
            onClick={() => void bookSelected()}
            className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
          >
            Mark selected as booked
          </button>
          {selected.size === 0 && (
            <span className="w-full text-2xs text-text-tertiary">Tick the scheduled gear that is confirmed.</span>
          )}
        </div>
      )}

      <OverbookingDialog
        projectId={projectId}
        warnings={warnings}
        onClose={() => setWarnings([])}
        act={act}
        onDone={(message) => {
          setWarnings([])
          if (message) setNotice(message)
        }}
      />
    </section>
  )
}

// ─── One booking ────────────────────────────────────────────────────────────

function BookingEditor({
  projectId,
  strip,
  booking,
  canEdit,
  busy,
  act,
  onWarnings,
  onNotice,
}: {
  projectId: string
  strip: CalendarStrip
  booking: GearBooking
  canEdit: boolean
  busy: boolean
  act: Act
  onWarnings: (warnings: GearWarning[]) => void
  onNotice: (message: string | null) => void
}) {
  const base = `/projects/${projectId}/gear/${booking.id}`
  const dates = datesBetween(strip.start_date, strip.end_date)
  const stripDays = new Map(strip.days.map((day) => [day.date, day]))
  const initial = React.useMemo(() => new Set(booking.dates), [booking.dates])
  const [days, setDays] = React.useState<Set<IsoDate>>(initial)
  const [quantity, setQuantity] = React.useState(String(booking.quantity))
  const [note, setNote] = React.useState('')
  const [asking, setAsking] = React.useState<GearRemoveResult | null>(null)
  React.useEffect(() => setDays(initial), [initial])
  React.useEffect(() => setQuantity(String(booking.quantity)), [booking.quantity])

  const editable = canEdit && booking.status !== 'cancelled'
  const wholeQuantity = /^\d+$/.test(quantity.trim()) && Number(quantity) >= 1

  const patch = async (body: Record<string, unknown>, failure: string) => {
    const result = await act(() => api.patch<GearBookingUpdated>(base, body), failure)
    if (result) {
      onWarnings(result.warnings)
      onNotice(
        result.message ??
          (result.recalculated.length
            ? `Also changed: ${result.recalculated.map((b) => `${b.quantity} × ${b.device_name}`).join(', ')}.`
            : null),
      )
    }
  }
  const status = (wanted: string) =>
    act(() => api.post<GearBooking>(`${base}/status`, { status: wanted, note: note.trim() || null }), 'The status could not be changed. Try again.')
  const remove = async (dependents?: 'cancel' | 'keep') => {
    const result = await act(
      () => api.post<GearRemoveResult>(`${base}/remove`, { note: note.trim() || null, ...(dependents ? { dependents } : {}) }),
      'The booking could not be removed. Try again.',
    )
    if (result && !result.removed) setAsking(result)
    else setAsking(null)
  }

  return (
    <div data-testid="strip-gear-editor" className="mt-2 space-y-2 border-t border-border/60 pt-2 text-xs">
      {booking.warning && (
        <p data-testid="strip-gear-warning" className="flex gap-1.5 rounded border border-status-error/40 bg-status-error/10 px-2 py-1 text-status-error">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          <span>{booking.warning.message}</span>
        </p>
      )}
      {booking.overbooking_note && <p className="text-text-secondary">Booked anyway: {booking.overbooking_note}</p>}
      {booking.status_note && (
        <p className="text-text-secondary">
          {booking.status === 'booked' ? 'Confirmed: ' : 'Note: '}
          {booking.status_note}
          {booking.booked_by_name ? ` (${booking.booked_by_name})` : ''}
        </p>
      )}
      {booking.added_via_device_name && (
        <p className="text-text-tertiary">
          Added with {booking.added_via_device_name}
          {booking.follows_parent ? ` — ${booking.quantity_per_parent} per ${booking.added_via_device_name}, follows its quantity.` : ' — set by hand.'}
        </p>
      )}

      <label className="flex items-center gap-1.5 text-text-secondary">
        Quantity
        <input
          data-testid="strip-gear-quantity-input"
          inputMode="numeric"
          disabled={!editable || busy}
          value={quantity}
          onChange={(event) => setQuantity(event.target.value)}
          className="h-7 w-16 rounded-md border border-border bg-bg-secondary px-2"
        />
        {editable && (
          <button
            type="button"
            data-testid="strip-gear-save-quantity"
            disabled={busy || !wholeQuantity || Number(quantity) === booking.quantity}
            onClick={() => void patch({ quantity: Number(quantity) }, 'The quantity could not be saved. Try again.')}
            className="rounded border border-border px-2 py-0.5 disabled:opacity-50"
          >
            Save quantity
          </button>
        )}
        {editable && !wholeQuantity && <span className="text-2xs text-text-tertiary">Type a whole number of at least 1.</span>}
      </label>

      <fieldset data-testid="strip-gear-days">
        <legend className="text-text-secondary">Days</legend>
        <ul className="mt-1 space-y-0.5">
          {dates.map((date) => {
            const day = stripDays.get(date)
            return (
              <li key={date} className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  aria-label={`Needed on ${dayLabel(date, true)}`}
                  data-testid="strip-gear-day"
                  data-date={date}
                  disabled={!editable}
                  checked={days.has(date)}
                  onChange={(event) => {
                    const next = new Set(days)
                    if (event.target.checked) next.add(date)
                    else next.delete(date)
                    setDays(next)
                  }}
                />
                <span className="w-20 text-text-primary">
                  {weekdayLetter(date)} {dayLabel(date)}
                </span>
                <span className="text-text-tertiary">{day ? day.type : 'no day'}</span>
              </li>
            )
          })}
        </ul>
        {editable && (
          <button
            type="button"
            data-testid="strip-gear-save-days"
            disabled={busy}
            onClick={() =>
              void patch(
                // Only days inside the strip (a shortened strip drops the rest).
                { dates: Array.from(days).filter((d) => d >= strip.start_date && d <= strip.end_date).sort() },
                'The days could not be saved. Try again.',
              )
            }
            className="mt-1 rounded border border-border px-2 py-0.5"
          >
            Save days
          </button>
        )}
      </fieldset>

      {editable && (
        <div className="flex flex-wrap items-center gap-1.5">
          <input
            data-testid="strip-gear-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder={booking.status === 'booked' ? 'Why reopen or cancel?' : 'Confirmed how? e.g. by phone'}
            className="h-7 min-w-[10rem] flex-1 rounded-md border border-border bg-bg-secondary px-2"
          />
          {booking.status === 'scheduled' && (
            <button type="button" data-testid="strip-gear-book" disabled={busy} onClick={() => void status('booked')} className="rounded bg-accent px-2 py-1 font-medium text-white">
              Mark booked
            </button>
          )}
          {booking.status === 'booked' && (
            <>
              <button
                type="button"
                data-testid="strip-gear-reopen"
                disabled={busy || !note.trim()}
                onClick={() => void status('scheduled')}
                className="rounded border border-border px-2 py-1 disabled:opacity-50"
              >
                Reopen
              </button>
              {!note.trim() && <span className="text-2xs text-text-tertiary">Reopening needs a note saying why.</span>}
            </>
          )}
          <button
            type="button"
            data-testid="strip-gear-remove"
            disabled={busy}
            onClick={() => void remove()}
            className="ml-auto text-text-tertiary hover:text-status-error"
          >
            Cancel booking
          </button>
        </div>
      )}
      {asking && (
        <div data-testid="strip-gear-dependents-question" role="alertdialog" className="rounded border border-status-warning/40 bg-status-warning/10 p-2">
          <p className="text-text-primary">{asking.question}</p>
          <div className="mt-1.5 flex gap-2">
            <button type="button" data-testid="strip-gear-dependents-cancel" disabled={busy} onClick={() => void remove('cancel')} className="rounded bg-accent px-2 py-1 font-medium text-white">
              Cancel them too
            </button>
            <button type="button" data-testid="strip-gear-dependents-keep" disabled={busy} onClick={() => void remove('keep')} className="rounded border border-border px-2 py-1">
              Keep them
            </button>
            <button type="button" onClick={() => setAsking(null)} className="ml-auto text-text-tertiary">
              Don’t remove
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── The picker ─────────────────────────────────────────────────────────────

function GearPicker({
  projectId,
  strip,
  onAdded,
}: {
  projectId: string
  strip: CalendarStrip
  onAdded: (created: GearBookingsCreated) => Promise<void> | void
}) {
  const [search, setSearch] = React.useState('')
  const [category, setCategory] = React.useState('')
  const [data, setData] = React.useState<GearAvailability | null>(null)
  const [categories, setCategories] = React.useState<string[]>([])
  const [picked, setPicked] = React.useState<string | null>(null)
  const [quantity, setQuantity] = React.useState('1')
  const [preview, setPreview] = React.useState<GearBookingsPreview | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => {
    const params = new URLSearchParams({ from: strip.start_date, to: strip.end_date })
    if (search.trim()) params.set('q', search.trim())
    if (category) params.set('category', category)
    const handle = setTimeout(() => {
      api
        .get<GearAvailability>(`/gear/availability?${params.toString()}`)
        .then((loaded) => {
          setData(loaded)
          setError(null)
          if (!category && !search.trim()) {
            setCategories(Array.from(new Set(loaded.devices.map((d) => d.category).filter((c): c is string => !!c))).sort())
          }
        })
        .catch((err) => setError(err instanceof ApiError ? err.detail : 'The gear could not be loaded. Try again.'))
    }, 150)
    return () => clearTimeout(handle)
  }, [strip.start_date, strip.end_date, search, category])

  const wholeQuantity = /^\d+$/.test(quantity.trim()) && Number(quantity) >= 1
  const entry = (chosen: { association_id: string; quantity: number | null }[] = []) => ({
    device_id: picked,
    quantity: Number(quantity),
    chosen_dependencies: chosen.map((c) => (c.quantity ? c : { association_id: c.association_id })),
  })

  const create = async (chosen: { association_id: string; quantity: number | null }[] = []) => {
    setBusy(true)
    setError(null)
    try {
      const created = await api.post<GearBookingsCreated>(`/projects/${projectId}/blocks/${strip.id}/gear`, { bookings: [entry(chosen)] })
      setPreview(null)
      setPicked(null)
      setQuantity('1')
      await onAdded(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The gear could not be added. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const add = async () => {
    setBusy(true)
    setError(null)
    try {
      const planned = await api.post<GearBookingsPreview>(`/projects/${projectId}/blocks/${strip.id}/gear/preview`, { bookings: [entry()] })
      if (planned.suggestions.length > 0) {
        setPreview(planned)
        setBusy(false)
        return
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The gear could not be added. Try again.')
      setBusy(false)
      return
    }
    setBusy(false)
    await create()
  }

  return (
    <div data-testid="gear-picker" className="mt-2 rounded-md border border-border bg-bg-secondary p-2 text-xs">
      <div className="flex flex-wrap gap-2">
        <input
          data-testid="gear-picker-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search gear"
          className="h-7 flex-1 rounded-md border border-border bg-bg-primary px-2"
        />
        <select
          data-testid="gear-picker-category"
          aria-label="Category"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          className="h-7 rounded-md border border-border bg-bg-primary px-1"
        >
          <option value="">Any category</option>
          {categories.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>
      <p className="mt-1 text-2xs text-text-tertiary">
        For {dayLabel(strip.start_date, true)} – {dayLabel(strip.end_date, true)}; the lowest day counts. Hover a line for each day.
      </p>

      {error && (
        <p role="alert" className="mt-2 text-status-error">
          {error}
        </p>
      )}
      {data === null ? (
        !error && <p className="mt-2 text-text-secondary">Loading the gear…</p>
      ) : data.devices.length === 0 ? (
        <p data-testid="gear-picker-empty" className="mt-2 text-text-secondary">
          No gear matches. Change the search or the category, or add the device on the Gear page first.
        </p>
      ) : (
        <ul className="mt-2 max-h-64 space-y-0.5 overflow-y-auto">
          {data.devices.map((device) => (
            <li key={device.device_id} data-testid="gear-picker-row" className="flex items-center gap-2">
              <input
                type="radio"
                name={`gear-pick-${strip.id}`}
                aria-label={`Pick ${device.name}`}
                checked={picked === device.device_id}
                onChange={() => setPicked(device.device_id)}
              />
              <DeviceThumb url={device.thumbnail_url} name={device.name} size={24} />
              <span className="w-28 truncate text-text-primary">{device.name}</span>
              <span
                data-testid="gear-picker-availability"
                data-overbooked={device.overbooked ? 'true' : undefined}
                title={dayBreakdown(device)}
                className={cn('flex-1 truncate', device.min_available > 0 ? 'text-status-success' : 'text-status-error')}
              >
                {availabilityLine(device)}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="inline-flex items-center gap-1 text-text-secondary">
          Quantity
          <input
            data-testid="gear-picker-quantity"
            inputMode="numeric"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
            className="h-7 w-14 rounded-md border border-border bg-bg-primary px-2"
          />
        </label>
        <button
          type="button"
          data-testid="gear-picker-add"
          disabled={busy || !picked || !wholeQuantity}
          onClick={() => void add()}
          className="rounded-md bg-accent px-2 py-1 font-medium text-white disabled:opacity-50"
        >
          Add to strip
        </button>
        {!picked && <span className="text-text-tertiary">Pick a device to add. It starts as scheduled.</span>}
        {picked && !wholeQuantity && <span className="text-text-tertiary">Type a whole number of at least 1.</span>}
      </div>

      <DependencyDialog
        preview={preview}
        busy={busy}
        onCancel={() => setPreview(null)}
        onConfirm={(chosen) => void create(chosen)}
      />
    </div>
  )
}

// ─── The dependency popup ───────────────────────────────────────────────────

function DependencyDialog({
  preview,
  busy,
  onCancel,
  onConfirm,
}: {
  preview: GearBookingsPreview | null
  busy: boolean
  onCancel: () => void
  onConfirm: (chosen: { association_id: string; quantity: number | null }[]) => void
}) {
  const [ticked, setTicked] = React.useState<Map<string, string>>(new Map())
  React.useEffect(() => setTicked(new Map()), [preview])
  const parent = preview?.bookings.find((b) => b.via === 'direct')
  const always = preview?.bookings.filter((b) => b.via === 'always') ?? []
  const suggestions: GearSuggestion[] = preview?.suggestions ?? []
  const invalid = Array.from(ticked.values()).some((value) => !/^\d+$/.test(value.trim()) || Number(value) < 1)

  return (
    <Dialog.Root open={preview !== null} onOpenChange={(open) => !open && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content
          data-testid="gear-dependencies-dialog"
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-md -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border bg-bg-secondary p-5 text-xs shadow-xl"
        >
          <Dialog.Title className="text-sm font-semibold text-text-primary">
            {parent ? `${parent.quantity} × ${parent.device_name}: also book?` : 'Also book?'}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-text-tertiary">
            Tick what goes out with it. The quantities are worked out per {parent?.device_name ?? 'device'}; change them if you need more or fewer.
          </Dialog.Description>
          <ul className="mt-3 space-y-1">
            {suggestions.map((suggestion) => {
              const on = ticked.has(suggestion.association_id)
              return (
                <li key={suggestion.association_id} data-testid="gear-dependency-ask" className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`Also book ${suggestion.device_name}`}
                    checked={on}
                    onChange={(event) => {
                      const next = new Map(ticked)
                      if (event.target.checked) next.set(suggestion.association_id, String(suggestion.quantity))
                      else next.delete(suggestion.association_id)
                      setTicked(next)
                    }}
                  />
                  <span className="flex-1 text-text-primary">{suggestion.device_name}</span>
                  <input
                    aria-label={`Quantity of ${suggestion.device_name}`}
                    data-testid="gear-dependency-quantity"
                    inputMode="numeric"
                    disabled={!on}
                    value={on ? ticked.get(suggestion.association_id) : String(suggestion.quantity)}
                    onChange={(event) => setTicked(new Map(ticked).set(suggestion.association_id, event.target.value))}
                    className="h-7 w-14 rounded-md border border-border bg-bg-primary px-2"
                  />
                </li>
              )
            })}
          </ul>
          {always.length > 0 && (
            <div data-testid="gear-dependency-always" className="mt-3 text-text-secondary">
              Added automatically: {always.map((b) => `${b.quantity} × ${b.device_name}`).join(', ')}.
            </div>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" onClick={onCancel} className="rounded border border-border px-3 py-1">
              Cancel
            </button>
            <button
              type="button"
              data-testid="gear-dependencies-confirm"
              disabled={busy || invalid}
              onClick={() =>
                onConfirm(
                  Array.from(ticked.entries()).map(([association_id, value]) => {
                    const suggested = suggestions.find((s) => s.association_id === association_id)
                    return { association_id, quantity: suggested && Number(value) === suggested.quantity ? null : Number(value) }
                  }),
                )
              }
              className="rounded bg-accent px-3 py-1 font-medium text-white disabled:opacity-50"
            >
              Add to strip
            </button>
          </div>
          {invalid && <p className="mt-1 text-right text-2xs text-text-tertiary">Each ticked quantity must be a whole number of at least 1.</p>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

// ─── The overbooking popup ──────────────────────────────────────────────────

export function OverbookingDialog({
  projectId,
  warnings,
  onClose,
  act,
  onDone,
}: {
  projectId: string
  warnings: GearWarning[]
  onClose: () => void
  act: Act
  onDone: (message: string | null) => void
}) {
  const [notes, setNotes] = React.useState<Record<string, string>>({})
  React.useEffect(() => setNotes({}), [warnings])
  const [handled, setHandled] = React.useState<Set<string>>(new Set())
  React.useEffect(() => setHandled(new Set()), [warnings])
  const open = warnings.some((w) => !handled.has(w.booking_id))

  const settle = (bookingId: string, message: string | null) => {
    const next = new Set(handled).add(bookingId)
    setHandled(next)
    if (warnings.every((w) => next.has(w.booking_id))) onDone(message)
  }

  const swap = async (warning: GearWarning, deviceId: string, name: string) => {
    const result = await act(
      () => api.patch<GearBookingUpdated>(`/projects/${projectId}/gear/${warning.booking_id}`, { device_id: deviceId }),
      'The device could not be swapped. Try again.',
    )
    if (result) settle(warning.booking_id, `Swapped ${warning.device_name} for ${name}.`)
  }
  const bookAnyway = async (warning: GearWarning) => {
    const note = (notes[warning.booking_id] ?? '').trim()
    if (note) {
      const result = await act(
        () => api.patch<GearBookingUpdated>(`/projects/${projectId}/gear/${warning.booking_id}`, { overbooking_note: note }),
        'The note could not be saved. Try again.',
      )
      if (!result) return
    }
    settle(warning.booking_id, `${warning.device_name} is booked although it is short by ${warning.shortage}.`)
  }
  const cancel = async (warning: GearWarning) => {
    const result = await act(
      () =>
        api.post<GearRemoveResult>(`/projects/${projectId}/gear/${warning.booking_id}/remove`, {
          note: 'Not booked: overbooked',
          dependents: 'cancel',
        }),
      'The booking could not be cancelled. Try again.',
    )
    if (result) settle(warning.booking_id, `${warning.device_name} was not booked.`)
  }

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content
          data-testid="gear-overbooking-dialog"
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border bg-bg-secondary p-5 text-xs shadow-xl"
        >
          <Dialog.Title className="text-sm font-semibold text-text-primary">Not enough free</Dialog.Title>
          <Dialog.Description className="mt-1 text-text-tertiary">
            The booking is saved and marked overbooked. Swap to an alternative, book anyway, or cancel it.
          </Dialog.Description>
          {warnings
            .filter((warning) => !handled.has(warning.booking_id))
            .map((warning) => (
              <section key={warning.booking_id} data-testid="gear-overbooking" className="mt-4 border-t border-border pt-3">
                <p data-testid="gear-overbooking-shortage" className="font-medium text-status-error">
                  {warning.device_name}: short by {warning.shortage} ({warning.owned} owned, {warning.quantity} wanted)
                </p>
                <p className="mt-1 text-text-secondary">{warning.message}</p>
                {warning.held_by.length > 0 && (
                  <ul data-testid="gear-overbooking-held-by" className="mt-2 space-y-0.5 text-text-secondary">
                    {warning.held_by.map((holder) => (
                      <li key={holder.booking_id}>
                        {holder.project_name} · strip “{holder.strip_name}” · {holder.dates.map((d) => dayLabel(d)).join(', ')} · {holder.status} {holder.quantity}
                      </li>
                    ))}
                  </ul>
                )}
                {warning.alternatives.length > 0 ? (
                  <ul className="mt-3 space-y-1">
                    {warning.alternatives.map((alt) => (
                      <li key={alt.alternative_id} data-testid="gear-overbooking-alternative" className="flex items-center gap-2">
                        <DeviceThumb url={alt.thumbnail_url} name={alt.name} size={24} />
                        <span className="min-w-0 flex-1">
                          <span className="text-text-primary">{alt.name}</span>
                          <span data-testid="gear-overbooking-alternative-availability" className={cn('ml-1', alt.enough ? 'text-status-success' : 'text-status-warning')}>
                            {alt.owned} owned · {alt.min_available} available on these days
                          </span>
                          {(alt.note || alt.price_hint) && (
                            <span className="block text-text-tertiary">{[alt.note, alt.price_hint].filter(Boolean).join(' · ')}</span>
                          )}
                        </span>
                        <button
                          type="button"
                          data-testid="gear-overbooking-swap"
                          onClick={() => void swap(warning, alt.device_id, alt.name)}
                          className="rounded border border-border px-2 py-1"
                        >
                          Swap
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-text-tertiary">{warning.device_name} has no alternatives set up. Add them on its Alternatives tab.</p>
                )}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <input
                    aria-label={`Why book ${warning.device_name} anyway`}
                    data-testid="gear-overbooking-note"
                    value={notes[warning.booking_id] ?? ''}
                    onChange={(event) => setNotes({ ...notes, [warning.booking_id]: event.target.value })}
                    placeholder="Why? e.g. renting in (optional)"
                    className="h-7 flex-1 rounded-md border border-border bg-bg-primary px-2"
                  />
                  <button
                    type="button"
                    data-testid="gear-overbooking-book-anyway"
                    onClick={() => void bookAnyway(warning)}
                    className="rounded bg-accent px-2 py-1 font-medium text-white"
                  >
                    Book anyway
                  </button>
                  <button
                    type="button"
                    data-testid="gear-overbooking-cancel"
                    onClick={() => void cancel(warning)}
                    className="rounded border border-border px-2 py-1"
                  >
                    Cancel booking
                  </button>
                </div>
              </section>
            ))}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
