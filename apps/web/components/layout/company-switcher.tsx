'use client'

import * as React from 'react'
import { Building2, Check, ChevronDown, Plus } from 'lucide-react'
import Link from 'next/link'
import { cn } from '@/lib/utils'
import { useCompanyStore } from '@/stores/company-store'
import { useAuthStore } from '@/stores/auth-store'

/**
 * Which company the app is acting in, in the top bar.
 *
 * **Hidden for a user with one company and no power to make another** (P0b-1
 * §2). Most installations have exactly one company, and a control with one
 * option only ever costs a click and a moment of "wait, is there another
 * one?".
 *
 * An installation administrator is the exception, added in P0b-2: "New
 * company…" lives in this menu, so for a superadmin the control appears even
 * with one company — otherwise the only way to create the second one is a URL
 * you have to know, which is what sent P0b-1's own acceptance through Swagger.
 * A superadmin with one company sees a menu with that company and the create
 * entry; everyone else with one company still sees nothing at all.
 *
 * It also renders nothing while the list is still loading, rather than a
 * skeleton: the header is 44px of chrome and a placeholder that appears and
 * vanishes is more noticeable than the thing arriving.
 */
export function CompanySwitcher() {
  const companies = useCompanyStore((state) => state.companies)
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const select = useCompanyStore((state) => state.select)
  const loadedCompanies = useCompanyStore((state) => state.loaded)
  const isSuperAdmin = useAuthStore((state) => state.isSuperAdmin)
  const [open, setOpen] = React.useState(false)
  const containerRef = React.useRef<HTMLDivElement>(null)

  // Close on an outside click or Escape. Both, because a dropdown that only
  // closes on one of them is a dropdown that gets stuck open for whoever
  // reaches for the other.
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

  const active = companies.find((company) => company.id === activeCompanyId) ?? null

  // A superadmin always gets the menu once the list has answered, because the
  // create entry lives in it — including when the list came back EMPTY, which
  // is the state a fresh install is in and the one case where the create entry
  // is the only thing anybody needs.
  const canCreate = isSuperAdmin
  if (!loadedCompanies) return null
  if (companies.length <= 1 && !canCreate) return null

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        data-testid="company-switcher"
        className="flex items-center gap-1.5 rounded-md border border-nav-border bg-nav-text/5 px-2.5 py-1 text-xs text-nav-text/80 hover:border-nav-text/50 hover:text-nav-text transition-colors max-w-[220px]"
      >
        <Building2 className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">
          {active ? active.display_name : 'No company'}
        </span>
        <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute right-0 z-40 mt-1 min-w-[240px] overflow-hidden rounded-md border border-border bg-bg-secondary py-1 shadow-lg"
        >
          {companies.map((company) => {
            const isActive = company.id === active?.id
            return (
              <button
                key={company.id}
                type="button"
                role="option"
                aria-selected={isActive}
                onClick={() => {
                  select(company.id)
                  setOpen(false)
                }}
                className={cn(
                  'flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors',
                  isActive
                    ? 'bg-bg-hover text-text-primary'
                    : 'text-text-secondary hover:bg-bg-hover/70 hover:text-text-primary',
                )}
              >
                <Check
                  className={cn('h-3.5 w-3.5 shrink-0', !isActive && 'opacity-0')}
                />
                <span className="flex flex-col overflow-hidden">
                  <span className="truncate">{company.display_name}</span>
                  {/* The role, because a person who is an owner in one
                      company and a tax advisor in another needs to know
                      which hat they are about to put on. */}
                  <span className="truncate text-[10px] text-text-tertiary">
                    {company.role.replace('_', ' ')}
                  </span>
                </span>
              </button>
            )
          })}

          {canCreate && (
            <>
              {companies.length > 0 && (
                <div className="my-1 border-t border-border" />
              )}
              <Link
                href="/settings/company/new"
                onClick={() => setOpen(false)}
                data-testid="company-switcher-new"
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] text-text-secondary transition-colors hover:bg-bg-hover/70 hover:text-text-primary"
              >
                <Plus className="h-3.5 w-3.5 shrink-0" />
                New company…
              </Link>
            </>
          )}
        </div>
      )}
    </div>
  )
}
