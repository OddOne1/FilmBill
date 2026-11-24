'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import type { BudgetImportPreview, BudgetImportResult } from '@/types'

/**
 * Import lines from a CSV: preview, then apply the same file with the
 * preview's digest. The API refuses an apply whose file no longer plans what
 * the preview showed, so what is applied is exactly the table on screen. It
 * never deletes a line, and an unknown section code is an error row.
 */

const ACTION_LABELS: Record<string, string> = {
  create: 'New line',
  update: 'Updates a line',
  error: 'Refused',
}

export function BudgetImport({
  budgetId,
  onApplied,
  onClose,
}: {
  budgetId: string
  onApplied: () => Promise<void> | void
  onClose: () => void
}) {
  const [file, setFile] = React.useState<File | null>(null)
  const [preview, setPreview] = React.useState<BudgetImportPreview | null>(null)
  const [applied, setApplied] = React.useState<BudgetImportResult | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  const runPreview = async (chosen: File) => {
    setBusy(true)
    setPreview(null)
    setApplied(null)
    setError(null)
    const form = new FormData()
    form.append('file', chosen)
    try {
      setPreview(await api.upload<BudgetImportPreview>(`/budgets/${budgetId}/import/preview`, form))
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
    try {
      setApplied(
        await api.upload<BudgetImportResult>(
          `/budgets/${budgetId}/import?expected_digest=${encodeURIComponent(preview.digest)}`,
          form,
        ),
      )
      await onApplied()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The import was refused')
    } finally {
      setBusy(false)
    }
  }

  const result = applied ?? preview
  const actionable = preview ? preview.creates + preview.updates : 0

  return (
    <section data-testid="budget-import" className="mt-4 rounded-md border border-border bg-bg-secondary p-4">
      <div className="flex items-start justify-between gap-3">
        <label className="text-xs text-text-secondary">
          Import lines from a CSV
          <input
            type="file"
            accept=".csv,text/csv,text/plain"
            data-testid="budget-import-file"
            onChange={(event) => {
              const chosen = event.target.files?.[0] ?? null
              setFile(chosen)
              if (chosen) void runPreview(chosen)
            }}
            className="mt-2 block text-sm text-text-primary"
          />
        </label>
        <button type="button" onClick={onClose} className="text-xs text-text-secondary">
          Close
        </button>
      </div>
      <p className="mt-2 text-xs text-text-tertiary">
        Columns as in the export. A line is found by <code>section_code</code> (or{' '}
        <code>section_name</code> for a sub-section without a code) and <code>description</code>;
        a blank number keeps what the line has. Write numbers with a decimal comma (
        <code>2,5</code>) or without separators (<code>10000</code>) — <code>10.000</code> is
        refused because it could mean ten or ten thousand. <code>period_global</code> /{' '}
        <code>quantity_global</code> name the global a line follows; empty means it follows none.
      </p>

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      {result && (
        <>
          <p data-testid="budget-import-summary" className="mt-3 text-sm text-text-primary">
            {applied ? 'Applied: ' : 'This file would make '}
            {result.creates} new, {result.updates} updated, {result.errors} refused.
          </p>
          <table className="mt-2 w-full text-xs">
            <thead>
              <tr className="border-b border-border text-left text-text-tertiary">
                <th className="py-1 pr-2 font-medium">Row</th>
                <th className="py-1 pr-2 font-medium">Section</th>
                <th className="py-1 pr-2 font-medium">Description</th>
                <th className="py-1 pr-2 font-medium">What happens</th>
                <th className="py-1 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row) => (
                <tr key={row.line_number} data-testid="budget-import-row" className="border-b border-border/60">
                  <td className="py-1 pr-2">{row.line_number}</td>
                  <td className="py-1 pr-2 font-mono">{row.section_code}</td>
                  <td className="py-1 pr-2">{row.description}</td>
                  <td className={`py-1 pr-2 ${row.action === 'error' ? 'text-danger' : ''}`}>
                    {ACTION_LABELS[row.action] ?? row.action}
                  </td>
                  <td className="py-1 text-text-secondary">{row.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!applied && (
            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                data-testid="budget-import-apply"
                disabled={busy || actionable === 0}
                onClick={() => void apply()}
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                Apply {actionable} row{actionable === 1 ? '' : 's'}
              </button>
              {actionable === 0 && (
                <span className="text-xs text-text-tertiary">Nothing in this file can be applied.</span>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
