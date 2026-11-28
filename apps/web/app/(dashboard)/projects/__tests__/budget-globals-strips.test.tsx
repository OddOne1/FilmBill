import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

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

import { api } from '@/lib/api'
import { GlobalsPanel } from '../[id]/budgets/[budgetId]/budget-panels'
import type { BudgetSheet, ProjectCalendar } from '@/types'
import { makeSheet } from './fixtures'

/**
 * B2a: a counted global can count all strips or one strip, and a global
 * whose strip was removed says so — the warning is the API's sentence and
 * the value the API's fallback, never a zero the screen invented.
 */

const CALENDAR: ProjectCalendar = {
  project_id: 'p1',
  project_start: '2026-03-02',
  project_end: null,
  counts: { shoot: 5 },
  strips: [
    {
      id: 'b-bts',
      kind: 'strip',
      type: 'shoot',
      label: 'BTS shoot',
      start_date: '2026-03-04',
      end_date: '2026-03-04',
      is_active: true,
      hotels_visible: false,
      counts: { shoot: 1 },
      days: [
        {
          id: 'd1',
          block_id: 'b-bts',
          date: '2026-03-04',
          type: 'shoot',
          is_override: false,
          has_own_data: false,
          updated_at: '2026-11-25T00:00:00Z',
        },
      ],
    },
  ],
}

function sheet(change: Partial<BudgetSheet['globals'][number]> = {}): BudgetSheet {
  const base = makeSheet()
  base.globals = base.globals.map((row) => (row.source.startsWith('days:') ? { ...row, ...change } : row))
  return base
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue(CALENDAR as never)
  vi.mocked(api.patch).mockResolvedValue({} as never)
})

describe('a counted global and its strips', () => {
  it('offers all strips or one strip, and sends the strip-limited source', async () => {
    const onChange = vi.fn()
    render(<GlobalsPanel sheet={sheet()} calc={null} editable onChange={onChange} onError={vi.fn()} />)
    const picker = screen.getByLabelText('Strips counted by shoot_days') as HTMLSelectElement
    await screen.findByRole('option', { name: 'BTS shoot' })
    expect(picker.value).toBe('')

    fireEvent.change(picker, { target: { value: 'b-bts' } })
    await waitFor(() => expect(api.patch).toHaveBeenCalled())
    expect(vi.mocked(api.patch).mock.calls[0][1]).toEqual({ source: 'days:shoot:b-bts' })
  })

  it('shows the API’s warning when the strip it counted was removed', () => {
    render(
      <GlobalsPanel
        sheet={sheet({
          source: 'days:shoot:gone',
          block_id: null,
          warning: '“Main” this global counted was removed, so it now counts the shoot days of all strips. Pick a strip again, or keep all strips.',
        })}
        calc={null}
        editable={false}
        onChange={vi.fn()}
        onError={vi.fn()}
      />,
    )
    expect(screen.getByTestId('global-warning').textContent).toContain('was removed')
  })
})
