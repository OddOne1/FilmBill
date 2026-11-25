import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
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
import CompanyGeneralPage from '../page'
import { useCompanyStore } from '@/stores/company-store'

/**
 * B1.2 part 2 — Settings → Company → Default production fee.
 *
 * Read from and saved to `/company/production-fee`, its own endpoint, so the
 * fee never rides on `GET /company`. Gated on the fetched preset rather than
 * on the form's static chrome (rule 17b).
 */

const COMPANY = {
  id: 'c1',
  legal_name: 'YON Studio OG',
  address_country: 'AT',
  default_currency: 'EUR',
  default_language: 'de',
  timezone: 'Europe/Vienna',
  fiscal_year_start_month: 1,
}

function seed() {
  useCompanyStore.setState({
    companies: [
      {
        id: 'c1',
        legal_name: 'YON Studio OG',
        trading_name: null,
        display_name: 'YON Studio OG',
        default_currency: 'EUR',
        role: 'owner',
        permissions: ['company.view', 'company.settings.edit'],
        expires_at: null,
      },
    ],
    activeCompanyId: 'c1',
    loaded: true,
    error: null,
  })
}

function mockApi(preset: { percent: string | null; kinds: string[] }) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/company') return COMPANY as never
    if (path === '/company/production-fee') return preset as never
    throw new Error(`unexpected GET ${path}`)
  })
  vi.mocked(api.put).mockImplementation(async (_path: string, body: unknown) => body as never)
}

beforeEach(() => {
  vi.clearAllMocks()
  seed()
})

describe('Default production fee', () => {
  it('shows the preset the server has', async () => {
    mockApi({ percent: '8.0000', kinds: [] })
    render(<CompanyGeneralPage />)
    await waitFor(() => expect(screen.getByLabelText('Default production fee percent')).toHaveValue('8'))
  })

  it('saves a percent on certain kinds', async () => {
    mockApi({ percent: '8.0000', kinds: [] })
    render(<CompanyGeneralPage />)
    await waitFor(() => expect(screen.getByLabelText('Default production fee percent')).toHaveValue('8'))
    fireEvent.change(screen.getByLabelText('Default production fee percent'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('Default production fee on kinds'), { target: { value: 'kinds' } })
    const form = screen.getByTestId('default-production-fee')
    fireEvent.click(within(form).getByLabelText('Equipment rental'))
    fireEvent.click(within(form).getByRole('button', { name: 'Save default fee' }))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0]).toEqual([
      '/company/production-fee',
      { percent: '10', kinds: ['equipment_rental'] },
    ])
  })

  it('an empty box clears the preset', async () => {
    mockApi({ percent: '8.0000', kinds: [] })
    render(<CompanyGeneralPage />)
    await waitFor(() => expect(screen.getByLabelText('Default production fee percent')).toHaveValue('8'))
    fireEvent.change(screen.getByLabelText('Default production fee percent'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save default fee' }))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual({ percent: null, kinds: [] })
  })
})
