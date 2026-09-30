import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(() => Promise.resolve({})) },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))

import { api, setActiveCompanyId } from '@/lib/api'
import { useCompanyStore } from '../company-store'
import type { CompanySummary } from '@/types'

function company(id: string, overrides: Partial<CompanySummary> = {}): CompanySummary {
  return {
    id,
    legal_name: `Company ${id}`,
    trading_name: null,
    display_name: `Company ${id}`,
    default_currency: 'EUR',
    role: 'owner',
    permissions: ['company.view', 'company.members.manage'],
    expires_at: null,
    ...overrides,
  }
}

describe('company store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useCompanyStore.setState({
      companies: [],
      activeCompanyId: null,
      loaded: false,
      error: null,
    })
  })

  it('tells the API client which company every later request belongs to', async () => {
    // The one thing this store exists to do. `lib/api` attaches the header
    // from that value and nothing else writes it, so a store that loaded the
    // list without calling this would leave every request companyless.
    vi.mocked(api.get).mockResolvedValue([company('a')])

    await useCompanyStore.getState().load()

    expect(setActiveCompanyId).toHaveBeenCalledWith('a')
  })

  it('restores the remembered company when it is still one of yours', async () => {
    vi.mocked(api.get).mockResolvedValue([company('a'), company('b')])

    await useCompanyStore.getState().load('b')

    expect(useCompanyStore.getState().activeCompanyId).toBe('b')
    expect(setActiveCompanyId).toHaveBeenCalledWith('b')
  })

  it('falls back to the first company when the remembered one is gone', async () => {
    // Access revoked, or the company archived. The server would answer 404
    // for it — this is what stops the app opening on that 404.
    vi.mocked(api.get).mockResolvedValue([company('a')])

    await useCompanyStore.getState().load('deleted-company')

    expect(useCompanyStore.getState().activeCompanyId).toBe('a')
  })

  it('clears the header when the user is in no company at all', async () => {
    vi.mocked(api.get).mockResolvedValue([])

    await useCompanyStore.getState().load('stale')

    expect(setActiveCompanyId).toHaveBeenLastCalledWith(null)
    expect(useCompanyStore.getState().activeCompanyId).toBeNull()
  })

  it('finishes loading even when the request fails', async () => {
    // `loaded` must become true regardless, or the shell waits forever on a
    // list that is never coming (rule 17c: never dead and silent).
    vi.mocked(api.get).mockRejectedValue(new Error('network down'))

    await useCompanyStore.getState().load()

    expect(useCompanyStore.getState().loaded).toBe(true)
    expect(useCompanyStore.getState().error).toBe('network down')
  })

  it('remembers a switch on the server, not in this browser', async () => {
    vi.mocked(api.get).mockResolvedValue([company('a'), company('b')])
    await useCompanyStore.getState().load()

    useCompanyStore.getState().select('b')

    expect(setActiveCompanyId).toHaveBeenLastCalledWith('b')
    expect(api.patch).toHaveBeenCalledWith('/auth/me/preferences', {
      active_company_id: 'b',
    })
  })

  it('ignores a switch to a company the user is not in', async () => {
    // Nothing in the UI offers one, so this is about a stale preference or a
    // hand-crafted call: the header must never be pointed at a company the
    // server would 404, because the whole app would then be that 404.
    vi.mocked(api.get).mockResolvedValue([company('a')])
    await useCompanyStore.getState().load()
    vi.mocked(setActiveCompanyId).mockClear()

    useCompanyStore.getState().select('someone-elses-company')

    expect(setActiveCompanyId).not.toHaveBeenCalled()
    expect(useCompanyStore.getState().activeCompanyId).toBe('a')
  })

  it('reports permissions from the ACTIVE company, not the union of all', async () => {
    // A person who is an owner of one company and a tax advisor at another
    // must see the advisor's menu while the advisor's company is active. A
    // union would quietly hand them the owner's navigation in both.
    vi.mocked(api.get).mockResolvedValue([
      company('owned', { role: 'owner' }),
      company('client', {
        role: 'tax_advisor',
        permissions: ['company.view', 'archive.view', 'archive.download'],
      }),
    ])
    await useCompanyStore.getState().load()

    useCompanyStore.getState().select('client')

    expect(useCompanyStore.getState().can('archive.view')).toBe(true)
    expect(useCompanyStore.getState().can('company.members.manage')).toBe(false)
  })

  it('grants nothing while no company is active', async () => {
    expect(useCompanyStore.getState().can('archive.view')).toBe(false)
  })
})
