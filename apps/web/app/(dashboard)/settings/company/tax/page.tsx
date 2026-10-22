'use client'

import * as React from 'react'
import { AlertTriangle, Check, Loader2, ShieldQuestion } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { CompanySection, Field } from '@/components/settings/company-section'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type {
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

// ─── Codes ──────────────────────────────────────────────────────────────────

function CodesTab() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const { can } = usePermissions()
  const canEdit = can('company.settings.edit')

  const [codes, setCodes] = React.useState<TaxCode[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [openId, setOpenId] = React.useState<string | null>(null)

  const refresh = React.useCallback(async () => {
    setLoading(true)
    try {
      setCodes(await api.get<TaxCode[]>('/company/tax/codes'))
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
    void refresh()
    // Keyed on the active company: switching must not leave another
    // company's rates on screen.
  }, [activeCompanyId, refresh])

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
        it is installed — Austria, Germany, or a generic EU pack whose rate you
        set yourself.
      </p>
    )
  }

  return (
    <div className="mt-6 space-y-3">
      {!canEdit && (
        <p className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary">
          You can read these but not change them — changing a rate needs the
          company settings permission.
        </p>
      )}
      {codes.map((code) => (
        <CodeRow
          key={code.id}
          code={code}
          canEdit={canEdit}
          open={openId === code.id}
          onToggle={() => setOpenId(openId === code.id ? null : code.id)}
          onSaved={refresh}
        />
      ))}
    </div>
  )
}

/** Whether this is the generic pack's unset standard rate. */
function needsARate(code: TaxCode): boolean {
  return !code.is_active && code.category === 'S' && code.rate === '0.0000'
}

function CodeRow({
  code,
  canEdit,
  open,
  onToggle,
  onSaved,
}: {
  code: TaxCode
  canEdit: boolean
  open: boolean
  onToggle: () => void
  onSaved: () => Promise<void>
}) {
  const [name, setName] = React.useState(code.name)
  const [rate, setRate] = React.useState(code.rate)
  const [notes, setNotes] = React.useState<Record<string, string>>(
    () => ({ ...code.legal_notes }),
  )
  const [reviewed, setReviewed] = React.useState(code.notes_reviewed)
  const [active, setActive] = React.useState(code.is_active)
  const [saving, setSaving] = React.useState(false)
  const [saveError, setSaveError] = React.useState<string | null>(null)

  // Re-seed from the server's copy when it changes, so what is shown is what
  // was stored rather than what was typed.
  React.useEffect(() => {
    setName(code.name)
    setRate(code.rate)
    setNotes({ ...code.legal_notes })
    setReviewed(code.notes_reviewed)
    setActive(code.is_active)
  }, [code])

  const languages = React.useMemo(() => {
    const present = Object.keys(notes)
    // `de` and `en` always offered: every shipped note has both, and a code
    // that has none still needs somewhere to write one.
    return Array.from(new Set(['de', 'en', ...present]))
  }, [notes])

  const dirty =
    name !== code.name ||
    rate !== code.rate ||
    reviewed !== code.notes_reviewed ||
    active !== code.is_active ||
    JSON.stringify(notes) !== JSON.stringify(code.legal_notes)

  async function save() {
    setSaving(true)
    setSaveError(null)
    try {
      await api.patch(`/company/tax/codes/${code.id}`, {
        name,
        rate,
        legal_notes: notes,
        notes_reviewed: reviewed,
        is_active: active,
      })
      await onSaved()
    } catch (err) {
      setSaveError(
        err instanceof ApiError ? err.detail : 'Could not save this tax code',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-md border border-border">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-bg-hover/50"
      >
        <span className="w-24 font-mono text-sm text-text-primary">
          {code.code}
        </span>
        <span className="flex-1 truncate text-sm text-text-secondary">
          {code.name}
        </span>
        <span className="w-20 text-right font-mono text-sm text-text-primary">
          {code.rate} %
        </span>
        <span className="w-10 text-center text-xs text-text-tertiary">
          {code.category}
        </span>
        <span className="w-32 text-right text-xs">
          {code.is_active ? (
            <span className="text-text-tertiary">Active</span>
          ) : (
            <span className="text-status-warning">Inactive</span>
          )}
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
      </button>

      {needsARate(code) && (
        <p
          data-testid={`tax-code-needs-rate-${code.code}`}
          className="border-t border-border bg-status-warning/10 px-3 py-2 text-xs text-status-warning"
        >
          Set your rate. This is the generic pack’s standard rate: it is 0 %
          and inactive, so nothing can be proposed from it until you enter the
          rate for your country and switch it on.
        </p>
      )}

      {open && canEdit && (
        <div className="space-y-4 border-t border-border p-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" hint="What the dropdown shows beside the code.">
              <Input value={name} onChange={(event) => setName(event.target.value)} />
            </Field>
            <Field
              label="Rate (%)"
              hint="A percent, not a factor: 20 means 20 %."
            >
              <Input
                value={rate}
                inputMode="decimal"
                onChange={(event) => setRate(event.target.value)}
              />
            </Field>
          </div>

          <p className="text-xs text-text-tertiary">
            The code <span className="font-mono">{code.code}</span> and the
            category <span className="font-mono">{code.category}</span> cannot
            be changed. Documents that used this code keep a copy of both, so
            renaming it would restate invoices that are already issued.
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
                FilmBill ships generic wording and no statute numbers. Tick
                this once somebody in your company — or your advisor — has
                confirmed the sentence.
              </span>
            </span>
          </label>

          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={active}
              onChange={(event) => setActive(event.target.checked)}
            />
            Active
          </label>

          {saveError && (
            <p
              role="alert"
              className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
            >
              {saveError}
            </p>
          )}

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

        <Field label="Buyer country" hint="Two-letter ISO code, e.g. DE.">
          <Input
            value={draft.buyer_country}
            maxLength={2}
            onChange={(event) =>
              set('buyer_country', event.target.value.toUpperCase())
            }
          />
        </Field>

        <Field label="Buyer">
          <select
            aria-label="Buyer"
            value={draft.buyer_type}
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
            disabled={draft.buyer_type !== 'business'}
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
            {classes.length === 0 && <option value="standard">standard</option>}
            {classes.map((entry) => (
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
          codes={codes}
          onKind={(value) => set('item_override', value)}
          onCode={(value) => set('item_override_code', value)}
        />
        <OverrideField
          label="Party override"
          kind={draft.party_override}
          code={draft.party_override_code}
          codes={codes}
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
