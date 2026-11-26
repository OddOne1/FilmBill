import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    upload: vi.fn(),
    download: vi.fn(),
  },
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
  usePathname: () => '/crew',
  useParams: () => ({}),
}))

import { api } from '@/lib/api'
import CrewListPage from '../page'
import { useCompanyStore } from '@/stores/company-store'

/**
 * The crew page. What is under test is what the page SENDS and what it
 * shows of what came back: the list is the API's (search, role and sort are
 * query parameters), quick add posts exactly the form, and the import sends
 * the preview's digest and the ticked duplicate lines — read off the
 * FormData that actually went out.
 */

function seed(permissions = ['parties.view', 'parties.edit']) {
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

function member(overrides: Record<string, unknown> = {}) {
  return {
    id: 'p1',
    name: 'Anna Huber',
    roles: ['DoP'],
    email: 'anna@example.com',
    phone: '+43 660 1',
    notes: null,
    is_active: true,
    is_customer: false,
    is_supplier: false,
    party_number: null,
    created_at: '2026-11-26T10:00:00Z',
    ...overrides,
  }
}

function routeGets(members = [member()]) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/crew/roles') return { used: ['DoP', 'Sound'], catalog: ['Gaffer'] }
    if (path.startsWith('/crew')) return { members, total: members.length }
    throw new Error(`unexpected GET ${path}`)
  })
}

