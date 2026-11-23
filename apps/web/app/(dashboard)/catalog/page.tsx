'use client'

import * as React from 'react'
import Link from 'next/link'
import { Loader2, Plus, Search } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { useCompanyStore } from '@/stores/company-store'
import type { ItemCategory, ItemKind, ItemPage, Unit } from '@/types'
import {
  CatalogNoAccess,
  CatalogReadOnlyNotice,
  CatalogTabs,
  useCatalogAccess,
} from './catalog-chrome'

/**
 * The item list — the price list itself.
 *
 * **Nothing on this screen adds a number up.** Rates are rendered exactly as
 * the API serialised them (`"1200.00"`, a string — rule 1), and the only
 * arithmetic anywhere in the catalog UI is on "Try the engine", which asks
 * `POST /calc/preview` for it.
 *
 * **The search and the filters are the server's.** A client-side filter
 * would only ever search the page it had already loaded, so every request
 * goes out as a query string and the list renders what comes back.
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

/** `bill`/`day` out of an item's rate list, or an em dash.
 *
 *  Picked, never computed: an item billed per week and costed per day has
 *  two rates and no "the rate", and guessing one here would put a number on
 *  screen that the engine would never use. */
function rateOf(
  item: ItemPage['items'][number],
  rateType: 'bill' | 'cost',
): string | null {
  const found = item.rates?.find(
    (rate) => rate.rate_type === rateType && rate.period_unit === 'day',
  )
  return found ? found.amount : null
}

