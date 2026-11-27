'use client'

import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { api, ApiError } from '@/lib/api'
import { deviceCountsSentence, isRemoved, UNIT_STATUS_LABELS } from '@/lib/device-counts'
import { formatMoney } from '@/lib/money'
import type { DeviceDetail, DeviceUnit, DeviceUnitChangeResult, DeviceUnitsAddResult, PartyPage } from '@/types'
import { AMBIGUOUS_AMOUNT, amountForApi, amountIsAmbiguous } from '../gear-chrome'

/**
 * The Units tab (B2b-2): the list of current units with their actions, the
 * three counts in words, a toggle for removed units, and the Add new item(s)
 * popup. The popup replaced B2b's single text box: one input per serial
 * number, the purchase details typed once for all of them, one request.
 */
export function UnitsTab({
  device,
  canEdit,
  canPickSeller,
  currency,
  onDevice,
}: {
  device: DeviceDetail
  canEdit: boolean
  canPickSeller: boolean
  currency: string
  onDevice: (next: DeviceDetail, message: string) => void
}) {
  const [showRemoved, setShowRemoved] = React.useState(false)
  const [adding, setAdding] = React.useState(false)
  const [skipped, setSkipped] = React.useState<DeviceUnitsAddResult['skipped']>([])
  const removedCount = (device.units ?? []).filter((unit) => isRemoved(unit.status)).length

  return (
    <section data-testid="units-tab" className="mt-5">
      <p data-testid="units-counts" className="text-sm text-text-secondary">
        {deviceCountsSentence(device.counts)}
      </p>
      <label className="mt-2 inline-flex items-center gap-2 text-xs text-text-secondary">
        <input
          type="checkbox"
          data-testid="units-show-removed"
          checked={showRemoved}
          onChange={(event) => setShowRemoved(event.target.checked)}
        />
        Show removed units ({removedCount})
      </label>
      <UnitsTable
        device={device}
        canEdit={canEdit}
        currency={currency}
        canPickSeller={canPickSeller}
        showRemoved={showRemoved}
        onChanged={(result, message) => onDevice(result.device, message)}
      />
      {canEdit && device.is_active && (
        <button
          type="button"
          data-testid="units-add-open"
          onClick={() => setAdding(true)}
          className="mt-4 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
        >
          Add new item(s)
        </button>
      )}
      {canEdit && !device.is_active && (
        <p className="mt-4 text-xs text-text-tertiary">
          This device is archived. Reactivate it to add units.
        </p>
      )}
      {skipped.length > 0 && (
        <ul data-testid="units-skipped" className="mt-2 list-disc pl-5 text-xs text-warning">
          {skipped.map((entry) => (
            <li key={`${entry.reason}-${entry.serial_number}`}>Skipped: {entry.detail}</li>
          ))}
        </ul>
      )}
      <AddUnitsDialog
        open={adding}
        onOpenChange={setAdding}
        device={device}
        canPickSeller={canPickSeller}
        onAdded={(result) => {
          setAdding(false)
          setSkipped(result.skipped ?? [])
          const n = result.created.length
          onDevice(
            result.device,
            `${n} ${n === 1 ? 'unit' : 'units'} added. The quantity owned is still ${result.device.quantity_owned}.`,
          )
        }}
      />
    </section>
  )
}

// ─── The Add new item(s) popup ───────────────────────────────────────────────

type Row = { key: number; serial: string }

/** The API's own rule: trimmed, case-insensitive. */
export function serialKey(serial: string): string {
  return serial.trim().toLocaleLowerCase()
}

/** A paste of several lines into one row becomes one row per line, starting
 *  at that row. Empty lines are dropped. Pure, so the rule has its own test. */
export function spreadPaste(rows: Row[], index: number, pasted: string, nextKey: () => number): Row[] {
  const lines = pasted
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  if (lines.length === 0) return rows
  const replaced: Row[] = lines.map((serial, offset) =>
    offset === 0 ? { key: rows[index].key, serial } : { key: nextKey(), serial },
  )
  return [...rows.slice(0, index), ...replaced, ...rows.slice(index + 1)]
}

