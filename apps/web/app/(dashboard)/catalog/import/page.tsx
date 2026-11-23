'use client'

import * as React from 'react'
import Link from 'next/link'
import { api, ApiError } from '@/lib/api'
import type { ImportApplyResult, ImportPreview, ImportRow } from '@/types'
import {
  CatalogNoAccess,
  CatalogReadOnlyNotice,
  CatalogTabs,
  useCatalogAccess,
} from '../catalog-chrome'

/**
 * Import a price list from a CSV file.
 *
 * **Preview first, then apply the same file with the preview's digest.**
 * The digest is why: `POST /catalog/items/import` takes the digest the
 * preview returned and refuses to write anything if the file no longer
 * plans the same thing. So somebody who previews, edits the file, and then
 * presses Apply gets a refusal naming both digests rather than an import
 * that quietly did something else. The file is sent twice — the API is
 * stateless and holds no half-finished import.
 *
 * The preview table is the API's rows, verdict and all. This screen does
 * not decide what a row would do.
 */

const ACTION_LABELS: Record<string, string> = {
  create: 'New item',
  update: 'Updates an existing item',
  error: 'Refused',
}

function actionClass(action: string): string {
  if (action === 'error') return 'text-danger'
  if (action === 'create') return 'text-success'
  return 'text-text-secondary'
}

