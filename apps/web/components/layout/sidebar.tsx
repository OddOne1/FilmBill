'use client'

import * as React from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  Archive,
  ChevronsLeft,
  Clapperboard,
  LayoutDashboard,
  MapPin,
  Package,
  Tags,
  Users,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { useSiteSettings } from '@/hooks/use-site-settings'
import { usePermissions } from '@/hooks/use-permissions'
import { useThemeStore } from '@/stores/theme-store'
import { Avatar } from '@/components/shared/avatar'
import { ARCHIVE_NAV_ENABLED } from '@/lib/nav-flags'

interface NavItem {
  href: string
  label: string
  icon: React.ElementType
  /** The company permission this entry needs, if any.
   *
   *  Undefined means "everyone in the company sees it" — not "no check".
   *  The distinction matters because every gated entry leads to endpoints
   *  that enforce the same key server-side; this only decides what to draw,
   *  so a wrong answer here shows a menu item that 404s rather than opening
   *  anything. */
  permission?: string
  /** A switch an entry is drawn behind, read on every render. */
  shown?: () => boolean
}

// Parties, Catalog, Projects and Documents land with the phases that build
// them (SCOPE §13); each one is a line here.
//
// Archive was present from P0b-1 even though P5 fills it in, because
// `archive.view` is a tax advisor's ONLY permission. UI-1 hides it until P5
// gives it content (ARCHIVE_NAV_ENABLED, lib/nav-flags.ts): an entry that
// opens an empty page was worse than none (Mathias, 2026-11-26).
const navItems: NavItem[] = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  // P1b. Customers, suppliers, partners and the people in them. A top-level
  // entry rather than a company setting, because a customer list is not a
  // setting — it is one of the two things the application is about.
  { href: '/contacts', label: 'Contacts', icon: Users, permission: 'parties.view' },
  // No Crew entry (UI-1): crew are people, and they live in Contacts → the
  // Crew tab. `/crew` redirects there (next.config.js).
  // A1. The price list, and the calculator that runs on the same engine
  // every quote and invoice will. `catalog.view` is held by everybody except
  // the tax advisor — staff included, because looking up what a truck costs
  // per day is reading a price list, not reading the customer base.
  { href: '/catalog', label: 'Catalog', icon: Tags, permission: 'catalog.view' },
  // A2. Projects, their production days and their budgets. `projects.view`
  // includes staff — a crew member may see which days are shoot days — but
  // the budgets inside are closed to them (`budgets.view`, D77).
  { href: '/projects', label: 'Projects', icon: Clapperboard, permission: 'projects.view' },
  // B2b. The devices the company owns. `devices.view` includes staff — the
  // crew needs to know what exists — but a purchase price is shown only with
  // `budgets.view`.
  { href: '/gear', label: 'Gear', icon: Package, permission: 'devices.view' },
  // L1. Studios, hotels and hospitals as records. `locations.view` includes
  // staff — the crew has to know where the shoot is — but hotels inside it
  // are only listed for roles that see crew (D111).
  { href: '/locations', label: 'Locations', icon: MapPin, permission: 'locations.view' },
  // Hidden until P5, the bookkeeping document archive; see lib/nav-flags.ts.
  {
    href: '/archive',
    label: 'Archive',
    icon: Archive,
    permission: 'archive.view',
    shown: () => ARCHIVE_NAV_ENABLED,
  },
]

