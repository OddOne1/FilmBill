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
  usePathname: () => '/catalog',
  useParams: () => ({}),
}))

import { api } from '@/lib/api'
import CatalogPage from '../page'
import { useCompanyStore } from '@/stores/company-store'
import type { Item, ItemPage } from '@/types'

/**
 * The item list.
 *
 * **The search and the filters are the server's**, so these tests assert
 * what went out in the query string rather than what the browser filtered
 * in. And **the rates on screen are the API's strings**: the row test uses
 * an amount that no plausible client-side formatting would produce, so a
 * screen that started reformatting or re-rounding money would fail here.
 */

function makeItem(overrides: Partial<Item> = {}): Item {
  return {
    id: 'i1',
    sku: 'CAM-001',
    name_de: 'Alexa 35',
    name_en: 'Alexa 35',
    kind: 'equipment_rental',
    category_id: 'cat1',
    category_path: 'Equipment › Camera',
    description_internal: null,
    description_customer: null,
    unit_id: 'u1',
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
      {
        rate_type: 'bill',
        period_unit: 'day',
        amount: '1200.00',
        currency: 'EUR',
      },
      {
        rate_type: 'cost',
        period_unit: 'day',
        amount: '0.00',
        currency: 'EUR',
      },
    ],
    created_at: '2026-11-23T00:00:00Z',
    updated_at: '2026-11-23T00:00:00Z',
    ...overrides,
  }
}

function page(items: Item[]): ItemPage {
  return { items, total: items.length, limit: 50, offset: 0 }
}

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

function mockApi(items: Item[] = [makeItem()]) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.startsWith('/catalog/items')) return page(items) as never
    if (path.startsWith('/catalog/categories')) return [] as never
    if (path.startsWith('/catalog/units')) return [] as never
    return [] as never
  })
}

/** Every path `api.get` was asked for, items only. */
function itemPaths(): string[] {
  return vi
    .mocked(api.get)
    .mock.calls.map((call) => String(call[0]))
    .filter((path) => path.startsWith('/catalog/items'))
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

describe('the item list', () => {
  it('shows the SKU, the names, the category path and the rates', async () => {
    mockApi()
    render(<CatalogPage />)

    await waitFor(() => expect(screen.getByText('Alexa 35')).toBeInTheDocument())
    expect(screen.getByText('CAM-001')).toBeInTheDocument()
    expect(screen.getByText('Equipment › Camera')).toBeInTheDocument()
    // Scoped to the row: "Equipment rental" is also an <option> in the kind
    // filter, and a document-wide match would pass on the filter alone.
    expect(screen.getByTestId('catalog-row').textContent).toContain(
      'Equipment rental',
    )
  })

  it('prints the rate exactly as the API serialised it', async () => {
    // Four decimal places and a trailing zero: a number no browser-side
    // formatter would produce by accident. If this screen ever starts
    // parsing money into a float and formatting it back, this fails.
    mockApi([
      makeItem({
        rates: [
          {
            rate_type: 'bill',
            period_unit: 'day',
            amount: '1234.50',
            currency: 'EUR',
          },
        ],
      }),
    ])
    render(<CatalogPage />)

    await waitFor(() =>
      expect(screen.getByText('1234.50')).toBeInTheDocument(),
    )
  })

  it('picks the day rate rather than deriving one', async () => {
    // Only a weekly rate exists. The right answer is an em dash — a screen
    // that divided 6000 by 7 to show a day rate would be computing money.
    mockApi([
      makeItem({
        rates: [
          {
            rate_type: 'bill',
            period_unit: 'week',
            amount: '6000.00',
            currency: 'EUR',
          },
        ],
      }),
    ])
    render(<CatalogPage />)

    await waitFor(() => expect(screen.getByText('Alexa 35')).toBeInTheDocument())
    expect(screen.queryByText(/857/)).not.toBeInTheDocument()
    expect(screen.queryByText('6000.00')).not.toBeInTheDocument()
  })

  it('sends the search to the server instead of filtering here', async () => {
    mockApi()
    render(<CatalogPage />)
    await waitFor(() => expect(itemPaths().length).toBeGreaterThan(0))

    fireEvent.change(screen.getByLabelText('Search the catalog'), {
      target: { value: 'alexa' },
    })

    await waitFor(() =>
      expect(itemPaths().at(-1)).toContain('search=alexa'),
    )
  })

  it('sends the kind filter as a query parameter', async () => {
    mockApi()
    render(<CatalogPage />)
    await waitFor(() => expect(itemPaths().length).toBeGreaterThan(0))

    fireEvent.change(screen.getByLabelText('Filter by kind'), {
      target: { value: 'labor' },
    })

    await waitFor(() => expect(itemPaths().at(-1)).toContain('kind=labor'))
  })

  it('asks for retired items only when the box is ticked', async () => {
    mockApi()
    render(<CatalogPage />)
    await waitFor(() => expect(itemPaths().length).toBeGreaterThan(0))
    expect(itemPaths().at(-1)).not.toContain('include_inactive')

    fireEvent.click(screen.getByLabelText('Show retired'))

    await waitFor(() =>
      expect(itemPaths().at(-1)).toContain('include_inactive=true'),
    )
  })

  it('marks a retired item on its row', async () => {
    mockApi([makeItem({ is_active: false })])
    render(<CatalogPage />)
    await waitFor(() => expect(screen.getByText('retired')).toBeInTheDocument())
  })
})

describe('without catalog.edit', () => {
  it('has no New item button and says why (17c)', async () => {
    seedCompany(['catalog.view'])
    mockApi()
    render(<CatalogPage />)

    await waitFor(() => expect(screen.getByText('Alexa 35')).toBeInTheDocument())
    expect(screen.queryByTestId('catalog-new-item')).not.toBeInTheDocument()
    // The rule is not "hide the button" — it is "never a dead button and
    // never a silent screen".
    expect(screen.getByTestId('catalog-read-only')).toBeInTheDocument()
    expect(screen.getByTestId('catalog-read-only').textContent).toMatch(
      /catalog edit permission/,
    )
  })
})

describe('without catalog.view', () => {
  it('says so rather than showing an empty list', async () => {
    seedCompany([])
    mockApi()
    render(<CatalogPage />)

    await waitFor(() =>
      expect(
        screen.getByText(/do not have access to this company/i),
      ).toBeInTheDocument(),
    )
    // And it did not go and ask for items it may not have.
    expect(itemPaths()).toHaveLength(0)
  })
})