export default function ImportPage() {
  const { loaded, canEdit, refused } = useCatalogAccess()
  const [file, setFile] = React.useState<File | null>(null)
  const [preview, setPreview] = React.useState<ImportPreview | null>(null)
  const [applied, setApplied] = React.useState<ImportApplyResult | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  if (refused) return <CatalogNoAccess title="Import CSV" />

  const result: ImportPreview | null = applied ?? preview

  const runPreview = async (chosen: File) => {
    setBusy(true)
    setApplied(null)
    setPreview(null)
    setError(null)
    const form = new FormData()
    form.append('file', chosen)
    try {
      setPreview(
        await api.upload<ImportPreview>('/catalog/items/import/preview', form),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not read that file')
    } finally {
      setBusy(false)
    }
  }

  const apply = async () => {
    if (!file || !preview) return
    setBusy(true)
    setError(null)
    const form = new FormData()
    form.append('file', file)
    // The preview's digest, so a file that changed underneath us is refused
    // instead of imported.
    form.append('digest', preview.digest)
    try {
      setApplied(
        await api.upload<ImportApplyResult>('/catalog/items/import', form),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The import was refused')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-5xl p-8">
      <h1 className="text-lg font-semibold text-text-primary">Import CSV</h1>
      <p className="mt-1 text-sm text-text-secondary">
        A price list as a CSV file. Existing SKUs are updated, new ones
        created; nothing is ever deleted or retired by an import.
      </p>

      {loaded && !canEdit && <CatalogReadOnlyNotice />}
      <CatalogTabs />

      {canEdit ? (
        <div className="mt-5 rounded-md border border-border bg-bg-secondary p-4">
          <label className="block text-xs text-text-secondary">
            Choose a file
            <input
              type="file"
              accept=".csv,text/csv,text/plain"
              data-testid="import-file"
              onChange={(event) => {
                const chosen = event.target.files?.[0] ?? null
                setFile(chosen)
                if (chosen) void runPreview(chosen)
              }}
              className="mt-2 block text-sm text-text-primary"
            />
          </label>
          <p className="mt-3 text-xs text-text-tertiary">
            Columns: <code>sku</code>, <code>name</code>, <code>kind</code>,{' '}
            <code>category</code>, <code>unit</code>,{' '}
            <code>bill_rate_day</code>, <code>cost_rate_day</code>,{' '}
            <code>tax_class</code>, <code>active</code>. German headers
            (&ldquo;Bezeichnung&rdquo;, &ldquo;Tagessatz&rdquo;) are
            understood too, as are semicolons, tabs and Windows encodings — a
            file exported from Excel in Vienna works as it is.
          </p>
        </div>
      ) : null}

      {busy && (
        <p className="mt-4 text-sm text-text-secondary">Working&hellip;</p>
      )}

      {error && (
        <p role="alert" data-testid="import-error" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {result && (
        <>
          <div className="mt-6 flex flex-wrap items-center gap-4 text-sm">
            <span data-testid="import-counts" className="text-text-primary">
              {applied
                ? `${applied.applied} row${applied.applied === 1 ? '' : 's'} imported`
                : `${result.created + result.updated} row${
                    result.created + result.updated === 1 ? '' : 's'
                  } would be imported`}
              {' — '}
              {result.created} new, {result.updated} updated,{' '}
              {result.errors} refused
            </span>
            <span className="text-xs text-text-tertiary">
              separator &ldquo;{result.delimiter}&rdquo;
            </span>
            {/* `?? []` because the generated type makes a field with a
                server-side default optional; the API always sends it. */}
            {(result.ignored_columns ?? []).length > 0 && (
              <span className="text-xs text-text-tertiary">
                ignored columns: {(result.ignored_columns ?? []).join(', ')}
              </span>
            )}
          </div>

          {!applied && canEdit && (
            <div className="mt-4">
              <button
                type="button"
                data-testid="import-apply"
                disabled={busy || result.created + result.updated === 0}
                onClick={() => void apply()}
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                Import these rows
              </button>
              {result.created + result.updated === 0 && (
                // 17c: the button is dead and here is why.
                <p className="mt-2 text-xs text-text-tertiary">
                  Nothing in this file can be imported — every row was
                  refused. Fix the rows below and choose the file again.
                </p>
              )}
              {result.errors > 0 && result.created + result.updated > 0 && (
                <p className="mt-2 text-xs text-text-tertiary">
                  The {result.errors} refused row
                  {result.errors === 1 ? '' : 's'} will be skipped; the rest
                  will be imported.
                </p>
              )}
            </div>
          )}

          {applied && (
            <p role="status" className="mt-4 text-sm text-success">
              Done.{' '}
              <Link href="/catalog" className="underline">
                Back to the item list
              </Link>
              .
            </p>
          )}

          <table className="mt-5 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
                <th className="py-2 pr-3 font-medium">Line</th>
                <th className="py-2 pr-3 font-medium">SKU</th>
                <th className="py-2 pr-3 font-medium">Name</th>
                <th className="py-2 pr-3 font-medium">Kind</th>
                <th className="py-2 pr-3 font-medium">Category</th>
                <th className="py-2 pr-3 text-right font-medium">Rate / day</th>
                <th className="py-2 pr-3 text-right font-medium">Cost / day</th>
                <th className="py-2 pr-3 font-medium">What happens</th>
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row: ImportRow) => (
                <tr
                  key={row.line_number}
                  data-testid="import-row"
                  data-action={row.action}
                  className="border-b border-border/60 align-top"
                >
                  <td className="py-2 pr-3 font-mono text-xs text-text-tertiary">
                    {row.line_number}
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs text-text-secondary">
                    {row.sku ?? '—'}
                  </td>
                  <td className="py-2 pr-3 text-text-primary">
                    {row.name_de ?? '—'}
                  </td>
                  <td className="py-2 pr-3 text-text-secondary">
                    {row.kind ?? '—'}
                  </td>
                  <td className="py-2 pr-3 text-text-secondary">
                    {row.category ?? '—'}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono text-xs">
                    {row.bill_rate_day ?? '—'}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono text-xs text-text-secondary">
                    {row.cost_rate_day ?? '—'}
                  </td>
                  <td className={`py-2 pr-3 text-xs ${actionClass(row.action)}`}>
                    <span className="font-medium">
                      {ACTION_LABELS[row.action] ?? row.action}
                    </span>
                    {row.message ? (
                      <span className="block text-text-secondary">
                        {row.message}
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
