'use client'

import * as React from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import type {
  Item,
  ItemCategory,
  ItemKind,
  ItemRate,
  PeriodUnit,
  RateType,
  RentalFactorGroup,
  TaxClass,
  Unit,
} from '@/types'
import {
  CatalogNoAccess,
  CatalogReadOnlyNotice,
  CatalogTabs,
  useCatalogAccess,
} from '../catalog-chrome'

/**
 * One catalog item: what it is, and what it costs.
 *
 * The rate table is a `PUT` of the whole set, because that is what the
 * endpoint is — `PUT /catalog/items/{id}/rates` replaces the lot and writes
 * one `catalog.item.rates_changed` audit event with the before and after on
 * it. Patching one row at a time would produce an audit trail of four
 * events for one correction and no record of what the set looked like.
 *
 * Amounts are typed as text and sent as text. This screen never parses a
 * money string (rule 1) — "1.200,00" and "1200.00" are a server decision.
 */

const KIND_LABELS: Record<string, string> = {
  labor: 'Crew & labour',
  equipment_rental: 'Equipment rental',
  space_rental: 'Space rental',
  travel: 'Travel',
  per_diem: 'Per diem',
  storage: 'Storage',
  product: 'Product',
  software_license: 'Software licence',
  fee: 'Fee',
  other: 'Other',
}

const KINDS = Object.keys(KIND_LABELS) as ItemKind[]
const RATE_TYPES: RateType[] = ['bill', 'cost']
const PERIOD_UNITS: PeriodUnit[] = [
  'hour',
  'day',
  'week',
  'month',
  'piece',
  'flat',
]

interface RateDraft {
  rate_type: RateType
  period_unit: PeriodUnit
  amount: string
}

export default function ItemDetailPage() {
  const params = useParams<{ id: string }>()
  const itemId = params?.id
  const { loaded, canEdit, refused } = useCatalogAccess()

  const [item, setItem] = React.useState<Item | null>(null)
  const [categories, setCategories] = React.useState<ItemCategory[]>([])
  const [units, setUnits] = React.useState<Unit[]>([])
  const [groups, setGroups] = React.useState<RentalFactorGroup[]>([])
  const [taxClasses, setTaxClasses] = React.useState<string[]>([])
  const [error, setError] = React.useState<string | null>(null)
  const [saved, setSaved] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)

  const load = React.useCallback(async () => {
    if (!itemId) return
    try {
      setItem(await api.get<Item>(`/catalog/items/${itemId}`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the item')
    } finally {
      setLoading(false)
    }
  }, [itemId])

  React.useEffect(() => {
    if (!loaded) return
    void load()
  }, [loaded, load])

  React.useEffect(() => {
    if (!loaded) return
    void (async () => {
      const results = await Promise.allSettled([
        api.get<ItemCategory[]>('/catalog/categories'),
        api.get<Unit[]>('/catalog/units'),
        api.get<RentalFactorGroup[]>('/catalog/rental-factor-groups'),
        api.get<TaxClass[]>('/company/tax/classes'),
      ])
      if (results[0].status === 'fulfilled') setCategories(results[0].value)
      if (results[1].status === 'fulfilled') setUnits(results[1].value)
      if (results[2].status === 'fulfilled') setGroups(results[2].value)
      if (results[3].status === 'fulfilled') {
        // Only the active ones: `_resolve_item_references` refuses a
        // `default_tax_class` whose class is switched off, and offering it
        // in the picker would be offering a 409.
        setTaxClasses(
          results[3].value
            .filter((entry) => entry.is_active)
            .map((entry) => entry.key),
        )
      }
    })()
  }, [loaded])

  if (refused) return <CatalogNoAccess title="Item" />

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-text-secondary">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading&hellip;
      </div>
    )
  }

  if (!item) {
    return (
      <div className="p-8">
        <Link
          href="/catalog"
          className="inline-flex items-center gap-1 text-sm text-text-secondary hover:text-accent"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Catalog
        </Link>
        <p role="alert" className="mt-4 text-sm text-danger">
          {error ?? 'That item does not exist in this company.'}
        </p>
      </div>
    )
  }

  const announce = (message: string) => {
    setSaved(message)
    setError(null)
    setTimeout(() => setSaved(null), 2500)
  }

  return (
    <div className="max-w-4xl p-8">
      <Link
        href="/catalog"
        className="inline-flex items-center gap-1 text-sm text-text-secondary hover:text-accent"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Catalog
      </Link>

      <div className="mt-3 flex items-start justify-between gap-4">
        <div>
          <h1 data-testid="item-name" className="text-lg font-semibold text-text-primary">
            {item.name_de}
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            {KIND_LABELS[item.kind] ?? item.kind}
            {item.sku ? ` · ${item.sku}` : ''}
            {item.category_path ? ` · ${item.category_path}` : ''}
            {item.is_active ? '' : ' · retired'}
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            data-testid="item-toggle-active"
            onClick={async () => {
              const action = item.is_active ? 'deactivate' : 'reactivate'
              try {
                setItem(
                  await api.post<Item>(`/catalog/items/${item.id}/${action}`),
                )
                announce(
                  item.is_active ? 'Item retired.' : 'Item back in the list.',
                )
              } catch (err) {
                setError(
                  err instanceof ApiError ? err.detail : 'Could not change that',
                )
              }
            }}
            className="rounded-md border border-border px-3 py-1.5 text-sm text-text-secondary"
          >
            {item.is_active ? 'Retire item' : 'Reactivate item'}
          </button>
        )}
      </div>

      {loaded && !canEdit && <CatalogReadOnlyNotice />}

      <CatalogTabs />

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}
      {saved && (
        <p role="status" className="mt-4 text-sm text-success">
          {saved}
        </p>
      )}

      <ItemFields
        item={item}
        canEdit={canEdit}
        categories={categories}
        units={units}
        groups={groups}
        taxClasses={taxClasses}
        onSaved={(next) => {
          setItem(next)
          announce('Item saved.')
        }}
        onError={setError}
      />

      <RateTable
        item={item}
        canEdit={canEdit}
        onSaved={(rates) => {
          setItem({ ...item, rates })
          announce('Rates saved.')
        }}
        onError={setError}
      />
    </div>
  )
}

