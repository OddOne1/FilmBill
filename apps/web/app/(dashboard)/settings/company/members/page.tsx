'use client'

import * as React from 'react'
import { Loader2, UserPlus, X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { usePermissions } from '@/hooks/use-permissions'
import type { CompanyMember, CompanyRole } from '@/types'

/**
 * Settings → Company → Members.
 *
 * Invite by email with a role and an optional expiry; change either; revoke.
 * Everything here talks to `/company/members`, which resolves the company
 * from the header `lib/api` attaches — there is no company id anywhere on
 * this page, which is what makes "the screen is showing the wrong company's
 * members" impossible rather than unlikely.
 *
 * The expiry field is the reason the whole screen exists in P0b-1 rather than
 * with the rest of company settings in P0b-2: a tax advisor is engaged for a
 * period (SCOPE §9.3b), and granting one without being able to say until when
 * is how a temporary access becomes a permanent one.
 */

const ROLES: { value: CompanyRole; label: string; hint: string }[] = [
  { value: 'owner', label: 'Owner', hint: 'Everything, including members and settings' },
  { value: 'admin', label: 'Administrator', hint: 'Members and company settings' },
  { value: 'accountant', label: 'Accountant', hint: 'Books, documents, archive, reports' },
  { value: 'producer', label: 'Producer', hint: 'Projects and documents' },
  { value: 'staff', label: 'Staff', hint: 'Day-to-day work, no financial settings' },
  { value: 'tax_advisor', label: 'Tax advisor', hint: 'The archive only — external access' },
]

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleDateString()
}

export default function CompanyMembersPage() {
  const { can, loaded, active } = usePermissions()
  const [members, setMembers] = React.useState<CompanyMember[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  const [email, setEmail] = React.useState('')
  const [name, setName] = React.useState('')
  const [role, setRole] = React.useState<CompanyRole>('staff')
  const [expiresAt, setExpiresAt] = React.useState('')
  const [inviting, setInviting] = React.useState(false)
  const [notice, setNotice] = React.useState<string | null>(null)

  const allowed = can('company.members.manage')

  const refresh = React.useCallback(async () => {
    setLoading(true)
    try {
      setMembers(await api.get<CompanyMember[]>('/company/members'))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load members')
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    if (!loaded || !allowed) {
      setLoading(false)
      return
    }
    void refresh()
    // Re-fetches when the active company changes, which is the whole point of
    // keying on it: switching company must not leave the previous company's
    // members on screen.
  }, [loaded, allowed, active?.id, refresh])

  async function invite(event: React.FormEvent) {
    event.preventDefault()
    setInviting(true)
    setNotice(null)
    setError(null)
    try {
      const created = await api.post<CompanyMember>('/company/members', {
        email: email.trim(),
        role,
        name: name.trim() || null,
        // A date input gives "2026-10-30" with no time. Sent as the end of
        // that day in UTC rather than its start, so "expires 30 October"
        // means the 30th is still a working day for them.
        expires_at: expiresAt ? new Date(`${expiresAt}T23:59:59Z`).toISOString() : null,
      })
      setNotice(
        created.status === 'pending_invite'
          ? `Invitation sent to ${created.email}.`
          : `${created.email} was added.`,
      )
      setEmail('')
      setName('')
      setExpiresAt('')
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not add that person')
    } finally {
      setInviting(false)
    }
  }

  async function changeRole(member: CompanyMember, next: CompanyRole) {
    setError(null)
    try {
      await api.patch(`/company/members/${member.id}`, { role: next })
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not change that role')
    }
  }

  async function revoke(member: CompanyMember) {
    setError(null)
    try {
      await api.delete(`/company/members/${member.id}`)
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not revoke that access')
    }
  }

  if (!loaded) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-text-tertiary">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading…
      </div>
    )
  }

  if (!allowed) {
    // Stated, not blank. Rule 17c: a screen that shows nothing and says
    // nothing is the state this codebase has agreed never to produce.
    return (
      <div className="p-8">
        <h1 className="text-lg font-semibold text-text-primary">Members</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role in {active?.display_name ?? 'this company'} does not
          include managing members.
        </p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-3xl">
      <h1 className="text-lg font-semibold text-text-primary">Members</h1>
      <p className="mt-1 text-sm text-text-secondary">
        Who may work in {active?.display_name ?? 'this company'}, and as what.
      </p>

      <form
        onSubmit={invite}
        className="mt-6 space-y-3 rounded-lg border border-border bg-bg-secondary p-4"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Email</span>
            <Input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="advisor@example.com"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">
              Name <span className="text-text-tertiary">(for the invitation)</span>
            </span>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Anna Berger"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">Role</span>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as CompanyRole)}
              className="h-9 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              {ROLES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-text-tertiary">
              {ROLES.find((option) => option.value === role)?.hint}
            </span>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text-secondary">
              Access ends <span className="text-text-tertiary">(optional)</span>
            </span>
            <Input
              type="date"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
            />
            <span className="text-[11px] text-text-tertiary">
              Leave empty for access that does not expire.
            </span>
          </label>
        </div>

        <div className="flex items-center gap-3">
          {/* Disabled only while the request is in flight, and it says so.
              A control that is dead because something is merely unknown is
              exactly what rule 17c forbids — the server enforces the rules
              regardless, so there is nothing to wait for. */}
          <Button type="submit" disabled={inviting}>
            {inviting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <UserPlus className="h-4 w-4" />
            )}
            {inviting ? 'Adding…' : 'Add member'}
          </Button>
          {notice && <span className="text-xs text-text-secondary">{notice}</span>}
        </div>
      </form>

      {error && (
        <p className="mt-4 rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error">
          {error}
        </p>
      )}

      <div className="mt-6 overflow-hidden rounded-lg border border-border">
        {loading ? (
          <div className="flex items-center gap-2 p-4 text-sm text-text-tertiary">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading members…
          </div>
        ) : members.length === 0 ? (
          <p className="p-4 text-sm text-text-tertiary">No members yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-bg-secondary text-left text-xs text-text-tertiary">
              <tr>
                <th className="px-3 py-2 font-medium">Person</th>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Access ends</th>
                <th className="px-3 py-2 font-medium">2FA</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.id} className="border-t border-border">
                  <td className="px-3 py-2">
                    <div className="text-text-primary">{member.name}</div>
                    <div className="text-xs text-text-tertiary">{member.email}</div>
                    {member.status === 'pending_invite' && (
                      <div className="text-[11px] text-text-tertiary">
                        Invitation not accepted yet
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <select
                      value={member.role}
                      onChange={(e) =>
                        changeRole(member, e.target.value as CompanyRole)
                      }
                      className="h-8 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
                    >
                      {ROLES.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {formatDate(member.expires_at)}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {member.two_factor_enabled ? 'On' : '—'}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => revoke(member)}
                      title="Revoke access"
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-tertiary hover:bg-bg-hover hover:text-status-error transition-colors"
                    >
                      <X className="h-3.5 w-3.5" />
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
