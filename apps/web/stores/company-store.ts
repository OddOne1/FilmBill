import { create } from 'zustand'
import { api, setActiveCompanyId } from '@/lib/api'
import type { CompanySummary } from '@/types'

/**
 * Which company this browser is acting in, and what the user may do in it.
 *
 * The single writer of `lib/api`'s company header. Nothing else calls
 * `setActiveCompanyId`, so "which company did that request go to" has exactly
 * one answer and one place to look.
 *
 * The choice is persisted to the user's own `preferences` on the server, not
 * to localStorage. Two reasons, and the second is the real one:
 *
 *  - it follows the person to their other machine, which is what someone who
 *    works in one of five companies expects;
 *  - localStorage is per-origin and shared by every account that signs in on
 *    this browser, so a shared machine would hand the next person the
 *    previous person's company id. The server would refuse it with a 404 —
 *    it is their preference, not their authority — but the app would open on
 *    an error for no reason.
 *
 * A remembered id that is no longer valid (access revoked, company archived)
 * simply is not in `companies`, and `select` falls back to the first one.
 */

//: Where the choice lives inside `user.preferences`.
const PREFERENCE_KEY = 'active_company_id'

interface CompanyState {
  companies: CompanySummary[]
  activeCompanyId: string | null
  /** False until `load` has answered once. The shell holds the app back on
   *  it, so that navigation is never rendered from an empty permission list
   *  and then re-rendered a moment later with the real one. */
  loaded: boolean
  error: string | null

  load: (rememberedId?: string | null) => Promise<void>
  select: (companyId: string) => void
  reset: () => void

  /** The active company's entry, or null while nothing is selected. */
  active: () => CompanySummary | null
  /** Whether the active company grants `permission`.
   *
   *  Presentation only. Every endpoint enforces its own check server-side, so
   *  this decides what to RENDER, never what is allowed — a client that got
   *  this wrong would show a menu entry that 404s, not open a door. */
  can: (permission: string) => boolean
}

function apply(id: string | null): string | null {
  setActiveCompanyId(id)
  return id
}

export const useCompanyStore = create<CompanyState>()((set, get) => ({
  companies: [],
  activeCompanyId: null,
  loaded: false,
  error: null,

  load: async (rememberedId) => {
    try {
      const companies = await api.get<CompanySummary[]>('/companies')
      const remembered = companies.find((c) => c.id === rememberedId)
      const chosen = remembered ?? companies[0] ?? null
      set({
        companies,
        activeCompanyId: apply(chosen ? chosen.id : null),
        loaded: true,
        error: null,
      })
    } catch (err) {
      // `loaded` is still set to true. A failed load must not leave the app
      // spinning forever — the shell shows "no company" and offers the only
      // action that helps, which is the same screen an install with genuinely
      // no companies gets (rule 17c: never dead and silent).
      set({
        companies: [],
        activeCompanyId: apply(null),
        loaded: true,
        error: err instanceof Error ? err.message : 'Could not load companies',
      })
    }
  },

  select: (companyId) => {
    if (!get().companies.some((c) => c.id === companyId)) return
    set({ activeCompanyId: apply(companyId) })
    // Fire and forget: the switch has already taken effect locally, and a
    // failure to remember it is not worth blocking the person over. They
    // land in the same company they chose; only the memory of it is lost.
    void api
      .patch('/auth/me/preferences', { [PREFERENCE_KEY]: companyId })
      .catch(() => {})
  },

  reset: () => {
    set({ companies: [], activeCompanyId: apply(null), loaded: false, error: null })
  },

  active: () => {
    const { companies, activeCompanyId } = get()
    return companies.find((c) => c.id === activeCompanyId) ?? null
  },

  can: (permission) => {
    const company = get().active()
    return company ? company.permissions.includes(permission) : false
  },
}))

export { PREFERENCE_KEY as ACTIVE_COMPANY_PREFERENCE_KEY }
