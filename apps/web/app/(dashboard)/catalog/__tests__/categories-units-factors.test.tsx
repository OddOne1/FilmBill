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
  usePathname: () => '/catalog/categories',
  useParams: () => ({}),
}))

import { api, ApiError } from '@/lib/api'
import CategoriesPage from '../categories/page'
import { buildTree } from '../categories/tree'
import UnitsPage from '../units/page'
import RentalFactorsPage from '../rental-factors/page'
import { useCompanyStore } from '@/stores/company-store'
import type { ItemCategory, RentalFactorGroup, Unit } from '@/types'

/**
 * Categories, units and factor tables.
 *
 * Three things these tests are about, each one a place where the browser
 * could quietly take over a server decision:
 *   1. the tree is nested from `parent_id`, not parsed out of `path`;
 *   2. a refused move shows the server's sentence, which names the route;
 *   3. the factor lookup is a request, not a read of the table on screen.
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

function category(overrides: Partial<ItemCategory>): ItemCategory {
  return {
    id: 'x',
    name: 'X',
    kind: 'equipment_rental',
    parent_id: null,
    position: 0,
    is_active: true,
    path: 'X',
    depth: 1,
    item_count: 0,
    ...overrides,
  }
}

const TREE: ItemCategory[] = [
  category({ id: 'equipment', name: 'Equipment', path: 'Equipment' }),
  category({
    id: 'camera',
    name: 'Camera',
    parent_id: 'equipment',
    path: 'Equipment › Camera',
    depth: 2,
    item_count: 4,
  }),
  category({
    id: 'cinema',
    name: 'Cinema',
    parent_id: 'camera',
    path: 'Equipment › Camera › Cinema',
    depth: 3,
  }),
  category({ id: 'crew', name: 'Crew', kind: 'labor', path: 'Crew' }),
]

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

describe('buildTree', () => {
  it('nests by parent_id', () => {
    const roots = buildTree(TREE)
    expect(roots.map((node) => node.category.id)).toEqual(['equipment', 'crew'])
    expect(roots[0].children[0].category.id).toBe('camera')
    expect(roots[0].children[0].children[0].category.id).toBe('cinema')
  })

  it('keeps a child whose parent is not in the list', () => {
    // A switched-off parent with an active child: dropping the child would
    // hide a category somebody needs to fix.
    const roots = buildTree([TREE[1]])
    expect(roots.map((node) => node.category.id)).toEqual(['camera'])
  })

  it('does not split the display path to find the hierarchy', () => {
    // A name containing the separator. Parsing `path` would read this as
    // three levels; `parent_id` says it is one.
    const odd = category({
      id: 'odd',
      name: 'A › B',
      path: 'A › B',
      parent_id: null,
    })
    const roots = buildTree([odd])
    expect(roots).toHaveLength(1)
    expect(roots[0].children).toHaveLength(0)
  })
})

describe('the categories screen', () => {
  it('draws the tree and each category’s item count', async () => {
    vi.mocked(api.get).mockResolvedValue(TREE as never)
    render(<CategoriesPage />)

    await waitFor(() =>
      expect(screen.getAllByTestId('category-row')).toHaveLength(4),
    )
    expect(screen.getByText('Cinema')).toBeInTheDocument()
    expect(
      screen.getAllByTestId('category-item-count').map((node) => node.textContent),
    ).toContain('4 items')
  })

  it('shows the server’s sentence when a move is refused', async () => {
    vi.mocked(api.get).mockResolvedValue(TREE as never)
    vi.mocked(api.patch).mockRejectedValue(
      new ApiError(
        409,
        "that would put 'Equipment' inside itself (Equipment › Camera › Equipment)",
      ),
    )
    render(<CategoriesPage />)
    await waitFor(() =>
      expect(screen.getAllByTestId('category-row')).toHaveLength(4),
    )

    fireEvent.click(screen.getAllByText('Edit')[0])
    fireEvent.change(screen.getByLabelText('Parent category'), {
      target: { value: 'camera' },
    })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Equipment › Camera › Equipment',
      ),
    )
  })

  it('sends the move as a parent_id patch', async () => {
    vi.mocked(api.get).mockResolvedValue(TREE as never)
    vi.mocked(api.patch).mockResolvedValue(TREE[2] as never)
    render(<CategoriesPage />)
    await waitFor(() =>
      expect(screen.getAllByTestId('category-row')).toHaveLength(4),
    )

    fireEvent.click(screen.getAllByText('Edit')[0])
    fireEvent.change(screen.getByLabelText('Parent category'), {
      target: { value: 'crew' },
    })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => expect(api.patch).toHaveBeenCalled())
    expect(vi.mocked(api.patch).mock.calls[0][0]).toBe(
      '/catalog/categories/equipment',
    )
    expect(vi.mocked(api.patch).mock.calls[0][1]).toMatchObject({
      parent_id: 'crew',
    })
  })

  it('has no edit controls without catalog.edit, and says why', async () => {
    seedCompany(['catalog.view'])
    vi.mocked(api.get).mockResolvedValue(TREE as never)
    render(<CategoriesPage />)

    await waitFor(() =>
      expect(screen.getAllByTestId('category-row')).toHaveLength(4),
    )
    expect(screen.queryByText('Edit')).not.toBeInTheDocument()
    expect(screen.queryByTestId('category-new')).not.toBeInTheDocument()
    expect(screen.getByTestId('catalog-read-only')).toBeInTheDocument()
  })
})

const UNITS: Unit[] = [
  {
    id: 'u1',
    key: 'day',
    label_de: 'Tag',
    label_en: 'day',
    period_unit: 'day',
    is_system: true,
    position: 10,
    is_active: true,
  },
  {
    id: 'u2',
    key: 'halbtag',
    label_de: 'Halbtag',
    label_en: null,
    period_unit: 'day',
    is_system: false,
    position: 50,
    is_active: true,
  },
]

describe('the units screen', () => {
  it('shows an em dash rather than inventing an English label', async () => {
    vi.mocked(api.get).mockResolvedValue(UNITS as never)
    render(<UnitsPage />)

    await waitFor(() =>
      expect(screen.getAllByTestId('unit-row')).toHaveLength(2),
    )
    const rows = screen.getAllByTestId('unit-row')
    expect(rows[1].textContent).toContain('Halbtag')
    // Not "Halbtag" twice: `label_en` is null and the column says so.
    expect(rows[1].textContent).toContain('—')
  })

  it('marks which units came with the company', async () => {
    vi.mocked(api.get).mockResolvedValue(UNITS as never)
    render(<UnitsPage />)

    await waitFor(() =>
      expect(screen.getAllByTestId('unit-row')).toHaveLength(2),
    )
    expect(screen.getAllByTestId('unit-row')[0].textContent).toContain('system')
    expect(screen.getAllByTestId('unit-row')[1].textContent).toContain('yours')
  })

  it('sends a blank English label as null, not as an empty string', async () => {
    vi.mocked(api.get).mockResolvedValue(UNITS as never)
    vi.mocked(api.post).mockResolvedValue(UNITS[1] as never)
    render(<UnitsPage />)
    await waitFor(() => expect(screen.getByTestId('unit-new')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('unit-new'))
    fireEvent.change(screen.getByLabelText(/^Key/), {
      target: { value: 'pauschale' },
    })
    fireEvent.change(screen.getByLabelText(/^German label/), {
      target: { value: 'Pauschale' },
    })
    fireEvent.click(screen.getByText('Add'))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0][1]).toMatchObject({
      key: 'pauschale',
      label_de: 'Pauschale',
      label_en: null,
    })
  })

  it('shows the server’s refusal when a unit is still in use', async () => {
    vi.mocked(api.get).mockResolvedValue(UNITS as never)
    vi.mocked(api.patch).mockRejectedValue(
      new ApiError(
        409,
        'the unit "Tag" is used by 4 active items (Alexa 35, DoP, …)',
      ),
    )
    render(<UnitsPage />)
    await waitFor(() =>
      expect(screen.getAllByTestId('unit-row')).toHaveLength(2),
    )

    fireEvent.click(screen.getAllByText('Switch off')[0])

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'used by 4 active items',
      ),
    )
  })
})

const GROUPS: RentalFactorGroup[] = [
  {
    id: 'g1',
    name: 'Camera & lenses',
    notes: null,
    is_active: true,
    factors: [
      { days: 1, factor: '1.0000' },
      { days: 2, factor: '1.5000' },
      { days: 3, factor: '2.0000' },
      { days: 5, factor: '2.8000' },
      { days: 7, factor: '3.5000' },
    ],
  },
]

describe('the rental factors screen', () => {
  it('shows the table as the API sent it', async () => {
    vi.mocked(api.get).mockResolvedValue(GROUPS as never)
    render(<RentalFactorsPage />)

    await waitFor(() =>
      expect(screen.getAllByTestId('factor-row')).toHaveLength(5),
    )
    expect(screen.getByDisplayValue('2.8000')).toBeInTheDocument()
  })

  it('asks the API what N days cost instead of reading the table', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('/factor?days=')) {
        return {
          days: 6,
          factor: '2.8000',
          matched_days: 5,
          rule: 'next_lower',
          billable_days: '2.8000',
        } as never
      }
      return GROUPS as never
    })
    render(<RentalFactorsPage />)
    await waitFor(() =>
      expect(screen.getAllByTestId('factor-row')).toHaveLength(5),
    )

    fireEvent.change(screen.getByLabelText('Rental days'), {
      target: { value: '6' },
    })

    await waitFor(() =>
      expect(screen.getByTestId('factor-answer')).toBeInTheDocument(),
    )
    // The request went out — the answer was not read off the table above.
    expect(
      vi
        .mocked(api.get)
        .mock.calls.map((call) => String(call[0]))
        .some((path) => path === '/catalog/rental-factor-groups/g1/factor?days=6'),
    ).toBe(true)
    const answer = screen.getByTestId('factor-answer').textContent ?? ''
    expect(answer).toContain('2.8000')
    expect(answer).toContain('from the 5-day row')
    expect(answer).toContain('never up')
  })

  it('explains a factor of 1 below the shortest row', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('/factor?days=')) {
        return {
          days: 1,
          factor: '1',
          matched_days: null,
          rule: 'below_lowest',
          billable_days: '1',
        } as never
      }
      return GROUPS as never
    })
    render(<RentalFactorsPage />)
    await waitFor(() =>
      expect(screen.getAllByTestId('factor-row')).toHaveLength(5),
    )

    fireEvent.change(screen.getByLabelText('Rental days'), {
      target: { value: '1' },
    })

    await waitFor(() =>
      expect(screen.getByTestId('factor-answer')).toHaveTextContent(
        /the factor is 1/,
      ),
    )
  })

  it('sends the whole table on save, days as numbers and factors as typed', async () => {
    vi.mocked(api.get).mockResolvedValue(GROUPS as never)
    vi.mocked(api.put).mockResolvedValue(GROUPS[0] as never)
    render(<RentalFactorsPage />)
    await waitFor(() =>
      expect(screen.getAllByTestId('factor-row')).toHaveLength(5),
    )

    fireEvent.change(screen.getAllByLabelText('Factor')[3], {
      target: { value: '2.7500' },
    })
    fireEvent.click(screen.getByTestId('factors-save'))

    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0][0]).toBe(
      '/catalog/rental-factor-groups/g1/factors',
    )
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual([
      { days: 1, factor: '1.0000' },
      { days: 2, factor: '1.5000' },
      { days: 3, factor: '2.0000' },
      { days: 5, factor: '2.7500' },
      { days: 7, factor: '3.5000' },
    ])
  })

  it('is read-only without catalog.edit, and says why', async () => {
    seedCompany(['catalog.view'])
    vi.mocked(api.get).mockResolvedValue(GROUPS as never)
    render(<RentalFactorsPage />)

    await waitFor(() =>
      expect(screen.getAllByTestId('factor-row')).toHaveLength(5),
    )
    expect(screen.queryByTestId('factors-save')).not.toBeInTheDocument()
    expect(screen.queryByTestId('factor-group-new')).not.toBeInTheDocument()
    expect(screen.getByTestId('catalog-read-only')).toBeInTheDocument()
    // Reading is still possible, and the lookup still works.
    expect(screen.getByLabelText('Rental days')).toBeInTheDocument()
  })
})
