'use client'

import * as React from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { usePermissions } from '@/hooks/use-permissions'

/**
 * The furniture every catalog screen shares: the sub-navigation, the
 * read-only notice, and the no-access page.
 *
 * One component rather than six copies, because of rule 17c. The rule is
 * "a disabled control must always say why", and the shape it keeps taking
 * is a screen where somebody without `catalog.edit` sees buttons that do
 * nothing and no explanation. Six screens is six chances to forget the
 * sentence; here there is one, and `CatalogReadOnlyNotice` is either
 * rendered or the person can edit.
 */

const TABS: { href: string; label: string }[] = [
  { href: '/catalog', label: 'Items' },
  { href: '/catalog/categories', label: 'Categories' },
  { href: '/catalog/units', label: 'Units' },
  { href: '/catalog/rental-factors', label: 'Rental factors' },
  { href: '/catalog/import', label: 'Import CSV' },
  { href: '/catalog/calculator', label: 'Try the engine' },
]

export function CatalogTabs() {
  const pathname = usePathname()
  return (
    <nav
      aria-label="Catalog"
      className="mt-5 flex flex-wrap gap-1 border-b border-border"
    >
      {TABS.map((tab) => {
        // `/catalog` is active only for itself and for an item's own page;
        // the sub-screens are their own tabs. A `startsWith` here would
        // light up Items on every screen.
        const active =
          tab.href === '/catalog'
            ? pathname === '/catalog' || /^\/catalog\/[0-9a-f-]{8,}/.test(pathname)
            : pathname.startsWith(tab.href)
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? 'page' : undefined}
            className={
              active
                ? 'border-b-2 border-accent px-3 py-2 text-sm font-medium text-text-primary'
                : 'border-b-2 border-transparent px-3 py-2 text-sm text-text-secondary hover:text-text-primary'
            }
          >
            {tab.label}
          </Link>
        )
      })}
    </nav>
  )
}

/** Why the buttons are not there. Rendered whenever `catalog.edit` is
 *  absent — never a dead button, never a silent screen (17c). */
export function CatalogReadOnlyNotice() {
  return (
    <p
      data-testid="catalog-read-only"
      className="mt-4 rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary"
    >
      You can read the price list but not change it — adding or editing an
      item, a category, a unit or a factor table needs the catalog edit
      permission. An owner or administrator can grant it under Settings →
      Company → Members.
    </p>
  )
}

/** The whole screen, when the person cannot even read the catalog.
 *
 *  A sentence rather than a redirect: a redirect makes a permission problem
 *  look like a broken link, and the one role this happens to — the tax
 *  advisor — is an outside party who should be told plainly. */
export function CatalogNoAccess({ title = 'Catalog' }: { title?: string }) {
  return (
    <div className="p-8">
      <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
      <p className="mt-2 text-sm text-text-secondary">
        You do not have access to this company&rsquo;s price list. An owner or
        administrator can change that under Settings → Company → Members.
      </p>
    </div>
  )
}

/** `loaded && !can('catalog.view')` in one place, so no screen gets the
 *  condition subtly wrong and renders the refusal while permissions are
 *  still loading. */
export function useCatalogAccess() {
  const { can, loaded } = usePermissions()
  return {
    loaded,
    canView: can('catalog.view'),
    canEdit: can('catalog.edit'),
    refused: loaded && !can('catalog.view'),
  }
}
