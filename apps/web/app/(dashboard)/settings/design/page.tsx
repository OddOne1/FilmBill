'use client'

import * as React from 'react'
import { LayoutTemplate } from 'lucide-react'

/**
 * Placeholder for the layout designer (SCOPE §7), which ships in P4.
 *
 * It exists now so the settings navigation is final from day one — see the
 * comment on settingsNavGroups in ../layout.tsx. The page says plainly what
 * it is rather than pretending to be a feature: a disabled control or a fake
 * canvas would be worse than an honest empty state.
 */
export default function DesignPage() {
  return (
    <div className="p-6 max-w-3xl space-y-8">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-muted">
          <LayoutTemplate className="h-5 w-5 text-accent" />
        </div>
        <div className="flex-1">
          <h1 className="text-lg font-semibold text-text-primary">Design</h1>
          <p className="text-sm text-text-secondary">
            Document layouts for quotes, invoices and letters
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-bg-secondary p-6">
        <h2 className="text-sm font-medium text-text-primary">
          Document layout designer — coming in P4
        </h2>
        <p className="mt-2 text-sm text-text-secondary">
          This is where you will lay out what your documents look like: the grid,
          the address window, the items table and totals, the footer columns, and
          a separate layout per document type and language.
        </p>
        <p className="mt-2 text-sm text-text-secondary">
          Until then, documents use the built-in default layout. Colours, logo and
          fonts already come from{' '}
          <span className="text-text-primary font-medium">Branding</span> and will
          carry over unchanged.
        </p>
      </div>
    </div>
  )
}