describe('the crew page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seed()
    routeGets()
  })

  it('lists what the API returned, each name linking to the full party record', async () => {
    routeGets([member(), member({ id: 'p2', name: 'Ben Ohr', roles: ['Sound'], is_supplier: true })])
    render(<CrewListPage />)
    const rows = await screen.findAllByTestId('crew-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByRole('link', { name: 'Anna Huber' })).toHaveAttribute('href', '/parties/p1')
    expect(within(rows[1]).getByText('also a supplier')).toBeInTheDocument()
    expect(screen.getByTestId('crew-total')).toHaveTextContent('2 people')
  })

  it('sends the search, the role filter, the sort and the archived toggle to the API', async () => {
    render(<CrewListPage />)
    await screen.findAllByTestId('crew-row')
    // The role filter offers the roles in use.
    await waitFor(() =>
      expect(within(screen.getByTestId('crew-role-filter')).getByText('Sound')).toBeInTheDocument(),
    )
    fireEvent.change(screen.getByTestId('crew-search'), { target: { value: 'hub' } })
    fireEvent.change(screen.getByTestId('crew-role-filter'), { target: { value: 'Sound' } })
    fireEvent.change(screen.getByTestId('crew-sort'), { target: { value: '-name' } })
    fireEvent.click(screen.getByTestId('crew-include-inactive'))
    await waitFor(() =>
      expect(vi.mocked(api.get)).toHaveBeenCalledWith(
        '/crew?search=hub&role=Sound&sort=-name&include_inactive=true',
      ),
    )
  })

  it('quick add says why it cannot save without a name, then posts exactly the form', async () => {
    vi.mocked(api.post).mockResolvedValue({
      member: member({ id: 'new', name: 'Lea Ton', roles: ['Sound'] }),
      duplicates: [{ party_id: 'old', name: 'Lea Ton', reason: 'name', detail: 'There is already a party called Lea Ton.' }],
    })
    render(<CrewListPage />)
    fireEvent.click(await screen.findByTestId('crew-add-open'))
    expect(screen.getByTestId('crew-add-save')).toBeDisabled()
    expect(screen.getByTestId('crew-add-why')).toHaveTextContent(
      'Type a first or last name to add this person.',
    )

    // P1b-4: a middle name alone is not a name — still disabled, still says why.
    fireEvent.change(screen.getByTestId('crew-add-middle'), { target: { value: 'M.' } })
    expect(screen.getByTestId('crew-add-save')).toBeDisabled()
    fireEvent.change(screen.getByTestId('crew-add-middle'), { target: { value: '' } })
    fireEvent.change(screen.getByTestId('crew-add-first'), { target: { value: 'Lea' } })
    fireEvent.change(screen.getByTestId('crew-add-last'), { target: { value: 'Ton' } })
    // A suggested role, clicked.
    fireEvent.click(screen.getAllByTestId('role-suggestion').find((b) => b.textContent?.includes('Sound'))!)
    fireEvent.change(screen.getByTestId('crew-add-email'), { target: { value: 'lea@ton.at' } })
    expect(screen.queryByTestId('crew-add-why')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('crew-add-save'))

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/crew', {
        first_name: 'Lea',
        middle_name: null,
        last_name: 'Ton',
        roles: ['Sound'],
        phone: null,
        email: 'lea@ton.at',
        notes: null,
      }),
    )
    expect(await screen.findByTestId('crew-add-done')).toHaveTextContent('Lea Ton was added.')
    expect(screen.getByTestId('crew-add-duplicates')).toHaveTextContent(
      'There is already a party called Lea Ton.',
    )
    // Emptied for the next person.
    expect(screen.getByTestId('crew-add-first')).toHaveValue('')
    expect(screen.getByTestId('crew-add-last')).toHaveValue('')
  })

  it('the import sends the preview digest and only the ticked duplicate lines', async () => {
    vi.mocked(api.upload).mockImplementation(async (path: string) => {
      if (path === '/crew/import/preview') {
        return {
          rows: [
            { line_number: 2, action: 'create', name: 'Anna Huber', roles: ['DoP'] },
            {
              line_number: 3,
              action: 'duplicate',
              name: 'Ben Ohr',
              roles: [],
              message: 'There is already a party called Ben Ohr.',
            },
            { line_number: 4, action: 'duplicate', name: 'Cleo', roles: [], message: 'Line 2 …' },
            { line_number: 5, action: 'error', name: null, roles: [], message: 'This line has no name.' },
          ],
          created: 1,
          duplicates: 2,
          errors: 1,
          digest: 'abc123',
          delimiter: ';',
          ignored_columns: [],
        }
      }
      return { rows: [], created: 2, skipped: 1, errors: 1, digest: 'abc123' }
    })
    render(<CrewListPage />)
    fireEvent.click(await screen.findByTestId('crew-import-open'))
    const file = new File(['name\nAnna'], 'crew.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByTestId('crew-import-file'), { target: { files: [file] } })

    expect(await screen.findByTestId('crew-import-counts')).toHaveTextContent(
      '1 new, 2 held back as possible duplicates, 1 cannot be read.',
    )
    const ticks = screen.getAllByTestId('crew-import-tick')
    expect(ticks).toHaveLength(2)
    expect(screen.getByTestId('crew-import-apply')).toHaveTextContent('Add 1 person')
    fireEvent.click(ticks[0])
    expect(screen.getByTestId('crew-import-apply')).toHaveTextContent('Add 2 people')
    fireEvent.click(screen.getByTestId('crew-import-apply'))

    await waitFor(() => expect(vi.mocked(api.upload)).toHaveBeenCalledTimes(2))
    const [path, form] = vi.mocked(api.upload).mock.calls[1]
    expect(path).toBe('/crew/import')
    expect((form as FormData).get('digest')).toBe('abc123')
    expect((form as FormData).get('include_lines')).toBe('3')
    expect(await screen.findByTestId('crew-import-counts')).toHaveTextContent(
      '2 added, 1 skipped, 1 could not be read.',
    )
  })

  it('says why nothing can be imported when every readable line is held back', async () => {
    vi.mocked(api.upload).mockResolvedValue({
      rows: [{ line_number: 2, action: 'duplicate', name: 'Ben Ohr', roles: [] }],
      created: 0,
      duplicates: 1,
      errors: 0,
      digest: 'd',
      delimiter: ';',
      ignored_columns: [],
    })
    render(<CrewListPage />)
    fireEvent.click(await screen.findByTestId('crew-import-open'))
    fireEvent.change(screen.getByTestId('crew-import-file'), {
      target: { files: [new File(['x'], 'crew.csv')] },
    })
    expect(await screen.findByTestId('crew-import-why')).toHaveTextContent(
      'Tick the ones you want added anyway.',
    )
    expect(screen.getByTestId('crew-import-apply')).toBeDisabled()
  })

  it('reads without editing for a role that may view parties but not edit them', async () => {
    seed(['parties.view'])
    render(<CrewListPage />)
    await screen.findAllByTestId('crew-row')
    expect(screen.getByTestId('crew-read-only')).toHaveTextContent('permission to edit parties')
    expect(screen.queryByTestId('crew-add-open')).not.toBeInTheDocument()
    expect(screen.queryByTestId('crew-import-open')).not.toBeInTheDocument()
  })

  it('tells somebody without the parties permission why the page is closed, and asks nothing', async () => {
    seed(['projects.view', 'devices.view'])
    render(<CrewListPage />)
    expect(screen.getByTestId('crew-no-access')).toHaveTextContent('crew list')
    expect(vi.mocked(api.get)).not.toHaveBeenCalled()
  })
})
