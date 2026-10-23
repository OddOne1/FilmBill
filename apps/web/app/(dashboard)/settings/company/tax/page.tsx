'use client'

import * as React from 'react'
import { AlertTriangle, Check, Loader2, Plus, ShieldQuestion } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { CompanySection, Field } from '@/components/settings/company-section'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type {
  NoTaxCategoryOption,
  Party,
  PartyPage,
  ResolvedRecipient,
  TaxClass,
  TaxCode,
  TaxOverrideKind,
  TaxProposal,
  TaxRule,
} from '@/types'
import { TAX_WARNING_NOTE_NOT_REVIEWED } from '@/types'

/**
 * Settings → Company → Tax.
 *
 * **This screen decides no tax.** Not the rate, not the treatment, not which
 * rule applies. Every answer in the "Test a case" tab comes from
 * `POST /company/tax/propose`, which runs the same `propose_tax` that P2's
 * finalize will run (CLAUDE.md rule 2). A rule evaluator in TypeScript would
 * be a second implementation of the thing that decides what goes on an
 * invoice, and the one on screen would be the one nobody tested against a
 * real region pack.
 *
 * It also never formats a rate into a sentence of its own: the proposal
 * arrives with `reasons` and `warnings` already written, so the explanation a
 * user reads is the explanation the engine produced.
 *
 * **Read by everyone, edited by owner and admin.** Reads are `company.view`
 * — a rate is printed on every document the company sends, and an accountant
 * or tax advisor who cannot see the table cannot check an invoice against it.
 * Editing needs `company.settings.edit`, so for everybody else the rows are
 * shown with a sentence saying why they cannot be changed rather than with a
 * dead button (rule 17c).
 */

type Tab = 'codes' | 'test'

/** The override kinds the screen offers, with the sentence that explains each. */
const OVERRIDE_CHOICES: {
  value: '' | TaxOverrideKind
  label: string
  help: string
}[] = [
  { value: '', label: 'None', help: '' },
  {
    value: 'domestic_for_foreign_business',
    label: 'Domestic rate for foreign business buyers',
    help: 'SCOPE §6.3’s studio case: “Austrian VAT whatever the customer.” It does nothing on a domestic sale or for a private buyer — there the rules already answer the domestic rate.',
  },
  {
    value: 'code',
    label: 'Always this tax code',
    help: 'Pins one code regardless of where the customer is.',
  },
]

