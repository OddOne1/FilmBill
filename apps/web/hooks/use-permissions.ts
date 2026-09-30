'use client'

import { useCompanyStore } from '@/stores/company-store'

/**
 * What the signed-in user may do in the company they are currently in.
 *
 * A hook rather than reading the store directly at each call site, so that
 * every gated piece of UI subscribes to the same slice and re-renders
 * together when the company switches. A component that read `companies` and
 * derived its own answer would keep showing the previous company's menu until
 * something else happened to re-render it.
 *
 * Presentation only, always. The server runs `require(...)` on every
 * endpoint; this decides what to DRAW.
 */
export function usePermissions() {
  const can = useCompanyStore((state) => state.can)
  const companies = useCompanyStore((state) => state.companies)
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId)
  const loaded = useCompanyStore((state) => state.loaded)

  const active = companies.find((company) => company.id === activeCompanyId) ?? null

  return {
    can,
    active,
    role: active?.role ?? null,
    loaded,
    /** True when the user is in more than one company — the only condition
     *  under which the switcher renders at all (§2). */
    hasMultiple: companies.length > 1,
  }
}
