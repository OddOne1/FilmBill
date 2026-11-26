'use client'

import * as React from 'react'
import Link from 'next/link'
import { api, ApiError } from '@/lib/api'
import { formatMoney } from '@/lib/money'
import type { DeviceDetail, RentalFactorGroup } from '@/types'
import { AMBIGUOUS_AMOUNT, amountForApi, amountIsAmbiguous } from '../gear-chrome'

/**
 * Rental prices (B2b-2). A device has no prices of its own: these ARE the
 * rates of its catalog item, read and written through the API's catalog
 * service, so this page and a budget line made from the item always agree.
 *
 * There is no picker of other items. Without an item the section offers
 * "Set up rental price" — or "Use the existing 'X'" when the catalog already
 * has an unlinked equipment item of exactly this name. The API decides which;
 * both buttons send the same request.
 *
 * Rates are shown only to a role that may see budgets (`rates_visible`), and
 * written only with the catalog edit permission (`can_edit`); otherwise the
 * section says why (rule 17c).
 */
export function RentalPrices({
  device,
  currency,
  onChanged,
}: {
  device: DeviceDetail
  currency: string
  onChanged: (next: DeviceDetail, message: string) => void
}) {
  const rental = device.rental
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const setUp = async () => {
    setBusy(true)
    setError(null)
    try {
      const next = await api.post<DeviceDetail>(`/devices/${device.id}/rental/setup`, {})
      onChanged(next, `The rental price of ${device.name} is now kept in the catalog as ${next.rental.item_name}.`)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The rental price could not be set up. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section data-testid="rental-prices" className="mt-8">
      <h2 className="text-sm font-semibold text-text-primary">Rental prices</h2>
      {!rental.linked ? (
        <div className="mt-2 text-sm text-text-secondary">
          <p>This device has no rental price yet.</p>
          {rental.can_edit ? (
            <button
              type="button"
              data-testid="rental-setup"
              disabled={busy}
              onClick={() => void setUp()}
              className="mt-2 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {rental.existing_item ? `Use the existing '${rental.existing_item.name}'` : 'Set up rental price'}
            </button>
          ) : (
            <p data-testid="rental-why" className="mt-1 text-xs text-text-tertiary">
              {rental.edit_blocked_reason}
            </p>
          )}
          {rental.can_edit && rental.existing_item && (
            <p className="mt-1 text-xs text-text-tertiary">
              The catalog already has an equipment item called {rental.existing_item.name}; its prices
              become this device&rsquo;s.
            </p>
          )}
        </div>
      ) : rental.can_edit ? (
        <RentalForm device={device} onChanged={onChanged} />
      ) : (
        <>
          <dl className="mt-2 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            {rental.rates_visible && (
              <>
                <dt className="text-text-tertiary">Price per day (net)</dt>
                <dd data-testid="rental-price-read" className="text-text-primary">
                  {rental.price_per_day ? formatMoney(rental.price_per_day, currency) : '—'}
                </dd>
                <dt className="text-text-tertiary">Cost per day (net)</dt>
                <dd className="text-text-primary">
                  {rental.cost_per_day ? formatMoney(rental.cost_per_day, currency) : '—'}
                </dd>
              </>
            )}
            <dt className="text-text-tertiary">Rental factor table</dt>
            <dd className="text-text-primary">{rental.rental_factor_group_name ?? 'None — every day is billed'}</dd>
          </dl>
          <p data-testid="rental-why" className="mt-2 text-xs text-text-tertiary">
            {!rental.rates_visible && 'Prices and costs are only shown to people who can see budgets. '}
            {rental.edit_blocked_reason}
          </p>
        </>
      )}
      {rental.linked && (
        <p data-testid="rental-catalog-name" className="mt-2 text-xs text-text-tertiary">
          Priced in the catalog as{' '}
          <Link href={`/catalog/${rental.item_id}`} className="underline">
            &lsquo;{rental.item_name}&rsquo;
          </Link>
          {rental.item_is_active === false && ' — that item is retired in the catalog.'}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
    </section>
  )
}

function RentalForm({
  device,
  onChanged,
}: {
  device: DeviceDetail
  onChanged: (next: DeviceDetail, message: string) => void
}) {
  const rental = device.rental
  const initial = React.useMemo(
    () => ({
      price: rental.price_per_day ? rental.price_per_day.replace('.', ',') : '',
      cost: rental.cost_per_day ? rental.cost_per_day.replace('.', ',') : '',
      group: rental.rental_factor_group_id ?? '',
    }),
    [rental],
  )
  const [draft, setDraft] = React.useState(initial)
  const [groups, setGroups] = React.useState<RentalFactorGroup[]>([])
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => setDraft(initial), [initial])
  React.useEffect(() => {
    api
      .get<RentalFactorGroup[]>('/catalog/rental-factor-groups')
      .then(setGroups)
      .catch(() => setGroups([]))
  }, [])

  const ambiguous =
    amountIsAmbiguous(draft.price) || (rental.rates_visible && amountIsAmbiguous(draft.cost))
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial)

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (ambiguous || !dirty) return
    const body: Record<string, unknown> = {}
    if (draft.price !== initial.price) body.price_per_day = amountForApi(draft.price)
    if (rental.rates_visible && draft.cost !== initial.cost) body.cost_per_day = amountForApi(draft.cost)
    if (draft.group !== initial.group) body.rental_factor_group_id = draft.group || null
    setBusy(true)
    setError(null)
    try {
      onChanged(
        await api.put<DeviceDetail>(`/devices/${device.id}/rental`, body),
        'Rental prices saved — the catalog item has the same numbers.',
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The rental prices could not be saved. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const field = 'mt-1 h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary'
  return (
    <form data-testid="rental-form" onSubmit={(event) => void save(event)} className="mt-2">
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-xs text-text-secondary">
          Price per day (net)
          <input
            data-testid="rental-price"
            inputMode="decimal"
            value={draft.price}
            onChange={(e) => setDraft({ ...draft, price: e.target.value })}
            className={field}
          />
        </label>
        {rental.rates_visible && (
          <label className="text-xs text-text-secondary">
            Cost per day (net)
            <input
              data-testid="rental-cost"
              inputMode="decimal"
              value={draft.cost}
              placeholder="What a rental day costs us"
              onChange={(e) => setDraft({ ...draft, cost: e.target.value })}
              className={field}
            />
          </label>
        )}
        <label className="text-xs text-text-secondary">
          Rental factor table
          <select
            data-testid="rental-group"
            value={draft.group}
            onChange={(e) => setDraft({ ...draft, group: e.target.value })}
            className={field}
          >
            <option value="">None — every day is billed</option>
            {rental.rental_factor_group_id && !groups.some((g) => g.id === rental.rental_factor_group_id) && (
              <option value={rental.rental_factor_group_id}>{rental.rental_factor_group_name ?? 'The current table'}</option>
            )}
            {groups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="submit"
          data-testid="rental-save"
          disabled={busy || !dirty || ambiguous}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Save rental prices
        </button>
        <span data-testid="rental-save-why" className="text-xs text-text-tertiary">
          {ambiguous ? AMBIGUOUS_AMOUNT : !dirty ? 'Nothing changed yet.' : 'Saved to the catalog item as well.'}
        </span>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  )
}
