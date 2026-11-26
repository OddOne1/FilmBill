'use client'

import * as React from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { deviceCountsSentence, isRemoved, UNIT_STATUS_LABELS } from '@/lib/device-counts'
import { formatMoney } from '@/lib/money'
import { usePermissions } from '@/hooks/use-permissions'
import type {
  DeviceDetail,
  DeviceUnit,
  DeviceUnitChangeResult,
  DeviceUnitsAddResult,
  PartyPage,
} from '@/types'
import { RentalPrices } from './rental-prices'
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
 * unit is removed (retired, lost or sold — always with what happened, in
 * words); "also raise it by 1" when a removed unit comes back. The last two
 * are ticked by default and can be unticked. Adding serial numbers never
 * touches it — the API guarantees that, and this screen says so.
 *
 * **What a unit cost and who sold it** (its own price when it differs from
 * the device's, the seller, the invoice number) is shown and edited only by
 * somebody who may see purchase prices — the API withholds it otherwise.
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

          <RentalPrices
            device={device}
            currency={currency}
            onChanged={(next, message) => {
              setDevice(next)
              setNotice(message)
            }}
          />

          <h2 className="mt-8 text-sm font-semibold text-text-primary">Units and serial numbers</h2>
          {canEdit && device.is_active && (
            <AddSerials
              device={device}
              canPickSeller={can('parties.view')}
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
            currency={currency}
            canPickSeller={can('parties.view')}
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
  onSaved,
}: {
  device: DeviceDetail
  onSaved: (next: DeviceDetail) => void
}) {
  const initial = React.useMemo(
    () => ({
      name: device.name,
      category: device.category ?? '',
      quantity: String(device.quantity_owned),
      price: device.purchase_price ? device.purchase_price.replace('.', ',') : '',
      purchaseDate: device.purchase_date ?? '',
      notes: device.notes ?? '',
    }),
    [device],
  )
  const [draft, setDraft] = React.useState(initial)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => setDraft(initial), [initial])

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

// ─── Purchase details: price, seller, invoice number ────────────────────────

type Purchase = { price: string; sellerPartyId: string; sellerName: string; reference: string }

const EMPTY_PURCHASE: Purchase = { price: '', sellerPartyId: '', sellerName: '', reference: '' }

function purchaseForApi(purchase: Purchase) {
  return {
    purchase_price: amountForApi(purchase.price),
    seller_party_id: purchase.sellerPartyId || null,
    seller_name: purchase.sellerPartyId ? null : purchase.sellerName || null,
    purchase_reference: purchase.reference || null,
  }
}

function useSuppliers(enabled: boolean) {
  const [suppliers, setSuppliers] = React.useState<{ id: string; name: string }[]>([])
  React.useEffect(() => {
    if (!enabled) return
    api
      .get<PartyPage>('/parties?role=supplier&limit=200')
      .then((page) => setSuppliers(page.parties.map((party) => ({ id: party.id, name: party.name }))))
      .catch(() => setSuppliers([]))
  }, [enabled])
  return suppliers
}

function PurchaseFields({
  value,
  onChange,
  suppliers,
  canPickSeller,
}: {
  value: Purchase
  onChange: (next: Purchase) => void
  suppliers: { id: string; name: string }[]
  canPickSeller: boolean
}) {
  const field = 'mt-1 h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary'
  return (
    <div className="grid gap-3 sm:grid-cols-4">
      <label className="text-xs text-text-secondary">
        Price of this unit (net)
        <input
          data-testid="purchase-price"
          inputMode="decimal"
          value={value.price}
          placeholder="Empty: the device's price"
          onChange={(e) => onChange({ ...value, price: e.target.value })}
          className={field}
        />
      </label>
      {canPickSeller && (
        <label className="text-xs text-text-secondary">
          Seller on file
          <select
            data-testid="purchase-seller-party"
            value={value.sellerPartyId}
            onChange={(e) => onChange({ ...value, sellerPartyId: e.target.value })}
            className={field}
          >
            <option value="">— not on file —</option>
            {suppliers.map((supplier) => (
              <option key={supplier.id} value={supplier.id}>
                {supplier.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {!value.sellerPartyId && (
        <label className="text-xs text-text-secondary">
          Seller{canPickSeller ? ' (if not on file)' : ''}
          <input
            data-testid="purchase-seller-name"
            value={value.sellerName}
            onChange={(e) => onChange({ ...value, sellerName: e.target.value })}
            className={field}
          />
        </label>
      )}
      <label className="text-xs text-text-secondary">
        Invoice or order number
        <input
          data-testid="purchase-reference"
          value={value.reference}
          onChange={(e) => onChange({ ...value, reference: e.target.value })}
          className={field}
        />
      </label>
    </div>
  )
}

// ─── Adding serial numbers ───────────────────────────────────────────────────

function AddSerials({
  device,
  canPickSeller,
  onAdded,
}: {
  device: DeviceDetail
  canPickSeller: boolean
  onAdded: (result: DeviceUnitsAddResult) => void
}) {
  const [text, setText] = React.useState('')
  const [acquiredOn, setAcquiredOn] = React.useState('')
  const [purchase, setPurchase] = React.useState<Purchase>(EMPTY_PURCHASE)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [skipped, setSkipped] = React.useState<DeviceUnitsAddResult['skipped']>([])
  const suppliers = useSuppliers(device.price_visible && canPickSeller)

  const serials = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const ambiguous = device.price_visible && amountIsAmbiguous(purchase.price)

  const add = async () => {
    if (!serials.length || ambiguous) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<DeviceUnitsAddResult>(`/devices/${device.id}/units`, {
        serial_numbers: serials,
        acquired_on: acquiredOn || null,
        ...(device.price_visible ? purchaseForApi(purchase) : {}),
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
      {device.price_visible && (
        <div className="mt-3">
          <p className="mb-1 text-xs text-text-tertiary">
            Bought together? The price, seller and invoice number below go on every
            serial in the list — all optional, and each unit can be changed later.
          </p>
          <PurchaseFields value={purchase} onChange={setPurchase} suppliers={suppliers} canPickSeller={canPickSeller} />
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="text-xs text-text-secondary">
          Bought on{' '}
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
          disabled={busy || serials.length === 0 || ambiguous}
          onClick={() => void add()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Add {serials.length || ''} {serials.length === 1 ? 'serial number' : 'serial numbers'}
        </button>
        <span data-testid="add-serials-why" className="text-xs text-text-tertiary">
          {serials.length === 0
            ? 'Type or paste at least one serial number.'
            : ambiguous
              ? AMBIGUOUS_AMOUNT
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

type Panel = { unit: string; kind: 'remove' | 'reactivate' | 'purchase' }

function UnitsTable({
  device,
  canEdit,
  currency,
  canPickSeller,
  onChanged,
}: {
  device: DeviceDetail
  canEdit: boolean
  currency: string
  canPickSeller: boolean
  onChanged: (result: DeviceUnitChangeResult, message: string) => void
}) {
  const [open, setOpen] = React.useState<Panel | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const showPurchase = device.price_visible

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

  const setStatus = async (unit: DeviceUnit, status: 'active' | 'needs_check' | 'in_repair') => {
    setError(null)
    try {
      const result = await api.patch<DeviceUnitChangeResult>(`/devices/${device.id}/units/${unit.id}`, { status })
      onChanged(result, `${unit.serial_number} is now: ${UNIT_STATUS_LABELS[status].toLowerCase()}.`)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'That did not work. Try again.')
    }
  }

  const columns = canEdit ? (showPurchase ? 7 : 5) : showPurchase ? 6 : 4

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
            <th className="py-2 pr-3 font-medium">Bought on</th>
            {showPurchase && <th className="py-2 pr-3 text-right font-medium">Price</th>}
            {showPurchase && <th className="py-2 pr-3 font-medium">Seller</th>}
            <th className="py-2 pr-3 font-medium">Notes</th>
            {canEdit && <th className="py-2 pr-3 font-medium" />}
          </tr>
        </thead>
        <tbody>
          {units.map((unit) => {
            const removed = isRemoved(unit.status)
            const button = 'mr-3 underline'
            return (
              <React.Fragment key={unit.id}>
                <tr data-testid="unit-row" data-status={unit.status} className="border-b border-border/60 align-top">
                  <td className="py-2 pr-3 font-mono text-xs text-text-primary">{unit.serial_number}</td>
                  <td className="py-2 pr-3 text-text-secondary">
                    {UNIT_STATUS_LABELS[unit.status] ?? unit.status}
                    {removed && (
                      <span data-testid="unit-removed-reason" className="block text-xs text-text-tertiary">
                        {unit.removed_on}: {unit.removed_reason}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-xs text-text-secondary">{unit.acquired_on ?? ''}</td>
                  {showPurchase && (
                    <td className="py-2 pr-3 text-right font-mono text-xs">
                      {unit.purchase_price ? (
                        formatMoney(unit.purchase_price, currency)
                      ) : (
                        <span className="text-text-tertiary" title="No price of its own: the device's price applies">
                          as device
                        </span>
                      )}
                    </td>
                  )}
                  {showPurchase && (
                    <td className="py-2 pr-3 text-xs text-text-secondary">
                      {unit.seller_party_name ?? unit.seller_name ?? ''}
                      {unit.purchase_reference && (
                        <span className="block text-text-tertiary">{unit.purchase_reference}</span>
                      )}
                    </td>
                  )}
                  <td className="py-2 pr-3 text-xs text-text-secondary">{unit.notes ?? ''}</td>
                  {canEdit && (
                    <td className="py-2 pr-3 text-right text-xs">
                      {!removed && unit.status !== 'active' && (
                        <button type="button" data-testid="unit-back" onClick={() => void setStatus(unit, 'active')} className={button}>
                          Back to active
                        </button>
                      )}
                      {unit.status === 'active' && (
                        <button type="button" data-testid="unit-needs-check" onClick={() => void setStatus(unit, 'needs_check')} className={button}>
                          Needs check
                        </button>
                      )}
                      {(unit.status === 'active' || unit.status === 'needs_check') && (
                        <button type="button" data-testid="unit-repair" onClick={() => void setStatus(unit, 'in_repair')} className={button}>
                          In repair
                        </button>
                      )}
                      {!removed && (
                        <button type="button" data-testid="unit-remove" onClick={() => setOpen({ unit: unit.id, kind: 'remove' })} className={button}>
                          Remove…
                        </button>
                      )}
                      {removed && (
                        <button type="button" data-testid="unit-reactivate" onClick={() => setOpen({ unit: unit.id, kind: 'reactivate' })} className={button}>
                          Back to active…
                        </button>
                      )}
                      {showPurchase && (
                        <button type="button" data-testid="unit-purchase" onClick={() => setOpen({ unit: unit.id, kind: 'purchase' })} className="underline">
                          Purchase…
                        </button>
                      )}
                    </td>
                  )}
                </tr>
                {open?.unit === unit.id && (
                  <tr>
                    <td colSpan={columns} className="pb-3">
                      {open.kind === 'remove' && (
                        <RemovePanel
                          device={device}
                          unit={unit}
                          onCancel={() => setOpen(null)}
                          onDone={(result) => {
                            setOpen(null)
                            onChanged(result, quantityMessage(`${unit.serial_number} is ${UNIT_STATUS_LABELS[result.unit.status].toLowerCase()}.`, result))
                          }}
                        />
                      )}
                      {open.kind === 'reactivate' && (
                        <ReactivatePanel
                          device={device}
                          unit={unit}
                          onCancel={() => setOpen(null)}
                          onDone={(result) => {
                            setOpen(null)
                            onChanged(result, quantityMessage(`${unit.serial_number} is active again.`, result))
                          }}
                        />
                      )}
                      {open.kind === 'purchase' && (
                        <PurchasePanel
                          device={device}
                          unit={unit}
                          canPickSeller={canPickSeller}
                          onCancel={() => setOpen(null)}
                          onDone={(result) => {
                            setOpen(null)
                            onChanged(result, `The purchase details of ${unit.serial_number} are saved.`)
                          }}
                        />
                      )}
                    </td>
                  </tr>
                )}
              </React.Fragment>
            )
          })}
        </tbody>
      </table>
    </>
  )
}

function quantityMessage(first: string, result: DeviceUnitChangeResult): string {
  return result.quantity_before === result.quantity_after
    ? `${first} The quantity owned stays ${result.quantity_after}.`
    : `${first} The quantity owned went from ${result.quantity_before} to ${result.quantity_after}.`
}

function RemovePanel({
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
  const [status, setStatus] = React.useState<'retired' | 'lost' | 'sold'>('retired')
  const [reason, setReason] = React.useState('')
  const [removedOn, setRemovedOn] = React.useState('')
  // Ticked by default: a unit that is gone cannot be booked.
  const [reduce, setReduce] = React.useState(device.quantity_owned > 0)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const confirm = async () => {
    if (!reason.trim()) return
    setBusy(true)
    setError(null)
    try {
      onDone(
        await api.post<DeviceUnitChangeResult>(`/devices/${device.id}/units/${unit.id}/remove`, {
          status,
          reason,
          removed_on: removedOn || null,
          reduce_quantity: reduce,
        }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The unit could not be removed. Try again.')
      setBusy(false)
    }
  }

  return (
    <div data-testid="remove-panel" className="rounded-md border border-border bg-bg-secondary p-3 text-sm">
      <p className="font-medium text-text-primary">Remove {unit.serial_number} from use</p>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-text-secondary">
        {(['retired', 'lost', 'sold'] as const).map((value) => (
          <label key={value} className="inline-flex items-center gap-1.5">
            <input
              type="radio"
              name="remove-status"
              data-testid={`remove-status-${value}`}
              checked={status === value}
              onChange={() => setStatus(value)}
            />
            {UNIT_STATUS_LABELS[value]}
          </label>
        ))}
        <label>
          On{' '}
          <input
            type="date"
            data-testid="remove-date"
            value={removedOn}
            onChange={(event) => setRemovedOn(event.target.value)}
            className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          />
        </label>
      </div>
      <label className="mt-3 block text-xs text-text-secondary">
        What happened, exactly
        <textarea
          data-testid="remove-reason"
          rows={2}
          value={reason}
          placeholder="Fell off the crane on the 1000m shoot, 14 January — insurance claim 2026-118"
          onChange={(event) => setReason(event.target.value)}
          className="mt-1 w-full rounded-md border border-border bg-bg-secondary px-2 py-1 text-sm text-text-primary"
        />
      </label>
      <label className="mt-3 flex items-center gap-2 text-sm text-text-primary">
        <input
          type="checkbox"
          data-testid="remove-reduce"
          checked={reduce}
          disabled={device.quantity_owned === 0}
          onChange={(event) => setReduce(event.target.checked)}
        />
        Also reduce the quantity owned by 1 (from {device.quantity_owned} to {Math.max(0, device.quantity_owned - 1)})
      </label>
      <p className="mt-1 text-xs text-text-tertiary">
        {device.quantity_owned === 0
          ? 'The quantity owned is already 0, so there is nothing to reduce.'
          : 'A unit that is gone cannot be booked. Untick this if the quantity is already right.'}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="remove-confirm"
          disabled={busy || !reason.trim()}
          onClick={() => void confirm()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Remove
        </button>
        <button type="button" onClick={onCancel} className="text-sm text-text-secondary hover:text-text-primary">
          Cancel
        </button>
        {!reason.trim() && (
          <span data-testid="remove-why" className="text-xs text-text-tertiary">
            Say what happened before removing the unit — it stays on record.
          </span>
        )}
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
      <p className="mt-1 text-xs text-text-tertiary">Why it was removed stays in the audit log.</p>
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

function PurchasePanel({
  device,
  unit,
  canPickSeller,
  onCancel,
  onDone,
}: {
  device: DeviceDetail
  unit: DeviceUnit
  canPickSeller: boolean
  onCancel: () => void
  onDone: (result: DeviceUnitChangeResult) => void
}) {
  const [purchase, setPurchase] = React.useState<Purchase>({
    price: unit.purchase_price ? unit.purchase_price.replace('.', ',') : '',
    sellerPartyId: unit.seller_party_id ?? '',
    sellerName: unit.seller_name ?? '',
    reference: unit.purchase_reference ?? '',
  })
  const [acquiredOn, setAcquiredOn] = React.useState(unit.acquired_on ?? '')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const suppliers = useSuppliers(canPickSeller)
  const ambiguous = amountIsAmbiguous(purchase.price)

  const save = async () => {
    if (ambiguous) return
    setBusy(true)
    setError(null)
    try {
      onDone(
        await api.patch<DeviceUnitChangeResult>(`/devices/${device.id}/units/${unit.id}`, {
          ...purchaseForApi(purchase),
          acquired_on: acquiredOn || null,
        }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The purchase details could not be saved. Try again.')
      setBusy(false)
    }
  }

  return (
    <div data-testid="purchase-panel" className="rounded-md border border-border bg-bg-secondary p-3 text-sm">
      <p className="font-medium text-text-primary">Purchase of {unit.serial_number}</p>
      <p className="mt-1 text-xs text-text-tertiary">
        For a repair, a return or an insurance claim. The price is only needed when
        it differs from the device&rsquo;s.
      </p>
      <div className="mt-2">
        <PurchaseFields value={purchase} onChange={setPurchase} suppliers={suppliers} canPickSeller={canPickSeller} />
      </div>
      <label className="mt-3 block text-xs text-text-secondary">
        Bought on{' '}
        <input
          type="date"
          data-testid="purchase-date"
          value={acquiredOn}
          onChange={(event) => setAcquiredOn(event.target.value)}
          className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
        />
      </label>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="purchase-save"
          disabled={busy || ambiguous}
          onClick={() => void save()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Save
        </button>
        <button type="button" onClick={onCancel} className="text-sm text-text-secondary hover:text-text-primary">
          Cancel
        </button>
        {ambiguous && <span className="text-xs text-text-tertiary">{AMBIGUOUS_AMOUNT}</span>}
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  )
}
