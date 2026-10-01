'use client'

import * as React from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  User,
  Bell,
  Shield,
  Palette,
  Brush,
  LayoutTemplate,
  Users,
  Building2,
  Landmark,
  Calculator,
  ShieldCheck,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { usePermissions } from '@/hooks/use-permissions'

interface SettingsNavItem {
  href: string
  label: string
  icon: React.ElementType
  /** Gated on the INSTALLATION role (User.role === superadmin): branding,
   *  design, user administration. Nothing to do with companies. */
  adminOnly?: boolean
  /** Highlight only on an exact path match.
   *
   *  For a parent path whose children are siblings in the same list:
   *  /settings/company is the General screen AND the prefix of every other
   *  company entry, so without this it stays highlighted on all of them. */
  exact?: boolean
  /** Gated on a COMPANY permission in the company currently active.
   *
   *  The two are separate on purpose and must stay so — an installation
   *  administrator is not automatically anything inside a company, and a
   *  company owner is not automatically anything on the installation. See
   *  apps/api/services/permissions.py. */
  permission?: string
}

/**
 * Grouped rather than flat, so a divider belongs to a group instead of
 * sitting at a fixed index. Branding/Design/Admin are all admin-gated — an
 * index-counted separator would leave a stray line whenever the item beside
 * it is hidden. A group renders no divider when it has no visible items.
 *
 * This order is final from day one, Design included, even though the Design
 * page is a placeholder until P4 (SCOPE §7). A navigation that reshuffles
 * once the real page lands teaches people the wrong muscle memory and
 * invalidates every screenshot and instruction written before it.
 */
const settingsNavGroups: SettingsNavItem[][] = [
  [
    { href: '/settings/profile', label: 'Profile', icon: User },
    { href: '/settings/appearance', label: 'Appearance', icon: Palette },
    { href: '/settings/notifications', label: 'Notifications', icon: Bell },
  ],
  [
    { href: '/settings/branding', label: 'Branding', icon: Brush, adminOnly: true },
    { href: '/settings/design', label: 'Design', icon: LayoutTemplate, adminOnly: true },
  ],
  // ── The active company ────────────────────────────────────────────────
  //
  // One group, in the order someone actually fills it in: who we are, where
  // we are paid, how we are booked, who must have 2FA, who is in. Each entry
  // is gated on the permission its own endpoints enforce, so a member who
  // cannot edit settings sees only the entries they can use rather than a
  // list of 404s.
  //
  // `exact` on General, because every other entry in this group is a path
  // UNDER /settings/company — without it, General would highlight on all of
  // them.
  [
    {
      href: '/settings/company',
      label: 'Company',
      icon: Building2,
      permission: 'company.settings.edit',
      exact: true,
    },
    {
      href: '/settings/company/bank-accounts',
      label: 'Bank accounts',
      icon: Landmark,
      permission: 'company.settings.edit',
    },
    {
      href: '/settings/company/accounting',
      label: 'Accounting',
      icon: Calculator,
      permission: 'company.settings.edit',
    },
    {
      href: '/settings/company/security',
      label: 'Security',
      icon: ShieldCheck,
      permission: 'company.settings.edit',
    },
    {
      href: '/settings/company/members',
      label: 'Members',
      icon: Users,
      permission: 'company.members.manage',
    },
  ],
  [
    { href: '/settings/admin', label: 'Admin', icon: Shield, adminOnly: true },
  ],
]

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()
  const { user, isSuperAdmin } = useAuthStore()
  const { can, loaded: companiesLoaded } = usePermissions()

  // Filter first, then drop empty groups, so the dividers below can be a
  // simple "every group after the first" rule.
  const visibleGroups = settingsNavGroups
    .map((group) =>
      group.filter((item) => {
        if (item.adminOnly && !isSuperAdmin) return false
        // Hidden until the company list has answered rather than shown and
        // then withdrawn — same reasoning as the main sidebar's Archive
        // entry.
        if (item.permission && !(companiesLoaded && can(item.permission))) {
          return false
        }
        return true
      }),
    )
    .filter((group) => group.length > 0)

  return (
    <div className="flex h-full">
      {/* Settings Sidebar */}
      <aside className="w-56 border-r border-border bg-bg-secondary shrink-0">
        <div className="p-4 border-b border-border">
          <h2 className="text-sm font-semibold text-text-primary">Settings</h2>
          <p className="text-xs text-text-tertiary mt-0.5">
            {user?.name ?? 'User'}
          </p>
        </div>

        <nav className="p-2">
          {visibleGroups.map((group, index) => (
            <div
              key={group[0].href}
              data-testid="settings-nav-group"
              className={cn(
                'space-y-0.5',
                // The divider is the previous group's bottom border, so it
                // only ever exists between two groups that are both showing.
                index > 0 && 'mt-2 border-t border-border pt-2',
              )}
            >
              {group.map((item) => {
                const isActive = item.exact
                  ? pathname === item.href
                  : pathname === item.href ||
                    pathname?.startsWith(item.href + '/')
                const Icon = item.icon

                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
                      isActive
                        ? 'bg-bg-hover text-text-primary font-medium'
                        : 'text-text-secondary hover:bg-bg-hover/70 hover:text-text-primary',
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span>{item.label}</span>
                  </Link>
                )
              })}
            </div>
          ))}
        </nav>
      </aside>

      {/* Settings Content */}
      <main className="flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  )
}
