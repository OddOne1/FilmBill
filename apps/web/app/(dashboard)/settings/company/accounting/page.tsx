'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import {
  CompanySection,
  Field,
  SaveRow,
} from '@/components/settings/company-section'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type { ArchiveDateBasis, BookkeepingMode, Company, VatTiming } from '@/types'

/**
 * Settings → Company → Accounting.
 *
 * **Every selector here is stored and read by nothing.** No calculation
 * consults them, no export looks at them, no document renders differently.
 * SCOPE §10 (D13) puts the behaviour in P6; the answers are collected now
 * because they are settled once, with a tax advisor, long before P6 needs them
 * — and a company that has already recorded "Ist-Versteuerung,
 * Kleinunternehmer" is a company whose first invoice can be right rather than
 * migrated.
 *
 * The screen says so, in the banner at the top. A settings page whose controls
 * secretly do nothing is the same bug as a notification category that gates
 * nothing (see Settings → Notifications): it is a false statement to the user,
 * and the fix is to say what is true rather than to hide the control.
 *
 * The option lists for the two placeholder selectors come from region packs
 * (CLAUDE.md rule 7), which do not exist yet — hence `PLACEHOLDER_*` below,
 * and hence those two being free strings server-side while the other three are
 * `Literal`s the API enforces.
 */

const BOOKKEEPING: { value: BookkeepingMode; label: string; help: string }[] = [
  {
    value: 'ear',
    label: 'Einnahmen-Ausgaben-Rechnung',
    help: 'Cash-basis. The ordinary choice for a small production company.',
  },
  {
    value: 'double_entry',
    label: 'Doppelte Buchführung',
    help: 'Double-entry. Required above the Austrian revenue thresholds, and for a GmbH.',
  },
]

const VAT: { value: VatTiming; label: string; help: string }[] = [
  {
    value: 'soll',
    label: 'Soll-Versteuerung',
    help: 'VAT is owed in the month the invoice is issued.',
  },
  {
    value: 'ist',
    label: 'Ist-Versteuerung',
    help: 'VAT is owed in the month the invoice is paid.',
  },
]

const ARCHIVE_BASIS: { value: ArchiveDateBasis; label: string; help: string }[] = [
  {
    value: 'invoice_date',
    label: 'Invoice date',
    help: 'A document is filed under the period it was issued in.',
  },
  {
    value: 'payment_date',
    label: 'Payment date',
    help: 'A document is filed under the period it was paid in. Usually follows Ist-Versteuerung.',
  },
]

//: Stand-ins. The real lists arrive with the region packs (SCOPE §16), which is
//: also why these two are free strings in the API and not `Literal`s — pinning
//: today's options into the schema would make a region pack a migration.
const PLACEHOLDER_CHARTS = [
  { value: '', label: 'Not chosen yet' },
  { value: 'at_eakr', label: 'Austria — Einheitskontenrahmen (placeholder)' },
  { value: 'at_skr03', label: 'Austria — SKR03-style (placeholder)' },
]

const PLACEHOLDER_EXPORTS = [
  { value: '', label: 'Not chosen yet' },
  { value: 'bmd_csv', label: 'BMD NTCS CSV (placeholder)' },
  { value: 'rzl_csv', label: 'RZL CSV (placeholder)' },
  { value: 'datev_csv', label: 'DATEV CSV (placeholder)' },
]

const ADVISOR_HINT = 'Decide this with your tax advisor.'

