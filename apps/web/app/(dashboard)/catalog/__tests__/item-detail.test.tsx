import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn(), upload: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.name = 'ApiError'
      this.status = status
      this.detail = detail
    }
  },
}))

vi.mock('next/navigation', () => ({
  usePathname: () => '/catalog/i1',
  useParams: () => ({ id: 'i1' }),
}))

import { api, ApiError } from '@/lib/api'
import ItemDetailPage from '../[id]/page'
import { useCompanyStore } from '@/stores/company-store'
import type { Item, TaxClass } from '@/types'

/**
 * One item.
 *
 * The rate editor is a **whole-set PUT**, so the test that matters asserts
 * the full body: editing one amount and sending only that row would lose
 * the others, and the endpoint would do exactly as it was told.
 */

function seedCompany(permissions = ['catalog.view', 'catalog.edit']) {
  useCompanyStore.setState({
    companies: [
      {
        id: 'c1',
        legal_name: 'YON Studio OG',
        trading_name: null,
        display_name: 'YON Studio OG',
        default_currency: 'EUR',
        role: 'owner',
        permissions,
        expires_at: null,
      },
    ],
    activeCompanyId: 'c1',
    loaded: true,
    error: null,
  })
}

function makeItem(overrides: Partial<Item> = {}): Item {
  return {
    id: 'i1',
    sku: 'CAM-001',
    name_de: 'Alexa 35',
    name_en: null,
    kind: 'equipment_rental',
    category_id: null,
    category_path: 'Equipment › Camera',
    description_internal: null,
    description_customer: null,
    unit_id: null,
    unit_key: 'day',
    default_tax_class: null,
    rental_factor_group_id: null,
    rental_factor_group_name: null,
    revenue_account: null,
    expense_account: null,
    rebilled_cost: false,
    notes: null,
    is_active: true,
    rates: [
      { rate_type: 'bill', period_unit: 'day', amount: '1200.00', currency: 'EUR' },
      { rate_type: 'bill', period_unit: 'week', amount: '4800.00', currency: 'EUR' },
      { rate_type: 'cost', period_unit: 'day', amount: '300.00', currency: 'EUR' },
    ],
    created_at: '2026-11-23T00:00:00Z',
    updated_at: '2026-11-23T00:00:00Z',
    ...overrides,
  }
}

const TAX_CLASSES = [
  { key: 'standard', label: 'Standard rate', is_active: true },
  { key: 'retired', label: 'An old class', is_active: false },
] as unknown as TaxClass[]

function mockApi(item = makeItem()) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/catalog/items/i1') return item as never
    if (path === '/company/tax/classes') return TAX_CLASSES as never
    return [] as never
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

describe('the item', () => {
  it('shows its name, SKU, kind and category path', async () => {
    mockApi()
    render(<ItemDetailPage />)

    await waitFor(() =>
      expect(screen.getByTestId('item-name')).toHaveTextContent('Alexa 35'),
    )
    expect(screen.getByText(/CAM-001/)).toBeInTheDocument()
    expect(screen.getByText(/Equipment › Camera/)).toBeInTheDocument()
  })

  it('says so when the item is not in this company', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/catalog/items/i1') {
        throw new ApiError(404, 'that item does not exist in this company')
      }
      return [] as never
    })
    render(<ItemDetailPage />)

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'does not exist in this company',
      ),
    )
  })

  it('offers only active tax classes', async () => {
    mockApi()
    render(<ItemDetailPage />)

    await waitFor(() =>
      expect(screen.getByText('standard')).toBeInTheDocument(),
    )
    // A switched-off class in the picker would be offering a refusal.
    expect(screen.queryByText('retired')).not.toBeInTheDocument()
  })

  it('shows the rebilled-cost switch only for travel', async () => {
    mockApi(makeItem({ kind: 'travel', rebilled_cost: true }))
    render(<ItemDetailPage />)

    await waitFor(() =>
      expect(screen.getByText(/Rebilled cost/)).toBeInTheDocument(),
    )
  })

  it('does not show the rebilled-cost switch for equipment', async () => {
    mockApi()
    render(<ItemDetailPage />)

    await waitFor(() =>
      expect(screen.getByTestId('item-fields')).toBeInTheDocument(),
    )
    expect(screen.queryByText(/Rebilled cost/)).not.toBeInTheDocument()
  })
})

