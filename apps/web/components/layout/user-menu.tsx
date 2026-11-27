'use client'

import * as React from 'react'
import Link from 'next/link'
import { CalendarDays, HardHat, UserRound } from 'lucide-react'
import { api } from '@/lib/api'
import { Avatar } from '@/components/shared/avatar'
import { useAuthStore } from '@/stores/auth-store'
import { useCompanyStore } from '@/stores/company-store'
import type { MyCrewProfile } from '@/types'

/**
 * R2. The user menu, top right: Profile, My crew profile, and My schedule —
 * the last only for somebody who is linked to a contact and works as crew.
 *
 * The crew state is asked for when the menu OPENS, for the active company:
 * nothing is fetched on every page load, and switching company cannot leave
 * the previous company's answer in the menu. A failed or refused answer
 * (a tax advisor has no crew profile) leaves Profile alone in the menu.
 */
export function UserMenu() {
  const user = useAuthStore((state) => state.user)
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const [open, setOpen] = React.useState(false)
  const [crew, setCrew] = React.useState<{ company: string; profile: MyCrewProfile | null } | null>(null)
  const containerRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (!open) return
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  React.useEffect(() => {
    if (!open || !activeCompanyId) return
    let cancelled = false
    api
      .get<MyCrewProfile>('/me/crew')
      .then((profile) => !cancelled && setCrew({ company: activeCompanyId, profile }))
      .catch(() => !cancelled && setCrew({ company: activeCompanyId, profile: null }))
    return () => {
      cancelled = true
    }
  }, [open, activeCompanyId])

  const profile = crew && crew.company === activeCompanyId ? crew.profile : null
  const item =
    'flex items-center gap-2 rounded px-2 py-1.5 text-sm text-text-primary hover:bg-bg-hover'

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        data-testid="user-menu"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Your account"
        onClick={() => setOpen((value) => !value)}
        className="flex items-center rounded-full ring-offset-1 hover:ring-2 hover:ring-nav-text/30"
      >
        <Avatar src={user?.avatar_url} name={user?.name} size="sm" />
      </button>
      {open && (
        <div
          role="menu"
          data-testid="user-menu-items"
          className="absolute right-0 top-9 z-30 w-56 rounded-md border border-border bg-bg-primary p-1 shadow-lg"
        >
          <div className="px-2 py-1.5">
            <div className="truncate text-sm font-medium text-text-primary">{user?.name ?? 'Account'}</div>
            <div className="truncate text-xs text-text-tertiary">{user?.email ?? ''}</div>
          </div>
          <Link role="menuitem" href="/settings/profile" className={item} onClick={() => setOpen(false)}>
            <UserRound className="h-4 w-4" /> Profile
          </Link>
          {profile && (
            <Link role="menuitem" href="/my/crew-profile" className={item} onClick={() => setOpen(false)}>
              <HardHat className="h-4 w-4" /> My crew profile
            </Link>
          )}
          {profile?.is_crew && (
            <Link role="menuitem" href="/my/schedule" className={item} onClick={() => setOpen(false)}>
              <CalendarDays className="h-4 w-4" /> My schedule
            </Link>
          )}
        </div>
      )}
    </div>
  )
}
