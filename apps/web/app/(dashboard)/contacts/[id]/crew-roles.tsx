'use client'

import * as React from 'react'
import Link from 'next/link'
import { api, ApiError } from '@/lib/api'
import { formatMoney } from '@/lib/money'
import { usePermissions } from '@/hooks/use-permissions'
import type { CrewRole, Item, ItemPage, PartyCrewRoles } from '@/types'

/**
 * Roles & rates on a crew person's page (R1).
 *
 * A role is one of the catalog's labor items; the typical day rate is that
 * item's cost rate and is edited in the catalog ("Edit typical rates"), not
 * here. The person may have an own rate per role. Every number on this
 * section is the API's — `effective_day_rate`, `rate_source` and
 * `difference_to_typical` are never worked out on the page — and there are
 * none at all for somebody without `budgets.view` (`rates_visible: false`).
 *
 * A tag that matches a labor item is OFFERED as a role ("Use 'Gaffer' as a
 * role"), one click per tag; nothing is converted by itself.
 */
export function CrewRolesSection({
  partyId,
  partyName,
  canEdit,
  onChanged,
}: {
  partyId: string
  partyName: string
  canEdit: boolean
  /** The party's tags may change (a tag became a role). */
  onChanged: () => void
}) {
  const { active } = usePermissions()
  const currency = active?.default_currency ?? 'EUR'
  const [data, setData] = React.useState<PartyCrewRoles | null>(null)
  const [items, setItems] = React.useState<Item[]>([])
  const [error, setError] = React.useState<string | null>(null)
  const [search, setSearch] = React.useState('')
  const [itemId, setItemId] = React.useState('')
  const [ownRate, setOwnRate] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setData(await api.get<PartyCrewRoles>(`/parties/${partyId}/crew-roles`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The roles could not be loaded. Try again.')
    }
  }, [partyId])

  React.useEffect(() => {
    void load()
  }, [load])

  React.useEffect(() => {
    if (!canEdit) return
    api
      .get<ItemPage>('/catalog/items?kind=labor&limit=500')
      .then((page) => setItems(page.items))
      .catch(() => setItems([]))
  }, [canEdit])

  const act = async (call: () => Promise<PartyCrewRoles>, failure: string, tagsChanged = false) => {
    setBusy(true)
    setError(null)
    try {
      setData(await call())
      if (tagsChanged) onChanged()
      return true
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : failure)
      return false
    } finally {
      setBusy(false)
    }
  }

  if (data === null) {
    return error ? (
      <p role="alert" className="mt-6 text-sm text-status-error">
        {error}
      </p>
    ) : null
  }

  const held = new Set(data.roles.map((role) => role.item_id))
  const needle = search.trim().toLowerCase()
  const choices = items.filter(
    (item) => item.is_active && !held.has(item.id) && (!needle || item.name_de.toLowerCase().includes(needle)),
  )
  const visible = data.rates_visible

  const add = async () => {
    if (!itemId) return
    const ok = await act(
      () =>
        api.post<PartyCrewRoles>(`/parties/${partyId}/crew-roles`, {
          item_id: itemId,
          ...(visible && ownRate.trim() ? { own_day_rate: ownRate.trim().replace(',', '.') } : {}),
        }),
      'The role could not be added. Try again.',
    )
    if (ok) {
      setItemId('')
      setOwnRate('')
      setSearch('')
    }
  }

  return (
    <section data-testid="crew-roles" aria-label="Roles & rates" className="mt-8 border-t border-border pt-5">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-medium text-text-primary">Roles &amp; rates</h2>
        <Link href="/catalog?kind=labor" data-testid="crew-roles-typical-link" className="text-xs text-accent hover:underline">
          Edit typical rates
        </Link>
      </div>
      <p className="mt-1 text-xs text-text-tertiary">
        A role is a labor item in the catalog; its cost per day there is the typical rate.
        {visible ? ' An own rate here is what this person costs per day in that role.' : ''}
      </p>
      {!visible && (
        <p data-testid="crew-roles-no-rates" className="mt-2 text-xs text-text-secondary">
          Rates are hidden: seeing what people cost needs access to budgets. An owner or
          administrator can grant it under Settings → Company → Members.
        </p>
      )}
      {!canEdit && (
        <p className="mt-2 text-xs text-text-secondary">
          You can read these roles but not change them — that needs permission to edit contacts.
        </p>
      )}

      {data.roles.length === 0 ? (
        <p data-testid="crew-roles-empty" className="mt-3 text-sm text-text-secondary">
          {partyName} has no roles from the catalog yet.
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-border/60">
          {data.roles.map((role) => (
            <RoleRow
              key={role.id}
              role={role}
              visible={visible}
              currency={currency}
              canEdit={canEdit}
              busy={busy}
              onRate={(value) =>
                act(
                  () =>
                    api.patch<PartyCrewRoles>(`/parties/${partyId}/crew-roles/${role.id}`, {
                      own_day_rate: value === '' ? null : value.replace(',', '.'),
                    }),
                  'The rate could not be saved. Try again.',
                )
              }
              onRemove={() =>
                act(
                  () => api.post<PartyCrewRoles>(`/parties/${partyId}/crew-roles/${role.id}/remove`, {}),
                  'The role could not be removed. Try again.',
                )
              }
            />
          ))}
        </ul>
      )}

      {canEdit && data.tag_suggestions.length > 0 && (
        <div data-testid="crew-roles-tag-suggestions" className="mt-3 flex flex-wrap gap-2">
          {data.tag_suggestions.map((entry) => (
            <button
              key={entry.tag}
              type="button"
              disabled={busy}
              onClick={() =>
                void act(
                  () => api.post<PartyCrewRoles>(`/parties/${partyId}/crew-roles/from-tag`, { tag: entry.tag }),
                  'The tag could not be turned into a role. Try again.',
                  true,
                )
              }
              className="rounded-md border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-secondary"
            >
              Use ‘{entry.tag}’ as a role
            </button>
          ))}
        </div>
      )}

      {canEdit && (
        <div className="mt-4 flex flex-wrap items-end gap-2">
          <label className="text-xs text-text-secondary">
            Find a role
            <input
              data-testid="crew-roles-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="DoP, Gaffer, Sound…"
              className="mt-1 block h-8 w-40 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
            />
          </label>
          <label className="text-xs text-text-secondary">
            Role
            <select
              data-testid="crew-roles-item"
              value={itemId}
              onChange={(event) => setItemId(event.target.value)}
              className="mt-1 block h-8 w-48 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
            >
              <option value="">Choose a labor item…</option>
              {choices.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name_de}
                </option>
              ))}
            </select>
          </label>
          {visible && (
            <label className="text-xs text-text-secondary">
              Own day rate (optional)
              <input
                data-testid="crew-roles-own-rate"
                inputMode="decimal"
                value={ownRate}
                onChange={(event) => setOwnRate(event.target.value)}
                className="mt-1 block h-8 w-28 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
              />
            </label>
          )}
          <button
            type="button"
            data-testid="crew-roles-add"
            disabled={busy || !itemId}
            onClick={() => void add()}
            className="h-8 rounded-md bg-accent px-3 text-sm font-medium text-white disabled:opacity-50"
          >
            Add role
          </button>
          {!itemId && (
            // 17c: why the button is dead.
            <span className="text-xs text-text-tertiary">
              {items.length === 0
                ? 'The catalog has no labor items yet. Add one under Catalog, then choose it here.'
                : 'Choose a role to add it.'}
            </span>
          )}
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-status-error">
          {error}
        </p>
      )}
    </section>
  )
}