describe('the rate table', () => {
  it('shows every rate with its period and amount, as sent', async () => {
    mockApi()
    render(<ItemDetailPage />)

    await waitFor(() =>
      expect(screen.getAllByTestId('rate-row')).toHaveLength(3),
    )
    expect(screen.getByDisplayValue('1200.00')).toBeInTheDocument()
    expect(screen.getByDisplayValue('4800.00')).toBeInTheDocument()
    expect(screen.getByDisplayValue('300.00')).toBeInTheDocument()
  })

  it('sends the whole set when one amount is edited', async () => {
    mockApi()
    vi.mocked(api.put).mockResolvedValue([] as never)
    render(<ItemDetailPage />)
    await waitFor(() =>
      expect(screen.getAllByTestId('rate-row')).toHaveLength(3),
    )

    fireEvent.change(screen.getAllByLabelText('Amount')[0], {
      target: { value: '1250.00' },
    })
    fireEvent.click(screen.getByTestId('rates-save'))

    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0][0]).toBe('/catalog/items/i1/rates')
    // All three, not just the edited one. A PUT replaces the set.
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual([
      { rate_type: 'bill', period_unit: 'day', amount: '1250.00' },
      { rate_type: 'bill', period_unit: 'week', amount: '4800.00' },
      { rate_type: 'cost', period_unit: 'day', amount: '300.00' },
    ])
  })

  it('sends an empty list when every rate is removed', async () => {
    mockApi()
    vi.mocked(api.put).mockResolvedValue([] as never)
    render(<ItemDetailPage />)
    await waitFor(() =>
      expect(screen.getAllByTestId('rate-row')).toHaveLength(3),
    )

    for (let i = 0; i < 3; i += 1) {
      fireEvent.click(screen.getAllByLabelText('Remove rate')[0])
    }
    fireEvent.click(screen.getByTestId('rates-save'))

    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual([])
  })

  it('shows the server’s refusal of a negative amount', async () => {
    mockApi()
    vi.mocked(api.put).mockRejectedValue(
      new ApiError(422, 'a rate cannot be negative; got -5.00'),
    )
    render(<ItemDetailPage />)
    await waitFor(() =>
      expect(screen.getAllByTestId('rate-row')).toHaveLength(3),
    )

    fireEvent.change(screen.getAllByLabelText('Amount')[0], {
      target: { value: '-5.00' },
    })
    fireEvent.click(screen.getByTestId('rates-save'))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('cannot be negative'),
    )
  })
})

describe('retiring and reactivating', () => {
  it('posts to deactivate rather than deleting', async () => {
    mockApi()
    vi.mocked(api.post).mockResolvedValue(
      makeItem({ is_active: false }) as never,
    )
    render(<ItemDetailPage />)
    await waitFor(() =>
      expect(screen.getByTestId('item-toggle-active')).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByTestId('item-toggle-active'))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    // Rule 3's shape in the catalog: retiring an item is a POST to its own
    // route. That there is no DELETE anywhere is asserted against the real
    // OpenAPI document in `test_catalog_api.py`; asserting it against this
    // file's own `vi.mock` would only prove what the mock was given.
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe(
      '/catalog/items/i1/deactivate',
    )
  })

  it('offers reactivation for a retired item', async () => {
    mockApi(makeItem({ is_active: false }))
    render(<ItemDetailPage />)

    await waitFor(() =>
      expect(screen.getByTestId('item-toggle-active')).toHaveTextContent(
        'Reactivate item',
      ),
    )
  })
})

describe('without catalog.edit', () => {
  it('disables every field, offers no save, and says why', async () => {
    seedCompany(['catalog.view'])
    mockApi()
    render(<ItemDetailPage />)

    await waitFor(() =>
      expect(screen.getByTestId('item-name')).toBeInTheDocument(),
    )
    expect(screen.getByTestId('catalog-read-only')).toBeInTheDocument()
    expect(screen.queryByTestId('item-save')).not.toBeInTheDocument()
    expect(screen.queryByTestId('rates-save')).not.toBeInTheDocument()
    expect(screen.queryByTestId('item-toggle-active')).not.toBeInTheDocument()
    expect(screen.getAllByLabelText('Amount')[0]).toBeDisabled()
  })
})
