'use client'

import * as React from 'react'
import { Loader2, Plus, Star, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { CompanySection, Field } from '@/components/settings/company-section'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type { CompanyBankAccount } from '@/types'

/**
 * Settings → Company → Bank accounts.
 *
 * **The IBAN is not validated here.** It is checked server-side
 * (`apps/api/core/iban.py`: structure, country length, ISO 7064 MOD-97-10) and
 * this screen shows what comes back. A second copy of mod-97 in TypeScript
 * would be a second implementation to keep in step, and the one that matters
 * is the one the database is behind — a browser check that disagreed would
 * either block a valid account or promise a save the API then refuses.
 *
 * What the browser does do is `inputMode="text"` and no auto-capitalisation
 * fighting, so pasting an IBAN off a bank statement works.
 *
 * **Exactly one default, and it is the server's job.** The client cannot
 * demote-then-promote atomically, so "make default" is one request and the
 * previous default is demoted inside it.
 */

export default function BankAccountsPage() {
  const { can, loaded } = usePermissions()
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)

  const [accounts, setAccounts] = React.useState<CompanyBankAccount[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  const [label, setLabel] = React.useState('')
  const [iban, setIban] = React.useState('')
  const [bic, setBic] = React.useState('')
  const [bankName, setBankName] = React.useState('')
  const [adding, setAdding] = React.useState(false)
  const [addError, setAddError] = React.useState<string | null>(null)

  const allowed = loaded && can('company.settings.edit')

  const refresh = React.useCallback(async () => {
    setLoading(true)
    try {
      setAccounts(await api.get<CompanyBankAccount[]>('/company/bank-accounts'))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load bank accounts')
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    if (!allowed) {
      setLoading(false)
      return
    }
    void refresh()
    // Keyed on the active company: switching must not leave another company's
    // account numbers on screen.
  }, [allowed, activeCompanyId, refresh])

  async function add(event: React.FormEvent) {
    event.preventDefault()
    setAdding(true)
    setAddError(null)
    try {
      await api.post<CompanyBankAccount>('/company/bank-accounts', {
        label: label.trim() || null,
        iban: iban.trim(),
        bic: bic.trim() || null,
        bank_name: bankName.trim() || null,
      })
      setLabel('')
      setIban('')
      setBic('')
      setBankName('')
      await refresh()
    } catch (err) {
      // The server's own sentence, verbatim — "The check digits do not match.
      // One character is probably wrong — compare it against your bank
      // statement." Rewriting it here would be a second copy of the reason.
      setAddError(err instanceof ApiError ? err.detail : 'Could not add that account')
    } finally {
      setAdding(false)
    }
  }

  async function makeDefault(account: CompanyBankAccount) {
    setError(null)
    try {
      await api.patch(`/company/bank-accounts/${account.id}`, { is_default: true })
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not change the default')
    }
  }

  async function remove(account: CompanyBankAccount) {
    setError(null)
    try {
      await api.delete(`/company/bank-accounts/${account.id}`)
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not remove that account')
    }
  }

  return (
    <CompanySection
      title="Bank accounts"
      description="Where this company is paid —"
    >
      <form
        onSubmit={add}
        className="space-y-3 rounded-lg border border-border bg-bg-secondary p-4"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="IBAN" hint="Spaces are fine — paste it as printed.">
            <Input
              required
              value={iban}
              onChange={(e) => setIban(e.target.value)}
              placeholder="AT61 1904 3002 3457 3201"
              autoCapitalize="characters"
              spellCheck={false}
            />
          </Field>
          <Field label="Label" hint="Optional. “Production”, “Payroll” …">
            <Input value={label} onChange={(e) => setLabel(e.target.value)} />
          </Field>
          <Field label="BIC" hint="Optional — a SEPA transfer does not need it.">
            <Input
              value={bic}
              onChange={(e) => setBic(e.target.value)}
              placeholder="GIBAATWWXXX"
              spellCheck={false}
            />
          </Field>
          <Field label="Bank" hint="Optional.">
            <Input value={bankName} onChange={(e) => setBankName(e.target.value)} />
          </Field>
        </div>

        <div className="flex items-center gap-3">
          {/* Disabled only while a request is in flight, and it says so.
              Never disabled on "the IBAN looks wrong" — the server decides
              that, and a dead button with no reason is the state rule 17c
              forbids. */}
          <button
            type="submit"
            disabled={adding}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-accent px-4 text-sm font-medium text-accent-foreground transition-all hover:bg-accent-hover disabled:opacity-50"
          >
            {adding ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            {adding ? 'Checking…' : 'Add account'}
          </button>
          {addError && (
            <span className="text-xs text-status-error" role="alert">
              {addError}
            </span>
          )}
        </div>
      </form>

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {error}
        </p>
      )}

      <div className="mt-6 overflow-hidden rounded-lg border border-border">
        {loading ? (
          <div className="flex items-center gap-2 p-4 text-sm text-text-tertiary">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading…
          </div>
        ) : accounts.length === 0 ? (
          <p className="p-4 text-sm text-text-tertiary">
            No bank account yet. Documents will have no account number on them
            until there is one.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-bg-secondary text-left text-xs text-text-tertiary">
              <tr>
                <th className="px-3 py-2 font-medium">Account</th>
                <th className="px-3 py-2 font-medium">Bank</th>
                <th className="px-3 py-2 font-medium">Default</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.id} className="border-t border-border">
                  <td className="px-3 py-2">
                    <div className="font-mono text-text-primary">
                      {account.iban_formatted}
                    </div>
                    {account.label && (
                      <div className="text-xs text-text-tertiary">
                        {account.label}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {account.bank_name ?? '—'}
                    {account.bic && (
                      <div className="text-xs text-text-tertiary">{account.bic}</div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {account.is_default ? (
                      <span className="inline-flex items-center gap-1 text-xs text-text-primary">
                        <Star className="h-3.5 w-3.5 fill-current" />
                        Default
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => makeDefault(account)}
                        className="text-xs text-text-tertiary underline-offset-2 hover:text-text-primary hover:underline"
                      >
                        Make default
                      </button>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => remove(account)}
                      title="Remove this account"
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-tertiary transition-colors hover:bg-bg-hover hover:text-status-error"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </CompanySection>
  )
}
