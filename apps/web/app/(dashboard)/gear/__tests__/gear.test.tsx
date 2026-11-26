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

const OWNER = ['devices.view', 'devices.edit', 'budgets.view', 'catalog.view', 'parties.view']
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
  needs_check: 0,
  in_repair: 0,
  in_hand: 2,
  lost: 0,
  retired: 0,
  sold: 0,
  removed: 0,
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
    purchase_visible: true,
    purchase_price: null,
    seller_party_id: null,
    seller_party_name: null,
    seller_name: null,
    purchase_reference: null,
    removed_on: null,
    removed_reason: null,
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
    if (path.startsWith('/parties')) {
      return { parties: [{ id: 's1', name: 'ARRI Rental Wien' }], total: 1, limit: 200, offset: 0 }
    }
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

  it('removing needs what happened in words, asks to reduce the quantity (ticked) and sends both', async () => {
    vi.mocked(api.post).mockResolvedValue(changeResult(3, 2))
    render(<DevicePage />)
    const rows = await screen.findAllByTestId('unit-row')
    fireEvent.click(within(rows[0]).getByTestId('unit-remove'))
    expect(screen.getByTestId('remove-reduce')).toBeChecked()
    expect(screen.getByTestId('remove-panel')).toHaveTextContent('from 3 to 2')
    // No reason, no removal — and the screen says why.
    expect(screen.getByTestId('remove-confirm')).toBeDisabled()
    expect(screen.getByTestId('remove-why')).toHaveTextContent('Say what happened')
    fireEvent.click(screen.getByTestId('remove-status-lost'))
    fireEvent.change(screen.getByTestId('remove-reason'), { target: { value: 'Left in a taxi in Kaprun' } })
    fireEvent.click(screen.getByTestId('remove-confirm'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/devices/d1/units/u1/remove', {
        status: 'lost',
        reason: 'Left in a taxi in Kaprun',
        removed_on: null,
        reduce_quantity: true,
      }),
    )
    expect(await screen.findByTestId('device-notice')).toHaveTextContent('went from 3 to 2')
  })

  it('removing with the box unticked says the quantity stays', async () => {
    vi.mocked(api.post).mockResolvedValue(changeResult(3, 3))
    render(<DevicePage />)
    const rows = await screen.findAllByTestId('unit-row')
    fireEvent.click(within(rows[1]).getByTestId('unit-remove'))
    fireEvent.click(screen.getByTestId('remove-reduce'))
    fireEvent.change(screen.getByTestId('remove-reason'), { target: { value: 'Kept as a museum piece' } })
    fireEvent.click(screen.getByTestId('remove-confirm'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/devices/d1/units/u2/remove',
        expect.objectContaining({ status: 'retired', reduce_quantity: false }),
      ),
    )
    expect(await screen.findByTestId('device-notice')).toHaveTextContent('The quantity owned stays 3.')
  })

  it('a removed unit shows what happened, and bringing it back asks to raise the quantity (ticked)', async () => {
    routeGets(
      device({
        units: [unit({ status: 'sold', removed_on: '2026-11-01', removed_reason: 'Sold to Studio X' })],
      }),
    )
    vi.mocked(api.post).mockResolvedValue(changeResult(3, 4, 'active'))
    render(<DevicePage />)
    const row = (await screen.findAllByTestId('unit-row'))[0]
    expect(within(row).getByTestId('unit-removed-reason')).toHaveTextContent('2026-11-01: Sold to Studio X')
    fireEvent.click(within(row).getByTestId('unit-reactivate'))
    expect(screen.getByTestId('reactivate-raise')).toBeChecked()
    fireEvent.click(screen.getByTestId('reactivate-confirm'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/devices/d1/units/u1/reactivate', {
        raise_quantity: true,
      }),
    )
  })

  it('needs check and in repair are one click and never ask about the quantity', async () => {
    vi.mocked(api.patch).mockResolvedValue(changeResult(3, 3, 'needs_check'))
    render(<DevicePage />)
    const rows = await screen.findAllByTestId('unit-row')
    fireEvent.click(within(rows[0]).getByTestId('unit-needs-check'))
    await waitFor(() =>
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith('/devices/d1/units/u1', { status: 'needs_check' }),
    )
  })

  it('a pasted list can carry one purchase: price, seller on file and invoice number', async () => {
    vi.mocked(api.post).mockResolvedValue({ device: device(), created: [unit({ id: 'u3' })], skipped: [] })
    render(<DevicePage />)
    await screen.findAllByTestId('unit-row')
    fireEvent.change(screen.getByTestId('add-serials-text'), { target: { value: 'AX-3\nAX-4' } })
    fireEvent.change(screen.getByTestId('purchase-price'), { target: { value: '61.750,00' } })
    await waitFor(() =>
      expect(within(screen.getByTestId('purchase-seller-party')).getByText('ARRI Rental Wien')).toBeInTheDocument(),
    )
    fireEvent.change(screen.getByTestId('purchase-seller-party'), { target: { value: 's1' } })
    expect(screen.queryByTestId('purchase-seller-name')).not.toBeInTheDocument()
    fireEvent.change(screen.getByTestId('purchase-reference'), { target: { value: 'RE-2026-0311' } })
    fireEvent.click(screen.getByTestId('add-serials-save'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/devices/d1/units', {
        serial_numbers: ['AX-3', 'AX-4'],
        acquired_on: null,
        purchase_price: '61750.00',
        seller_party_id: 's1',
        seller_name: null,
        purchase_reference: 'RE-2026-0311',
      }),
    )
  })

  it('the purchase of one unit can be corrected with a seller who is not on file', async () => {
    vi.mocked(api.patch).mockResolvedValue(changeResult(3, 3, 'active'))
    render(<DevicePage />)
    const rows = await screen.findAllByTestId('unit-row')
    expect(rows[0]).toHaveTextContent('as device')
    fireEvent.click(within(rows[0]).getByTestId('unit-purchase'))
    const panel = screen.getByTestId('purchase-panel')
    fireEvent.change(within(panel).getByTestId('purchase-seller-name'), { target: { value: 'Ebay, cam_shop_graz' } })
    fireEvent.click(within(panel).getByTestId('purchase-save'))
    await waitFor(() =>
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith('/devices/d1/units/u1', {
        purchase_price: null,
        seller_party_id: null,
        seller_name: 'Ebay, cam_shop_graz',
        purchase_reference: null,
        acquired_on: null,
      }),
    )
  })

  it('a pasted list becomes one serial number per line, and what was skipped is listed', async () => {
    seed(['devices.view', 'devices.edit'])
    routeGets(device({ purchase_price: null, price_visible: false }))
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
      // Without budgets.view no purchase fields are offered or sent.
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
    routeGets(
      device({
        purchase_price: null,
        price_visible: false,
        units: [unit({ purchase_visible: false })],
      }),
    )
    render(<DevicePage />)
    expect(await screen.findByTestId('device-counts')).toHaveTextContent('3 owned')
    expect(screen.queryByTestId('device-form')).not.toBeInTheDocument()
    expect(screen.queryByText('Purchase price')).not.toBeInTheDocument()
    expect(screen.queryByText('Seller')).not.toBeInTheDocument()
    expect(screen.queryByTestId('unit-remove')).not.toBeInTheDocument()
    expect(screen.queryByTestId('add-serials')).not.toBeInTheDocument()
    expect(screen.getByTestId('gear-read-only')).toBeInTheDocument()
  })
})