export default function TaxPage() {
  const [tab, setTab] = React.useState<Tab>('codes')

  return (
    <CompanySection
      title="Tax"
      description="Tax codes, classes and rules for"
      // Reading is `company.view`; the Codes tab gates its own editing.
      permission="company.view"
    >
      <p className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary">
        FilmBill <em>proposes</em> the tax and you decide. The rates and the
        rules below arrived from a region pack and are yours to change — none
        of them has been reviewed by a tax advisor.
      </p>

      <div
        role="tablist"
        aria-label="Tax"
        className="mt-5 flex gap-1 border-b border-border"
      >
        {(
          [
            ['codes', 'Codes'],
            ['test', 'Test a case'],
          ] as [Tab, string][]
        ).map(([value, label]) => (
          <button
            key={value}
            role="tab"
            type="button"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
            className={
              tab === value
                ? 'border-b-2 border-accent px-3 py-2 text-sm font-medium text-text-primary'
                : 'border-b-2 border-transparent px-3 py-2 text-sm text-text-secondary hover:text-text-primary'
            }
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'codes' ? <CodesTab /> : <TestTab />}
    </CompanySection>
  )
}

// ─── Codes: two sections, Rates and No-tax cases ────────────────────────────
//
// Two sections rather than one flat table, because they are two different
// things (Mathias, 2026-10-23). A RATE is what an item is labelled with, so it
// is a tax class and its code as a pair. A NO-TAX CASE is a property of a
// situation — this customer, this supply — so it has no class and is chosen as
// an override. Mixing them in one list made the shipped reverse-charge code
// look like something an item could be set to.

/** Whether this code is an unset rate: category `S`, 0 %, switched off. */
function needsARate(code: TaxCode): boolean {
  return !code.is_active && code.category === 'S' && code.rate === '0.0000'
}

function CodesTab() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const { can } = usePermissions()
  const canEdit = can('company.settings.edit')

  const [codes, setCodes] = React.useState<TaxCode[]>([])
  const [classes, setClasses] = React.useState<TaxClass[]>([])
  const [categories, setCategories] = React.useState<NoTaxCategoryOption[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [adding, setAdding] = React.useState<'rate' | 'no-tax' | null>(null)

  // `spinner` only on the FIRST load. A refresh after a save that flipped
  // `loading` would unmount this whole section, taking the row's own state
  // with it — including the notice the server just sent about what a rate
  // change means, which would appear for one frame and vanish.
  const refresh = React.useCallback(async (spinner = false) => {
    if (spinner) setLoading(true)
    try {
      const [loadedCodes, loadedClasses, loadedCategories] = await Promise.all([
        api.get<TaxCode[]>('/company/tax/codes'),
        api.get<TaxClass[]>('/company/tax/classes'),
        api.get<NoTaxCategoryOption[]>('/company/tax/no-tax-categories'),
      ])
      setCodes(loadedCodes)
      setClasses(loadedClasses)
      setCategories(loadedCategories)
      setError(null)
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : 'Could not load the tax codes',
      )
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    void refresh(true)
    // Keyed on the active company: switching must not leave another
    // company's rates on screen.
  }, [activeCompanyId, refresh])

  const byId = React.useMemo(() => {
    const map = new Map<string, TaxCode>()
    codes.forEach((code) => map.set(code.id, code))
    return map
  }, [codes])

  // A no-tax case is a code no class points at. Derived rather than guessed
  // from the category, so a company's own rated code with no class — which
  // should not happen, and would otherwise vanish from the screen entirely —
  // still shows up somewhere a person can see it.
  const noTaxCases = React.useMemo(() => {
    const classed = new Set(classes.map((entry) => entry.code_id))
    return codes.filter((code) => !classed.has(code.id))
  }, [codes, classes])

  if (loading) {
    return (
      <div className="mt-6 flex items-center gap-2 p-4 text-sm text-text-tertiary">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading…
      </div>
    )
  }

  if (error) {
    return (
      <p
        role="alert"
        className="mt-6 rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
      >
        {error}
      </p>
    )
  }

  if (codes.length === 0) {
    // The state of a company with no country: the migration and the seeder
    // both skip it on purpose. Says what to do rather than showing nothing.
    return (
      <p
        data-testid="tax-codes-empty"
        className="mt-6 rounded-md border border-border px-3 py-4 text-sm text-text-secondary"
      >
        This company has no tax codes yet, because it has no country. Set the
        country on the <strong>Company</strong> screen and the region pack for
        it is installed — Austria, Germany, or two blank rates you fill in
        yourself.
      </p>
    )
  }

  return (
    <div className="mt-6 space-y-8">
      {!canEdit && (
        <p className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary">
          You can read these but not change them — adding or changing a rate
          needs the company settings permission.
        </p>
      )}

      <section>
        <SectionHeader
          title="Rates"
          hint="What an item is labelled with. Each rate is a tax class and the code that carries its percentage."
          action={
            canEdit ? (
              <button
                type="button"
                data-testid="tax-add-rate"
                onClick={() => setAdding('rate')}
                className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-sm text-text-primary hover:bg-bg-hover"
              >
                <Plus className="h-3.5 w-3.5" /> Add rate
              </button>
            ) : null
          }
        />
        <div className="mt-3 space-y-2">
          {classes.map((entry) => {
            const code = byId.get(entry.code_id)
            if (!code) return null
            return (
              <RateRow
                key={entry.id}
                taxClass={entry}
                code={code}
                canEdit={canEdit}
                onSaved={refresh}
              />
            )
          })}
        </div>
      </section>

      <section>
        <SectionHeader
          title="No-tax cases"
          hint="Reverse charge, exports, exemptions. Not a property of an item — picked per customer, per item or per document."
          action={
            canEdit ? (
              <button
                type="button"
                data-testid="tax-add-no-tax-case"
                onClick={() => setAdding('no-tax')}
                className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-sm text-text-primary hover:bg-bg-hover"
              >
                <Plus className="h-3.5 w-3.5" /> Add no-tax case
              </button>
            ) : null
          }
        />
        <div className="mt-3 space-y-2">
          {noTaxCases.map((code) => (
            <NoTaxRow
              key={code.id}
              code={code}
              categories={categories}
              canEdit={canEdit}
              onSaved={refresh}
            />
          ))}
        </div>
      </section>

      {adding === 'rate' && (
        <AddRateDialog
          onClose={() => setAdding(null)}
          onCreated={async () => {
            setAdding(null)
            await refresh()
          }}
        />
      )}
      {adding === 'no-tax' && (
        <AddNoTaxCaseDialog
          categories={categories}
          onClose={() => setAdding(null)}
          onCreated={async () => {
            setAdding(null)
            await refresh()
          }}
        />
      )}
    </div>
  )
}

function SectionHeader({
  title,
  hint,
  action,
}: {
  title: string
  hint: string
  action: React.ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border pb-2">
      <div>
        <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
        <p className="mt-0.5 text-xs text-text-tertiary">{hint}</p>
      </div>
      {action}
    </div>
  )
}

/** The Activate / Deactivate control, and the 409 it may come back with.
 *
 *  Shared by both rows because the refusal is the interesting part and must
 *  read the same in both places: the server names what still points at the
 *  code, and showing a generic "could not save" instead would leave the user
 *  with nowhere to go. */
function ActivityButton({
  active,
  disabled,
  onToggle,
  testId,
}: {
  active: boolean
  disabled: boolean
  onToggle: (next: boolean) => Promise<void>
  testId: string
}) {
  const [busy, setBusy] = React.useState(false)
  return (
    <button
      type="button"
      data-testid={testId}
      disabled={disabled || busy}
      onClick={async () => {
        setBusy(true)
        try {
          await onToggle(!active)
        } finally {
          setBusy(false)
        }
      }}
      className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover disabled:opacity-50"
    >
      {busy ? '…' : active ? 'Deactivate' : 'Activate'}
    </button>
  )
}

function RateRow({
  taxClass,
  code,
  canEdit,
  onSaved,
}: {
  taxClass: TaxClass
  code: TaxCode
  canEdit: boolean
  onSaved: () => Promise<void>
}) {
  const [open, setOpen] = React.useState(false)
  const [label, setLabel] = React.useState(taxClass.label)
  const [rate, setRate] = React.useState(code.rate)
  const [saving, setSaving] = React.useState(false)
  const [message, setMessage] = React.useState<string | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)

  React.useEffect(() => {
    setLabel(taxClass.label)
    setRate(code.rate)
  }, [taxClass, code])

  const dirty = label !== taxClass.label || rate !== code.rate

  async function save() {
    setSaving(true)
    setMessage(null)
    setNotice(null)
    try {
      // Two requests, because a rate is two rows: the label belongs to the
      // class and the percentage to the code. The class goes first, so a
      // refused rate change does not leave the rename unsaved as well.
      if (label !== taxClass.label) {
        await api.patch(`/company/tax/classes/${taxClass.id}`, { label })
      }
      if (rate !== code.rate) {
        const updated = await api.patch<TaxCode>(
          `/company/tax/codes/${code.id}`,
          { rate },
        )
        // The server's own sentence about what a rate change means.
        setNotice(updated.notices[0] ?? null)
      }
      await onSaved()
    } catch (err) {
      setMessage(
        err instanceof ApiError ? err.detail : 'Could not save this rate',
      )
    } finally {
      setSaving(false)
    }
  }

  async function toggle(next: boolean) {
    setMessage(null)
    try {
      // Through the CLASS, which switches the code with it: a rate is a 1:1
      // pair and one half off is a state nothing here could explain.
      await api.patch(`/company/tax/classes/${taxClass.id}`, {
        is_active: next,
      })
      await onSaved()
    } catch (err) {
      setMessage(
        err instanceof ApiError
          ? err.detail
          : 'Could not change whether this rate is active',
      )
    }
  }

  return (
    <div className="rounded-md border border-border">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <span className="w-28 font-mono text-sm text-text-primary">
          {code.code}
        </span>
        <span className="flex-1 truncate text-sm text-text-secondary">
          {taxClass.label}
          {taxClass.is_custom && (
            <span className="ml-2 text-xs text-text-tertiary">added here</span>
          )}
        </span>
        <span className="w-20 text-right font-mono text-sm text-text-primary">
          {code.rate} %
        </span>
        <span className="w-24 text-right text-xs">
          {taxClass.is_active ? (
            <span className="text-text-tertiary">Active</span>
          ) : (
            <span className="text-status-warning">Inactive</span>
          )}
        </span>
        {canEdit && (
          <>
            <button
              type="button"
              onClick={() => setOpen(!open)}
              className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
            >
              Edit
            </button>
            <ActivityButton
              active={taxClass.is_active}
              disabled={false}
              onToggle={toggle}
              testId={`tax-rate-toggle-${code.code}`}
            />
          </>
        )}
      </div>

      {needsARate(code) && (
        <p
          data-testid={`tax-code-needs-rate-${code.code}`}
          className="border-t border-border bg-status-warning/10 px-3 py-2 text-xs text-status-warning"
        >
          Set your rate. This rate is 0 % and switched off, so nothing can be
          proposed from it until you enter the percentage for your country and
          activate it.
        </p>
      )}

      {message && (
        <p
          role="alert"
          data-testid={`tax-rate-error-${code.code}`}
          className="border-t border-border bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {message}
        </p>
      )}
      {notice && (
        <p
          data-testid={`tax-rate-notice-${code.code}`}
          className="border-t border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary"
        >
          {notice}
        </p>
      )}

      {open && canEdit && (
        <div className="space-y-4 border-t border-border p-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" hint="What the item picker shows.">
              <Input
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </Field>
            <Field label="Rate (%)" hint="A percent, not a factor: 20 means 20 %.">
              <Input
                value={rate}
                inputMode="decimal"
                onChange={(event) => setRate(event.target.value)}
              />
            </Field>
          </div>
          <p className="text-xs text-text-tertiary">
            The code <span className="font-mono">{code.code}</span> cannot be
            changed. Documents that used it keep a copy, so renaming it would
            restate invoices that are already issued.
          </p>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || !dirty}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          >
            {saving ? 'Saving…' : dirty ? 'Save' : 'No changes'}
          </button>
        </div>
      )}
    </div>
  )
}