function AddUnitsDialog({
  open,
  onOpenChange,
  device,
  canPickSeller,
  onAdded,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  device: DeviceDetail
  canPickSeller: boolean
  onAdded: (result: DeviceUnitsAddResult) => void
}) {
  const counter = React.useRef(1)
  const nextKey = () => ++counter.current
  const [rows, setRows] = React.useState<Row[]>([{ key: 1, serial: '' }])
  const [acquiredOn, setAcquiredOn] = React.useState('')
  const [purchase, setPurchase] = React.useState<Purchase>(EMPTY_PURCHASE)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const suppliers = useSuppliers(open && device.price_visible && canPickSeller)
  const inputs = React.useRef<Map<number, HTMLInputElement>>(new Map())
  const focusKey = React.useRef<number | null>(null)

  React.useEffect(() => {
    if (!open) return
    counter.current = 1
    setRows([{ key: 1, serial: '' }])
    setAcquiredOn('')
    setPurchase(EMPTY_PURCHASE)
    setError(null)
  }, [open])

  React.useEffect(() => {
    if (focusKey.current === null) return
    inputs.current.get(focusKey.current)?.focus()
    focusKey.current = null
  })

  const recorded = React.useMemo(() => {
    const map = new Map<string, string>()
    for (const unit of device.units ?? []) map.set(serialKey(unit.serial_number), unit.serial_number)
    return map
  }, [device.units])

  /** Why a row will be skipped, said in the row before anything is saved. */
  const flags = React.useMemo(() => {
    const seen = new Set<string>()
    return rows.map((row) => {
      const key = serialKey(row.serial)
      if (!key) return null
      if (recorded.has(key)) return `${recorded.get(key)} is already recorded on ${device.name}; it will be skipped.`
      if (seen.has(key)) return 'This serial number is already in the list; it will be added once.'
      seen.add(key)
      return null
    })
  }, [rows, recorded, device.name])

  const serials = rows.map((row) => row.serial.trim()).filter(Boolean)
  const toAdd = rows.filter((row, index) => row.serial.trim() && flags[index] === null).length
  const ambiguous = device.price_visible && amountIsAmbiguous(purchase.price)
  const why =
    serials.length === 0
      ? 'Type at least one serial number.'
      : toAdd === 0
        ? 'Every serial number here is already recorded on this device.'
        : ambiguous
          ? AMBIGUOUS_AMOUNT
          : null

  const addRow = () => {
    const key = nextKey()
    focusKey.current = key
    setRows((current) => [...current, { key, serial: '' }])
  }

  const save = async () => {
    if (why) return
    setBusy(true)
    setError(null)
    try {
      onAdded(
        await api.post<DeviceUnitsAddResult>(`/devices/${device.id}/units`, {
          serial_numbers: serials,
          acquired_on: acquiredOn || null,
          ...(device.price_visible ? purchaseForApi(purchase) : {}),
        }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The units could not be added. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const field = 'h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary'
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content
          data-testid="units-add-dialog"
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border bg-bg-secondary p-6 shadow-xl"
        >
          <Dialog.Title className="text-sm font-semibold text-text-primary">
            Add new item(s) to {device.name}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-text-tertiary">
            One serial number per row. Pasting a list into a row spreads it over rows. The quantity
            owned does not change.
          </Dialog.Description>

          <ol className="mt-4 space-y-2">
            {rows.map((row, index) => (
              <li key={row.key} data-testid="units-add-row">
                <div className="flex items-center gap-2">
                  <input
                    aria-label={`Serial number ${index + 1}`}
                    data-testid="units-add-serial"
                    ref={(element) => {
                      if (element) inputs.current.set(row.key, element)
                      else inputs.current.delete(row.key)
                    }}
                    value={row.serial}
                    onChange={(event) =>
                      setRows((current) =>
                        current.map((entry) => (entry.key === row.key ? { ...entry, serial: event.target.value } : entry)),
                      )
                    }
                    onPaste={(event) => {
                      const text = event.clipboardData.getData('text')
                      if (!/\r?\n/.test(text.trim())) return
                      event.preventDefault()
                      setRows((current) => spreadPaste(current, index, text, nextKey))
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault()
                        addRow()
                      }
                    }}
                    className={`${field} font-mono ${flags[index] ? 'border-warning' : ''}`}
                  />
                  {rows.length > 1 && (
                    <button
                      type="button"
                      data-testid="units-add-remove-row"
                      aria-label={`Remove serial number ${index + 1}`}
                      onClick={() => setRows((current) => current.filter((entry) => entry.key !== row.key))}
                      className="text-xs text-text-secondary underline"
                    >
                      Remove
                    </button>
                  )}
                </div>
                {flags[index] && (
                  <p data-testid="units-add-flag" className="mt-1 text-xs text-warning">
                    {flags[index]}
                  </p>
                )}
              </li>
            ))}
          </ol>
          <button
            type="button"
            data-testid="units-add-another"
            onClick={addRow}
            className="mt-2 text-sm text-accent underline"
          >
            Add another serial
          </button>

          <div className="mt-5 border-t border-border pt-4">
            <p className="mb-2 text-xs text-text-tertiary">
              Bought together? What you enter here goes on every serial number above — all optional,
              and each unit can be changed later.
            </p>
            {device.price_visible && (
              <PurchaseFields value={purchase} onChange={setPurchase} suppliers={suppliers} canPickSeller={canPickSeller} />
            )}
            <label className="mt-3 block text-xs text-text-secondary">
              Bought on
              <input
                type="date"
                data-testid="units-add-date"
                value={acquiredOn}
                onChange={(event) => setAcquiredOn(event.target.value)}
                className={`${field} mt-1 max-w-[12rem]`}
              />
            </label>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid="units-add-save"
              disabled={busy || why !== null}
              onClick={() => void save()}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Add {toAdd || ''} {toAdd === 1 ? 'unit' : 'units'}
            </button>
            <Dialog.Close asChild>
              <button type="button" className="text-sm text-text-secondary hover:text-text-primary">
                Cancel
              </button>
            </Dialog.Close>
            {why && (
              <span data-testid="units-add-why" className="text-xs text-text-tertiary">
                {why}
              </span>
            )}
          </div>
          {error && (
            <p role="alert" className="mt-3 text-sm text-danger">
              {error}
            </p>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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

// ─── The units ───────────────────────────────────────────────────────────────

type Panel = { unit: string; kind: 'remove' | 'reactivate' | 'purchase' }

function UnitsTable({
  device,
  canEdit,
  currency,
  canPickSeller,
  showRemoved,
  onChanged,
}: {
  device: DeviceDetail
  canEdit: boolean
  currency: string
  canPickSeller: boolean
  showRemoved: boolean
  onChanged: (result: DeviceUnitChangeResult, message: string) => void
}) {
  const [open, setOpen] = React.useState<Panel | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const showPurchase = device.price_visible

  const all = device.units ?? []
  const units = showRemoved ? all : all.filter((unit) => !isRemoved(unit.status))
  if (units.length === 0) {
    return (
      <p data-testid="units-empty" className="mt-3 text-sm text-text-secondary">
        {all.length === 0 ? 'No serial numbers recorded yet.' : 'No units in hand — the removed ones are hidden.'}
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

  const columns = (showPurchase ? 7 : 4) + (canEdit ? 1 : 0)

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
            {showPurchase && <th className="py-2 pr-3 text-right font-medium">Price</th>}
            {showPurchase && <th className="py-2 pr-3 font-medium">Seller</th>}
            {showPurchase && <th className="py-2 pr-3 font-medium">Invoice number</th>}
            <th className="py-2 pr-3 font-medium">Bought on</th>
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
                    </td>
                  )}
                  {showPurchase && (
                    <td data-testid="unit-reference" className="py-2 pr-3 text-xs text-text-secondary">
                      {unit.purchase_reference ?? ''}
                    </td>
                  )}
                  <td className="py-2 pr-3 text-xs text-text-secondary">{unit.acquired_on ?? ''}</td>
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
