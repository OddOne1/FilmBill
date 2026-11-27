'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import type { CrewImportPreview, CrewImportResult, CrewImportRow } from '@/types'

/**
 * Import the crew list from a CSV file: preview, tick, apply.
 *
 * **The preview is the API's.** This screen does not decide which line is a
 * duplicate; it shows the verdict and lets the person tick the duplicates
 * they want created anyway. Apply sends the same file, the preview's digest
 * and the ticked line numbers — so a file that changed in between is refused
 * instead of imported.
 *
 * An import only creates: nobody already on file is changed.
 */

const ACTION_LABELS: Record<string, string> = {
  create: 'New person',
  duplicate: 'Looks like somebody on file',
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

export function CrewImport({ onApplied }: { onApplied: () => void }) {
  const [file, setFile] = React.useState<File | null>(null)
  const [preview, setPreview] = React.useState<CrewImportPreview | null>(null)
  const [applied, setApplied] = React.useState<CrewImportResult | null>(null)
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
      setPreview(await api.upload<CrewImportPreview>('/crew/import/preview', form))
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
      setApplied(await api.upload<CrewImportResult>('/crew/import', form))
      onApplied()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The import was refused. Choose the file again.')
    } finally {
      setBusy(false)
    }
  }

  const toCreate = preview ? preview.created + ticked.size : 0
  const rows: CrewImportRow[] = applied?.rows ?? preview?.rows ?? []

  return (
    <div data-testid="crew-import" className="mt-5 rounded-md border border-border bg-bg-secondary p-4">
      <label className="block text-xs text-text-secondary">
        Choose a CSV file
        <input
          type="file"
          accept=".csv,text/csv,text/plain"
          data-testid="crew-import-file"
          onChange={(event) => {
            const chosen = event.target.files?.[0] ?? null
            setFile(chosen)
            if (chosen) void runPreview(chosen)
          }}
          className="mt-2 block text-sm text-text-primary"
        />
      </label>
      <p className="mt-2 text-xs text-text-tertiary">
        Columns <code>first_name;middle_name;last_name;roles;email;phone;notes;day_rate</code>{' '}
        — a first or last name is required. A file with the older single{' '}
        <code>name</code> column still works: those people keep the name as
        written and are listed under Needs attention until you split it. Several roles are separated by <code>|</code>, for example{' '}
        <code>DoP|Camera operator</code>. A role that is a labor item in the
        catalog becomes a role; any other is kept as a tag. <code>day_rate</code>{' '}
        is the person&apos;s own rate in their first catalog role (650 or 650,00).
        Semicolons or commas, with or without
        a BOM. People who look like somebody already on file are held back
        until you tick them; nobody already on file is ever changed.
      </p>

      {busy && <p className="mt-3 text-sm text-text-secondary">Working…</p>}
      {error && (
        <p role="alert" data-testid="crew-import-error" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      {preview && (
        <>
          <p data-testid="crew-import-counts" className="mt-4 text-sm text-text-primary">
            {applied
              ? `${applied.created} added, ${applied.skipped} skipped, ${applied.errors} could not be read.`
              : `${preview.created} new, ${preview.duplicates} held back as possible duplicates, ${preview.errors} cannot be read.`}
          </p>

          {!applied && (
            <div className="mt-3">
              <button
                type="button"
                data-testid="crew-import-apply"
                disabled={busy || toCreate === 0}
                onClick={() => void apply()}
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                Add {toCreate} {toCreate === 1 ? 'person' : 'people'}
              </button>
              {toCreate === 0 && (
                // 17c: why the button does nothing.
                <p data-testid="crew-import-why" className="mt-2 text-xs text-text-tertiary">
                  {preview.duplicates > 0
                    ? 'Every readable line looks like somebody already on file. Tick the ones you want added anyway.'
                    : 'No line in this file can be imported. Fix the lines below and choose the file again.'}
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
                <th className="py-2 pr-3 font-medium">Roles</th>
                <th className="py-2 pr-3 font-medium">Email</th>
                <th className="py-2 pr-3 font-medium">Phone</th>
                <th className="py-2 pr-3 font-medium">What happens</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.line_number}
                  data-testid="crew-import-row"
                  data-action={row.action}
                  className="border-b border-border/60 align-top"
                >
                  <td className="py-2 pr-3">
                    {!applied && row.action === 'duplicate' ? (
                      <input
                        type="checkbox"
                        aria-label={`Add line ${row.line_number} anyway`}
                        data-testid="crew-import-tick"
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
                  <td className="py-2 pr-3 text-xs text-text-secondary">
                    {(row.matched_roles ?? []).length > 0 || (row.unknown_roles ?? []).length > 0 ? (
                      <>
                        {(row.matched_roles ?? []).join(', ')}
                        {(row.unknown_roles ?? []).length > 0 && (
                          <span data-testid="crew-import-unknown-roles" className="block text-warning">
                            Tag only: {(row.unknown_roles ?? []).join(', ')}
                          </span>
                        )}
                      </>
                    ) : (
                      (row.roles ?? []).join(', ')
                    )}
                    {row.day_rate && <span className="block text-text-tertiary">day rate {row.day_rate}</span>}
                  </td>
                  <td className="py-2 pr-3 text-xs text-text-secondary">{row.email ?? ''}</td>
                  <td className="py-2 pr-3 text-xs text-text-secondary">{row.phone ?? ''}</td>
                  <td className={`py-2 pr-3 text-xs ${actionClass(row.action)}`}>
                    <span className="font-medium">{ACTION_LABELS[row.action] ?? row.action}</span>
                    {row.message ? <span className="block text-text-secondary">{row.message}</span> : null}
                    {(row.role_notes ?? []).map((note) => (
                      <span key={note} className="block text-text-secondary">
                        {note}
                      </span>
                    ))}
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