/** "own, +50.00 above typical" — the words for the API's numbers. */
export function rateSourceText(role: CrewRole, currency: string): string {
  if (role.rate_source === 'own') {
    const diff = role.difference_to_typical
    if (diff === null || diff === undefined) return 'own rate (no typical rate in the catalog)'
    if (diff.startsWith('-')) return `own, ${formatMoney(diff.slice(1), currency)} below typical`
    if (/^0+(\.0+)?$/.test(diff)) return 'own, same as typical'
    return `own, +${formatMoney(diff, currency)} above typical`
  }
  if (role.rate_source === 'typical') return 'typical rate'
  if (role.rate_source === 'none') return 'no rate: the catalog item has no cost per day'
  return ''
}

function RoleRow({
  role,
  visible,
  currency,
  canEdit,
  busy,
  onRate,
  onRemove,
}: {
  role: CrewRole
  visible: boolean
  currency: string
  canEdit: boolean
  busy: boolean
  onRate: (value: string) => Promise<boolean>
  onRemove: () => void
}) {
  const [draft, setDraft] = React.useState(role.own_day_rate ?? '')
  React.useEffect(() => setDraft(role.own_day_rate ?? ''), [role.own_day_rate])
  const dirty = draft.trim() !== (role.own_day_rate ?? '')

  return (
    <li data-testid="crew-role" className="flex flex-wrap items-center gap-3 py-2 text-sm">
      <span className="min-w-[8rem] font-medium text-text-primary">{role.name}</span>
      {visible && (
        <>
          <span data-testid="crew-role-effective" className="text-text-primary">
            {role.effective_day_rate ? `${formatMoney(role.effective_day_rate, currency)} / day` : '—'}
          </span>
          <span data-testid="crew-role-source" className="text-xs text-text-secondary">
            {rateSourceText(role, currency)}
          </span>
          {canEdit && (
            <span className="flex items-center gap-1">
              <input
                aria-label={`Own day rate for ${role.name}`}
                data-testid="crew-role-own-rate"
                inputMode="decimal"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="own rate"
                className="h-7 w-24 rounded-md border border-border bg-bg-secondary px-2 text-sm"
              />
              <span data-testid="crew-role-typical" className="text-xs text-text-tertiary">
                typical: {role.typical_day_rate ? formatMoney(role.typical_day_rate, currency) : 'none'}
              </span>
              {dirty && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onRate(draft.trim())}
                  className="rounded border border-border px-2 py-0.5 text-xs"
                >
                  Save
                </button>
              )}
            </span>
          )}
        </>
      )}
      {canEdit && (
        <button
          type="button"
          disabled={busy}
          onClick={onRemove}
          className="ml-auto text-xs text-text-tertiary hover:text-status-error"
        >
          Remove
        </button>
      )}
    </li>
  )
}