function NoTaxRow({
  code,
  categories,
  canEdit,
  onSaved,
}: {
  code: TaxCode
  categories: NoTaxCategoryOption[]
  canEdit: boolean
  onSaved: () => Promise<void>
}) {
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState(code.name)
  const [notes, setNotes] = React.useState<Record<string, string>>(() => ({
    ...code.legal_notes,
  }))
  const [reviewed, setReviewed] = React.useState(code.notes_reviewed)
  const [saving, setSaving] = React.useState(false)
  const [message, setMessage] = React.useState<string | null>(null)

  React.useEffect(() => {
    setName(code.name)
    setNotes({ ...code.legal_notes })
    setReviewed(code.notes_reviewed)
  }, [code])

  const kind = categories.find((entry) => entry.category === code.category)
  const languages = Array.from(new Set(['de', 'en', ...Object.keys(notes)]))
  const dirty =
    name !== code.name ||
    reviewed !== code.notes_reviewed ||
    JSON.stringify(notes) !== JSON.stringify(code.legal_notes)

  async function save() {
    setSaving(true)
    setMessage(null)
    try {
      await api.patch(`/company/tax/codes/${code.id}`, {
        name,
        legal_notes: notes,
        notes_reviewed: reviewed,
      })
      await onSaved()
    } catch (err) {
      setMessage(
        err instanceof ApiError ? err.detail : 'Could not save this case',
      )
    } finally {
      setSaving(false)
    }
  }

  async function toggle(next: boolean) {
    setMessage(null)
    try {
      await api.patch(`/company/tax/codes/${code.id}`, { is_active: next })
      await onSaved()
    } catch (err) {
      setMessage(
        err instanceof ApiError
          ? err.detail
          : 'Could not change whether this case is active',
      )
    }
  }

  return (
    <div className="rounded-md border border-border">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <span className="w-28 font-mono text-sm text-text-primary">
          {code.code}
        </span>
        <span className="flex-1 truncate text-sm text-text-secondary">
          {code.name}
        </span>
        <span className="w-56 truncate text-xs text-text-tertiary">
          {kind ? (kind.labels.en ?? code.category) : code.category}
        </span>
        <span className="w-36 text-right text-xs">
          {Object.keys(code.legal_notes).length === 0 ? (
            <span className="text-text-tertiary">No legal note</span>
          ) : code.notes_reviewed ? (
            <span className="inline-flex items-center gap-1 text-status-success">
              <Check className="h-3 w-3" /> Note reviewed
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-status-warning">
              <AlertTriangle className="h-3 w-3" /> Note not reviewed
            </span>
          )}
        </span>
        <span className="w-20 text-right text-xs">
          {code.is_active ? (
            <span className="text-text-tertiary">Active</span>
          ) : (
            <span className="text-status-warning">Inactive</span>
          )}
        </span>
        {canEdit && (
          <>
            <button
              type="button"
              onClick={() => setOpen(!open)}
              className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
            >
              Edit
            </button>
            <ActivityButton
              active={code.is_active}
              disabled={false}
              onToggle={toggle}
              testId={`tax-case-toggle-${code.code}`}
            />
          </>
        )}
      </div>

      {needsARate(code) && (
        <p
          data-testid={`tax-code-needs-rate-${code.code}`}
          className="border-t border-border bg-status-warning/10 px-3 py-2 text-xs text-status-warning"
        >
          Set your rate. This rate is 0 % and switched off, so nothing can be
          proposed from it until you enter the percentage for your country and
          activate it.
        </p>
      )}

      {message && (
        <p
          role="alert"
          data-testid={`tax-case-error-${code.code}`}
          className="border-t border-border bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {message}
        </p>
      )}

      {open && canEdit && (
        <div className="space-y-4 border-t border-border p-4">
          <Field label="Name">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <p className="text-xs text-text-tertiary">
            The kind (<span className="font-mono">{code.category}</span>) and
            the code cannot be changed. Both go into e-invoices, where a
            recipient&rsquo;s system validates them.
          </p>
          <div className="space-y-2">
            <p className="text-xs font-medium text-text-secondary">
              Legal note, per language
            </p>
            {languages.map((lang) => (
              <Field key={lang} label={lang.toUpperCase()}>
                <Input
                  value={notes[lang] ?? ''}
                  placeholder="No note in this language"
                  onChange={(event) => {
                    const next = { ...notes }
                    if (event.target.value === '') delete next[lang]
                    else next[lang] = event.target.value
                    setNotes(next)
                  }}
                />
              </Field>
            ))}
          </div>
          <label className="flex items-start gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(event) => setReviewed(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              This note has been reviewed
              <span className="block text-xs text-text-tertiary">
                The wording is yours and yours to be right about. Check it
                with your tax advisor, then tick this.
              </span>
            </span>
          </label>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || !dirty}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          >
            {saving ? 'Saving…' : dirty ? 'Save' : 'No changes'}
          </button>
        </div>
      )}
    </div>
  )
}