interface SidebarProps {
  collapsed: boolean
  onToggle: () => void
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname()
  const { user } = useAuthStore()
  const { orgName, logoDarkUrl, logoLightUrl } = useSiteSettings()
  const { resolvedTheme } = useThemeStore()
  const { can, loaded: companiesLoaded } = usePermissions()
    // Pick logo based on resolved theme; fall back to the other if only one is set.
    // Uses resolvedTheme (not theme) because theme can be 'system', which never
    // strictly equals 'light' — that bug pinned the logo to the dark variant
    // whenever the user had 'system' selected instead of an explicit theme.
  const customLogo = resolvedTheme === 'light'
    ? (logoLightUrl ?? logoDarkUrl)
    : (logoDarkUrl ?? logoLightUrl)
  return (
    <>
    <aside
      className={cn(
        'fixed left-0 top-0 z-30 flex h-screen flex-col border-r border-nav-border',
        // bg-nav-bg defaults to the same token as the rest of the theme (see
        // globals.css) but can be overridden per theme in Branding settings ->
        // Theme colors.
        'bg-nav-bg transition-[width] duration-200 overflow-hidden',
        collapsed ? 'w-[52px]' : 'w-[192px]',
      )}
    >
      {/* Logo — click to go home. h-11 (44px) to match the global Header
          bar's height (also h-11) so the border-b line here lines up with
          the Header's border-b across the whole app. */}
      <Link
        href="/"
        className={cn(
          'flex h-11 items-center shrink-0 border-b border-nav-border hover:bg-nav-text/10 transition-colors',
          collapsed ? 'justify-center px-0' : 'px-4 gap-2.5',
        )}
        title="Go to dashboard"
      >
        {/* Logo: theme-aware custom logo, or the default FilmBill icons */}
        {customLogo ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={customLogo}
            alt={orgName}
            className="h-7 w-7 shrink-0 object-contain rounded"
          />
        ) : (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/logo-icon.png"
              alt={orgName}
              className="h-7 w-7 shrink-0 object-contain logo-dark"
            />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/logo-icon-dark.png"
              alt={orgName}
              className="h-7 w-7 shrink-0 object-contain logo-light"
            />
          </>
        )}
        {!collapsed && (
          <span className="text-sm font-semibold text-nav-text tracking-tight">
            {orgName}
          </span>
        )}
      </Link>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto overflow-x-hidden py-2 px-2 space-y-0.5">
        {navItems
          // Hidden until the company list has answered, rather than shown
          // and then removed: an entry that appears for a moment and
          // vanishes reads as a bug, and for a tax advisor the flicker would
          // be the whole sidebar.
          .filter((item) => !item.shown || item.shown())
          .filter((item) => !item.permission || (companiesLoaded && can(item.permission)))
          .map((item) => {
          const isActive =
            item.href === '/' ? pathname === '/' : pathname.startsWith(item.href)
          const Icon = item.icon
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'group relative flex items-center rounded-md transition-colors duration-100',
                collapsed ? 'justify-center h-9 w-9 mx-auto' : 'gap-2.5 px-2.5 h-9',
                isActive
                  ? 'bg-nav-text/15 text-nav-text'
                  : 'text-nav-text/60 hover:bg-nav-text/10 hover:text-nav-text',
              )}
              title={collapsed ? item.label : undefined}
            >
              <Icon className="h-[18px] w-[18px] shrink-0" strokeWidth={isActive ? 2 : 1.5} />
              {!collapsed && (
                <span className={cn('text-[13px]', isActive && 'font-medium')}>
                  {item.label}
                </span>
              )}
            </Link>
          )
        })}

        {/* No Notifications entry: the bell lives in the top bar beside
            Search and opens a small popup there (UI-1). */}
      </nav>

      {/* Bottom section */}
      <div className="border-t border-nav-border p-2 space-y-1 shrink-0">
        {/* A plain link, not a menu. Settings gates what each user sees
            once they arrive, so routing by role here would be picking a
            destination the destination already handles. Log out lives on
            the Profile page, which is where this lands. */}
        <Link
          href="/settings/profile"
          title={collapsed ? (user?.name ?? 'Account') : undefined}
          className={cn(
            'flex w-full items-center rounded-md text-nav-text/60 hover:bg-nav-text/10 hover:text-nav-text transition-colors',
            collapsed ? 'justify-center h-9 w-9 mx-auto' : 'gap-2.5 px-2 py-1.5',
          )}
        >
          <Avatar src={user?.avatar_url} name={user?.name} size="sm" />
          {!collapsed && (
            <div className="flex flex-col items-start overflow-hidden min-w-0">
              <span className="truncate text-[13px] font-medium text-nav-text leading-tight w-full text-left">
                {user?.name ?? 'User'}
              </span>
              <span className="truncate text-[10px] text-nav-text/50 leading-tight w-full text-left">
                {user?.email ?? ''}
              </span>
            </div>
          )}
        </Link>

        {/* Collapse toggle */}
        <button
          onClick={onToggle}
          className={cn(
            'flex w-full items-center rounded-md text-nav-text/50 hover:bg-nav-text/10 hover:text-nav-text transition-colors',
            collapsed ? 'justify-center h-8 w-8 mx-auto' : 'gap-2 px-2.5 h-8',
          )}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <ChevronsLeft className={cn('h-4 w-4 transition-transform', collapsed && 'rotate-180')} />
          {!collapsed && <span className="text-xs">Collapse</span>}
        </button>
      </div>
    </aside>
  </>
  )
}
