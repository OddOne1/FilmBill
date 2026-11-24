import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn(), upload: vi.fn(), download: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
      this.detail = detail
    }
  },
}))
vi.mock('next/navigation', () => ({
  usePathname: () => '/projects/p1/budgets/b1',
  useParams: () => ({ id: 'p1', budgetId: 'b1' }),
  useRouter: () => ({ push: vi.fn() }),
}))

import { api } from '@/lib/api'
import BudgetPage from '../[id]/budgets/[budgetId]/page'
import type { BudgetTracking, LineTracking } from '@/types'
import { ALL_PERMISSIONS, makeCalculated, makeLedger, makeSheet, makeTracking, seedCompany } from './fixtures'

/**
 * B1.1 part 2 — "Mark finished" on the screen.
 *
 * Whether a line is finished is the API's `finished` flag; the screen never
 * decides it from the amount. In `makeTracking()` the Alexa line has an open
 * order of 1 000.00, which is the case that has to ask before finishing.
 */

function mockApi(tracking: BudgetTracking = makeTracking()) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/budgets/b1') return makeSheet() as never
    if (path === '/budgets/b1/calculated') return makeCalculated() as never
    if (path === '/budgets/b1/tracking') return tracking as never
    if (path.startsWith('/budgets/b1/ledger')) return makeLedger() as never
    return {} as never
  })
}

function withFinished(id: string): BudgetTracking {
  const tracking = makeTracking()
  tracking.lines![id] = {
    ...tracking.lines![id],
    finished: true,
    etc: '0.00',
    remaining: '0.00',
    remaining_rule: 'etc',
  } as LineTracking
  return tracking
}

async function openDrawer(description: string) {
  render(<BudgetPage />)
  await screen.findByTestId('budget-sheet')
  fireEvent.click(screen.getByTestId('phase-efc'))
  const row = screen.getAllByTestId('phase-line-row').find((element) => element.textContent?.includes(description))
  fireEvent.click(within(row as HTMLElement).getByText(description))
  return screen.findByTestId('line-drawer')
}

function trackingFetches(): number {
  return vi.mocked(api.get).mock.calls.filter((call) => call[0] === '/budgets/b1/tracking').length
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany(ALL_PERMISSIONS)
  mockApi()
  vi.mocked(api.post).mockResolvedValue({} as never)
})

describe('Mark finished in the line drawer', () => {
  it('a line with nothing on order is finished with one POST, then the forecast is fetched again', async () => {
    const drawer = await openDrawer('DoP')
    const before = trackingFetches()
    fireEvent.click(within(drawer).getByTestId('line-finish'))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.post).mock.calls[0]).toEqual(['/budgets/b1/lines/l-dop/finish', { confirm_open_commitment: false }])
    await waitFor(() => expect(trackingFetches()).toBe(before + 1))
  })

  it('a line with an open order asks first, and only “Finish anyway” sends it', async () => {
    const drawer = await openDrawer('Alexa 35')
    fireEvent.click(within(drawer).getByTestId('line-finish'))
    const confirm = await within(drawer).findByTestId('finish-confirm')
    expect(confirm.textContent).toContain('still ordered and not yet invoiced')
    expect(api.post).not.toHaveBeenCalled()

    fireEvent.click(within(confirm).getByTestId('finish-confirm-yes'))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.post).mock.calls[0]).toEqual(['/budgets/b1/lines/l-alexa/finish', { confirm_open_commitment: true }])
  })

  it('a finished line says nothing more is expected and offers Reopen', async () => {
    mockApi(withFinished('l-dop'))
    const drawer = await openDrawer('DoP')
    expect(within(drawer).getByTestId('drawer-rule').textContent).toBe('Finished: nothing more is expected on this line.')
    expect(within(drawer).queryByTestId('line-finish')).toBeNull()
    fireEvent.click(within(drawer).getByTestId('line-reopen'))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe('/budgets/b1/lines/l-dop/reopen')
  })
})

describe('the finished marker', () => {
  it('shows on a finished line in every phase, and only on that line', async () => {
    mockApi(withFinished('l-dop'))
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    const marked = (testId: string) =>
      screen
        .getAllByTestId(testId)
        .filter((row) => within(row).queryByTestId('line-finished'))
        .map((row) => row.getAttribute('data-line'))

    // Planned is A2's sheet, drawn by another component.
    expect(marked('line-row')).toEqual(['l-dop'])
    for (const key of ['committed', 'actual', 'efc', 'variance']) {
      fireEvent.click(screen.getByTestId(`phase-${key}`))
      await screen.findByTestId('phase-sheet')
      expect(marked('phase-line-row'), key).toEqual(['l-dop'])
    }
  })
})

describe('a whole section at once', () => {
  it('Finish all lines says how many were skipped and names them', async () => {
    vi.mocked(api.post).mockResolvedValue({
      section_id: 's-crew',
      changed: ['l-dop'],
      already: [],
      skipped: [
        { line_id: 'l-gaffer', description: 'Gaffer', committed_open: '300.00' },
        { line_id: 'l-alexa', description: 'Alexa 35', committed_open: '1000.00' },
      ],
      message: '1 line marked finished. 2 lines skipped: still ordered, not invoiced.',
    } as never)
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-efc'))
    const crew = screen
      .getAllByTestId('phase-section-row')
      .find((row) => row.getAttribute('data-section') === 's-crew') as HTMLElement
    fireEvent.click(within(crew).getByTestId('section-finish-all'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/budgets/b1/sections/s-crew/finish'))

    const result = await within(crew).findByTestId('bulk-result')
    expect(result.textContent).toContain('2 lines skipped: still ordered, not invoiced')
    const skipped = within(result).getAllByTestId('bulk-skipped').map((item) => item.textContent)
    expect(skipped[0]).toContain('Gaffer')
    expect(skipped[1]).toContain('Alexa 35')
  })

  it('Reopen all is one POST for the section', async () => {
    vi.mocked(api.post).mockResolvedValue({
      section_id: 's-crew',
      changed: ['l-dop'],
      already: [],
      skipped: [],
      message: '1 line reopened.',
    } as never)
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-actual'))
    const crew = screen
      .getAllByTestId('phase-section-row')
      .find((row) => row.getAttribute('data-section') === 's-crew') as HTMLElement
    fireEvent.click(within(crew).getByTestId('section-reopen-all'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/budgets/b1/sections/s-crew/reopen'))
    expect((await within(crew).findByTestId('bulk-result')).textContent).toBe('1 line reopened.')
  })

  it('without budgets.edit there are no finish buttons anywhere', async () => {
    seedCompany(['projects.view', 'budgets.view'])
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-efc'))
    await screen.findByTestId('phase-sheet')
    expect(screen.queryByTestId('section-finish-all')).toBeNull()
    expect(screen.queryByTestId('section-reopen-all')).toBeNull()
    fireEvent.click(within(screen.getAllByTestId('phase-line-row')[0]).getAllByRole('button')[0])
    const drawer = await screen.findByTestId('line-drawer')
    expect(within(drawer).queryByTestId('line-finish')).toBeNull()
    expect(within(drawer).getByTestId('drawer-read-only')).toBeInTheDocument()
  })
})
