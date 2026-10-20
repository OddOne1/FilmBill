'use client'

import * as React from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { CompanySection, Field } from '@/components/settings/company-section'
import { useCompanyStore } from '@/stores/company-store'
import type {
  NumberPreview,
  NumberSeries,
  ResetMode,
} from '@/types'

/**
 * Settings → Company → Number series.
 *
 * **This screen never builds a number.** Not the padding, not the `{YYYY}`
 * substitution, not "the next one is 413". Every number on it comes from
 * `POST /company/number-series/preview`, which runs the same
 * `plan_number` the finalize transaction will run (CLAUDE.md rule 2). A
 * formatter in TypeScript would be a second implementation of the rule that
 * CLAUDE.md rule 5 makes gap-free, and the one on screen would be the one
 * nobody tested against a real counter.
 *
 * That is also why the preview is debounced rather than computed: typing a
 * pattern asks the server, and the answer includes the validation message
 * when the pattern is not usable. The alternative — a client-side regex that
 * agrees with the server today — is the shape that drifts.
 *
 * **Nothing here allocates.** A number is taken inside the finalize
 * transaction and nowhere else, so there is no "get the next number" button:
 * one would consume a number on every click, and every one of those would be
 * a gap.
 */

/** The two modes, with the one line of help each that actually decides it. */
const RESET_CHOICES: { value: ResetMode; label: string; help: string }[] = [
  {
    value: 'yearly',
    label: 'Restart every year',
    help: 'The counter goes back to 1 each January. The pattern needs {YYYY} or {YY}, so last year’s numbers are never issued again.',
  },
  {
    value: 'never',
    label: 'Continue without restart',
    help: 'One counter, for ever: AR-0001, AR-0002 … A year token is still allowed — it prints the year without restarting the count.',
  },
]

interface Draft {
  name: string
  pattern: string
  reset: ResetMode
  padding: number
  next_value: number
}

function draftFrom(series: NumberSeries): Draft {
  return {
    name: series.name,
    pattern: series.pattern,
    reset: series.reset,
    padding: series.padding,
    next_value: series.next_value,
  }
}

function changed(draft: Draft, series: NumberSeries): boolean {
  return (
    draft.name !== series.name ||
    draft.pattern !== series.pattern ||
    draft.reset !== series.reset ||
    draft.padding !== series.padding ||
    draft.next_value !== series.next_value
  )
}

export default function NumberSeriesPage() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)

  const [series, setSeries] = React.useState<NumberSeries[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [openId, setOpenId] = React.useState<string | null>(null)

  const refresh = React.useCallback(async () => {
    setLoading(true)
    try {
      setSeries(await api.get<NumberSeries[]>('/company/number-series'))
      setError(null)
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : 'Could not load the number series',
      )
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    void refresh()
    // Keyed on the active company: switching must not leave another
    // company's numbering on screen.
  }, [activeCompanyId, refresh])

  return (
    <CompanySection
      title="Number series"
      description="How documents are numbered in"
      permission="number_series.manage"
    >
      <p className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary">
        A number is taken when a document is finalised, and never before — so
        nothing on this screen issues one. The preview shows what the next
        number <em>would</em> be.
      </p>

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {error}
        </p>
      )}

      {loading ? (
        <div className="mt-6 flex items-center gap-2 p-4 text-sm text-text-tertiary">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading…
        </div>
      ) : (
        <div className="mt-6 space-y-3">
          {series.map((entry) => (
            <SeriesCard
              key={entry.id}
              series={entry}
              open={openId === entry.id}
              onToggle={() =>
                setOpenId(openId === entry.id ? null : entry.id)
              }
              onSaved={refresh}
            />
          ))}
        </div>
      )}
    </CompanySection>
  )
}

