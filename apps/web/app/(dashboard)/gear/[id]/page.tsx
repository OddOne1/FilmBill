'use client'

import * as React from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { deviceCountsSentence, RETIRED_REASON_LABELS, UNIT_STATUS_LABELS } from '@/lib/device-counts'
import { formatMoney } from '@/lib/money'
import { usePermissions } from '@/hooks/use-permissions'
import type {
  DeviceDetail,
  DeviceUnit,
  DeviceUnitChangeResult,
  DeviceUnitsAddResult,
  ItemPage,
} from '@/types'
import {
  AMBIGUOUS_AMOUNT,
  amountForApi,
  amountIsAmbiguous,
  GearNoAccess,
  GearReadOnlyNotice,
  useGearAccess,
} from '../gear-chrome'

/**
 * One device: its two numbers, its units, and what can be done to each.
 *
 * **The quantity owned is changed in exactly three ways**, all visible:
 * typed by hand in the form; "also reduce the quantity owned by 1" when a
 * unit is retired; "also raise it by 1" when a retired unit comes back. The
 * last two are ticked by default and can be unticked. Adding serial numbers
 * never touches it — the API guarantees that, and this screen says so.
 */
export default function DevicePage() {
  const params = useParams<{ id: string }>()
  const { can, active } = usePermissions()
  const currency = active?.default_currency ?? 'EUR'
  const { loaded, canView, canEdit, refused } = useGearAccess()
  const [device, setDevice] = React.useState<DeviceDetail | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)

  const load = React.useCallback(async () => {
    try {
      setDevice(await api.get<DeviceDetail>(`/devices/${params.id}`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'This device could not be loaded. Try again.')
    }
  }, [params.id])

  React.useEffect(() => {
    if (!loaded || !canView) return
    void load()
  }, [loaded, canView, load])

  if (refused) return <GearNoAccess title="Device" />

  const archiveToggle = async () => {
    if (!device) return
    setError(null)
    try {
      setDevice(
        await api.post<DeviceDetail>(
          `/devices/${device.id}/${device.is_active ? 'archive' : 'reactivate'}`,
          {},
        ),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'That did not work. Try again.')
    }
  }

  return (
    <div className="max-w-5xl p-8">
      <Link href="/gear" className="inline-flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary">
        <ArrowLeft className="h-3 w-3" /> Gear
      </Link>

      {error && (
        <p role="alert" data-testid="device-error" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {!device ? (
        !error && <p className="mt-4 text-sm text-text-secondary">Loading the device…</p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-lg font-semibold text-text-primary">
                {device.name}
                {!device.is_active && (
                  <span className="ml-2 rounded bg-bg-tertiary px-1.5 py-0.5 text-xs text-text-secondary">
                    archived
                  </span>
                )}
              </h1>
              <p data-testid="device-counts" className="mt-1 text-sm text-text-secondary">
                {deviceCountsSentence(device.counts)}
              </p>
            </div>
            {canEdit && (
              <button
                type="button"
                data-testid="device-archive-toggle"
                onClick={() => void archiveToggle()}
                className="rounded-md border border-border px-3 py-1.5 text-sm text-text-primary"
              >
                {device.is_active ? 'Archive' : 'Reactivate'}
              </button>
            )}
          </div>

          {!canEdit && <GearReadOnlyNotice />}
          {notice && (
            <p role="status" data-testid="device-notice" className="mt-4 text-sm text-success">
              {notice}
            </p>
          )}

          {canEdit ? (
            <DeviceForm
              device={device}
              canPickItem={can('catalog.view')}
              onSaved={(next) => {
                setDevice(next)
                setNotice('Saved.')
              }}
            />
          ) : (
            <dl className="mt-5 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              <dt className="text-text-tertiary">Category</dt>
              <dd className="text-text-primary">{device.category ?? '—'}</dd>
              <dt className="text-text-tertiary">Quantity owned</dt>
              <dd className="text-text-primary">{device.quantity_owned}</dd>
              {device.price_visible && (
                <>
                  <dt className="text-text-tertiary">Purchase price</dt>
                  <dd className="text-text-primary">{formatMoney(device.purchase_price, currency)}</dd>
                </>
              )}
              <dt className="text-text-tertiary">Notes</dt>
              <dd className="text-text-primary">{device.notes ?? '—'}</dd>
            </dl>
          )}

          <h2 className="mt-8 text-sm font-semibold text-text-primary">Units and serial numbers</h2>
          {canEdit && device.is_active && (
            <AddSerials
              device={device}
              onAdded={(result) => {
                setDevice(result.device)
                setNotice(
                  `${result.created.length} serial ${result.created.length === 1 ? 'number' : 'numbers'} added. The quantity owned is still ${result.device.quantity_owned}.`,
                )
              }}
            />
          )}
          <UnitsTable
            device={device}
            canEdit={canEdit}
            onChanged={(result, message) => {
              setDevice(result.device)
              setNotice(message)
            }}
          />
        </>
      )}
    </div>
  )
}

// ─── The form ────────────────────────────────────────────────────────────────

function DeviceForm({
  device,
  canPickItem,
  onSaved,
}: {
  device: DeviceDetail
  canPickItem: boolean
  onSaved: (next: DeviceDetail) => void
}) {
  const initial = React.useMemo(
    () => ({
      name: device.name,
      category: device.category ?? '',
      quantity: String(device.quantity_owned),
      price: device.purchase_price ? device.purchase_price.replace('.', ',') : '',
      purchaseDate: device.purchase_date ?? '',
      catalogItemId: device.catalog_item_id ?? '',
      notes: device.notes ?? '',
    }),
    [device],
  )
  const [draft, setDraft] = React.useState(initial)
  const [items, setItems] = React.useState<{ id: string; name: string }[]>([])
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => setDraft(initial), [initial])

  React.useEffect(() => {
    if (!canPickItem) return
    api
      .get<ItemPage>('/catalog/items?kind=equipment_rental&limit=200')
      .then((page) => setItems(page.items.map((item) => ({ id: item.id, name: item.name_de }))))
      .catch(() => setItems([]))
  }, [canPickItem])

  const quantityValid = /^\d+$/.test(draft.quantity.trim())
  const why = !draft.name.trim()
    ? 'A device needs a name.'
    : !quantityValid
      ? 'The quantity owned is a whole number — 0 is fine.'
      : device.price_visible && amountIsAmbiguous(draft.price)
        ? AMBIGUOUS_AMOUNT
        : null

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (why) return
    const changes: Record<string, unknown> = {}
    if (draft.name !== initial.name) changes.name = draft.name
    if (draft.category !== initial.category) changes.category = draft.category || null
    if (draft.quantity.trim() !== initial.quantity) changes.quantity_owned = Number.parseInt(draft.quantity.trim(), 10)
    if (device.price_visible && draft.price !== initial.price) changes.purchase_price = amountForApi(draft.price)
    if (draft.purchaseDate !== initial.purchaseDate) changes.purchase_date = draft.purchaseDate || null
    if (draft.catalogItemId !== initial.catalogItemId) changes.catalog_item_id = draft.catalogItemId || null
    if (draft.notes !== initial.notes) changes.notes = draft.notes || null
    if (Object.keys(changes).length === 0) return
    setBusy(true)
    setError(null)
    try {
      onSaved(await api.patch<DeviceDetail>(`/devices/${device.id}`, changes))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The device could not be saved. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const field = 'mt-1 h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary'
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial)

  return (
    <form onSubmit={(event) => void save(event)} data-testid="device-form" className="mt-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-xs text-text-secondary sm:col-span-2">
          Name
          <input data-testid="device-name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={field} />
        </label>
        <label className="text-xs text-text-secondary">
          Category
          <input data-testid="device-category" value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} className={field} />
        </label>
        <label className="text-xs text-text-secondary">
          Quantity owned
          <input
            data-testid="device-quantity"
            inputMode="numeric"
            value={draft.quantity}
            onChange={(e) => setDraft({ ...draft, quantity: e.target.value })}
            className={field}
          />
        </label>
        {device.price_visible && (
          <label className="text-xs text-text-secondary">
            Purchase price (net, each)
            <input
              data-testid="device-price"
              inputMode="decimal"
              value={draft.price}
              onChange={(e) => setDraft({ ...draft, price: e.target.value })}
              className={field}
            />
          </label>
        )}
        <label className="text-xs text-text-secondary">
          Purchased on
          <input
            type="date"
            data-testid="device-date"
            value={draft.purchaseDate}
            onChange={(e) => setDraft({ ...draft, purchaseDate: e.target.value })}
            className={field}
          />
        </label>
        {canPickItem && (
          <label className="text-xs text-text-secondary sm:col-span-2">
            Rented out as (catalog item)
            <select
              data-testid="device-catalog-item"
              value={draft.catalogItemId}
              onChange={(e) => setDraft({ ...draft, catalogItemId: e.target.value })}
              className={field}
            >
              <option value="">No catalog item</option>
              {device.catalog_item_id && !items.some((item) => item.id === device.catalog_item_id) && (
                <option value={device.catalog_item_id}>{device.catalog_item_name ?? 'The linked item'}</option>
              )}
              {items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="text-xs text-text-secondary sm:col-span-3">
          Notes
          <input data-testid="device-notes" value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} className={field} />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="submit"
          data-testid="device-save"
          disabled={busy || !dirty || why !== null}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Save
        </button>
        {why ? (
          <span data-testid="device-save-why" className="text-xs text-text-tertiary">
            {why}
          </span>
        ) : !dirty ? (
          <span className="text-xs text-text-tertiary">Nothing changed yet.</span>
        ) : null}
      </div>
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  )
}

// ─── Adding serial numbers ───────────────────────────────────────────────────

function AddSerials({
  device,
  onAdded,
}: {
  device: DeviceDetail
  onAdded: (result: DeviceUnitsAddResult) => void
}) {
  const [text, setText] = React.useState('')
  const [acquiredOn, setAcquiredOn] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [skipped, setSkipped] = React.useState<DeviceUnitsAddResult['skipped']>([])

  const serials = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)

  const add = async () => {
    if (!serials.length) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<DeviceUnitsAddResult>(`/devices/${device.id}/units`, {
        serial_numbers: serials,
        acquired_on: acquiredOn || null,
      })
      setSkipped(result.skipped ?? [])
      setText('')
      onAdded(result)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The serial numbers could not be added. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div data-testid="add-serials" className="mt-3 rounded-md border border-border bg-bg-secondary p-4">
      <label className="block text-xs text-text-secondary">
        Add serial numbers — one per line, or paste a list
        <textarea
          data-testid="add-serials-text"
          rows={3}
          value={text}
          onChange={(event) => setText(event.target.value)}
          className="mt-1 w-full rounded-md border border-border bg-bg-secondary px-2 py-1 font-mono text-sm text-text-primary"
        />
      </label>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <label className="text-xs text-text-secondary">
          Acquired on{' '}
          <input
            type="date"
            data-testid="add-serials-date"
            value={acquiredOn}
            onChange={(event) => setAcquiredOn(event.target.value)}
            className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          />
        </label>
        <button
          type="button"
          data-testid="add-serials-save"
          disabled={busy || serials.length === 0}
          onClick={() => void add()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Add {serials.length || ''} {serials.length === 1 ? 'serial number' : 'serial numbers'}
        </button>
        <span className="text-xs text-text-tertiary">
          {serials.length === 0
            ? 'Type or paste at least one serial number.'
            : 'The quantity owned does not change.'}
        </span>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
      {skipped.length > 0 && (
        <ul data-testid="add-serials-skipped" className="mt-2 list-disc pl-5 text-xs text-warning">
          {skipped.map((entry) => (
            <li key={`${entry.reason}-${entry.serial_number}`}>Skipped: {entry.detail}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ─── The units ───────────────────────────────────────────────────────────────

function UnitsTable({
  device,
  canEdit,
  onChanged,
}: {
  device: DeviceDetail
  canEdit: boolean
  onChanged: (result: DeviceUnitChangeResult, message: string) => void
}) {
  const [open, setOpen] = React.useState<{ unit: string; kind: 'retire' | 'reactivate' } | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  const units = device.units ?? []
  if (units.length === 0) {
    return (
      <p data-testid="units-empty" className="mt-3 text-sm text-text-secondary">
        No serial numbers recorded yet.
        {device.quantity_owned > 0 &&
          ` That is fine: the ${device.quantity_owned} owned can be booked all the same.`}
      </p>
    )
  }

  const setStatus = async (unit: DeviceUnit, status: 'active' | 'in_repair') => {
    setError(null)
    try {
      const result = await api.patch<DeviceUnitChangeResult>(`/devices/${device.id}/units/${unit.id}`, { status })
      onChanged(result, status === 'in_repair' ? `${unit.serial_number} is in repair.` : `${unit.serial_number} is back from repair.`)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'That did not work. Try again.')
    }
  }

  return (
    <>
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
      <table className="mt-3 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
            <th className="py-2 pr-3 font-medium">Serial number</th>
            <th className="py-2 pr-3 font-medium">Status</th>
            <th className="py-2 pr-3 font-medium">Acquired</th>
            <th className="py-2 pr-3 font-medium">Notes</th>
            {canEdit && <th className="py-2 pr-3 font-medium" />}
          </tr>
        </thead>
        <tbody>
          {units.map((unit) => (
            <React.Fragment key={unit.id}>
              <tr data-testid="unit-row" data-status={unit.status} className="border-b border-border/60 align-top">
                <td className="py-2 pr-3 font-mono text-xs text-text-primary">{unit.serial_number}</td>
                <td className="py-2 pr-3 text-text-secondary">
                  {UNIT_STATUS_LABELS[unit.status] ?? unit.status}
                  {unit.status === 'retired' && (
                    <span className="block text-xs text-text-tertiary">
                      {[unit.retired_reason ? RETIRED_REASON_LABELS[unit.retired_reason] : null, unit.retired_on]
                        .filter(Boolean)
                        .join(', ')}
                    </span>
                  )}
                </td>
                <td className="py-2 pr-3 text-xs text-text-secondary">{unit.acquired_on ?? ''}</td>
                <td className="py-2 pr-3 text-xs text-text-secondary">{unit.notes ?? ''}</td>
                {canEdit && (
                  <td className="py-2 pr-3 text-right text-xs">
                    {unit.status === 'active' && (
                      <>
                        <button type="button" data-testid="unit-repair" onClick={() => void setStatus(unit, 'in_repair')} className="mr-3 underline">
                          In repair
                        </button>
                        <button type="button" data-testid="unit-retire" onClick={() => setOpen({ unit: unit.id, kind: 'retire' })} className="underline">
                          Retire
                        </button>
                      </>
                    )}
                    {unit.status === 'in_repair' && (
                      <>
                        <button type="button" data-testid="unit-back-from-repair" onClick={() => void setStatus(unit, 'active')} className="mr-3 underline">
                          Back to active
                        </button>
                        <button type="button" data-testid="unit-retire" onClick={() => setOpen({ unit: unit.id, kind: 'retire' })} className="underline">
                          Retire
                        </button>
                      </>
                    )}
                    {unit.status === 'retired' && (
                      <button type="button" data-testid="unit-reactivate" onClick={() => setOpen({ unit: unit.id, kind: 'reactivate' })} className="underline">
                        Back to active
                      </button>
                    )}
                  </td>
                )}
              </tr>
              {open?.unit === unit.id && (
                <tr>
                  <td colSpan={5} className="pb-3">
                    {open.kind === 'retire' ? (
                      <RetirePanel
                        device={device}
                        unit={unit}
                        onCancel={() => setOpen(null)}
                        onDone={(result) => {
                          setOpen(null)
                          onChanged(
                            result,
                            result.quantity_before === result.quantity_after
                              ? `${unit.serial_number} is retired. The quantity owned stays ${result.quantity_after}.`
                              : `${unit.serial_number} is retired. The quantity owned went from ${result.quantity_before} to ${result.quantity_after}.`,
                          )
                        }}
                      />
                    ) : (
                      <ReactivatePanel
                        device={device}
                        unit={unit}
                        onCancel={() => setOpen(null)}
                        onDone={(result) => {
                          setOpen(null)
                          onChanged(
                            result,
                            result.quantity_before === result.quantity_after
                              ? `${unit.serial_number} is active again. The quantity owned stays ${result.quantity_after}.`
                              : `${unit.serial_number} is active again. The quantity owned went from ${result.quantity_before} to ${result.quantity_after}.`,
                          )
                        }}
                      />
                    )}
                  </td>
                </tr>
              )}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </>
  )
}

function RetirePanel({
  device,
  unit,
  onCancel,
  onDone,
}: {
  device: DeviceDetail
  unit: DeviceUnit
  onCancel: () => void
  onDone: (result: DeviceUnitChangeResult) => void
}) {
  const [reason, setReason] = React.useState('')
  const [retiredOn, setRetiredOn] = React.useState('')
  const [notes, setNotes] = React.useState('')
  // Ticked by default: a retired unit cannot be booked.
  const [reduce, setReduce] = React.useState(device.quantity_owned > 0)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      onDone(
        await api.post<DeviceUnitChangeResult>(`/devices/${device.id}/units/${unit.id}/retire`, {
          reason: reason || null,
          retired_on: retiredOn || null,
          notes: notes || null,
          reduce_quantity: reduce,
        }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The unit could not be retired. Try again.')
      setBusy(false)
    }
  }

  return (
    <div data-testid="retire-panel" className="rounded-md border border-border bg-bg-secondary p-3 text-sm">
      <p className="font-medium text-text-primary">Retire {unit.serial_number}</p>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-text-secondary">
        <label>
          Why{' '}
          <select
            data-testid="retire-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          >
            <option value="">No reason (for example, entered by mistake)</option>
            {Object.entries(RETIRED_REASON_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          On{' '}
          <input
            type="date"
            data-testid="retire-date"
            value={retiredOn}
            onChange={(event) => setRetiredOn(event.target.value)}
            className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          />
        </label>
        <label className="grow">
          Note{' '}
          <input
            data-testid="retire-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            className="ml-1 h-8 w-64 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          />
        </label>
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm text-text-primary">
        <input
          type="checkbox"
          data-testid="retire-reduce"
          checked={reduce}
          disabled={device.quantity_owned === 0}
          onChange={(event) => setReduce(event.target.checked)}
        />
        Also reduce the quantity owned by 1 (from {device.quantity_owned} to {Math.max(0, device.quantity_owned - 1)})
      </label>
      <p className="mt-1 text-xs text-text-tertiary">
        {device.quantity_owned === 0
          ? 'The quantity owned is already 0, so there is nothing to reduce.'
          : 'A retired unit cannot be booked. Untick this if the quantity is already right — for example when a unit is kept on the shelf as a spare.'}
      </p>
      <div className="mt-3 flex gap-3">
        <button
          type="button"
          data-testid="retire-confirm"
          disabled={busy}
          onClick={() => void confirm()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Retire
        </button>
        <button type="button" onClick={onCancel} className="text-sm text-text-secondary hover:text-text-primary">
          Cancel
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

function ReactivatePanel({
  device,
  unit,
  onCancel,
  onDone,
}: {
  device: DeviceDetail
  unit: DeviceUnit
  onCancel: () => void
  onDone: (result: DeviceUnitChangeResult) => void
}) {
  const [raise, setRaise] = React.useState(true)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      onDone(
        await api.post<DeviceUnitChangeResult>(`/devices/${device.id}/units/${unit.id}/reactivate`, {
          raise_quantity: raise,
        }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The unit could not be brought back. Try again.')
      setBusy(false)
    }
  }

  return (
    <div data-testid="reactivate-panel" className="rounded-md border border-border bg-bg-secondary p-3 text-sm">
      <p className="font-medium text-text-primary">Bring {unit.serial_number} back to active</p>
      <label className="mt-2 flex items-center gap-2 text-sm text-text-primary">
        <input
          type="checkbox"
          data-testid="reactivate-raise"
          checked={raise}
          onChange={(event) => setRaise(event.target.checked)}
        />
        Also raise the quantity owned by 1 (from {device.quantity_owned} to {device.quantity_owned + 1})
      </label>
      <div className="mt-3 flex gap-3">
        <button
          type="button"
          data-testid="reactivate-confirm"
          disabled={busy}
          onClick={() => void confirm()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Back to active
        </button>
        <button type="button" onClick={onCancel} className="text-sm text-text-secondary hover:text-text-primary">
          Cancel
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  )
}
