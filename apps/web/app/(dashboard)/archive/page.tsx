'use client'

import { Archive } from 'lucide-react'
import { usePermissions } from '@/hooks/use-permissions'

/**
 * A placeholder, on purpose, and it earns its place before P5 builds it.
 *
 * `archive.view` is the tax advisor's ONLY permission (SCOPE §9.3b), so
 * without a page behind it their whole account is a dashboard with an empty
 * sidebar and no way to tell a correctly-restricted account from a broken
 * one. That distinction is exactly what P0b-1's browser acceptance has to
 * establish, and it cannot be established against nothing.
 *
 * The page is rendered from the permission the SERVER reported (via
 * /companies), not from a route guard. When P5 fills it in, the endpoints
 * behind it enforce `require("archive.view")` on their own and this stays
 * presentation — as it is today.
 */
export default function ArchivePage() {
  const { can, loaded, active } = usePermissions()

  if (!loaded) return null

  if (!can('archive.view')) {
    // Said plainly rather than shown as an empty page or a redirect. A
    // redirect would make a permission problem look like a broken link, and
    // a blank screen is the dead-and-silent state rule 17c exists to forbid.
    return (
      <div className="p-8">
        <h1 className="text-lg font-semibold text-text-primary">Archive</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role in {active?.display_name ?? 'this company'} does not
          include the archive. Ask an owner or an administrator if you need
          access to it.
        </p>
      </div>
    )
  }

  return (
    <div className="p-8">
      <h1 className="text-lg font-semibold text-text-primary">Archive</h1>
      <div className="mt-6 flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-16 text-center">
        <Archive className="h-8 w-8 text-text-tertiary" strokeWidth={1.5} />
        <p className="mt-3 text-sm font-medium text-text-primary">
          Archive — coming in P5
        </p>
        <p className="mt-1 max-w-sm text-xs text-text-tertiary">
          Finalized documents for {active?.display_name ?? 'this company'} will
          be listed and downloadable here.
        </p>
      </div>
    </div>
  )
}
