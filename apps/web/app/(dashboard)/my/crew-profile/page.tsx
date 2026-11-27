'use client'

import * as React from 'react'
import Link from 'next/link'
import * as Switch from '@radix-ui/react-switch'
import { Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { usePermissions } from '@/hooks/use-permissions'
import type { MyCrewProfile, MyCrewSwitchResult } from '@/types'

/**
 * R2. My crew profile, from the user menu (not a sidebar item).
 *
 * "I also work as crew", and the roles I can do (the catalog's labor items).
 * Roles I set are self-reported until an admin confirms them. There is no
 * rate anywhere on this page: day rates are set by an admin, on the contact.
 * Everything here is about the caller's OWN login — the API takes no person
 * from the page.
 */
export default function MyCrewProfilePage() {
  const { loaded, active } = usePermissions()
  const [profile, setProfile] = React.useState<MyCrewProfile | null>(null)
  const [closed, setClosed] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [picked, setPicked] = React.useState<Set<string>>(new Set())

  const take = (next: MyCrewProfile) => {
    setProfile(next)
    setPicked(new Set((next.roles ?? []).map((role) => role.item_id)))
  }

  const load = React.useCallback(async () => {
    try {
      take(await api.get<MyCrewProfile>('/me/crew'))
      setClosed(false)
      setError(null)
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setClosed(true)
      else setError(err instanceof ApiError ? err.detail : 'Your crew profile could not be loaded. Try again.')
    }
  }, [])

  React.useEffect(() => {
    if (!loaded) return
    void load()
  }, [loaded, active?.id, load])

  const toggle = async (on: boolean) => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await api.put<MyCrewSwitchResult>('/me/crew', { is_crew: on })
      take(result.profile)
      if (!on) {
        const count = (result.future_bookings ?? []).length
        setNotice(
          count === 0
            ? 'You no longer work as crew. Nothing was deleted.'
            : `You no longer work as crew. Your ${count === 1 ? 'future booking stays' : `${count} future bookings stay`} as ${count === 1 ? 'it is' : 'they are'}; nothing was deleted.`,
        )
      }
    } catch (err) {
      // The needs-link case answers 409 with the sentence to show.
      setError(err instanceof ApiError ? err.detail : 'That could not be changed. Try again.')
      await load()
    } finally {
      setBusy(false)
    }
  }

  const saveRoles = async () => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      take(await api.put<MyCrewProfile>('/me/crew/roles', { item_ids: Array.from(picked) }))
      setNotice('Your roles were saved. An admin confirms new ones.')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Your roles could not be saved. Try again.')
    } finally {
      setBusy(false)
    }
  }

  if (!loaded || (!profile && !closed && !error)) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-text-tertiary">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading…
      </div>
    )
  }

  if (closed) {
    return (
      <div className="p-8">
        <h1 className="text-lg font-semibold text-text-primary">My crew profile</h1>
        <p data-testid="crew-profile-closed" className="mt-2 text-sm text-text-secondary">
          Your access to {active?.display_name ?? 'your company'} does not include a crew profile.
        </p>
      </div>
    )
  }

  const held = new Map((profile?.roles ?? []).map((role) => [role.item_id, role]))

  return (
    <div className="max-w-2xl p-8">
      <h1 className="text-lg font-semibold text-text-primary">My crew profile</h1>
      <p className="mt-1 text-sm text-text-secondary">
        Whether you can be booked as crew in {active?.display_name ?? 'your company'}, and in which roles.
      </p>

      {profile && (
        <div className="mt-6 flex items-center gap-3">
          <Switch.Root
            id="works-as-crew"
            data-testid="works-as-crew-switch"
            checked={profile.is_crew}
            disabled={busy}
            onCheckedChange={(on) => void toggle(on)}
            className="relative h-5 w-9 rounded-full bg-bg-tertiary data-[state=checked]:bg-accent"
          >
            <Switch.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white transition-transform data-[state=checked]:translate-x-[18px]" />
          </Switch.Root>
          <label htmlFor="works-as-crew" className="text-sm text-text-primary">
            I also work as crew
          </label>
          {busy && <span className="text-xs text-text-tertiary">Saving…</span>}
          {profile.contact_name && (
            <span className="text-xs text-text-tertiary">Your contact: {profile.contact_name}</span>
          )}
        </div>
      )}

      {profile?.message && (
        <p data-testid="needs-link-message" className="mt-4 rounded-md border border-border bg-bg-secondary px-3 py-2 text-sm text-text-secondary">
          {profile.message}
        </p>
      )}
      {error && error !== profile?.message && (
        <p role="alert" className="mt-4 rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error">
          {error}
        </p>
      )}
      {notice && (
        <p data-testid="crew-profile-notice" className="mt-4 text-sm text-text-secondary">
          {notice}
        </p>
      )}

      {profile?.is_crew ? (
        <section className="mt-6">
          <h2 className="text-sm font-medium text-text-primary">My roles</h2>
          <p className="mt-1 text-xs text-text-tertiary">An admin confirms these. Day rates are set by an admin.</p>
          {(profile.labor_items ?? []).length === 0 ? (
            <p className="mt-2 text-sm text-text-secondary">
              Your company has no roles in its catalog yet. Ask an admin to add them.
            </p>
          ) : (
            <ul data-testid="my-role-picker" className="mt-2 space-y-1">
              {(profile.labor_items ?? []).map((item) => {
                const role = held.get(item.item_id)
                return (
                  <li key={item.item_id} className="flex items-center gap-2 text-sm text-text-primary">
                    <input
                      id={`role-${item.item_id}`}
                      type="checkbox"
                      checked={picked.has(item.item_id)}
                      onChange={(event) => {
                        const next = new Set(picked)
                        if (event.target.checked) next.add(item.item_id)
                        else next.delete(item.item_id)
                        setPicked(next)
                      }}
                    />
                    <label htmlFor={`role-${item.item_id}`}>{item.name}</label>
                    {role && (
                      <span data-testid={`role-state-${item.item_id}`} className="rounded bg-bg-tertiary px-1.5 py-0.5 text-2xs text-text-secondary">
                        {role.confirmed ? 'confirmed' : 'self-reported'}
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          <Button type="button" className="mt-3" onClick={() => void saveRoles()} disabled={busy}>
            {busy ? 'Saving…' : 'Save roles'}
          </Button>
          <p className="mt-6 text-sm">
            <Link href="/my/schedule" className="text-accent hover:underline">
              My schedule
            </Link>
          </p>
        </section>
      ) : (
        !profile?.message && (
          <p className="mt-6 text-sm text-text-secondary">
            Switch on “I also work as crew” to choose your roles.
          </p>
        )
      )}
    </div>
  )
}