function SeriesCard({
  series,
  open,
  onToggle,
  onSaved,
}: {
  series: NumberSeries
  open: boolean
  onToggle: () => void
  onSaved: () => Promise<void>
}) {
  const [draft, setDraft] = React.useState<Draft>(() => draftFrom(series))
  const [previewDate, setPreviewDate] = React.useState('')
  const [preview, setPreview] = React.useState<NumberPreview | null>(null)
  const [previewError, setPreviewError] = React.useState<string | null>(null)
  const [saving, setSaving] = React.useState(false)
  const [saveError, setSaveError] = React.useState<string | null>(null)
  const [saved, setSaved] = React.useState(false)

  // Re-seed the draft when the server's copy changes, so a save that the
  // server adjusted (switching to yearly leaves the counter alone until the
  // next boundary) shows what the server actually stored rather than what was
  // typed.
  React.useEffect(() => {
    setDraft(draftFrom(series))
  }, [series])

  // The preview, from the API, debounced. `previewDate` is sent only when it
  // is a complete date — an `<input type="date">` reports partial values while
  // somebody is typing, and asking the server about `2026-0` is a 422 that
  // reads as a broken screen.
  React.useEffect(() => {
    if (!open) return
    const handle = setTimeout(async () => {
      try {
        const body: Record<string, unknown> = {
          series_id: series.id,
          pattern: draft.pattern,
          reset: draft.reset,
          padding: draft.padding,
        }
        if (/^\d{4}-\d{2}-\d{2}$/.test(previewDate)) {
          body.document_date = previewDate
        }
        setPreview(await api.post<NumberPreview>(
          '/company/number-series/preview',
          body,
        ))
        setPreviewError(null)
      } catch (err) {
        setPreview(null)
        setPreviewError(
          err instanceof ApiError ? err.detail : 'Could not build a preview',
        )
      }
    }, 300)
    return () => clearTimeout(handle)
  }, [open, series.id, draft.pattern, draft.reset, draft.padding, previewDate])

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setSaveError(null)
    setSaved(false)
    try {
      await api.patch(`/company/number-series/${series.id}`, draft)
      setSaved(true)
      await onSaved()
    } catch (err) {
      // The server's own sentence, verbatim. It is the one that explains why
      // — "A series that restarts every year needs {YYYY} or {YY} …" — and a
      // second copy here would be a second thing to keep in step.
      setSaveError(
        err instanceof ApiError ? err.detail : 'Could not save that series',
      )
    } finally {
      setSaving(false)
    }
  }

  const dirty = changed(draft, series)

  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 bg-bg-secondary px-4 py-3 text-left transition-colors hover:bg-bg-hover"
      >
        <span>
          <span className="text-sm font-medium text-text-primary">
            {series.name}
          </span>
          <span className="ml-2 text-xs text-text-tertiary">
            {series.document_type}
          </span>
        </span>
        <span className="flex items-center gap-3 text-xs text-text-tertiary">
          <span className="font-mono text-text-secondary">{series.pattern}</span>
          <span>
            {series.allocated_count === 0
              ? 'none issued yet'
              : `${series.allocated_count} issued, last ${series.latest_number}`}
          </span>
        </span>
      </button>

      {open && (
        <form onSubmit={save} className="space-y-4 border-t border-border p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" hint="What this list calls it.">
              <Input
                required
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </Field>
            <Field
              label="Pattern"
              hint="Tokens: {YYYY}, {YY}, {MM}, {SEQ}. {SEQ} is the counter."
            >
              <Input
                required
                value={draft.pattern}
                onChange={(e) => setDraft({ ...draft, pattern: e.target.value })}
                spellCheck={false}
                className="font-mono"
              />
            </Field>
            <Field
              label="Digits"
              hint="A minimum, not a maximum — 10000 with 4 digits prints 10000."
            >
              <Input
                type="number"
                min={1}
                max={12}
                value={draft.padding}
                onChange={(e) =>
                  setDraft({ ...draft, padding: Number(e.target.value) })
                }
              />
            </Field>
            <Field
              label="Next number"
              hint="Lowering it past a number already issued is refused."
            >
              <Input
                type="number"
                min={1}
                value={draft.next_value}
                onChange={(e) =>
                  setDraft({ ...draft, next_value: Number(e.target.value) })
                }
              />
            </Field>
          </div>

          <fieldset className="space-y-2">
            <legend className="text-xs font-medium text-text-secondary">
              At the turn of the year
            </legend>
            {RESET_CHOICES.map((choice) => (
              <label
                key={choice.value}
                className="flex cursor-pointer gap-2.5 rounded-md border border-border p-2.5 has-[:checked]:border-accent has-[:checked]:bg-accent/5"
              >
                <input
                  type="radio"
                  name={`reset-${series.id}`}
                  className="mt-0.5"
                  checked={draft.reset === choice.value}
                  onChange={() => setDraft({ ...draft, reset: choice.value })}
                />
                <span>
                  <span className="block text-sm text-text-primary">
                    {choice.label}
                  </span>
                  <span className="block text-[11px] text-text-tertiary">
                    {choice.help}
                  </span>
                </span>
              </label>
            ))}
            {/* When each switch takes effect. Stated here because the two are
                NOT symmetrical and the asymmetry is the specification:
                keeping the counter is immediate, restarting is not. */}
            {draft.reset !== series.reset && (
              <p className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-[11px] text-text-secondary">
                {draft.reset === 'never'
                  ? 'Switching to “Continue without restart” keeps the counter exactly where it is: the next number is the one this series would have issued anyway. No gap, no repeat.'
                  : `Switching to “Restart every year” takes effect at the next turn of the year, not now. ${
                      series.current_period ?? 'The current period'
                    } carries on from ${series.next_value}; the restart at 1 happens in the following January.`}
              </p>
            )}
          </fieldset>

          <div className="rounded-md border border-border bg-bg-secondary p-3">
            <div className="flex flex-wrap items-end gap-4">
              <Field
                label="Preview for the date"
                hint="Optional. Empty means today, where this company is."
              >
                <Input
                  type="date"
                  value={previewDate}
                  onChange={(e) => setPreviewDate(e.target.value)}
                />
              </Field>
              <div>
                <span className="block text-xs font-medium text-text-secondary">
                  Next number
                </span>
                <span className="font-mono text-base text-text-primary">
                  {preview ? preview.number : '—'}
                </span>
              </div>
            </div>

            {previewError && (
              <p
                role="alert"
                className="mt-2 text-xs text-status-error"
              >
                {previewError}
              </p>
            )}

            {preview?.starts_new_period && (
              <p className="mt-2 text-[11px] text-text-tertiary">
                This would start a new period — the counter restarts at 1.
              </p>
            )}

            {preview?.warnings.map((warning) => (
              <p
                key={warning.code}
                role="status"
                className="mt-2 flex gap-2 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-xs text-text-primary"
              >
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-warning" />
                <span>{warning.message}</span>
              </p>
            ))}
          </div>

          <div className="flex items-center gap-3">
            {/* Disabled only while a request is in flight or while there is
                nothing to save, and both say so on the button (rule 17c). A
                failing preview never blocks the save — the server validates
                the pattern anyway, and a dead button beside a message about
                something else is the state this codebase does not produce. */}
            <button
              type="submit"
              disabled={saving || !dirty}
              className="inline-flex h-9 items-center gap-2 rounded-md bg-accent px-4 text-sm font-medium text-accent-foreground transition-all hover:bg-accent-hover disabled:opacity-50"
            >
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
              {saving ? 'Saving…' : dirty ? 'Save changes' : 'No changes'}
            </button>
            {saved && !saveError && (
              <span className="text-xs text-text-secondary">Saved.</span>
            )}
            {saveError && (
              <span className="text-xs text-status-error" role="alert">
                {saveError}
              </span>
            )}
          </div>
        </form>
      )}
    </div>
  )
}
