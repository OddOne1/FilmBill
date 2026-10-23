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
import type { Company, CompanyUpdate, CountryChangePlan } from '@/types'

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
  const [country, setCountry] = React.useState<string | null>(null)
  const [changingCountry, setChangingCountry] = React.useState(false)

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
        setCountry(company.address_country ?? null)
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

          {/* The country is NOT an ordinary text box, because it decides
              which tax rates this company has. It used to be one, and that
              was the bug: typing over it changed the country and installed
              nothing, so a company that corrected DE to AT kept German rates
              and every invoice carried the wrong one. It now goes through a
              dialog that shows exactly what will happen first. */}
          <Field
            label="Country"
            hint="Decides which tax rates this company uses, so it is changed through a dialog that shows what will happen."
          >
            <div className="flex items-center gap-2">
              <Input
                aria-label="Country"
                value={country ?? ''}
                placeholder="Not set"
                readOnly
                disabled
              />
              <button
                type="button"
                data-testid="company-change-country"
                onClick={() => setChangingCountry(true)}
                className="shrink-0 rounded-md border border-border px-2.5 py-1.5 text-sm text-text-primary hover:bg-bg-hover"
              >
                {country ? 'Change…' : 'Set…'}
              </button>
            </div>
          </Field>

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

      {changingCountry && (
        <ChangeCountryDialog
          current={country}
          onClose={() => setChangingCountry(false)}
          onChanged={async (next) => {
            setCountry(next)
            setChangingCountry(false)
            await reload(activeCompanyId)
          }}
        />
      )}
    </CompanySection>
  )
}

/**
 * Changing the country, with the plan in front of you first.
 *
 * **Nothing here decides anything.** The plan, its counts, its warnings and
 * every sentence shown come from `POST /company/tax/country-change/preview`;
 * Confirm sends the same country and mode to the real endpoint, which
 * recomputes the plan server-side and applies that. A dialog that described
 * the change in its own words would be a second implementation of the
 * reconciliation, and the one on screen would be the one nobody tested
 * against real rows.
 *
 * The preview is re-fetched when the mode changes, because `country_only`
 * and `apply_pack` are genuinely different plans and showing one while the
 * other radio is selected is the worst thing this dialog could do.
 */
function ChangeCountryDialog({
  current,
  onClose,
  onChanged,
}: {
  current: string | null
  onClose: () => void
  onChanged: (country: string) => Promise<void>
}) {
  const [next, setNext] = React.useState('')
  const [mode, setMode] = React.useState<'apply_pack' | 'country_only'>(
    'apply_pack',
  )
  const [plan, setPlan] = React.useState<CountryChangePlan | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  const ready = next.trim().length === 2

  // Debounced, and keyed on the mode as well as the country.
  React.useEffect(() => {
    if (!ready) {
      setPlan(null)
      return
    }
    const handle = setTimeout(async () => {
      try {
        setPlan(
          await api.post<CountryChangePlan>(
            '/company/tax/country-change/preview',
            { country: next.trim().toUpperCase(), mode },
          ),
        )
        setError(null)
      } catch (err) {
        setPlan(null)
        setError(
          err instanceof ApiError
            ? err.detail
            : 'Could not work out what this would change',
        )
      }
    }, 250)
    return () => clearTimeout(handle)
  }, [next, mode, ready])

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      const applied = await api.post<CountryChangePlan>(
        '/company/tax/country-change',
        { country: next.trim().toUpperCase(), mode },
      )
      await onChanged(applied.to_country)
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : 'Could not change the country',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-label="Change country"
      data-testid="change-country-dialog"
      className="mt-6 rounded-md border border-accent/40 bg-bg-secondary p-4"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-primary">
          {current ? `Change country from ${current}` : 'Set the country'}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
        >
          Cancel
        </button>
      </div>

      <div className="mt-4 space-y-4">
        <Field label="New country" hint="Two-letter ISO code, e.g. DE.">
          <Input
            aria-label="New country"
            value={next}
            maxLength={2}
            onChange={(event) => setNext(event.target.value.toUpperCase())}
          />
        </Field>

        <fieldset className="space-y-2">
          <legend className="text-xs font-medium text-text-secondary">
            What to do with the tax configuration
          </legend>
          {(
            [
              [
                'apply_pack',
                'Install the new country’s rates (recommended)',
                'Adds what is missing, moves the standard and reduced rates to the new country’s codes, and switches off the old country’s unused ones. Nothing is deleted and nothing you edited or created is touched.',
              ],
              [
                'country_only',
                'Change the country only',
                'For a company that has already set its rates by hand. The tax configuration is left exactly as it is, which may still be the previous country’s.',
              ],
            ] as ['apply_pack' | 'country_only', string, string][]
          ).map(([value, label, help]) => (
            <label key={value} className="flex items-start gap-2 text-sm text-text-secondary">
              <input
                type="radio"
                name="country-change-mode"
                value={value}
                checked={mode === value}
                onChange={() => setMode(value)}
                className="mt-1"
              />
              <span>
                {label}
                <span className="block text-xs text-text-tertiary">{help}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {error && (
          <p
            role="alert"
            data-testid="change-country-error"
            className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
          >
            {error}
          </p>
        )}

        {plan && (
          <div
            data-testid="change-country-plan"
            className="rounded-md border border-border bg-bg-primary px-3 py-2"
          >
            <p className="text-xs font-medium uppercase tracking-wide text-text-tertiary">
              What will happen
            </p>
            <ul className="mt-1 space-y-1 text-sm text-text-secondary">
              {plan.sentences.map((sentence, index) => (
                <li key={index}>{sentence}</li>
              ))}
            </ul>
            {plan.warnings.length > 0 && (
              <ul className="mt-2 space-y-1">
                {plan.warnings.map((warning, index) => (
                  <li
                    key={index}
                    data-testid="change-country-warning"
                    className="rounded-md border border-status-warning/40 bg-status-warning/10 px-2 py-1 text-xs text-status-warning"
                  >
                    {warning}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <p className="text-xs text-text-tertiary">
          Documents you have already finalised are unaffected: each one keeps
          a copy of the tax code and rate it was issued with.
        </p>

        <button
          type="button"
          data-testid="change-country-confirm"
          onClick={() => void confirm()}
          disabled={busy || !ready || plan === null}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
        >
          {busy
            ? 'Changing…'
            : plan === null
              ? 'Enter a country'
              : `Confirm — change to ${next.trim().toUpperCase()}`}
        </button>
      </div>
    </div>
  )
}
