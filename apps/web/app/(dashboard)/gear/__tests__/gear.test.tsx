import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

const push = vi.fn()

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
  usePathname: () => '/gear',
  useParams: () => ({ id: 'd1' }),
  useRouter: () => ({ push }),
}))

import { api } from '@/lib/api'
import GearPage from '../page'
import DevicePage from '../[id]/page'
import { useCompanyStore } from '@/stores/company-store'

/**
 * The gear screens. What is under test is what they SEND — the quantity
 * question on retire and reactivate, ticked by default; a pasted list split
 * into serial numbers; a German price turned into the API's dot — and that
 * a role without `budgets.view` is shown no price and no way to edit.
 */

const OWNER = ['devices.view', 'devices.edit', 'budgets.view', 'catalog.view']
const STAFF = ['devices.view', 'projects.view', 'catalog.view']

function seed(permissions: string[]) {
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

const counts = {
  owned: 3,
  recorded: 2,
  active: 2,
  in_repair: 0,
  retired: 0,
  not_yet_recorded: 1,
  beyond_quantity: 0,
}

function device(overrides: Record<string, unknown> = {}) {
  return {
    id: 'd1',
    name: 'Alexa 35',
    category: 'Camera',
    quantity_owned: 3,
    purchase_price: '65000.00',
    price_visible: true,
    purchase_date: null,
    catalog_item_id: null,
    catalog_item_name: null,
    notes: null,
    is_active: true,
    counts,
    created_at: '2026-11-26T10:00:00Z',
    updated_at: '2026-11-26T10:00:00Z',
    units: [
      unit({ id: 'u1', serial_number: 'AX-1' }),
      unit({ id: 'u2', serial_number: 'AX-2' }),
    ],
    ...overrides,
  }
}

function unit(overrides: Record<string, unknown> = {}) {
  return {
    id: 'u1',
    device_id: 'd1',
    serial_number: 'AX-1',
    status: 'active',
    acquired_on: null,
    retired_on: null,
    retired_reason: null,
    notes: null,
    created_at: '2026-11-26T10:00:00Z',
    updated_at: '2026-11-26T10:00:00Z',
    ...overrides,
  }
}

function routeGets(detail = device()) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.startsWith('/devices/d1')) return detail
    if (path.startsWith('/devices')) {
      const { units: _units, ...row } = detail
      return { devices: [row], total: 1, categories: ['Camera', 'Light'] }
    }
    if (path.startsWith('/catalog/items')) return { items: [], total: 0, limit: 200, offset: 0 }
    throw new Error(`unexpected GET ${path}`)
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  seed(OWNER)
  routeGets()
})

describe('the gear list', () => {
  it('puts the API counts into words on every row, with the price for a role that may see it', async () => {
    render(<GearPage />)
    const row = (await screen.findAllByTestId('gear-row'))[0]
    expect(within(row).getByTestId('gear-counts')).toHaveTextContent(
      '3 owned · 2 serial numbers recorded · 1 not yet recorded',
    )
    expect(within(row).getByRole('link', { name: 'Alexa 35' })).toHaveAttribute('href', '/gear/d1')
    expect(row).toHaveTextContent('65,000.00')
  })

  it('shows staff the gear and the counts, no price column, and why there is nothing to edit', async () => {
    seed(STAFF)
    routeGets(device({ purchase_price: null, price_visible: false }))
    render(<GearPage />)
    const row = (await screen.findAllByTestId('gear-row'))[0]
    expect(within(row).getByTestId('gear-counts')).toHaveTextContent('3 owned')
    expect(screen.queryByText('Purchase price')).not.toBeInTheDocument()
    expect(screen.getByTestId('gear-read-only')).toHaveTextContent('gear edit permission')
    expect(screen.queryByTestId('gear-add-open')).not.toBeInTheDocument()
  })

  it('adding a device sends a German price as the API wants it, and refuses an ambiguous one', async () => {
    vi.mocked(api.post).mockResolvedValue(device({ id: 'new' }))
    render(<GearPage />)
    fireEvent.click(await screen.findByTestId('gear-add-open'))
    expect(screen.getByTestId('gear-add-why')).toHaveTextContent('Type a name')
    fireEvent.change(screen.getByTestId('gear-add-name'), { target: { value: 'Alexa 35' } })
    fireEvent.change(screen.getByTestId('gear-add-quantity'), { target: { value: '3' } })
    fireEvent.change(screen.getByTestId('gear-add-price'), { target: { value: '1.500' } })
    expect(screen.getByTestId('gear-add-save')).toBeDisabled()
    expect(screen.getByTestId('gear-add-why')).toHaveTextContent('1.500 could mean either')

    fireEvent.change(screen.getByTestId('gear-add-price'), { target: { value: '65.000,00' } })
    fireEvent.click(screen.getByTestId('gear-add-save'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/devices', {
        name: 'Alexa 35',
        category: null,
        quantity_owned: 3,
        purchase_price: '65000.00',
        purchase_date: null,
        notes: null,
      }),
    )
    await waitFor(() => expect(push).toHaveBeenCalledWith('/gear/new'))
  })

  it('tells somebody without devices.view why the page is closed', () => {
    seed(['parties.view'])
    render(<GearPage />)
    expect(screen.getByTestId('gear-no-access')).toHaveTextContent('gear list')
    expect(vi.mocked(api.get)).not.toHaveBeenCalled()
  })
})

