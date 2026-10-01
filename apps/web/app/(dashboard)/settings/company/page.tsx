'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import {
  CompanySection,
  Field,
  SaveRow,
} from '@/components/settings/company-section'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type { Company, CompanyUpdate } from '@/types'

/**
 * Settings → Company → General.
 *
 * The fields that end up printed on an invoice. That is why they are columns
 * on `Company` rather than a settings blob — a finalized document snapshots
 * them (CLAUDE.md rule 4) — and why this screen is the one P0b-1's acceptance
 * was missing: it shipped `POST /companies` and `PATCH /company` with no way
 * to reach either, so its own walkthrough had to go through Swagger.
 *
 * Reloads the switcher after a save, because the legal or trading name is what
 * the switcher shows: without it the top bar keeps the old name until the next
 * full page load, which reads as the save not having worked.
 */

const FIELDS: { key: keyof CompanyUpdate; label: string; hint?: string }[] = [
  { key: 'legal_name', label: 'Legal name', hint: 'As it appears on the register. This goes on your invoices.' },
  { key: 'trading_name', label: 'Trading name', hint: 'Only if it differs from the legal name.' },
  { key: 'legal_form', label: 'Legal form', hint: 'OG, GmbH, e.U. …' },
  { key: 'register_number', label: 'Register number', hint: 'Firmenbuchnummer, e.g. FN 123456a.' },
  { key: 'register_court', label: 'Register court', hint: 'e.g. Handelsgericht Wien.' },
  { key: 'vat_id', label: 'VAT ID', hint: 'UID-Nummer, e.g. ATU12345678.' },
  { key: 'tax_number', label: 'Tax number', hint: 'Steuernummer.' },
  { key: 'address_street', label: 'Street' },
  { key: 'address_zip', label: 'Postcode' },
  { key: 'address_city', label: 'City' },
  { key: 'address_country', label: 'Country', hint: 'Two-letter ISO code, e.g. AT.' },
  { key: 'email', label: 'Email' },
  { key: 'phone', label: 'Phone' },
  { key: 'website', label: 'Website' },
  { key: 'default_currency', label: 'Default currency', hint: 'Three-letter ISO code, e.g. EUR.' },
  { key: 'default_language', label: 'Default language', hint: 'Two-letter code, e.g. de.' },
  { key: 'timezone', label: 'Timezone', hint: 'IANA name, e.g. Europe/Vienna.' },
]

//: The columns the API declares NOT NULL. Blanking one of these is a
//: refusal, not a null — see `save` below.
const NOT_NULL = new Set([
  'legal_name',
  'default_currency',
  'default_language',
  'timezone',
])

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export default function CompanyGeneralPage() {
  const { can, loaded } = usePermissions()
  const reload = useCompanyStore((state) => state.load)
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)

  const [values, setValues] = React.useState<Record<string, string>>({})
  const [fiscalMonth, setFiscalMonth] = React.useState(1)
  const [saving, setSaving] = React.useState(false)
  const [saved, setSaved] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const allowed = loaded && can('company.settings.edit')

  React.useEffect(() => {
    if (!allowed) return
    let cancelled = false
    void (async () => {
      try {
        const company = await api.get<Company>('/company')
        if (cancelled) return
        const next: Record<string, string> = {}
        for (const { key } of FIELDS) {
          const value = company[key as keyof Company]
          next[key as string] = typeof value === 'string' ? value : ''
        }
        setValues(next)
        setFiscalMonth(company.fiscal_year_start_month)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.detail : 'Could not load the company')
        }
      }
    })()
    return () => {
      cancelled = true
    }
    // Re-reads when the active company changes — otherwise switching company
    // would leave the previous company's VAT ID in the form, which is the one
    // mistake on this screen that nobody would notice until an invoice went
    // out.
  }, [allowed, activeCompanyId])

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setSaved(false)
    setError(null)
    try {
      // An empty box is sent as `null`, not `""`. The columns are nullable
      // and the two are not the same thing: a company with
      // `trading_name: ""` renders an empty line where a name should be,
      // while `null` means "there isn't one" and renders nothing.
      //
      // The four NOT NULL columns are omitted instead when blank, so a
      // cleared box is refused by the server rather than sent as a null the
      // database cannot store. The browser also marks `legal_name` required;
      // this is the half that holds when the browser is bypassed.
      const body: Record<string, unknown> = { fiscal_year_start_month: fiscalMonth }
      for (const { key } of FIELDS) {
        const name = key as string
        const raw = (values[name] ?? '').trim()
        if (raw !== '') {
          body[name] = raw
        } else if (!NOT_NULL.has(name)) {
          body[name] = null
        }
      }
      await api.patch<Company>('/company', body)
      setSaved(true)
      // The switcher shows the trading or legal name, so it has to be told.
      await reload(activeCompanyId)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <CompanySection
      title="Company"
      description="What goes on this company's invoices —"
    >
      <form onSubmit={save} className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2">
          {FIELDS.map(({ key, label, hint }) => (
            <Field key={key as string} label={label} hint={hint}>
              <Input
                value={values[key as string] ?? ''}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, [key as string]: e.target.value }))
                }
                required={key === 'legal_name'}
              />
            </Field>
          ))}

          <Field
            label="Fiscal year starts"
            hint="January in Austria. April in the UK — hence a setting."
          >
            <select
              value={fiscalMonth}
              onChange={(e) => setFiscalMonth(Number(e.target.value))}
              className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              {MONTHS.map((month, index) => (
                <option key={month} value={index + 1}>
                  {month}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {/* The logo belongs here and is not here yet. Said out loud rather
            than left as a gap somebody has to notice: per-company branding is
            P4's, with the document layout work it exists for, and SCOPE keeps
            it there deliberately. */}
        <p className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-tertiary">
          The company logo and document colours arrive with the layout work in
          P4. Until then documents use the instance branding from Settings →
          Branding.
        </p>

        <SaveRow saving={saving} saved={saved} error={error} />
      </form>
    </CompanySection>
  )
}
