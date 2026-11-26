'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import type { DeviceImportPreview, DeviceImportResult, DeviceImportRow } from '@/types'

/**
 * Import the gear list from a CSV file: preview, tick, apply.
 *
 * The verdict per line is the API's. A line naming a device you already have
 * is held back; when that device is archived the line can be ticked to add a
 * new one beside it, and when it is active it cannot — the sentence says to
 * rename the line in the file. An import never changes existing gear.
 */

const ACTION_LABELS: Record<string, string> = {
  create: 'New device',
  duplicate: 'Already on file',
  error: 'Cannot be read',
  created: 'Added',
  skipped: 'Skipped',
}

function actionClass(action: string): string {
  if (action === 'error') return 'text-danger'
  if (action === 'create' || action === 'created') return 'text-success'
  if (action === 'duplicate') return 'text-warning'
  return 'text-text-secondary'
}

export function GearImport({ onApplied }: { onApplied: () => void }) {
  const [file, setFile] = React.useState<File | null>(null)
  const [preview, setPreview] = React.useState<DeviceImportPreview | null>(null)
  const [applied, setApplied] = React.useState<DeviceImportResult | null>(null)
  const [ticked, setTicked] = React.useState<Set<number>>(new Set())
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  const runPreview = async (chosen: File) => {
    setBusy(true)
    setApplied(null)
    setPreview(null)
    setTicked(new Set())
    setError(null)
    const form = new FormData()
    form.append('file', chosen)
    try {
      setPreview(await api.upload<DeviceImportPreview>('/devices/import/preview', form))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'That file could not be read. Check it is a CSV file.')
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
    form.append('digest', preview.digest)
    if (ticked.size) form.append('include_lines', Array.from(ticked).sort((a, b) => a - b).join(','))
    try {
      setApplied(await api.upload<DeviceImportResult>('/devices/import', form))
      onApplied()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The import was refused. Choose the file again.')
    } finally {
      setBusy(false)
    }
  }

  const toCreate = preview ? preview.created + ticked.size : 0
  const rows: DeviceImportRow[] = applied?.rows ?? preview?.rows ?? []
  const showPrice = rows.some((row) => row.price != null)

  return (
    <div data-testid="gear-import" className="mt-5 rounded-md border border-border bg-bg-secondary p-4">
      <label className="block text-xs text-text-secondary">
        Choose a CSV file
        <input
          type="file"
          accept=".csv,text/csv,text/plain"
          data-testid="gear-import-file"
          onChange={(event) => {
            const chosen = event.target.files?.[0] ?? null
            setFile(chosen)
            if (chosen) void runPreview(chosen)
          }}
          className="mt-2 block text-sm text-text-primary"
        />
      </label>
      <p className="mt-2 text-xs text-text-tertiary">
        Columns <code>name;category;quantity;price;serials;notes</code> — only
        the name is required, an empty quantity is 0. Several serial numbers
        are separated by <code>|</code>. Prices with a decimal comma
        (<code>65.000,00</code>) or a dot (<code>65000.00</code>). Existing
        devices are never changed.
      </p>

      {busy && <p className="mt-3 text-sm text-text-secondary">Working…</p>}
      {error && (
        <p role="alert" data-testid="gear-import-error" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      {preview && (
        <>
          <p data-testid="gear-import-counts" className="mt-4 text-sm text-text-primary">
            {applied
              ? `${applied.created} added with ${applied.units_created} serial numbers, ${applied.skipped} skipped, ${applied.errors} could not be read.`
              : `${preview.created} new, ${preview.duplicates} already on file, ${preview.errors} cannot be read.`}
          </p>

          {!applied && (
            <div className="mt-3">
              <button
                type="button"
                data-testid="gear-import-apply"
                disabled={busy || toCreate === 0}
                onClick={() => void apply()}
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                Add {toCreate} {toCreate === 1 ? 'device' : 'devices'}
              </button>
              {toCreate === 0 && (
                <p data-testid="gear-import-why" className="mt-2 text-xs text-text-tertiary">
                  Nothing in this file is new. Lines already on file are explained
                  below; fix the file and choose it again.
                </p>
              )}
            </div>
          )}

          <table className="mt-4 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
                <th className="py-2 pr-3 font-medium">Add</th>
                <th className="py-2 pr-3 font-medium">Line</th>
                <th className="py-2 pr-3 font-medium">Name</th>
                <th className="py-2 pr-3 font-medium">Category</th>
                <th className="py-2 pr-3 text-right font-medium">Quantity</th>
                {showPrice && <th className="py-2 pr-3 text-right font-medium">Price</th>}
                <th className="py-2 pr-3 font-medium">Serial numbers</th>
                <th className="py-2 pr-3 font-medium">What happens</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.line_number}
                  data-testid="gear-import-row"
                  data-action={row.action}
                  className="border-b border-border/60 align-top"
                >
                  <td className="py-2 pr-3">
                    {!applied && row.action === 'duplicate' && row.can_include ? (
                      <input
                        type="checkbox"
                        aria-label={`Add line ${row.line_number} as a new device`}
                        data-testid="gear-import-tick"
                        checked={ticked.has(row.line_number)}
                        onChange={(event) => {
                          const next = new Set(ticked)
                          if (event.target.checked) next.add(row.line_number)
                          else next.delete(row.line_number)
                          setTicked(next)
                        }}
                      />
                    ) : null}
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs text-text-tertiary">{row.line_number}</td>
                  <td className="py-2 pr-3 text-text-primary">{row.name ?? '—'}</td>
                  <td className="py-2 pr-3 text-xs text-text-secondary">{row.category ?? ''}</td>
                  <td className="py-2 pr-3 text-right font-mono text-xs">{row.quantity ?? ''}</td>
                  {showPrice && <td className="py-2 pr-3 text-right font-mono text-xs">{row.price ?? ''}</td>}
                  <td className="py-2 pr-3 text-xs text-text-secondary">{(row.serials ?? []).join(', ')}</td>
                  <td className={`py-2 pr-3 text-xs ${actionClass(row.action)}`}>
                    <span className="font-medium">{ACTION_LABELS[row.action] ?? row.action}</span>
                    {row.message ? <span className="block text-text-secondary">{row.message}</span> : null}
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
