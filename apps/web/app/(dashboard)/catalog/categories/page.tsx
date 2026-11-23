'use client'

import * as React from 'react'
import { ChevronRight, Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import type { ItemCategory, ItemKind } from '@/types'
import { type CategoryNode, buildTree } from './tree'
import {
  CatalogNoAccess,
  CatalogReadOnlyNotice,
  CatalogTabs,
  useCatalogAccess,
} from '../catalog-chrome'

/**
 * The category tree.
 *
 * The tree itself is nested in `./tree.ts` — from `parent_id`, never from
 * `path`. That file says why, and why it is a file.
 *
 * Moving a category is a `parent_id` PATCH and the server owns the rules:
 * it refuses a cycle naming the route, and refuses a move that would nest
 * deeper than the limit counting the moved subtree. This screen does not
 * reimplement either check — it shows what the server says.
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

export default function CategoriesPage() {
  const { loaded, canEdit, refused } = useCatalogAccess()
  const [categories, setCategories] = React.useState<ItemCategory[]>([])
  const [includeInactive, setIncludeInactive] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [adding, setAdding] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setCategories(
        await api.get<ItemCategory[]>(
          `/catalog/categories${includeInactive ? '?include_inactive=true' : ''}`,
        ),
      )
      setError(null)
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : 'Could not load the categories',
      )
    } finally {
      setLoading(false)
    }
  }, [includeInactive])

  React.useEffect(() => {
    if (!loaded) return
    void load()
  }, [loaded, load])

  if (refused) return <CatalogNoAccess title="Categories" />

  const tree = buildTree(categories)

  return (
    <div className="max-w-4xl p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Categories</h1>
          <p className="mt-1 text-sm text-text-secondary">
            How the price list is grouped. A category belongs to one kind of
            item and may sit inside another.
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            data-testid="category-new"
            onClick={() => setAdding(true)}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
          >
            New category
          </button>
        )}
      </div>

      {loaded && !canEdit && <CatalogReadOnlyNotice />}
      <CatalogTabs />

      <label className="mt-4 flex items-center gap-2 text-xs text-text-secondary">
        <input
          type="checkbox"
          checked={includeInactive}
          onChange={(event) => setIncludeInactive(event.target.checked)}
        />
        Show switched off
      </label>

      {adding && (
        <NewCategoryForm
          categories={categories}
          onClose={() => setAdding(false)}
          onCreated={async () => {
            setAdding(false)
            await load()
          }}
          onError={setError}
        />
      )}

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {loading ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading&hellip;
        </div>
      ) : tree.length === 0 ? (
        <p className="mt-6 text-sm text-text-secondary">
          No categories yet.
        </p>
      ) : (
        <ul data-testid="category-tree" className="mt-5 space-y-1">
          {tree.map((node) => (
            <CategoryRow
              key={node.category.id}
              node={node}
              depth={0}
              canEdit={canEdit}
              categories={categories}
              onChanged={load}
              onError={setError}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function CategoryRow({
  node,
  depth,
  canEdit,
  categories,
  onChanged,
  onError,
}: {
  node: CategoryNode
  depth: number
  canEdit: boolean
  categories: ItemCategory[]
  onChanged: () => Promise<void>
  onError: (message: string) => void
}) {
  const { category } = node
  const [editing, setEditing] = React.useState(false)
  const [name, setName] = React.useState(category.name)
  const [parentId, setParentId] = React.useState(category.parent_id ?? '')
  const [busy, setBusy] = React.useState(false)

  const save = async () => {
    setBusy(true)
    try {
      await api.patch(`/catalog/categories/${category.id}`, {
        name: name.trim(),
        parent_id: parentId || null,
      })
      setEditing(false)
      await onChanged()
    } catch (err) {
      // The server's own sentence, verbatim: "that would put 'Camera' inside
      // itself (Camera › Cinema › Camera)" tells somebody what to do, and a
      // generic "invalid parent" does not.
      onError(err instanceof ApiError ? err.detail : 'Could not move that')
    } finally {
      setBusy(false)
    }
  }

  const toggle = async () => {
    setBusy(true)
    try {
      await api.post(
        `/catalog/categories/${category.id}/${
          category.is_active ? 'deactivate' : 'reactivate'
        }`,
      )
      await onChanged()
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : 'Could not change that')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li>
      <div
        data-testid="category-row"
        className="flex items-center gap-2 rounded px-2 py-1.5 hover:bg-bg-secondary"
        style={{ paddingLeft: `${depth * 1.25 + 0.5}rem` }}
      >
        {depth > 0 && (
          <ChevronRight className="h-3 w-3 shrink-0 text-text-tertiary" />
        )}
        {editing ? (
          <>
            <Input
              aria-label="Category name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="h-8 w-48"
            />
            <select
              aria-label="Parent category"
              value={parentId}
              onChange={(event) => setParentId(event.target.value)}
              className="rounded-md border border-border bg-bg-primary px-2 py-1 text-xs text-text-primary"
            >
              <option value="">No parent</option>
              {categories
                // Itself excluded here only as a convenience; the server
                // refuses it anyway and says so.
                .filter((other) => other.id !== category.id)
                .map((other) => (
                  <option key={other.id} value={other.id}>
                    {other.path || other.name}
                  </option>
                ))}
            </select>
            <button
              type="button"
              disabled={busy || !name.trim()}
              onClick={() => void save()}
              className="rounded bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="text-xs text-text-secondary"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <span className="text-sm text-text-primary">{category.name}</span>
            <span className="text-xs text-text-tertiary">
              {KIND_LABELS[category.kind] ?? category.kind}
            </span>
            <span
              data-testid="category-item-count"
              className="text-xs text-text-tertiary"
            >
              {category.item_count} item
              {category.item_count === 1 ? '' : 's'}
            </span>
            {!category.is_active && (
              <span className="rounded bg-bg-secondary px-1.5 py-0.5 text-xs text-text-tertiary">
                off
              </span>
            )}
            {canEdit && (
              <span className="ml-auto flex gap-2">
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="text-xs text-text-secondary hover:text-accent"
                >
                  Edit
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void toggle()}
                  className="text-xs text-text-secondary hover:text-accent disabled:opacity-50"
                >
                  {category.is_active ? 'Switch off' : 'Switch on'}
                </button>
              </span>
            )}
          </>
        )}
      </div>
      {node.children.length > 0 && (
        <ul className="space-y-1">
          {node.children.map((child) => (
            <CategoryRow
              key={child.category.id}
              node={child}
              depth={depth + 1}
              canEdit={canEdit}
              categories={categories}
              onChanged={onChanged}
              onError={onError}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

function NewCategoryForm({
  categories,
  onClose,
  onCreated,
  onError,
}: {
  categories: ItemCategory[]
  onClose: () => void
  onCreated: () => Promise<void>
  onError: (message: string) => void
}) {
  const [name, setName] = React.useState('')
  const [kind, setKind] = React.useState<ItemKind>('equipment_rental')
  const [parentId, setParentId] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  return (
    <form
      data-testid="category-new-form"
      onSubmit={async (event) => {
        event.preventDefault()
        setBusy(true)
        try {
          await api.post('/catalog/categories', {
            name: name.trim(),
            kind,
            parent_id: parentId || null,
          })
          await onCreated()
        } catch (err) {
          onError(
            err instanceof ApiError ? err.detail : 'Could not add the category',
          )
        } finally {
          setBusy(false)
        }
      }}
      className="mt-5 rounded-md border border-border bg-bg-secondary p-4"
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-text-secondary">
          Name
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            className="mt-1 w-48"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Kind
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as ItemKind)}
            className="mt-1 block rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            {KINDS.map((value) => (
              <option key={value} value={value}>
                {KIND_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-text-secondary">
          Inside
          <select
            value={parentId}
            onChange={(event) => setParentId(event.target.value)}
            className="mt-1 block rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            <option value="">Nothing — a top-level category</option>
            {categories.map((category) => (
              <option key={category.id} value={category.id}>
                {category.path || category.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          disabled={busy || !name.trim()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Add
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-text-secondary"
        >
          Cancel
        </button>
      </div>
      <p className="mt-2 text-xs text-text-tertiary">
        A category&rsquo;s kind cannot be changed afterwards — items are filed
        under it.
      </p>
    </form>
  )
}
