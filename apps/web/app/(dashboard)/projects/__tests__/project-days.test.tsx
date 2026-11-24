import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), upload: vi.fn(), download: vi.fn() },
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
  usePathname: () => '/projects/p1',
  useParams: () => ({ id: 'p1' }),
  useRouter: () => ({ push: vi.fn() }),
}))

import { api } from '@/lib/api'
import ProjectDetailPage from '../[id]/page'
import type { ProjectDayList } from '@/types'
import { ALL_PERMISSIONS, makeDay, makeProject, seedCompany } from './fixtures'

/**
 * The Days tab. The counts are the API's (`counts`) — the fixture says 7
 * shoot days while listing one, so a screen that counted the rows itself
 * would say 1. The range is the server's: the tab sends from/to/type and
 * prints the sentence that comes back.
 */

const DAYS: ProjectDayList = {
  days: [makeDay(), makeDay({ id: 'd2', date: '2026-03-02', type: 'prep', location: null, call_time: null })],
  counts: { shoot: 7, prep: 1 },
}

function mockApi() {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/projects/p1') return makeProject() as never
    if (path === '/projects/p1/days') return DAYS as never
    return [] as never
  })
}

async function openDays() {
  render(<ProjectDetailPage />)
  fireEvent.click(await screen.findByRole('tab', { name: 'Days' }))
  await screen.findAllByTestId('day-row')
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany(ALL_PERMISSIONS)
  mockApi()
})

describe('the Days tab', () => {
  it('prints the API’s counts, not the number of rows it was sent', async () => {
    await openDays()
    expect(screen.getByTestId('day-counts').textContent).toBe('1 prep · 7 shoot')
  })

  it('adds a range through the API and shows what the API says it did', async () => {
    vi.mocked(api.post).mockResolvedValue({
      created: [],
      existing: ['2026-03-04'],
      skipped: ['2026-03-07', '2026-03-08'],
      detail: 'Added 4 shoot days; 1 already existed; 2 weekend days skipped.',
    } as never)
    await openDays()

    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-03-03' } })
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-03-10' } })
    fireEvent.click(screen.getByLabelText('Skip weekends'))
    fireEvent.click(screen.getByText('Add days'))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0]).toEqual([
      '/projects/p1/days/range',
      { from: '2026-03-03', to: '2026-03-10', type: 'shoot', skip_weekends: true },
    ])
    expect((await screen.findByTestId('days-range-result')).textContent).toContain(
      '1 already existed',
    )
  })

  it('saves one edited field of one day on blur, and nothing when unchanged', async () => {
    vi.mocked(api.patch).mockResolvedValue(makeDay() as never)
    await openDays()

    const location = screen.getByLabelText('Location 2026-03-04')
    fireEvent.blur(location)
    expect(api.patch).not.toHaveBeenCalled()

    fireEvent.change(location, { target: { value: 'Studio B' } })
    fireEvent.blur(location)
    await waitFor(() => expect(api.patch).toHaveBeenCalled())
    expect(vi.mocked(api.patch).mock.calls[0]).toEqual([
      '/projects/p1/days/d1',
      { location: 'Studio B' },
    ])
  })

  it('marks the planned dates on the month strip', async () => {
    await openDays()
    const planned = screen
      .getAllByTestId('month-day-planned')
      .map((cell) => cell.getAttribute('data-date'))
    expect(planned).toEqual(['2026-03-02', '2026-03-04'])
  })

  it('without projects.edit: no range form, no inputs, and a sentence', async () => {
    seedCompany(['projects.view'])
    await openDays()
    expect(screen.queryByTestId('days-range-form')).toBeNull()
    expect(screen.queryByLabelText('Location 2026-03-04')).toBeNull()
    expect(screen.getByTestId('projects-read-only')).toBeInTheDocument()
  })

  it('a staff member sees the Budgets tab explain why it is closed', async () => {
    seedCompany(['projects.view'])
    render(<ProjectDetailPage />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Budgets' }))
    expect(screen.getByTestId('budgets-no-access').textContent).toContain('cost rates and margins')
    expect(
      vi.mocked(api.get).mock.calls.some((call) => String(call[0]).includes('/budgets')),
    ).toBe(false)
  })
})
