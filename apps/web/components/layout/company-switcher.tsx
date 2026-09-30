'use client'

import * as React from 'react'
import { Building2, Check, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCompanyStore } from '@/stores/company-store'

/**
 * Which company the app is acting in, in the top bar.
 *
 * Renders NOTHING when the user is in exactly one company (§2). Most
 * installations have one, and a control with one option is a control that
 * only ever costs a click and a moment of "wait, is there another one?".
 *
 * It also renders nothing while the list is still loading, rather than a
 * skeleton: the header is 44px of chrome and a placeholder that appears and
 * vanishes is more noticeable than the thing arriving.
 */
export function CompanySwitcher() {
  const companies = useCompanyStore((state) => state.companies)
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const select = useCompanyStore((state) => state.select)
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

  if (companies.length <= 1 || !active) return null

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
        <span className="truncate">{active.display_name}</span>
        <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute right-0 z-40 mt-1 min-w-[240px] overflow-hidden rounded-md border border-border bg-bg-secondary py-1 shadow-lg"
        >
          {companies.map((company) => {
            const isActive = company.id === active.id
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
        </div>
      )}
    </div>
  )
}
