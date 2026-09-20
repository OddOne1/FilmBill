'use client'

import * as React from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { User, Bell, Shield, Palette, Brush, LayoutTemplate } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'

interface SettingsNavItem {
  href: string
  label: string
  icon: React.ElementType
  adminOnly?: boolean
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

  // Filter first, then drop empty groups, so the dividers below can be a
  // simple "every group after the first" rule.
  const visibleGroups = settingsNavGroups
    .map((group) =>
      group.filter((item) => !(item.adminOnly && !isSuperAdmin)),
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
                const isActive =
                  pathname === item.href || pathname?.startsWith(item.href + '/')
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
