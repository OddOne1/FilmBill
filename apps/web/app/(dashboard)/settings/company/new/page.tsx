'use client'

import * as React from 'react'
import { useRouter } from 'next/navigation'
import { Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { Field } from '@/components/settings/company-section'
import { useAuthStore } from '@/stores/auth-store'
import { useCompanyStore } from '@/stores/company-store'
import type { Company } from '@/types'

/**
 * Create a company — the screen P0b-1 was missing.
 *
 * P0b-1 shipped `POST /companies` with nothing that calls it, so its own
 * acceptance walkthrough had to go through Swagger. That was the prompt
 * contradicting itself (it forbade starting the settings UI while also asking
 * the tester to create a company), and this closes it.
 *
 * **It does not use `CompanySection`.** That frame checks a company permission
 * in the ACTIVE company, and this page is reachable when there is no active
 * company at all — which is exactly the state a fresh install with an empty
 * switcher is in. The gate here is the installation role instead, which is
 * what the endpoint enforces.
 *
 * Only the four fields a company cannot sensibly be created without. The rest
 * of General is a PATCH away and asking for twenty boxes before anything
 * exists is how a create form becomes a thing people avoid.
 */

export default function NewCompanyPage() {
  const router = useRouter()
  const { isSuperAdmin, isLoading } = useAuthStore()
  const reload = useCompanyStore((state) => state.load)
  const select = useCompanyStore((state) => state.select)

  const [legalName, setLegalName] = React.useState('')
  const [country, setCountry] = React.useState('AT')
  const [currency, setCurrency] = React.useState('EUR')
  const [language, setLanguage] = React.useState('de')
  const [creating, setCreating] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-text-tertiary">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading…
      </div>
    )
  }

  if (!isSuperAdmin) {
    // Said plainly. The API answers 403 here rather than 404 for the same
    // reason: there is no company to be coy about, and the refusal is about
    // the caller's own installation role, which /auth/me already told them.
    return (
      <div className="p-8">
        <h1 className="text-lg font-semibold text-text-primary">New company</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Only an installation administrator can create a company. Ask whoever
          runs this FilmBill to create it and add you to it.
        </p>
      </div>
    )
  }

  async function create(event: React.FormEvent) {
    event.preventDefault()
    setCreating(true)
    setError(null)
    try {
      const company = await api.post<Company>('/companies', {
        legal_name: legalName.trim(),
        address_country: country.trim().toUpperCase() || null,
        default_currency: currency.trim().toUpperCase() || 'EUR',
        default_language: language.trim() || 'de',
      })
      // Reload FIRST, then select: `select` refuses an id that is not in the
      // list (it must — pointing the header at a company the server would 404
      // makes the whole app that 404), so selecting before the reload would
      // silently do nothing and leave the person in the previous company.
      await reload(company.id)
      select(company.id)
      router.push('/settings/company')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not create the company')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="max-w-xl p-8">
      <h1 className="text-lg font-semibold text-text-primary">New company</h1>
      <p className="mt-1 text-sm text-text-secondary">
        You will be its owner. Everything else can be filled in afterwards under
        Settings → Company.
      </p>

      <form onSubmit={create} className="mt-6 space-y-4">
        <Field
          label="Legal name"
          hint="As it appears on the register. This goes on the invoices."
        >
          <Input
            required
            autoFocus
            value={legalName}
            onChange={(e) => setLegalName(e.target.value)}
            placeholder="YON Studio OG"
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Country" hint="ISO code">
            <Input
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              maxLength={2}
              spellCheck={false}
            />
          </Field>
          <Field label="Currency" hint="ISO code">
            <Input
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
              maxLength={3}
              spellCheck={false}
            />
          </Field>
          <Field label="Language" hint="e.g. de">
            <Input
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              maxLength={5}
              spellCheck={false}
            />
          </Field>
        </div>

        <div className="flex items-center gap-3">
          {/* Disabled only while the request is in flight, and it says so. */}
          <button
            type="submit"
            disabled={creating}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-accent px-4 text-sm font-medium text-accent-foreground transition-all hover:bg-accent-hover disabled:opacity-50"
          >
            {creating && <Loader2 className="h-4 w-4 animate-spin" />}
            {creating ? 'Creating…' : 'Create company'}
          </button>
          {error && (
            <span className="text-xs text-status-error" role="alert">
              {error}
            </span>
          )}
        </div>
      </form>
    </div>
  )
}