function ItemFields({
  item,
  canEdit,
  categories,
  units,
  groups,
  taxClasses,
  onSaved,
  onError,
}: {
  item: Item
  canEdit: boolean
  categories: ItemCategory[]
  units: Unit[]
  groups: RentalFactorGroup[]
  taxClasses: string[]
  onSaved: (item: Item) => void
  onError: (message: string) => void
}) {
  const [draft, setDraft] = React.useState({
    sku: item.sku ?? '',
    name_de: item.name_de,
    name_en: item.name_en ?? '',
    kind: item.kind as ItemKind,
    category_id: item.category_id ?? '',
    unit_id: item.unit_id ?? '',
    rental_factor_group_id: item.rental_factor_group_id ?? '',
    default_tax_class: item.default_tax_class ?? '',
    revenue_account: item.revenue_account ?? '',
    expense_account: item.expense_account ?? '',
    rebilled_cost: item.rebilled_cost,
    description_customer: item.description_customer ?? '',
    notes: item.notes ?? '',
  })
  const [saving, setSaving] = React.useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    try {
      onSaved(
        await api.patch<Item>(`/catalog/items/${item.id}`, {
          sku: draft.sku.trim() || null,
          name_de: draft.name_de.trim(),
          name_en: draft.name_en.trim() || null,
          kind: draft.kind,
          category_id: draft.category_id || null,
          unit_id: draft.unit_id || null,
          rental_factor_group_id: draft.rental_factor_group_id || null,
          default_tax_class: draft.default_tax_class || null,
          revenue_account: draft.revenue_account.trim() || null,
          expense_account: draft.expense_account.trim() || null,
          rebilled_cost: draft.rebilled_cost,
          description_customer: draft.description_customer.trim() || null,
          notes: draft.notes.trim() || null,
        }),
      )
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : 'Could not save the item')
    } finally {
      setSaving(false)
    }
  }

  const set = <K extends keyof typeof draft>(
    key: K,
    value: (typeof draft)[K],
  ) => setDraft((current) => ({ ...current, [key]: value }))

  return (
    <form onSubmit={submit} data-testid="item-fields" className="mt-6">
      <h2 className="text-sm font-semibold text-text-primary">Details</h2>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-text-secondary">
          Name (German)
          <Input
            value={draft.name_de}
            disabled={!canEdit}
            onChange={(event) => set('name_de', event.target.value)}
            className="mt-1"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Name (English)
          <Input
            value={draft.name_en}
            disabled={!canEdit}
            onChange={(event) => set('name_en', event.target.value)}
            className="mt-1"
          />
        </label>
        <label className="text-xs text-text-secondary">
          SKU
          <Input
            value={draft.sku}
            disabled={!canEdit}
            onChange={(event) => set('sku', event.target.value)}
            className="mt-1"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Kind
          <select
            value={draft.kind}
            disabled={!canEdit}
            onChange={(event) => set('kind', event.target.value as ItemKind)}
            className="mt-1 w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            {KINDS.map((value) => (
              <option key={value} value={value}>
                {KIND_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-text-secondary">
          Category
          <select
            value={draft.category_id}
            disabled={!canEdit}
            onChange={(event) => set('category_id', event.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            <option value="">None</option>
            {categories.map((category) => (
              <option key={category.id} value={category.id}>
                {category.path || category.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-text-secondary">
          Unit
          <select
            value={draft.unit_id}
            disabled={!canEdit}
            onChange={(event) => set('unit_id', event.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            <option value="">None</option>
            {units.map((unit) => (
              <option key={unit.id} value={unit.id}>
                {unit.label_de}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-text-secondary">
          Rental factor table
          <select
            value={draft.rental_factor_group_id}
            disabled={!canEdit}
            onChange={(event) =>
              set('rental_factor_group_id', event.target.value)
            }
            className="mt-1 w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            <option value="">None — billed per unit</option>
            {groups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-text-secondary">
          Default tax class
          <select
            value={draft.default_tax_class}
            disabled={!canEdit}
            onChange={(event) => set('default_tax_class', event.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            <option value="">From the document</option>
            {taxClasses.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-text-secondary">
          Revenue account
          <Input
            value={draft.revenue_account}
            disabled={!canEdit}
            onChange={(event) => set('revenue_account', event.target.value)}
            className="mt-1"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Expense account
          <Input
            value={draft.expense_account}
            disabled={!canEdit}
            onChange={(event) => set('expense_account', event.target.value)}
            className="mt-1"
          />
        </label>
      </div>

      {draft.kind === 'travel' && (
        <label className="mt-3 flex items-center gap-2 text-xs text-text-secondary">
          <input
            type="checkbox"
            checked={draft.rebilled_cost}
            disabled={!canEdit}
            onChange={(event) => set('rebilled_cost', event.target.checked)}
          />
          Rebilled cost — passed on to the client at what it cost us
        </label>
      )}

      <label className="mt-3 block text-xs text-text-secondary">
        Text on the document
        <textarea
          value={draft.description_customer}
          disabled={!canEdit}
          rows={2}
          onChange={(event) => set('description_customer', event.target.value)}
          className="mt-1 w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
        />
      </label>
      <label className="mt-3 block text-xs text-text-secondary">
        Internal notes
        <textarea
          value={draft.notes}
          disabled={!canEdit}
          rows={2}
          onChange={(event) => set('notes', event.target.value)}
          className="mt-1 w-full rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
        />
      </label>

      {canEdit && (
        <button
          type="submit"
          data-testid="item-save"
          disabled={saving || !draft.name_de.trim()}
          className="mt-4 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save details'}
        </button>
      )}
      {canEdit && !draft.name_de.trim() && (
        // 17c again: never a dead button with nothing said.
        <p className="mt-2 text-xs text-text-tertiary">
          The German name cannot be empty — it is what the list and the
          document line show.
        </p>
      )}
    </form>
  )
}

function RateTable({
  item,
  canEdit,
  onSaved,
  onError,
}: {
  item: Item
  canEdit: boolean
  onSaved: (rates: ItemRate[]) => void
  onError: (message: string) => void
}) {
  const [rows, setRows] = React.useState<RateDraft[]>(
    (item.rates ?? []).map((rate) => ({
      rate_type: rate.rate_type as RateType,
      period_unit: rate.period_unit as PeriodUnit,
      amount: rate.amount,
    })),
  )
  const [saving, setSaving] = React.useState(false)

  const save = async () => {
    setSaving(true)
    try {
      // Sent whole: this is a replacement, and the server audits it as one.
      onSaved(
        await api.put<ItemRate[]>(
          `/catalog/items/${item.id}/rates`,
          rows.map((row) => ({
            rate_type: row.rate_type,
            period_unit: row.period_unit,
            amount: row.amount.trim(),
          })),
        ),
      )
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : 'Could not save the rates')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section data-testid="item-rates" className="mt-8">
      <h2 className="text-sm font-semibold text-text-primary">Rates</h2>
      <p className="mt-1 text-xs text-text-secondary">
        A bill rate is what the client pays; a cost rate is what it costs
        us. Both are optional, and an item can carry one of each per period.
      </p>

      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-text-secondary">
          No rates — this item gets its price on the document.
        </p>
      ) : (
        <table className="mt-3 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
              <th className="py-2 pr-3 font-medium">Type</th>
              <th className="py-2 pr-3 font-medium">Per</th>
              <th className="py-2 pr-3 font-medium">Amount</th>
              <th className="py-2 pr-3 font-medium">Currency</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} data-testid="rate-row" className="border-b border-border/60">
                <td className="py-2 pr-3">
                  <select
                    aria-label="Rate type"
                    value={row.rate_type}
                    disabled={!canEdit}
                    onChange={(event) =>
                      setRows(
                        rows.map((current, i) =>
                          i === index
                            ? {
                                ...current,
                                rate_type: event.target.value as RateType,
                              }
                            : current,
                        ),
                      )
                    }
                    className="rounded-md border border-border bg-bg-primary px-2 py-1 text-sm text-text-primary"
                  >
                    {RATE_TYPES.map((value) => (
                      <option key={value} value={value}>
                        {value === 'bill' ? 'Bill' : 'Cost'}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-2 pr-3">
                  <select
                    aria-label="Period"
                    value={row.period_unit}
                    disabled={!canEdit}
                    onChange={(event) =>
                      setRows(
                        rows.map((current, i) =>
                          i === index
                            ? {
                                ...current,
                                period_unit: event.target.value as PeriodUnit,
                              }
                            : current,
                        ),
                      )
                    }
                    className="rounded-md border border-border bg-bg-primary px-2 py-1 text-sm text-text-primary"
                  >
                    {PERIOD_UNITS.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-2 pr-3">
                  <Input
                    aria-label="Amount"
                    value={row.amount}
                    disabled={!canEdit}
                    onChange={(event) =>
                      setRows(
                        rows.map((current, i) =>
                          i === index
                            ? { ...current, amount: event.target.value }
                            : current,
                        ),
                      )
                    }
                    className="w-28 font-mono"
                  />
                </td>
                <td className="py-2 pr-3 text-xs text-text-tertiary">
                  {item.rates?.[index]?.currency ?? ''}
                </td>
                <td className="py-2 text-right">
                  {canEdit && (
                    <button
                      type="button"
                      aria-label="Remove rate"
                      onClick={() =>
                        setRows(rows.filter((_, i) => i !== index))
                      }
                      className="text-xs text-text-secondary hover:text-danger"
                    >
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canEdit && (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            data-testid="rate-add"
            onClick={() =>
              setRows([
                ...rows,
                { rate_type: 'bill', period_unit: 'day', amount: '' },
              ])
            }
            className="rounded-md border border-border px-3 py-1.5 text-sm text-text-secondary"
          >
            Add a rate
          </button>
          <button
            type="button"
            data-testid="rates-save"
            disabled={saving}
            onClick={() => void save()}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save rates'}
          </button>
        </div>
      )}
    </section>
  )
}
