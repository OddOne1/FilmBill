'use client'

import * as React from 'react'
import Link from 'next/link'
import * as Dialog from '@radix-ui/react-dialog'
import { api, ApiError } from '@/lib/api'
import type { DependencyHint, DeviceDependencies, DeviceDependency, DeviceDetail, DevicePage } from '@/types'
import { DeviceThumb } from '../gear-chrome'

/**
 * The Dependencies tab (B2b-2, SCOPE §4.4.3 "associated items"): what this
 * device needs — "2 × Battery V-Mount 150 Wh, always, included" — and which
 * devices need it. Data and screens only: booking with these is B2c, which
 * follows ONE level, so a battery's charger travels with a camera only when
 * the setup hint has added it to the camera too. The API refuses a loop and
 * says which chain it would close; this screen shows that sentence.
 */
export function DependenciesTab({ device, canEdit }: { device: DeviceDetail; canEdit: boolean }) {
  const [data, setData] = React.useState<DeviceDependencies | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [adding, setAdding] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setData(await api.get<DeviceDependencies>(`/devices/${device.id}/dependencies`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The dependencies could not be loaded. Try again.')
    }
  }, [device.id])

  React.useEffect(() => {
    void load()
  }, [load])

  const change = async (link: DeviceDependency, body: Record<string, unknown>) => {
    setError(null)
    try {
      setData(await api.patch<DeviceDependencies>(`/devices/${device.id}/dependencies/${link.id}`, body))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'That did not work. Try again.')
    }
  }

  const remove = async (link: DeviceDependency) => {
    setError(null)
    try {
      setData(await api.post<DeviceDependencies>(`/devices/${device.id}/dependencies/${link.id}/remove`, {}))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'That did not work. Try again.')
    }
  }

  return (
    <section data-testid="dependencies-tab" className="mt-5">
      <p className="text-xs text-text-tertiary">
        What goes out with {device.name} when it is booked. Only one level is followed: a
        dependency&rsquo;s own dependencies come along only if they are listed here too.
      </p>
      {error && (
        <p role="alert" data-testid="dependencies-error" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
      <h2 className="mt-4 text-sm font-semibold text-text-primary">This device needs</h2>
      {!data ? (
        <p className="mt-2 text-sm text-text-secondary">Loading…</p>
      ) : data.needs.length === 0 ? (
        <p data-testid="dependencies-empty" className="mt-2 text-sm text-text-secondary">
          No dependencies yet.
        </p>
      ) : (
        <table className="mt-2 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
              <th className="py-2 pr-3 font-medium">Device</th>
              <th className="py-2 pr-3 font-medium">Per {device.name}</th>
              <th className="py-2 pr-3 font-medium">Booked</th>
              <th className="py-2 pr-3 font-medium">Price</th>
              <th className="py-2 pr-3 font-medium">On documents</th>
              <th className="py-2 pr-3 font-medium">Note</th>
              {canEdit && <th className="py-2 pr-3 font-medium" />}
            </tr>
          </thead>
          <tbody>
            {data.needs.map((link) => (
              <DependencyRow key={link.id} link={link} canEdit={canEdit} onChange={change} onRemove={remove} />
            ))}
          </tbody>
        </table>
      )}
      {canEdit && device.is_active && (
        <button
          type="button"
          data-testid="dependency-add-open"
          onClick={() => setAdding(true)}
          className="mt-3 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
        >
          Add dependency
        </button>
      )}

      <h2 className="mt-8 text-sm font-semibold text-text-primary">Used as a dependency by</h2>
      {data && data.used_by.length === 0 ? (
        <p data-testid="used-by-empty" className="mt-2 text-sm text-text-secondary">
          No other device needs {device.name}.
        </p>
      ) : (
        <ul data-testid="used-by" className="mt-2 space-y-1 text-sm">
          {data?.used_by.map((link) => (
            <li key={link.id} className="flex items-center gap-2">
              <DeviceThumb url={link.parent.thumbnail_url} name={link.parent.name} size={24} />
              <Link href={`/gear/${link.parent.id}`} className="text-text-primary underline">
                {link.parent.name}
              </Link>
              <span className="text-text-secondary">
                needs {link.quantity_per_parent} × {device.name}
              </span>
            </li>
          ))}
        </ul>
      )}

      <AddDependencyDialog
        open={adding}
        onOpenChange={setAdding}
        device={device}
        taken={new Set((data?.needs ?? []).map((link) => link.associated.id))}
        onAdded={(next) => {
          setData(next)
          setAdding(false)
        }}
      />
    </section>
  )
}

function DependencyRow({
  link,
  canEdit,
  onChange,
  onRemove,
}: {
  link: DeviceDependency
  canEdit: boolean
  onChange: (link: DeviceDependency, body: Record<string, unknown>) => void
  onRemove: (link: DeviceDependency) => void
}) {
  const [quantity, setQuantity] = React.useState(String(link.quantity_per_parent))
  const [note, setNote] = React.useState(link.note ?? '')
  React.useEffect(() => setQuantity(String(link.quantity_per_parent)), [link.quantity_per_parent])
  React.useEffect(() => setNote(link.note ?? ''), [link.note])
  const field = 'h-7 rounded-md border border-border bg-bg-secondary px-1.5 text-sm text-text-primary'

  return (
    <tr data-testid="dependency-row" className="border-b border-border/60 align-top">
      <td className="py-2 pr-3">
        <span className="flex items-center gap-2">
          <DeviceThumb url={link.associated.thumbnail_url} name={link.associated.name} size={24} />
          <Link href={`/gear/${link.associated.id}`} className="text-text-primary underline">
            {link.associated.name}
          </Link>
        </span>
        {link.origin_device_name && (
          <span data-testid="dependency-origin" className="block text-xs text-text-tertiary">
            added via {link.origin_device_name}
          </span>
        )}
      </td>
      {canEdit ? (
        <>
          <td className="py-2 pr-3">
            <input
              aria-label={`Quantity of ${link.associated.name}`}
              data-testid="dependency-quantity"
              inputMode="numeric"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              onBlur={() => {
                const value = Number.parseInt(quantity, 10)
                if (Number.isInteger(value) && value >= 1 && value !== link.quantity_per_parent) {
                  onChange(link, { quantity_per_parent: value })
                } else setQuantity(String(link.quantity_per_parent))
              }}
              className={`${field} w-16`}
            />
          </td>
          <td className="py-2 pr-3">
            <select
              aria-label={`How ${link.associated.name} is booked`}
              data-testid="dependency-mode"
              value={link.booking_mode}
              onChange={(event) => onChange(link, { booking_mode: event.target.value })}
              className={field}
            >
              <option value="always">Always</option>
              <option value="ask">Ask when booking</option>
            </select>
          </td>
          <td className="py-2 pr-3">
            <select
              aria-label={`Price of ${link.associated.name}`}
              data-testid="dependency-pricing"
              value={link.pricing}
              onChange={(event) => onChange(link, { pricing: event.target.value })}
              className={field}
            >
              <option value="included">Included</option>
              <option value="charged">Charged</option>
            </select>
          </td>
          <td className="py-2 pr-3">
            <input
              type="checkbox"
              aria-label={`Show ${link.associated.name} on documents`}
              checked={link.show_on_documents}
              onChange={(event) => onChange(link, { show_on_documents: event.target.checked })}
            />
          </td>
          <td className="py-2 pr-3">
            <input
              aria-label={`Note on ${link.associated.name}`}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              onBlur={() => note !== (link.note ?? '') && onChange(link, { note: note || null })}
              className={`${field} w-full`}
            />
          </td>
          <td className="py-2 pr-3 text-right">
            <button
              type="button"
              data-testid="dependency-remove"
              onClick={() => onRemove(link)}
              className="text-xs underline"
            >
              Remove
            </button>
          </td>
        </>
      ) : (
        <>
          <td className="py-2 pr-3 text-text-secondary">{link.quantity_per_parent}</td>
          <td className="py-2 pr-3 text-text-secondary">{link.booking_mode === 'always' ? 'Always' : 'Ask when booking'}</td>
          <td className="py-2 pr-3 text-text-secondary">{link.pricing === 'included' ? 'Included' : 'Charged'}</td>
          <td className="py-2 pr-3 text-text-secondary">{link.show_on_documents ? 'Shown' : 'Hidden'}</td>
          <td className="py-2 pr-3 text-xs text-text-secondary">{link.note ?? ''}</td>
        </>
      )}
    </tr>
  )
}

// ─── Adding one, with the setup hint ────────────────────────────────────────

type HintChoice = { ticked: boolean; quantity: string }

function AddDependencyDialog({
  open,
  onOpenChange,
  device,
  taken,
  onAdded,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  device: DeviceDetail
  taken: Set<string>
  onAdded: (next: DeviceDependencies) => void
}) {
  const [devices, setDevices] = React.useState<DevicePage['devices']>([])
  const [search, setSearch] = React.useState('')
  const [chosen, setChosen] = React.useState<string | null>(null)
  const [quantity, setQuantity] = React.useState('1')
  const [mode, setMode] = React.useState<'always' | 'ask'>('always')
  const [pricing, setPricing] = React.useState<'included' | 'charged'>('included')
  const [show, setShow] = React.useState(false)
  const [note, setNote] = React.useState('')
  const [hint, setHint] = React.useState<DependencyHint | null>(null)
  const [choices, setChoices] = React.useState<Record<string, HintChoice>>({})
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (!open) return
    setSearch('')
    setChosen(null)
    setQuantity('1')
    setMode('always')
    setPricing('included')
    setShow(false)
    setNote('')
    setHint(null)
    setChoices({})
    setError(null)
    api
      .get<DevicePage>('/devices?limit=2000')
      .then((page) => setDevices(page.devices))
      .catch(() => setDevices([]))
  }, [open])

  const count = Number.parseInt(quantity, 10)
  const quantityValid = /^\d+$/.test(quantity.trim()) && count >= 1

  // The hint follows the chosen device and the quantity: its numbers are
  // per THIS device, pre-multiplied by the API.
  React.useEffect(() => {
    if (!chosen || !quantityValid) {
      setHint(null)
      return
    }
    let live = true
    api
      .get<DependencyHint>(
        `/devices/${device.id}/dependencies/hint?associated_device_id=${chosen}&quantity_per_parent=${count}`,
      )
      .then((next) => {
        if (!live) return
        setHint(next)
        setChoices(
          Object.fromEntries(
            next.rows.map((row) => [
              row.origin_association_id,
              { ticked: row.blocked_reason === null, quantity: String(row.suggested_quantity) },
            ]),
          ),
        )
      })
      .catch(() => live && setHint(null))
    return () => {
      live = false
    }
  }, [chosen, count, quantityValid, device.id])

  const candidates = devices.filter(
    (row) =>
      row.id !== device.id &&
      row.is_active &&
      (!search.trim() || row.name.toLowerCase().includes(search.trim().toLowerCase())),
  )
  const chosenName = devices.find((row) => row.id === chosen)?.name
  const why = !chosen
    ? 'Pick the device it needs.'
    : taken.has(chosen)
      ? `${device.name} already depends on ${chosenName}. Change the quantity in that row instead.`
      : !quantityValid
        ? 'The quantity is a whole number, 1 or more.'
        : null

  const save = async () => {
    if (why || !chosen) return
    setBusy(true)
    setError(null)
    try {
      const fromHint = (hint?.rows ?? [])
        .filter((row) => row.blocked_reason === null && choices[row.origin_association_id]?.ticked)
        .map((row) => {
          const typed = Number.parseInt(choices[row.origin_association_id].quantity, 10)
          return {
            origin_association_id: row.origin_association_id,
            quantity_per_parent: Number.isInteger(typed) && typed >= 1 ? typed : row.suggested_quantity,
          }
        })
      onAdded(
        await api.post<DeviceDependencies>(`/devices/${device.id}/dependencies`, {
          associated_device_id: chosen,
          quantity_per_parent: count,
          booking_mode: mode,
          pricing,
          show_on_documents: show,
          note: note || null,
          from_hint: fromHint,
        }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The dependency could not be added. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const field = 'h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary'
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content
          data-testid="dependency-dialog"
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border bg-bg-secondary p-6 shadow-xl"
        >
          <Dialog.Title className="text-sm font-semibold text-text-primary">
            Add a dependency to {device.name}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-text-tertiary">
            A device that goes out with {device.name}, such as its batteries.
          </Dialog.Description>

          <input
            aria-label="Find a device"
            data-testid="dependency-search"
            value={search}
            placeholder="Find a device"
            onChange={(event) => setSearch(event.target.value)}
            className={`${field} mt-4 w-full`}
          />
          <ul role="listbox" aria-label="Devices" className="mt-2 max-h-48 overflow-y-auto rounded-md border border-border">
            {candidates.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={chosen === row.id}
                  data-testid="dependency-option"
                  onClick={() => setChosen(row.id)}
                  className={`flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm ${
                    chosen === row.id ? 'bg-accent/10 text-text-primary' : 'text-text-secondary hover:bg-bg-tertiary'
                  }`}
                >
                  <DeviceThumb url={row.thumbnail_url} name={row.name} size={24} />
                  {row.name}
                  {row.category && <span className="text-xs text-text-tertiary">{row.category}</span>}
                </button>
              </li>
            ))}
            {candidates.length === 0 && <li className="px-2 py-1.5 text-sm text-text-tertiary">No device matches.</li>}
          </ul>

          <div className="mt-4 flex flex-wrap items-end gap-3 text-xs text-text-secondary">
            <label>
              How many per {device.name}
              <input
                data-testid="dependency-add-quantity"
                inputMode="numeric"
                value={quantity}
                onChange={(event) => setQuantity(event.target.value)}
                className={`${field} mt-1 block w-20`}
              />
            </label>
            <label>
              Booked
              <select
                data-testid="dependency-add-mode"
                value={mode}
                onChange={(event) => setMode(event.target.value as 'always' | 'ask')}
                className={`${field} mt-1 block`}
              >
                <option value="always">Always</option>
                <option value="ask">Ask when booking</option>
              </select>
            </label>
            <label>
              Price
              <select
                data-testid="dependency-add-pricing"
                value={pricing}
                onChange={(event) => setPricing(event.target.value as 'included' | 'charged')}
                className={`${field} mt-1 block`}
              >
                <option value="included">Included</option>
                <option value="charged">Charged</option>
              </select>
            </label>
            <label className="inline-flex items-center gap-1.5 pb-2">
              <input type="checkbox" checked={show} onChange={(event) => setShow(event.target.checked)} />
              Show on documents
            </label>
          </div>
          <label className="mt-3 block text-xs text-text-secondary">
            Note
            <input value={note} onChange={(event) => setNote(event.target.value)} className={`${field} mt-1 w-full`} />
          </label>

          {hint && hint.rows.length > 0 && (
            <div data-testid="dependency-hint" className="mt-4 rounded-md border border-border bg-bg-primary p-3 text-sm">
              <p className="text-text-primary">
                {hint.associated.name} has its own dependencies:{' '}
                {hint.rows
                  .map((row) => `${row.device.name} (${row.quantity_per_associated} per ${hint.associated.name})`)
                  .join(', ')}
                . They will not be booked through {device.name} unless you add them here.
              </p>
              <ul className="mt-2 space-y-1">
                {hint.rows.map((row) => {
                  const choice = choices[row.origin_association_id] ?? { ticked: false, quantity: '' }
                  return (
                    <li key={row.origin_association_id} data-testid="dependency-hint-row" className="flex flex-wrap items-center gap-2">
                      <input
                        type="checkbox"
                        aria-label={`Also add ${row.device.name}`}
                        data-testid="dependency-hint-tick"
                        disabled={row.blocked_reason !== null}
                        checked={choice.ticked && row.blocked_reason === null}
                        onChange={(event) =>
                          setChoices({ ...choices, [row.origin_association_id]: { ...choice, ticked: event.target.checked } })
                        }
                      />
                      <span className="text-text-primary">{row.device.name}</span>
                      <input
                        aria-label={`How many ${row.device.name} per ${device.name}`}
                        data-testid="dependency-hint-quantity"
                        inputMode="numeric"
                        disabled={row.blocked_reason !== null}
                        value={choice.quantity}
                        onChange={(event) =>
                          setChoices({ ...choices, [row.origin_association_id]: { ...choice, quantity: event.target.value } })
                        }
                        className={`${field} w-16`}
                      />
                      <span className="text-xs text-text-tertiary">per {device.name}</span>
                      {row.blocked_reason && (
                        <span data-testid="dependency-hint-blocked" className="text-xs text-warning">
                          {row.blocked_reason}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid="dependency-add-save"
              disabled={busy || why !== null}
              onClick={() => void save()}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Add dependency
            </button>
            <Dialog.Close asChild>
              <button type="button" className="text-sm text-text-secondary hover:text-text-primary">
                Cancel
              </button>
            </Dialog.Close>
            {why && (
              <span data-testid="dependency-add-why" className="text-xs text-text-tertiary">
                {why}
              </span>
            )}
          </div>
          {error && (
            <p role="alert" data-testid="dependency-add-error" className="mt-3 text-sm text-danger">
              {error}
            </p>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
