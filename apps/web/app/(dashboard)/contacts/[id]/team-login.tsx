'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import type { Party } from '@/types'

/**
 * R2. "Team · has a login: anna@…" under a person linked to a login. Read
 * only; an admin (company.members.manage) can Unlink — the contact stays as
 * it is, only the link goes.
 */
export function TeamLoginLine({
  party,
  canUnlink,
  onDone,
}: {
  party: Party
  canUnlink: boolean
  onDone: () => void | Promise<void>
}) {
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const unlink = async () => {
    if (!party.team_user_id) return
    setBusy(true)
    setError(null)
    try {
      await api.post(`/team/${party.team_user_id}/unlink`, {})
      await onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The link could not be removed. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div data-testid="team-login-line" className="mt-1 flex flex-wrap items-center gap-2 text-sm text-text-secondary">
      <span>
        Team · has a login: {party.team_login_email ?? 'a login of your company'}
      </span>
      {canUnlink && (
        <button
          type="button"
          data-testid="team-unlink"
          onClick={() => void unlink()}
          disabled={busy}
          title={busy ? 'Removing the link…' : 'The contact stays; only the link to the login is removed.'}
          className="rounded border border-border px-1.5 py-0.5 text-xs text-text-primary hover:bg-bg-hover"
        >
          {busy ? 'Unlinking…' : 'Unlink'}
        </button>
      )}
      {error && (
        <span role="alert" className="text-status-error">
          {error}
        </span>
      )}
    </div>
  )
}