/** The frame both dialogs share. Not a modal with a focus trap: these are two
 *  short forms, and a half-built dialog primitive is worse than a panel. */
function AddPanel({
  title,
  children,
  onClose,
}: {
  title: string
  children: React.ReactNode
  onClose: () => void
}) {
  return (
    <div
      role="dialog"
      aria-label={title}
      className="rounded-md border border-accent/40 bg-bg-secondary p-4"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-primary">{title}</h3>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
        >
          Cancel
        </button>
      </div>
      <div className="mt-4 space-y-4">{children}</div>
    </div>
  )
}

function AddRateDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: () => Promise<void>
}) {
  const [label, setLabel] = React.useState('')
  const [rate, setRate] = React.useState('')
  const [code, setCode] = React.useState('')
  const [saving, setSaving] = React.useState(false)
  const [message, setMessage] = React.useState<string | null>(null)

  async function create() {
    setSaving(true)
    setMessage(null)
    try {
      await api.post('/company/tax/rates', {
        label,
        rate,
        code: code.trim() || null,
      })
      await onCreated()
    } catch (err) {
      setMessage(
        err instanceof ApiError ? err.detail : 'Could not add this rate',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <AddPanel title="Add rate" onClose={onClose}>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Name" hint="What the item picker shows.">
          <Input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </Field>
        <Field label="Rate (%)" hint="Above 0 and below 100.">
          <Input
            value={rate}
            inputMode="decimal"
            onChange={(event) => setRate(event.target.value)}
          />
        </Field>
        <Field
          label="Code (optional)"
          hint="Left empty, the server names it after your country and the rate."
        >
          <Input
            value={code}
            onChange={(event) => setCode(event.target.value.toUpperCase())}
          />
        </Field>
      </div>
      <p className="text-xs text-text-tertiary">
        A rate is a tax class plus its code, created together. It is a
        standard-rated code (<span className="font-mono">S</span>) whatever
        the percentage — that is what a reduced rate is.
      </p>
      {message && (
        <p
          role="alert"
          data-testid="tax-add-rate-error"
          className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {message}
        </p>
      )}
      <button
        type="button"
        data-testid="tax-add-rate-submit"
        onClick={() => void create()}
        disabled={saving || !label.trim() || !rate.trim()}
        className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
      >
        {saving ? 'Adding…' : 'Add rate'}
      </button>
    </AddPanel>
  )
}

function AddNoTaxCaseDialog({
  categories,
  onClose,
  onCreated,
}: {
  categories: NoTaxCategoryOption[]
  onClose: () => void
  onCreated: () => Promise<void>
}) {
  const [category, setCategory] = React.useState<string>('')
  const [label, setLabel] = React.useState('')
  const [code, setCode] = React.useState('')
  const [notes, setNotes] = React.useState<Record<string, string>>({})
  const [saving, setSaving] = React.useState(false)
  const [message, setMessage] = React.useState<string | null>(null)

  const chosen = categories.find((entry) => entry.category === category)

  async function create() {
    setSaving(true)
    setMessage(null)
    try {
      await api.post('/company/tax/no-tax-cases', {
        label,
        category,
        code: code.trim() || null,
        legal_notes: notes,
      })
      await onCreated()
    } catch (err) {
      setMessage(
        err instanceof ApiError ? err.detail : 'Could not add this case',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <AddPanel title="Add no-tax case" onClose={onClose}>
      <Field
        label="Kind"
        hint="One of six. These go into e-invoices, where the recipient's system validates them — which is why the list is fixed."
      >
        <select
          aria-label="Kind"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          className="w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
        >
          <option value="">Choose a kind…</option>
          {categories.map((entry) => (
            <option key={entry.category} value={entry.category}>
              {entry.category} — {entry.labels.en}
            </option>
          ))}
        </select>
      </Field>
      {chosen && (
        <p
          data-testid="tax-no-tax-kind-help"
          className="rounded-md border border-border bg-bg-primary px-3 py-2 text-xs text-text-secondary"
        >
          <span className="block">{chosen.labels.en}</span>
          <span className="mt-1 block text-text-tertiary">
            {chosen.labels.de}
          </span>
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" hint="What you will recognise in a picker.">
          <Input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </Field>
        <Field
          label="Code (optional)"
          hint="Left empty, the server names it after your country and the kind."
        >
          <Input
            value={code}
            onChange={(event) => setCode(event.target.value.toUpperCase())}
          />
        </Field>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium text-text-secondary">
          Legal note, per language
        </p>
        {['de', 'en'].map((lang) => (
          <Field key={lang} label={lang.toUpperCase()}>
            <Input
              value={notes[lang] ?? ''}
              placeholder="The sentence printed beside the amount"
              onChange={(event) => {
                const next = { ...notes }
                if (event.target.value === '') delete next[lang]
                else next[lang] = event.target.value
                setNotes(next)
              }}
            />
          </Field>
        ))}
      </div>

      <p
        data-testid="tax-no-tax-responsibility"
        className="rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-xs text-status-warning"
      >
        This wording is yours and yours to be right about. FilmBill does not
        check it and cannot — have it confirmed by your tax advisor before you
        send an invoice carrying it.
      </p>

      {message && (
        <p
          role="alert"
          data-testid="tax-add-no-tax-error"
          className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {message}
        </p>
      )}
      <button
        type="button"
        data-testid="tax-add-no-tax-submit"
        onClick={() => void create()}
        disabled={saving || !label.trim() || !category}
        className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
      >
        {saving ? 'Adding…' : 'Add no-tax case'}
      </button>
    </AddPanel>
  )
}

// ─── Test a case ────────────────────────────────────────────────────────────

interface CaseDraft {
  buyer_country: string
  buyer_type: 'business' | 'private'
  buyer_vat_verified: boolean
  supply_kind: 'goods' | 'services'
  item_class: string
  item_override: '' | TaxOverrideKind
  item_override_code: string
  party_override: '' | TaxOverrideKind
  party_override_code: string
}

const EMPTY_CASE: CaseDraft = {
  buyer_country: 'AT',
  buyer_type: 'business',
  buyer_vat_verified: false,
  supply_kind: 'services',
  item_class: 'standard',
  item_override: '',
  item_override_code: '',
  party_override: '',
  party_override_code: '',
}

function overrideBody(
  kind: '' | TaxOverrideKind,
  code: string,
): { kind: TaxOverrideKind; code?: string } | undefined {
  if (!kind) return undefined
  if (kind === 'code') return { kind, code }
  return { kind }
}

function TestTab() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const [draft, setDraft] = React.useState<CaseDraft>(EMPTY_CASE)
  // P1b: take the buyer from a real party instead of typing one.
  const [parties, setParties] = React.useState<Party[]>([])
  const [fromPartyId, setFromPartyId] = React.useState('')
  const [resolved, setResolved] = React.useState<ResolvedRecipient | null>(null)
  const [codes, setCodes] = React.useState<TaxCode[]>([])
  const [classes, setClasses] = React.useState<TaxClass[]>([])
  const [rules, setRules] = React.useState<TaxRule[]>([])
  const [proposal, setProposal] = React.useState<TaxProposal | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [pending, setPending] = React.useState(false)

  React.useEffect(() => {
    void (async () => {
      try {
        const [loadedCodes, loadedClasses, loadedRules] = await Promise.all([
          api.get<TaxCode[]>('/company/tax/codes'),
          api.get<TaxClass[]>('/company/tax/classes'),
          api.get<TaxRule[]>('/company/tax/rules'),
        ])
        setCodes(loadedCodes)
        setClasses(loadedClasses)
        setRules(loadedRules)
      } catch (err) {
        setError(
          err instanceof ApiError
            ? err.detail
            : 'Could not load this company’s tax configuration',
        )
      }
    })()
  }, [activeCompanyId])

  // The party list, for the "take the buyer from" selector. Loaded once —
  // a company has tens of customers, not thousands, and the selector is a
  // convenience rather than the search.
  React.useEffect(() => {
    void (async () => {
      try {
        const page = await api.get<PartyPage>('/parties?limit=200')
        setParties(page.parties)
      } catch {
        // No `parties.view`, or none yet. The selector simply does not
        // appear; the fields stay typeable.
        setParties([])
      }
    })()
  }, [activeCompanyId])

  // When a party is chosen, the buyer fields come from the API's RESOLVED
  // recipient — never from the party row, and never worked out here. A
  // department inherits its organisation's VAT ID and verified flag, and
  // only the resolver knows that.
  React.useEffect(() => {
    if (!fromPartyId) {
      setResolved(null)
      return
    }
    void (async () => {
      try {
        const answer = await api.get<ResolvedRecipient>(
          `/parties/${fromPartyId}/resolved`,
        )
        setResolved(answer)
        setDraft((current) => ({
          ...current,
          buyer_country: String(answer.tax_country.value ?? ''),
          buyer_type: answer.buyer_type === 'private' ? 'private' : 'business',
          buyer_vat_verified: Boolean(answer.vat_verified.value),
        }))
      } catch (err) {
        setResolved(null)
        setError(
          err instanceof ApiError
            ? err.detail
            : 'Could not read that party’s details',
        )
      }
    })()
  }, [fromPartyId])

  // The proposal, from the API, debounced. Every option change asks the
  // server — there is no local evaluation to fall back on, by design.
  React.useEffect(() => {
    setPending(true)
    const handle = setTimeout(async () => {
      try {
        setProposal(
          await api.post<TaxProposal>('/company/tax/propose', {
            buyer_country: draft.buyer_country || null,
            buyer_type: draft.buyer_type,
            buyer_vat_verified: draft.buyer_vat_verified,
            supply_kind: draft.supply_kind,
            item_class: draft.item_class,
            item_override: overrideBody(
              draft.item_override,
              draft.item_override_code,
            ),
            party_override: overrideBody(
              draft.party_override,
              draft.party_override_code,
            ),
          }),
        )
        setError(null)
      } catch (err) {
        setProposal(null)
        setError(
          err instanceof ApiError ? err.detail : 'Could not propose a tax',
        )
      } finally {
        setPending(false)
      }
    }, 250)
    return () => clearTimeout(handle)
  }, [draft, activeCompanyId])

  function set<K extends keyof CaseDraft>(key: K, value: CaseDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  // Only what can actually be proposed. A retired rate in the picker is a
  // selection whose only possible answer is `blocking`, and a retired no-tax
  // case as an override is the same — so neither is offered. Both halves have
  // to be active: the class and its code move together, and a disagreement
  // would be a bug worth not hiding.
  const activeClasses = React.useMemo(
    () =>
      classes.filter((entry) => entry.is_active && entry.code_is_active),
    [classes],
  )
  const activeCodes = React.useMemo(
    () => codes.filter((entry) => entry.is_active),
    [codes],
  )

  const ruleByPosition = React.useMemo(() => {
    const map = new Map<number, TaxRule>()
    rules.forEach((rule) => map.set(rule.position, rule))
    return map
  }, [rules])

  return (
    <div className="mt-6 grid gap-6 lg:grid-cols-2">
      <div className="space-y-4">
        <Field
          label="Seller country"
          hint="Your company’s country, from the Company screen. It cannot be changed here — a proposal for a company you are not would be a rate somebody could invoice."
        >
          <Input
            value={proposal?.seller_country ?? ''}
            placeholder="Not set"
            readOnly
            disabled
          />
        </Field>

        {parties.length > 0 && (
          <Field
            label="Take buyer from a party"
            hint="Fills the buyer fields from what a document to that party would actually use — inheritance included."
          >
            <select
              aria-label="Take buyer from a party"
              value={fromPartyId}
              onChange={(event) => setFromPartyId(event.target.value)}
              className="w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
            >
              <option value="">Type the buyer by hand</option>
              {parties.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name} ({entry.type})
                </option>
              ))}
            </select>
          </Field>
        )}

        <Field
          label="Buyer country"
          hint={
            resolved
              ? undefined
              : 'Two-letter ISO code, e.g. DE.'
          }
        >
          <Input
            aria-label="Buyer country"
            value={draft.buyer_country}
            maxLength={2}
            readOnly={resolved !== null}
            disabled={resolved !== null}
            onChange={(event) =>
              set('buyer_country', event.target.value.toUpperCase())
            }
          />
        </Field>
        {resolved && (
          <p
            data-testid="tax-buyer-from-party"
            className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary"
          >
            From <strong>{String(resolved.names.organisation ?? resolved.names[resolved.own_level])}</strong>:
            country {String(resolved.tax_country.value ?? '—')} (from{' '}
            {resolved.tax_country.source ?? 'nowhere'}), VAT ID{' '}
            {String(resolved.vat_id.value ?? '—')} (from{' '}
            {resolved.vat_id.source ?? 'nowhere'}),{' '}
            {resolved.vat_verified.value ? 'verified' : 'not verified'},
            treated as {resolved.buyer_type}. The fields are read-only while a
            party is chosen.
          </p>
        )}

        <Field label="Buyer">
          <select
            aria-label="Buyer"
            value={draft.buyer_type}
            disabled={resolved !== null}
            onChange={(event) =>
              set('buyer_type', event.target.value as 'business' | 'private')
            }
            className="w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
          >
            <option value="business">Business</option>
            <option value="private">Private</option>
          </select>
        </Field>

        <label className="flex items-start gap-2 text-sm text-text-secondary">
          <input
            type="checkbox"
            checked={draft.buyer_vat_verified}
            disabled={draft.buyer_type !== 'business' || resolved !== null}
            onChange={(event) => set('buyer_vat_verified', event.target.checked)}
            className="mt-0.5"
          />
          <span>
            VAT ID verified
            <span className="block text-xs text-text-tertiary">
              {draft.buyer_type === 'business'
                ? 'Only a verified ID gets a reverse charge proposed.'
                : 'A private buyer has no VAT ID to verify.'}
            </span>
          </span>
        </label>

        <Field label="Supply">
          <select
            aria-label="Supply"
            value={draft.supply_kind}
            onChange={(event) =>
              set('supply_kind', event.target.value as 'goods' | 'services')
            }
            className="w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
          >
            <option value="services">Services</option>
            <option value="goods">Goods</option>
          </select>
        </Field>

        <Field
          label="Item tax class"
          hint="What the item points at. The domestic rate comes from here."
        >
          <select
            aria-label="Item tax class"
            value={draft.item_class}
            onChange={(event) => set('item_class', event.target.value)}
            className="w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
          >
            {activeClasses.length === 0 && (
              <option value="standard">standard</option>
            )}
            {activeClasses.map((entry) => (
              <option key={entry.key} value={entry.key}>
                {entry.label} — {entry.code}, {entry.rate} %
              </option>
            ))}
          </select>
        </Field>

        <OverrideField
          label="Item override"
          kind={draft.item_override}
          code={draft.item_override_code}
          codes={activeCodes}
          onKind={(value) => set('item_override', value)}
          onCode={(value) => set('item_override_code', value)}
        />
        <OverrideField
          label="Party override"
          kind={draft.party_override}
          code={draft.party_override_code}
          codes={activeCodes}
          onKind={(value) => set('party_override', value)}
          onCode={(value) => set('party_override_code', value)}
        />
      </div>

      <div>
        {error && (
          <p
            role="alert"
            className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
          >
            {error}
          </p>
        )}
        {!error && proposal === null && (
          <div className="flex items-center gap-2 p-4 text-sm text-text-tertiary">
            <Loader2 className="h-4 w-4 animate-spin" />
            Asking the server…
          </div>
        )}
        {proposal && (
          <ProposalPanel
            proposal={proposal}
            pending={pending}
            ruleByPosition={ruleByPosition}
          />
        )}
      </div>
    </div>
  )
}

function OverrideField({
  label,
  kind,
  code,
  codes,
  onKind,
  onCode,
}: {
  label: string
  kind: '' | TaxOverrideKind
  code: string
  codes: TaxCode[]
  onKind: (value: '' | TaxOverrideKind) => void
  onCode: (value: string) => void
}) {
  const choice = OVERRIDE_CHOICES.find((entry) => entry.value === kind)
  return (
    <div className="space-y-2">
      <Field label={label} hint={choice?.help || undefined}>
        <select
          aria-label={label}
          value={kind}
          onChange={(event) => onKind(event.target.value as '' | TaxOverrideKind)}
          className="w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
        >
          {OVERRIDE_CHOICES.map((entry) => (
            <option key={entry.value} value={entry.value}>
              {entry.label}
            </option>
          ))}
        </select>
      </Field>
      {kind === 'code' && (
        <Field label={`${label} — tax code`}>
          <select
            aria-label={`${label} — tax code`}
            value={code}
            onChange={(event) => onCode(event.target.value)}
            className="w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
          >
            <option value="">Pick a code…</option>
            {codes.map((entry) => (
              <option key={entry.id} value={entry.code}>
                {entry.code} — {entry.name}
              </option>
            ))}
          </select>
        </Field>
      )}
    </div>
  )
}

function ProposalPanel({
  proposal,
  pending,
  ruleByPosition,
}: {
  proposal: TaxProposal
  pending: boolean
  ruleByPosition: Map<number, TaxRule>
}) {
  const rule =
    proposal.applied_rule_position === null ||
    proposal.applied_rule_position === undefined
      ? undefined
      : ruleByPosition.get(proposal.applied_rule_position)

  return (
    <div
      data-testid="tax-proposal"
      className={
        pending
          ? 'rounded-md border border-border p-4 opacity-60'
          : 'rounded-md border border-border p-4'
      }
    >
      {proposal.blocking ? (
        <p
          data-testid="tax-proposal-blocked"
          className="flex items-start gap-2 text-sm font-medium text-status-error"
        >
          <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" />
          Nothing can be proposed for this case.
        </p>
      ) : (
        <>
          <p className="text-xs uppercase tracking-wide text-text-tertiary">
            {proposal.treatment_label}
          </p>
          <p className="mt-1 text-2xl font-semibold text-text-primary">
            <span className="font-mono">{proposal.code}</span>{' '}
            <span className="text-base font-normal text-text-secondary">
              {proposal.rate} %
            </span>
          </p>
        </>
      )}

      {proposal.legal_note && (
        <p className="mt-3 rounded-md border border-border bg-bg-secondary px-3 py-2 text-sm text-text-secondary">
          “{proposal.legal_note}”
          {proposal.warnings.some(
            (warning) => warning.code === TAX_WARNING_NOTE_NOT_REVIEWED,
          ) && (
            <span className="mt-1 block text-xs text-status-warning">
              Not reviewed
            </span>
          )}
        </p>
      )}

      {proposal.applied_rule && (
        <p className="mt-3 text-sm text-text-secondary">
          {proposal.applied_rule.startsWith('override:') ? (
            <>
              Decided by the{' '}
              <strong>
                {proposal.applied_rule === 'override:item' ? 'item' : 'party'}
              </strong>{' '}
              override.
            </>
          ) : (
            <>
              Decided by rule {proposal.applied_rule_position}:{' '}
              <strong>{rule?.name ?? proposal.applied_rule}</strong>
            </>
          )}
        </p>
      )}

      {proposal.would_have_applied && (
        <div
          data-testid="tax-would-have-applied"
          className="mt-3 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-sm text-status-warning"
        >
          <p className="font-medium">
            Without the override this would have been{' '}
            <span className="font-mono">
              {proposal.would_have_applied.code}
            </span>{' '}
            at {proposal.would_have_applied.rate} % —{' '}
            {proposal.would_have_applied.treatment_label}.
          </p>
          {proposal.would_have_applied.applied_rule_position !== null && (
            <p className="mt-0.5 text-xs">
              Rule {proposal.would_have_applied.applied_rule_position}:{' '}
              {proposal.would_have_applied.applied_rule}
            </p>
          )}
        </div>
      )}

      {proposal.warnings.length > 0 && (
        <ul className="mt-3 space-y-2">
          {proposal.warnings.map((warning) => (
            <li
              key={warning.code}
              data-testid={`tax-warning-${warning.code}`}
              className="flex items-start gap-2 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-sm text-status-warning"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{warning.text}</span>
            </li>
          ))}
        </ul>
      )}

      {proposal.reasons.length > 0 && (
        <>
          <p className="mt-4 text-xs font-medium uppercase tracking-wide text-text-tertiary">
            How it got there
          </p>
          <ol className="mt-1 space-y-1 text-sm text-text-secondary">
            {proposal.reasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ol>
        </>
      )}
    </div>
  )
}