export default function AccountingPage() {
  const { can, loaded } = usePermissions()
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)

  const [company, setCompany] = React.useState<Company | null>(null)
  const [saving, setSaving] = React.useState(false)
  const [saved, setSaved] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const allowed = loaded && can('company.settings.edit')

  React.useEffect(() => {
    if (!allowed) return
    let cancelled = false
    void (async () => {
      try {
        const loadedCompany = await api.get<Company>('/company')
        if (!cancelled) setCompany(loadedCompany)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.detail : 'Could not load the company')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [allowed, activeCompanyId])

  function set<K extends keyof Company>(key: K, value: Company[K]) {
    setCompany((prev) => (prev ? { ...prev, [key]: value } : prev))
    setSaved(false)
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!company) return
    setSaving(true)
    setSaved(false)
    setError(null)
    try {
      const updated = await api.patch<Company>('/company', {
        bookkeeping_mode: company.bookkeeping_mode,
        vat_timing: company.vat_timing,
        kleinunternehmer: company.kleinunternehmer,
        // Empty select means "not chosen", which is NULL — distinct from
        // "chose the first option", because the real option lists do not
        // exist yet and a stand-in must not look like a decision.
        chart_of_accounts_template: company.chart_of_accounts_template || null,
        export_format: company.export_format || null,
        archive_date_basis: company.archive_date_basis,
        month_approval_enabled: company.month_approval_enabled,
      })
      setCompany(updated)
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <CompanySection
      title="Accounting"
      description="How this company's books are kept —"
    >
      <p className="mb-6 rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary">
        These answers are <strong>recorded, not applied</strong>. Nothing
        calculates, exports or files differently because of them yet — the
        bookkeeping that reads them arrives in a later phase. They are asked
        now because they are settled once, with your tax advisor, and a company
        that has already answered them does not have to be migrated later.
      </p>

      {!company ? (
        <p className="text-sm text-text-tertiary">
          {error ?? 'Loading…'}
        </p>
      ) : (
        <form onSubmit={save} className="space-y-5">
          <Field label="Bookkeeping" hint={ADVISOR_HINT}>
            <select
              value={company.bookkeeping_mode}
              onChange={(e) => set('bookkeeping_mode', e.target.value as BookkeepingMode)}
              className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              {BOOKKEEPING.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-text-tertiary">
              {BOOKKEEPING.find((o) => o.value === company.bookkeeping_mode)?.help}
            </span>
          </Field>

          <Field label="VAT timing" hint={ADVISOR_HINT}>
            <select
              value={company.vat_timing}
              onChange={(e) => set('vat_timing', e.target.value as VatTiming)}
              className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              {VAT.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-text-tertiary">
              {VAT.find((o) => o.value === company.vat_timing)?.help}
            </span>
          </Field>

          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={company.kleinunternehmer}
              onChange={(e) => set('kleinunternehmer', e.target.checked)}
              className="mt-0.5"
            />
            <span className="flex flex-col">
              <span className="text-xs font-medium text-text-secondary">
                Kleinunternehmer (§6 UStG 1994)
              </span>
              <span className="text-[11px] text-text-tertiary">
                No VAT is charged and no input tax is deducted. {ADVISOR_HINT}
              </span>
            </span>
          </label>

          <Field label="Chart of accounts" hint={ADVISOR_HINT}>
            <select
              value={company.chart_of_accounts_template ?? ''}
              onChange={(e) => set('chart_of_accounts_template', e.target.value || null)}
              className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              {PLACEHOLDER_CHARTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-text-tertiary">
              Placeholder list. The real charts arrive with the region packs.
            </span>
          </Field>

          <Field label="Bookkeeping export" hint={ADVISOR_HINT}>
            <select
              value={company.export_format ?? ''}
              onChange={(e) => set('export_format', e.target.value || null)}
              className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              {PLACEHOLDER_EXPORTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-text-tertiary">
              Placeholder list — ask your advisor which format they read.
            </span>
          </Field>

          <Field label="Archive date basis" hint={ADVISOR_HINT}>
            <select
              value={company.archive_date_basis}
              onChange={(e) => set('archive_date_basis', e.target.value as ArchiveDateBasis)}
              className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              {ARCHIVE_BASIS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-text-tertiary">
              {ARCHIVE_BASIS.find((o) => o.value === company.archive_date_basis)?.help}
            </span>
          </Field>

          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={company.month_approval_enabled}
              onChange={(e) => set('month_approval_enabled', e.target.checked)}
              className="mt-0.5"
            />
            <span className="flex flex-col">
              <span className="text-xs font-medium text-text-secondary">
                A month has to be approved before it counts as closed
              </span>
              <span className="text-[11px] text-text-tertiary">
                An extra review step before a period is handed over. {ADVISOR_HINT}
              </span>
            </span>
          </label>

          <SaveRow saving={saving} saved={saved} error={error} />
        </form>
      )}
    </CompanySection>
  )
}