export default function CatalogPage() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const { loaded, canEdit, refused } = useCatalogAccess()

  const [search, setSearch] = React.useState('')
  const [kind, setKind] = React.useState('')
  const [categoryId, setCategoryId] = React.useState('')
  const [includeInactive, setIncludeInactive] = React.useState(false)
  const [offset, setOffset] = React.useState(0)
  const [page, setPage] = React.useState<ItemPage | null>(null)
  const [categories, setCategories] = React.useState<ItemCategory[]>([])
  const [units, setUnits] = React.useState<Unit[]>([])
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [adding, setAdding] = React.useState(false)

  const limit = 50

  const refresh = React.useCallback(
    async (spinner = false) => {
      if (spinner) setLoading(true)
      const params = new URLSearchParams()
      if (search.trim()) params.set('search', search.trim())
      if (kind) params.set('kind', kind)
      if (categoryId) params.set('category_id', categoryId)
      if (includeInactive) params.set('include_inactive', 'true')
      params.set('limit', String(limit))
      params.set('offset', String(offset))
      try {
        setPage(await api.get<ItemPage>(`/catalog/items?${params.toString()}`))
        setError(null)
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : 'Could not load the catalog')
      } finally {
        setLoading(false)
      }
    },
    [search, kind, categoryId, includeInactive, offset],
  )

  React.useEffect(() => {
    if (!loaded) return
    // Debounced, or the search is a request per keystroke.
    const handle = setTimeout(() => void refresh(page === null), 250)
    return () => clearTimeout(handle)
    // Keyed on the active company: switching must not leave another
    // company's prices on screen.
  }, [loaded, activeCompanyId, refresh])

  React.useEffect(() => {
    if (!loaded) return
    void (async () => {
      try {
        const [loadedCategories, loadedUnits] = await Promise.all([
          api.get<ItemCategory[]>('/catalog/categories'),
          api.get<Unit[]>('/catalog/units'),
        ])
        setCategories(loadedCategories)
        setUnits(loadedUnits)
      } catch {
        // The filters and the new-item form degrade to free text without
        // these; the list itself is what this screen is for.
      }
    })()
  }, [loaded, activeCompanyId])

  if (refused) return <CatalogNoAccess />

  const rows = page?.items ?? []

  return (
    <div className="max-w-6xl p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Catalog</h1>
          <p className="mt-1 text-sm text-text-secondary">
            What you sell and what it costs you — crew, equipment, space,
            travel, fees.
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            data-testid="catalog-new-item"
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
          >
            <Plus className="h-3.5 w-3.5" /> New item
          </button>
        )}
      </div>

      {loaded && !canEdit && <CatalogReadOnlyNotice />}

      <CatalogTabs />

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <div className="relative min-w-[16rem] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-text-tertiary" />
          <Input
            aria-label="Search the catalog"
            value={search}
            placeholder="SKU, German or English name"
            onChange={(event) => {
              setOffset(0)
              setSearch(event.target.value)
            }}
            className="pl-8"
          />
        </div>
        <select
          aria-label="Filter by kind"
          value={kind}
          onChange={(event) => {
            setOffset(0)
            setKind(event.target.value)
          }}
          className="rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
        >
          <option value="">All kinds</option>
          {KINDS.map((value) => (
            <option key={value} value={value}>
              {KIND_LABELS[value]}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by category"
          value={categoryId}
          onChange={(event) => {
            setOffset(0)
            setCategoryId(event.target.value)
          }}
          className="rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
        >
          <option value="">All categories</option>
          {categories.map((category) => (
            <option key={category.id} value={category.id}>
              {category.path || category.name}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 whitespace-nowrap text-xs text-text-secondary">
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(event) => {
              setOffset(0)
              setIncludeInactive(event.target.checked)
            }}
          />
          Show retired
        </label>
      </div>

      {adding && (
        <NewItemPanel
          categories={categories}
          units={units}
          onClose={() => setAdding(false)}
          onCreated={async () => {
            setAdding(false)
            await refresh()
          }}
        />
      )}

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {loading ? (
        <div className="mt-8 flex items-center gap-2 text-sm text-text-secondary">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading the price
          list&hellip;
        </div>
      ) : rows.length === 0 ? (
        <p className="mt-8 text-sm text-text-secondary">
          {search || kind || categoryId
            ? 'Nothing in the catalog matches that.'
            : 'The catalog is empty. Add an item, or import a price list from a CSV file.'}
        </p>
      ) : (
        <>
          <table className="mt-5 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
                <th className="py-2 pr-3 font-medium">SKU</th>
                <th className="py-2 pr-3 font-medium">Name</th>
                <th className="py-2 pr-3 font-medium">Kind</th>
                <th className="py-2 pr-3 font-medium">Category</th>
                <th className="py-2 pr-3 font-medium">Unit</th>
                <th className="py-2 pr-3 text-right font-medium">Rate / day</th>
                <th className="py-2 pr-3 text-right font-medium">Cost / day</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr
                  key={item.id}
                  data-testid="catalog-row"
                  className="border-b border-border/60"
                >
                  <td className="py-2 pr-3 font-mono text-xs text-text-secondary">
                    {item.sku ?? '—'}
                  </td>
                  <td className="py-2 pr-3">
                    <Link
                      href={`/catalog/${item.id}`}
                      className="font-medium text-text-primary hover:text-accent"
                    >
                      {item.name_de}
                    </Link>
                    {item.name_en && item.name_en !== item.name_de && (
                      <span className="ml-2 text-xs text-text-tertiary">
                        {item.name_en}
                      </span>
                    )}
                    {!item.is_active && (
                      <span className="ml-2 rounded bg-bg-secondary px-1.5 py-0.5 text-xs text-text-tertiary">
                        retired
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-text-secondary">
                    {KIND_LABELS[item.kind] ?? item.kind}
                  </td>
                  <td className="py-2 pr-3 text-text-secondary">
                    {item.category_path ?? '—'}
                  </td>
                  <td className="py-2 pr-3 text-text-secondary">
                    {item.unit_key ?? '—'}
                  </td>
                  {/* Rendered as the API sent them. No currency arithmetic,
                      no re-rounding, no locale reformatting that could turn
                      "1200.00" into something a reader checks against an
                      invoice and finds different. */}
                  <td className="py-2 pr-3 text-right font-mono text-xs text-text-primary">
                    {rateOf(item, 'bill') ?? '—'}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono text-xs text-text-secondary">
                    {rateOf(item, 'cost') ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="mt-4 flex items-center justify-between text-xs text-text-secondary">
            <span data-testid="catalog-total">
              {page?.total} item{page?.total === 1 ? '' : 's'}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}
                className="rounded border border-border px-2 py-1 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={offset + limit >= (page?.total ?? 0)}
                onClick={() => setOffset(offset + limit)}
                className="rounded border border-border px-2 py-1 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function NewItemPanel({
  categories,
  units,
  onClose,
  onCreated,
}: {
  categories: ItemCategory[]
  units: Unit[]
  onClose: () => void
  onCreated: () => Promise<void>
}) {
  const [nameDe, setNameDe] = React.useState('')
  const [sku, setSku] = React.useState('')
  const [kind, setKind] = React.useState<ItemKind>('equipment_rental')
  const [categoryId, setCategoryId] = React.useState('')
  const [unitId, setUnitId] = React.useState('')
  const [billRate, setBillRate] = React.useState('')
  const [costRate, setCostRate] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const [saving, setSaving] = React.useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    const rates: { rate_type: 'bill' | 'cost'; period_unit: 'day'; amount: string }[] = []
    // Sent as the strings they were typed as. Parsing them here would be
    // this app deciding what "1.200,00" means, and the one place that
    // decision belongs is the server (rule 1, rule 2).
    if (billRate.trim()) {
      rates.push({ rate_type: 'bill', period_unit: 'day', amount: billRate.trim() })
    }
    if (costRate.trim()) {
      rates.push({ rate_type: 'cost', period_unit: 'day', amount: costRate.trim() })
    }
    try {
      await api.post('/catalog/items', {
        name_de: nameDe.trim(),
        sku: sku.trim() || null,
        kind,
        category_id: categoryId || null,
        unit_id: unitId || null,
        rates: rates.length ? rates : null,
      })
      await onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not save the item')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      onSubmit={submit}
      data-testid="catalog-new-item-form"
      className="mt-5 rounded-md border border-border bg-bg-secondary p-4"
    >
      <h2 className="text-sm font-semibold text-text-primary">New item</h2>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-text-secondary">
          Name (German)
          <Input
            value={nameDe}
            onChange={(event) => setNameDe(event.target.value)}
            required
            className="mt-1"
          />
        </label>
        <label className="text-xs text-text-secondary">
          SKU <span className="text-text-tertiary">(optional)</span>
          <Input
            value={sku}
            onChange={(event) => setSku(event.target.value)}
            className="mt-1"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Kind
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as ItemKind)}
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
            value={categoryId}
            onChange={(event) => setCategoryId(event.target.value)}
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
            value={unitId}
            onChange={(event) => setUnitId(event.target.value)}
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
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs text-text-secondary">
            Rate / day
            <Input
              value={billRate}
              onChange={(event) => setBillRate(event.target.value)}
              placeholder="1200.00"
              className="mt-1"
            />
          </label>
          <label className="text-xs text-text-secondary">
            Cost / day
            <Input
              value={costRate}
              onChange={(event) => setCostRate(event.target.value)}
              placeholder="600.00"
              className="mt-1"
            />
          </label>
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      <div className="mt-4 flex gap-2">
        <button
          type="submit"
          disabled={saving || !nameDe.trim()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save item'}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-text-secondary"
        >
          Cancel
        </button>
      </div>
      {!nameDe.trim() && (
        // 17c: the button is disabled and the screen says what for.
        <p className="mt-2 text-xs text-text-tertiary">
          A German name is the one field an item cannot do without — it is
          what the list and the picker show.
        </p>
      )}
    </form>
  )
}
