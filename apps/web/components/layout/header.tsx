'use client'

import * as React from 'react'
import { usePathname } from 'next/navigation'
import { Search, ChevronRight } from 'lucide-react'
import Link from 'next/link'
import { useBreadcrumbStore } from '@/stores/breadcrumb-store'

interface HeaderProps {
  onSearchOpen: () => void
}

const LABEL_MAP: Record<string, string> = {
  notifications: 'Notifications',
  settings: 'Settings',
  new: 'New',
}

/** Looks like a UUID (8-4-4-4-12 hex) */
function isUuid(s: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)
}

/**
 * Route path segments that are structural only and should not appear in the
 * breadcrumb — a route prefix that carries no meaning for the reader.
 */
const SKIP_SEGMENTS = new Set<string>([])

function buildBreadcrumbs(pathname: string, dynamicLabels: Record<string, string>): { label: string; href: string }[] {
  const segments = pathname.split('/').filter(Boolean)
  const crumbs: { label: string; href: string }[] = []

  let path = ''
  for (const segment of segments) {
    path += `/${segment}`
    // Skip structural route segments
    if (SKIP_SEGMENTS.has(segment)) continue
    // Skip UUID segments that don't have a label registered
    if (isUuid(segment) && !dynamicLabels[segment]) continue
    const label =
      dynamicLabels[segment] ??
      LABEL_MAP[segment] ??
      segment.charAt(0).toUpperCase() + segment.slice(1).replace(/-/g, ' ')
    crumbs.push({ label, href: path })
  }

  return crumbs
}

export function Header({ onSearchOpen }: HeaderProps) {
  const pathname = usePathname()
  const { labels, extraCrumbs } = useBreadcrumbStore()
  const urlCrumbs = buildBreadcrumbs(pathname, labels)
  const breadcrumbs = [...urlCrumbs, ...extraCrumbs.map((c) => ({ label: c.label, href: c.href ?? '' }))]

  return (
    // Chrome color (top bar). bg-nav-bg / border-nav-border / text-nav-text
    // default to the same tokens as the rest of the theme (see globals.css)
    // but can be overridden per theme in Branding settings -> Theme colors.
    // Hover/active tints use nav-text at low opacity rather than a hardcoded
    // white so they still read correctly if nav-bg ends up light-colored.
    <header className="sticky top-0 z-20 flex h-11 items-center justify-between border-b border-nav-border bg-nav-bg px-4">
      {/* Breadcrumbs */}
      <nav className="flex items-center gap-1 text-[13px]">
        {breadcrumbs.map((crumb, index) => {
          const isLast = index === breadcrumbs.length - 1
          return (
            <React.Fragment key={`${crumb.href}-${index}`}>
              {index > 0 && (
                <ChevronRight className="h-3 w-3 text-nav-text/40" />
              )}
              {isLast ? (
                <span className="font-medium text-nav-text">{crumb.label}</span>
              ) : crumb.href ? (
                <Link
                  href={crumb.href}
                  className="text-nav-text/70 hover:text-nav-text transition-colors"
                >
                  {crumb.label}
                </Link>
              ) : (
                <span className="text-nav-text/70">{crumb.label}</span>
              )}
            </React.Fragment>
          )
        })}
      </nav>

      {/* Right side actions */}
      <div className="flex items-center gap-1.5">
        {/* Search trigger */}
        <button
          onClick={onSearchOpen}
          className="flex items-center gap-1.5 rounded-md border border-nav-border bg-nav-text/5 px-2.5 py-1 text-xs text-nav-text/70 hover:border-nav-text/50 hover:text-nav-text transition-colors"
        >
          <Search className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Search</span>
          <kbd className="hidden sm:inline-flex items-center gap-0.5 rounded border border-nav-border bg-nav-text/10 px-1 py-0.5 font-mono text-[10px] text-nav-text/70">
            <span>⌘</span>K
          </kbd>
        </button>

      </div>
    </header>
  )
}
