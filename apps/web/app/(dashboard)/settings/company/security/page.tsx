'use client'

import * as React from 'react'
import { AlertTriangle } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import {
  CompanySection,
  SaveRow,
} from '@/components/settings/company-section'
import { usePermissions } from '@/hooks/use-permissions'
import { useCompanyStore } from '@/stores/company-store'
import type { Company, CompanyMember, CompanyRole } from '@/types'

/**
 * Settings → Company → Security.
 *
 * `require_2fa_roles` and `tax_advisor_reports` have been columns since P0b-1
 * and `PATCH /company` has always written them; what was missing was the
 * screen.
 *
 * **The consequence is stated before the save, not after.** Adding a role here
 * forces every member holding it into two-factor enrolment at their next
 * login, and `two_factor_required_for` then refuses to let them turn it off
 * again. For someone whose mail is broken and who has no authenticator, that
 * is a lockout — recoverable only by a superadmin through Settings → Admin.
 *
 * So the warning is not a toast afterwards and not help text beside the field.
 * It appears the moment a role is ticked, it NAMES the people it would affect
 * and says which of them have 2FA already, and the save button says what it is
 * about to do. The person making the change is the only one who can know
 * whether those people can receive mail.
 *
 * `tax_advisor` is shown as permanently on and cannot be unticked: that role
 * is required to have 2FA on every install, because it is the one role
 * routinely held by someone outside the company.
 */

const ROLES: { value: CompanyRole; label: string }[] = [
  { value: 'owner', label: 'Owner' },
  { value: 'admin', label: 'Administrator' },
  { value: 'accountant', label: 'Accountant' },
  { value: 'producer', label: 'Producer' },
  { value: 'staff', label: 'Staff' },
]

export default function CompanySecurityPage() {
  const { can, loaded } = usePermissions()
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)

  const [company, setCompany] = React.useState<Company | null>(null)
  const [roles, setRoles] = React.useState<CompanyRole[]>([])
  const [members, setMembers] = React.useState<CompanyMember[]>([])
  const [saving, setSaving] = React.useState(false)
  const [saved, setSaved] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const allowed = loaded && can('company.settings.edit')
  // Members are only readable with `company.members.manage`. Owners and admins
  // hold both keys, so in practice this is always available on this screen —
  // but asked for rather than assumed, because the warning degrades to a
  // general sentence rather than 404-ing the page if it ever is not.
  const canSeeMembers = loaded && can('company.members.manage')

  React.useEffect(() => {
    if (!allowed) return
    let cancelled = false
    void (async () => {
      try {
        const loadedCompany = await api.get<Company>('/company')
        if (cancelled) return
        setCompany(loadedCompany)
        setRoles(loadedCompany.require_2fa_roles)
        if (canSeeMembers) {
          setMembers(await api.get<CompanyMember[]>('/company/members'))
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.detail : 'Could not load the company')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [allowed, canSeeMembers, activeCompanyId])

  const stored = company?.require_2fa_roles ?? []
  const added = roles.filter((role) => !stored.includes(role))

  /** Members who hold a role being ADDED and have no second factor yet.
   *
   *  The whole point of the warning: these are the accounts that will be
   *  forced into enrolment, and the ones among them who cannot receive mail
   *  are the ones who will be locked out. Named rather than counted, because
   *  the person saving has to recognise them to judge it. */
  const affected = members.filter(
    (member) =>
      member.is_active &&
      added.includes(member.role) &&
      !member.two_factor_enabled,
  )

  function toggle(role: CompanyRole, on: boolean) {
    setRoles((prev) => (on ? [...prev, role] : prev.filter((r) => r !== role)))
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
        require_2fa_roles: roles,
        tax_advisor_reports: company.tax_advisor_reports,
      })
      setCompany(updated)
      setRoles(updated.require_2fa_roles)
      setSaved(true)
      if (canSeeMembers) {
        setMembers(await api.get<CompanyMember[]>('/company/members'))
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <CompanySection
      title="Security"
      description="Who must have two-factor authentication in —"
    >
      {!company ? (
        <p className="text-sm text-text-tertiary">{error ?? 'Loading…'}</p>
      ) : (
        <form onSubmit={save} className="space-y-6">
          <fieldset className="space-y-2">
            <legend className="text-xs font-medium text-text-secondary">
              Roles that must use two-factor authentication
            </legend>

            {ROLES.map((role) => (
              <label key={role.value} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={roles.includes(role.value)}
                  onChange={(e) => toggle(role.value, e.target.checked)}
                />
                <span className="text-sm text-text-primary">{role.label}</span>
              </label>
            ))}

            <label
              className="flex items-center gap-2 opacity-70"
              title="A tax advisor always needs two-factor authentication — this cannot be turned off."
            >
              <input type="checkbox" checked disabled readOnly />
              <span className="text-sm text-text-primary">Tax advisor</span>
              <span className="text-[11px] text-text-tertiary">
                always required — an external role
              </span>
            </label>
          </fieldset>

          {/* The consequence, BEFORE the save, and only when there is one. */}
          {added.length > 0 && (
            <div
              role="status"
              data-testid="require-2fa-consequence"
              className="flex gap-2.5 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2.5"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-status-warning" />
              <div className="space-y-1.5 text-xs text-text-secondary">
                <p className="font-medium text-text-primary">
                  Saving this will force two-factor setup on the next login.
                </p>
                <p>
                  Anyone holding{' '}
                  {added
                    .map((role) => ROLES.find((r) => r.value === role)?.label ?? role)
                    .join(', ')}{' '}
                  will have to enrol before they can use FilmBill again, and
                  will not be able to turn it off afterwards.
                </p>
                {canSeeMembers ? (
                  affected.length > 0 ? (
                    <p>
                      That affects{' '}
                      <strong className="text-text-primary">
                        {affected.map((member) => member.name).join(', ')}
                      </strong>
                      . Make sure they can receive email at their sign-in
                      address, or have an authenticator app — otherwise they
                      will be locked out and a FilmBill administrator will have
                      to reset them.
                    </p>
                  ) : (
                    <p>
                      Everyone currently holding{' '}
                      {added.length === 1 ? 'that role' : 'those roles'} already
                      has two-factor authentication, so nobody is locked out
                      today. It will still apply to anyone added later.
                    </p>
                  )
                ) : (
                  <p>
                    Check who holds{' '}
                    {added.length === 1 ? 'that role' : 'those roles'} before
                    saving — anyone who cannot receive mail and has no
                    authenticator will be locked out.
                  </p>
                )}
              </div>
            </div>
          )}

          <label className="flex items-start gap-2 border-t border-border pt-5">
            <input
              type="checkbox"
              checked={company.tax_advisor_reports}
              onChange={(e) => {
                setCompany({ ...company, tax_advisor_reports: e.target.checked })
                setSaved(false)
              }}
              className="mt-0.5"
            />
            <span className="flex flex-col">
              <span className="text-xs font-medium text-text-secondary">
                A tax advisor may also see reports and download exports
              </span>
              <span className="text-[11px] text-text-tertiary">
                Off by default. The archive is what an advisor needs to do the
                books; reports are this company&apos;s own analysis of itself,
                which is a separate decision.
              </span>
            </span>
          </label>

          <SaveRow
            saving={saving}
            saved={saved}
            error={error}
            label={added.length > 0 ? 'Save and require two-factor' : 'Save changes'}
          />
        </form>
      )}
    </CompanySection>
  )
}