describe('one device', () => {
  function changeResult(before: number, after: number, status = 'retired') {
    return {
      device: device({ quantity_owned: after }),
      unit: unit({ status }),
      quantity_before: before,
      quantity_after: after,
    }
  }

  it('retiring asks to reduce the quantity, ticked by default, and sends the answer', async () => {
    vi.mocked(api.post).mockResolvedValue(changeResult(3, 2))
    render(<DevicePage />)
    const rows = await screen.findAllByTestId('unit-row')
    fireEvent.click(within(rows[0]).getByTestId('unit-retire'))
    const reduce = screen.getByTestId('retire-reduce')
    expect(reduce).toBeChecked()
    expect(screen.getByTestId('retire-panel')).toHaveTextContent('from 3 to 2')
    fireEvent.change(screen.getByTestId('retire-reason'), { target: { value: 'destroyed' } })
    fireEvent.click(screen.getByTestId('retire-confirm'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/devices/d1/units/u1/retire', {
        reason: 'destroyed',
        retired_on: null,
        notes: null,
        reduce_quantity: true,
      }),
    )
    expect(await screen.findByTestId('device-notice')).toHaveTextContent('went from 3 to 2')
  })

  it('retiring with the box unticked says the quantity stays', async () => {
    vi.mocked(api.post).mockResolvedValue(changeResult(3, 3))
    render(<DevicePage />)
    const rows = await screen.findAllByTestId('unit-row')
    fireEvent.click(within(rows[1]).getByTestId('unit-retire'))
    fireEvent.click(screen.getByTestId('retire-reduce'))
    fireEvent.change(screen.getByTestId('retire-reason'), { target: { value: 'kept' } })
    fireEvent.click(screen.getByTestId('retire-confirm'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/devices/d1/units/u2/retire',
        expect.objectContaining({ reason: 'kept', reduce_quantity: false }),
      ),
    )
    expect(await screen.findByTestId('device-notice')).toHaveTextContent('The quantity owned stays 3.')
  })

  it('bringing a retired unit back asks to raise the quantity, ticked by default', async () => {
    routeGets(device({ units: [unit({ status: 'retired', retired_reason: 'lost', retired_on: '2026-11-01' })] }))
    vi.mocked(api.post).mockResolvedValue(changeResult(3, 4, 'active'))
    render(<DevicePage />)
    const row = (await screen.findAllByTestId('unit-row'))[0]
    expect(row).toHaveTextContent('Lost, 2026-11-01')
    fireEvent.click(within(row).getByTestId('unit-reactivate'))
    expect(screen.getByTestId('reactivate-raise')).toBeChecked()
    fireEvent.click(screen.getByTestId('reactivate-confirm'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/devices/d1/units/u1/reactivate', {
        raise_quantity: true,
      }),
    )
  })

  it('a pasted list becomes one serial number per line, and what was skipped is listed', async () => {
    vi.mocked(api.post).mockResolvedValue({
      device: device(),
      created: [unit({ id: 'u3', serial_number: 'AX-3' })],
      skipped: [{ serial_number: 'ax-1', reason: 'already_recorded', detail: 'AX-1 is already recorded on Alexa 35.' }],
    })
    render(<DevicePage />)
    await screen.findAllByTestId('unit-row')
    fireEvent.change(screen.getByTestId('add-serials-text'), { target: { value: 'AX-3\n\n ax-1 \r\n' } })
    expect(screen.getByTestId('add-serials-save')).toHaveTextContent('Add 2 serial numbers')
    fireEvent.click(screen.getByTestId('add-serials-save'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/devices/d1/units', {
        serial_numbers: ['AX-3', 'ax-1'],
        acquired_on: null,
      }),
    )
    expect(await screen.findByTestId('add-serials-skipped')).toHaveTextContent(
      'AX-1 is already recorded on Alexa 35.',
    )
    expect(screen.getByTestId('device-notice')).toHaveTextContent('The quantity owned is still 3.')
  })

  it('setting the quantity by hand sends only what changed', async () => {
    vi.mocked(api.patch).mockResolvedValue(device({ quantity_owned: 5 }))
    render(<DevicePage />)
    fireEvent.change(await screen.findByTestId('device-quantity'), { target: { value: '5' } })
    fireEvent.click(screen.getByTestId('device-save'))
    await waitFor(() =>
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith('/devices/d1', { quantity_owned: 5 }),
    )
  })

  it('staff read the device without a price, a form or unit actions', async () => {
    seed(STAFF)
    routeGets(device({ purchase_price: null, price_visible: false }))
    render(<DevicePage />)
    expect(await screen.findByTestId('device-counts')).toHaveTextContent('3 owned')
    expect(screen.queryByTestId('device-form')).not.toBeInTheDocument()
    expect(screen.queryByText('Purchase price')).not.toBeInTheDocument()
    expect(screen.queryByTestId('unit-retire')).not.toBeInTheDocument()
    expect(screen.queryByTestId('add-serials')).not.toBeInTheDocument()
    expect(screen.getByTestId('gear-read-only')).toBeInTheDocument()
  })
})
