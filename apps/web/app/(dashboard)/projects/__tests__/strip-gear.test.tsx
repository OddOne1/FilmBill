import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
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
vi.mock('next/navigation', () => ({ usePathname: () => '/projects/p1', useParams: () => ({ id: 'p1' }) }))

import { api } from '@/lib/api'
import { datesBetween, monthGridFor } from '@/lib/calendar-dates'
import { StripGear } from '../[id]/calendar/strip-gear'
import { MonthGrid } from '../[id]/calendar/month-grid'
import { GearTab } from '../[id]/gear-tab'
import { GearPlanner } from '../../gear/gear-planner'
import { BookingsTab } from '../../gear/[id]/bookings-tab'
import type {
  CalendarStrip,
  DeviceAvailability,
  GearBooking,
  GearBookingsPreview,
  GearDay,
  GearWarning,
  ProjectGearStrip,
} from '@/types'

/**
 * Gear on the calendar (B2c-2 part 3).
 *
 * Rendered with the API's own shapes. Under test is what the screens SEND —
 * the device and quantity, the ticked `ask` dependencies with their
 * quantities, a swap, a "book anyway" note, the answer to "what about the
 * batteries?" — and which of the API's numbers they draw. Availability,
 * shortages, counts and weights are never worked out on the page.
 */

const SHOOT: CalendarStrip = {
  id: 'b1',
  kind: 'strip',
  type: 'shoot',
  label: 'Shoot',
  start_date: '2026-07-06',
  end_date: '2026-07-08',
  location: null,
  default_call_time: null,
  default_wrap_time: null,
  notes: null,
  position: null,
  is_active: true,
  hotels_visible: false,
  counts: { shoot: 3 },
  days: datesBetween('2026-07-06', '2026-07-08').map((date) => ({
    id: `d-${date}`,
    block_id: 'b1',
    date,
    type: 'shoot',
    location: null,
    call_time: null,
    wrap_time: null,
    notes: null,
    effective_location: null,
    effective_call_time: null,
    effective_wrap_time: null,
    is_override: false,
    has_own_data: false,
    updated_at: '2026-11-27T10:00:00Z',
  })),
}

const HOLDER = {
  booking_id: 'other',
  project_id: 'p2',
  project_name: 'Doku',
  project_number: 'PRJ-2',
  block_id: 'b2',
  strip_name: 'Dreh',
  status: 'booked' as const,
  quantity: 2,
  dates: ['2026-07-07', '2026-07-08'],
}

const WARNING: GearWarning = {
  kind: 'overbooked',
  booking_id: 'g1',
  device_id: 'camA',
  device_name: 'Camera A',
  owned: 3,
  quantity: 2,
  shortage: 1,
  worst_day: '2026-07-07',
  dates: ['2026-07-07', '2026-07-08'],
  message:
    'Camera A is overbooked by 1 on 7 July 2026 – 8 July 2026: 3 owned, 2 wanted here. Doku (strip “Dreh”, booked 2) holds it there. Swap to an alternative, or book anyway with a note.',
  held_by: [HOLDER],
  alternatives: [
    {
      alternative_id: 'alt1',
      device_id: 'camB',
      name: 'Camera B',
      thumbnail_url: null,
      position: 0,
      note: 'needs a different mount',
      price_hint: 'about +150 per day',
      owned: 1,
      min_available: 1,
      worst_day: '2026-07-06',
      enough: false,
    },
  ],
}

function booking(overrides: Partial<GearBooking> = {}): GearBooking {
  return {
    id: 'g1',
    project_id: 'p1',
    project_name: '1000m',
    project_number: 'PRJ-1000',
    block_id: 'b1',
    strip_name: 'Shoot',
    device_id: 'camA',
    device_name: 'Camera A',
    category: 'Camera',
    thumbnail_url: null,
    quantity: 2,
    status: 'scheduled',
    status_note: null,
    booked_on: null,
    booked_by_name: null,
    added_via_booking_id: null,
    added_via_device_name: null,
    association_id: null,
    quantity_per_parent: null,
    follows_parent: false,
    overbooking_note: null,
    removal_note: null,
    weight_kg: '2.500',
    dates: ['2026-07-06', '2026-07-07', '2026-07-08'],
    warning: null,
    created_at: '2026-11-27T10:00:00Z',
    ...overrides,
  }
}

const BATTERY = booking({
  id: 'g2',
  device_id: 'bat',
  device_name: 'Battery',
  quantity: 4,
  added_via_booking_id: 'g1',
  added_via_device_name: 'Camera A',
  association_id: 'as-bat',
  quantity_per_parent: 2,
  follows_parent: true,
  weight_kg: '0.400',
})

function gearStrip(bookings: GearBooking[]): ProjectGearStrip {
  return {
    block_id: 'b1',
    strip_name: 'Shoot',
    type: 'shoot',
    start_date: '2026-07-06',
    end_date: '2026-07-08',
    // The API's counts, deliberately not what the rows would add up to, so
    // a page that counted for itself would show something else.
    counts: { scheduled: 6, booked: 0, overbooked: bookings.filter((b) => b.warning).length },
    total_weight_kg: '6.600',
    items_without_weight: 2,
    bookings,
  }
}

function day(date: string, numbers: Partial<GearDay>): GearDay {
  return { date, owned: 3, scheduled: 0, booked: 0, available: 3, overbooked: false, shortage: 0, holders: [], ...numbers }
}

/** Camera A over 6–8 July: 3 free on the 6th, 1 on the 7th, 2 on the 8th.
 *  The lowest day governs: 1 available, 1 booked, 1 scheduled. */
const CAMERA_A: DeviceAvailability = {
  device_id: 'camA',
  name: 'Camera A',
  category: 'Camera',
  thumbnail_url: null,
  owned: 3,
  min_available: 1,
  worst_day: '2026-07-07',
  scheduled: 1,
  booked: 1,
  overbooked: false,
  shortage: 0,
  days: [
    day('2026-07-06', {}),
    day('2026-07-07', { booked: 1, scheduled: 1, available: 1, holders: [{ ...HOLDER, quantity: 1, dates: ['2026-07-07'] }] }),
    day('2026-07-08', { booked: 1, available: 2 }),
  ],
}
const LIGHT: DeviceAvailability = {
  device_id: 'light',
  name: 'Light',
  category: 'Light',
  thumbnail_url: null,
  owned: 2,
  min_available: -1,
  worst_day: '2026-07-06',
  scheduled: 1,
  booked: 2,
  overbooked: true,
  shortage: 1,
  days: [
    day('2026-07-06', { owned: 2, booked: 2, scheduled: 1, available: -1, overbooked: true, shortage: 1, holders: [HOLDER] }),
    day('2026-07-07', { owned: 2, booked: 2, available: 0 }),
    day('2026-07-08', { owned: 2, booked: 1, available: 1 }),
  ],
}

function routeGets(devices: DeviceAvailability[] = [CAMERA_A, LIGHT]) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.startsWith('/gear/availability')) return { range_start: '2026-07-06', range_end: '2026-07-08', devices }
    throw new Error(`unexpected GET ${path}`)
  })
}

function renderGear(bookings: GearBooking[], { canEdit = true } = {}) {
  const onChanged = vi.fn()
  render(<StripGear projectId="p1" strip={SHOOT} gear={gearStrip(bookings)} canEdit={canEdit} onChanged={onChanged} />)
  return onChanged
}

const NO_SUGGESTIONS: GearBookingsPreview = {
  bookings: [
    { device_id: 'camA', device_name: 'Camera A', thumbnail_url: null, quantity: 2, via: 'direct', parent_device_id: null, association_id: null, dates: [], owned: 3, min_available: 1, available_after: -1 },
  ],
  suggestions: [],
}
const WITH_FILTER: GearBookingsPreview = {
  bookings: [
    ...NO_SUGGESTIONS.bookings,
    { device_id: 'bat', device_name: 'Battery', thumbnail_url: null, quantity: 4, via: 'always', parent_device_id: 'camA', association_id: 'as-bat', dates: [], owned: 6, min_available: 6, available_after: 2 },
  ],
  suggestions: [
    { parent_device_id: 'camA', parent_device_name: 'Camera A', association_id: 'as-filter', device_id: 'filter', device_name: 'Filter', thumbnail_url: null, quantity: 2, quantity_per_parent: 1, note: null },
  ],
}

async function pickCamera(quantity = '2') {
  fireEvent.click(screen.getByTestId('strip-gear-add'))
  await screen.findAllByTestId('gear-picker-row')
  fireEvent.click(screen.getByRole('radio', { name: 'Pick Camera A' }))
  fireEvent.change(screen.getByTestId('gear-picker-quantity'), { target: { value: quantity } })
  fireEvent.click(screen.getByTestId('gear-picker-add'))
}

describe('the strip panel: Gear', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets()
  })

  it('lists the bookings with quantity, status chip, the parent and an overbooked marker with the API sentence', () => {
    renderGear([booking({ warning: WARNING }), BATTERY])
    const rows = screen.getAllByTestId('strip-gear-row')
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining('2 × Camera A'),
      expect.stringContaining('4 × Battery · with Camera A'),
    ])
    expect(within(rows[0]).getByTestId('strip-gear-overbooked')).toHaveAttribute('title', WARNING.message)
    expect(within(rows[1]).queryByTestId('strip-gear-overbooked')).toBeNull()
    expect(screen.getByTestId('strip-gear-counts')).toHaveTextContent('6 scheduled')
  })

  it('the picker shows owned · available · booked · scheduled as the API counted them, with the days on hover', async () => {
    renderGear([])
    fireEvent.click(screen.getByTestId('strip-gear-add'))
    const rows = await screen.findAllByTestId('gear-picker-row')
    const camera = within(rows[0]).getByTestId('gear-picker-availability')
    expect(camera).toHaveTextContent('3 owned · 1 available · 1 booked · 1 scheduled')
    expect(camera.getAttribute('title')).toBe(
      [
        'M 6 Jul: 3 available (0 booked, 0 scheduled)',
        'T 7 Jul: 1 available (1 booked, 1 scheduled)',
        'W 8 Jul: 2 available (1 booked, 0 scheduled)',
      ].join('\n'),
    )
    const light = within(rows[1]).getByTestId('gear-picker-availability')
    expect(light).toHaveTextContent('2 owned · -1 available · 2 booked · 1 scheduled')
    expect(light).toHaveAttribute('data-overbooked', 'true')
    expect(api.get).toHaveBeenCalledWith('/gear/availability?from=2026-07-06&to=2026-07-08')
    fireEvent.change(screen.getByTestId('gear-picker-search'), { target: { value: 'cam' } })
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/gear/availability?from=2026-07-06&to=2026-07-08&q=cam'))
  })

  it('the Add button is dead until a device is picked, and says so', async () => {
    renderGear([])
    fireEvent.click(screen.getByTestId('strip-gear-add'))
    await screen.findAllByTestId('gear-picker-row')
    expect(screen.getByTestId('gear-picker-add')).toBeDisabled()
    expect(screen.getByText('Pick a device to add. It starts as scheduled.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Pick Camera A' }))
    fireEvent.change(screen.getByTestId('gear-picker-quantity'), { target: { value: '0' } })
    expect(screen.getByTestId('gear-picker-add')).toBeDisabled()
    expect(screen.getByText('Type a whole number of at least 1.')).toBeInTheDocument()
  })

  it('without ask dependencies, a pick is added at once and the notice names what came with it', async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/preview')) return NO_SUGGESTIONS
      return { bookings: [booking(), BATTERY], warnings: [], suggestions: [] }
    })
    const onChanged = renderGear([])
    await pickCamera()
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b1/gear', {
        bookings: [{ device_id: 'camA', quantity: 2, chosen_dependencies: [] }],
      }),
    )
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(screen.getByTestId('strip-gear-notice')).toHaveTextContent('2 × Camera A added as scheduled, with 4 × Battery.')
    expect(screen.queryByTestId('gear-dependencies-dialog')).toBeNull()
  })

  it('the dependency popup lists the ask ones unticked with their quantities and the always ones as added automatically', async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/preview')) return WITH_FILTER
      return { bookings: [booking(), BATTERY], warnings: [], suggestions: [] }
    })
    renderGear([])
    await pickCamera()
    const dialog = await screen.findByTestId('gear-dependencies-dialog')
    expect(within(dialog).getByText('2 × Camera A: also book?')).toBeInTheDocument()
    expect(within(dialog).getByTestId('gear-dependency-always')).toHaveTextContent('Added automatically: 4 × Battery.')
    const filter = within(dialog).getByRole('checkbox', { name: 'Also book Filter' })
    expect(filter).not.toBeChecked()
    expect(within(dialog).getByTestId('gear-dependency-quantity')).toHaveValue('2')
    expect(api.post).toHaveBeenCalledTimes(1) // only the preview so far
    fireEvent.click(filter)
    fireEvent.change(within(dialog).getByTestId('gear-dependency-quantity'), { target: { value: '1' } })
    fireEvent.click(within(dialog).getByTestId('gear-dependencies-confirm'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b1/gear', {
        bookings: [{ device_id: 'camA', quantity: 2, chosen_dependencies: [{ association_id: 'as-filter', quantity: 1 }] }],
      }),
    )
  })

  it('an ask dependency left unticked is not sent', async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/preview')) return WITH_FILTER
      return { bookings: [booking(), BATTERY], warnings: [], suggestions: [] }
    })
    renderGear([])
    await pickCamera()
    fireEvent.click(within(await screen.findByTestId('gear-dependencies-dialog')).getByTestId('gear-dependencies-confirm'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b1/gear', {
        bookings: [{ device_id: 'camA', quantity: 2, chosen_dependencies: [] }],
      }),
    )
  })

  it('a save that comes back short opens the overbooking popup: who holds it, the alternatives, and Swap', async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/preview')) return NO_SUGGESTIONS
      return { bookings: [booking({ warning: WARNING })], warnings: [WARNING], suggestions: [] }
    })
    vi.mocked(api.patch).mockResolvedValue({ booking: booking({ device_id: 'camB', device_name: 'Camera B' }), warnings: [], recalculated: [], stopped_following: false, message: null })
    renderGear([])
    await pickCamera()
    const dialog = await screen.findByTestId('gear-overbooking-dialog')
    expect(within(dialog).getByTestId('gear-overbooking-shortage')).toHaveTextContent('Camera A: short by 1 (3 owned, 2 wanted)')
    expect(within(dialog).getByText(WARNING.message)).toBeInTheDocument()
    expect(within(dialog).getByTestId('gear-overbooking-held-by')).toHaveTextContent('Doku · strip “Dreh” · 7 Jul, 8 Jul · booked 2')
    const alternative = within(dialog).getByTestId('gear-overbooking-alternative')
    expect(within(alternative).getByTestId('gear-overbooking-alternative-availability')).toHaveTextContent('1 owned · 1 available on these days')
    expect(alternative).toHaveTextContent('needs a different mount · about +150 per day')
    fireEvent.click(within(alternative).getByTestId('gear-overbooking-swap'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/gear/g1', { device_id: 'camB' }))
    await waitFor(() => expect(screen.queryByTestId('gear-overbooking-dialog')).toBeNull())
    expect(screen.getByTestId('strip-gear-notice')).toHaveTextContent('Swapped Camera A for Camera B.')
  })

  it('Book anyway keeps the booking and records the note', async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/preview')) return NO_SUGGESTIONS
      return { bookings: [booking({ warning: WARNING })], warnings: [WARNING], suggestions: [] }
    })
    vi.mocked(api.patch).mockResolvedValue({ booking: booking({ warning: WARNING, overbooking_note: 'renting in' }), warnings: [WARNING], recalculated: [], stopped_following: false, message: null })
    renderGear([])
    await pickCamera()
    const dialog = await screen.findByTestId('gear-overbooking-dialog')
    fireEvent.change(within(dialog).getByTestId('gear-overbooking-note'), { target: { value: 'renting in' } })
    fireEvent.click(within(dialog).getByTestId('gear-overbooking-book-anyway'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/gear/g1', { overbooking_note: 'renting in' }))
    await waitFor(() => expect(screen.queryByTestId('gear-overbooking-dialog')).toBeNull())
    expect(screen.getByTestId('strip-gear-notice')).toHaveTextContent('Camera A is booked although it is short by 1.')
  })

  it('Cancel in the popup removes the booking with its dependencies', async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith('/preview')) return NO_SUGGESTIONS
      if (path.endsWith('/remove')) return { removed: true, booking: booking({ status: 'cancelled' }), dependents: [], dependents_choice: null, question: null }
      return { bookings: [booking({ warning: WARNING })], warnings: [WARNING], suggestions: [] }
    })
    renderGear([])
    await pickCamera()
    fireEvent.click(within(await screen.findByTestId('gear-overbooking-dialog')).getByTestId('gear-overbooking-cancel'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/gear/g1/remove', { note: 'Not booked: overbooked', dependents: 'cancel' }),
    )
  })

  it('removing a parent asks what happens to its dependents, and sends the answer', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({
      removed: false,
      booking: booking(),
      dependents: [BATTERY],
      dependents_choice: null,
      question: 'Camera A was booked with 4 × Battery. Cancel them too, or keep them on the strip as bookings of their own?',
    })
    vi.mocked(api.post).mockResolvedValueOnce({ removed: true, booking: booking({ status: 'cancelled' }), dependents: [BATTERY], dependents_choice: 'cancel', question: null })
    renderGear([booking(), BATTERY])
    fireEvent.click(screen.getByRole('button', { name: /2 × Camera A/ }))
    fireEvent.change(screen.getByTestId('strip-gear-note'), { target: { value: 'shoot cut' } })
    fireEvent.click(screen.getByTestId('strip-gear-remove'))
    const question = await screen.findByTestId('strip-gear-dependents-question')
    expect(question).toHaveTextContent('Camera A was booked with 4 × Battery. Cancel them too, or keep them')
    expect(api.post).toHaveBeenLastCalledWith('/projects/p1/gear/g1/remove', { note: 'shoot cut' })
    fireEvent.click(within(question).getByTestId('strip-gear-dependents-cancel'))
    await waitFor(() => expect(api.post).toHaveBeenLastCalledWith('/projects/p1/gear/g1/remove', { note: 'shoot cut', dependents: 'cancel' }))
    await waitFor(() => expect(screen.queryByTestId('strip-gear-dependents-question')).toBeNull())
  })

  it('Mark booked sends the note, and Mark selected as booked sends the ticked ones', async () => {
    vi.mocked(api.post).mockResolvedValue(booking({ status: 'booked' }))
    renderGear([booking(), BATTERY])
    fireEvent.click(screen.getByRole('button', { name: /2 × Camera A/ }))
    fireEvent.change(screen.getByTestId('strip-gear-note'), { target: { value: 'by phone' } })
    fireEvent.click(screen.getByTestId('strip-gear-book'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects/p1/gear/g1/status', { status: 'booked', note: 'by phone' }))

    vi.mocked(api.post).mockResolvedValue({ changed: 2, skipped: [], bookings: [] })
    expect(screen.getByTestId('strip-gear-bulk-book')).toBeDisabled()
    expect(screen.getByText('Tick the scheduled gear that is confirmed.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Camera A' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Battery' }))
    fireEvent.change(screen.getByTestId('strip-gear-bulk-note'), { target: { value: 'by mail' } })
    fireEvent.click(screen.getByTestId('strip-gear-bulk-book'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/gear/status', { booking_ids: ['g1', 'g2'], status: 'booked', note: 'by mail' }),
    )
    expect(await screen.findByTestId('strip-gear-notice')).toHaveTextContent('2 marked booked.')
  })

  it('a dependent changed by hand shows the API sentence that it no longer follows', async () => {
    vi.mocked(api.patch).mockResolvedValue({
      booking: { ...BATTERY, quantity: 5, follows_parent: false },
      warnings: [],
      recalculated: [],
      stopped_following: true,
      message: 'Battery no longer follows Camera A: its quantity and days stay as you set them.',
    })
    renderGear([booking(), BATTERY])
    fireEvent.click(screen.getByRole('button', { name: /4 × Battery/ }))
    expect(screen.getByText('Added with Camera A — 2 per Camera A, follows its quantity.')).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('strip-gear-quantity-input'), { target: { value: '5' } })
    fireEvent.click(screen.getByTestId('strip-gear-save-quantity'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/gear/g2', { quantity: 5 }))
    expect(await screen.findByTestId('strip-gear-notice')).toHaveTextContent(
      'Battery no longer follows Camera A: its quantity and days stay as you set them.',
    )
  })

  it('is read-only without projects.edit and says why', () => {
    renderGear([booking(), BATTERY], { canEdit: false })
    expect(screen.getByTestId('strip-gear-read-only')).toHaveTextContent('needs the project edit permission')
    expect(screen.queryByTestId('strip-gear-add')).toBeNull()
    expect(screen.queryByRole('checkbox')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /2 × Camera A/ }))
    expect(screen.queryByTestId('strip-gear-book')).toBeNull()
    expect(screen.queryByTestId('strip-gear-save-days')).toBeNull()
    expect(screen.queryByTestId('strip-gear-remove')).toBeNull()
  })
})

describe('the strip badge on the calendar', () => {
  it('reads "Gear: 6 booked · 2 scheduled" with a marker when something is overbooked', () => {
    render(
      <MonthGrid
        month="2026-07"
        weeks={monthGridFor('2026-07')}
        strips={[SHOOT]}
        today="2026-07-01"
        projectStart="2026-07-01"
        projectEnd={null}
        canEdit={false}
        selectedStripId={null}
        pick={null}
        onPickRange={() => {}}
        onPickDay={() => {}}
        onOpenStrip={() => {}}
        onMove={() => {}}
        onResize={() => {}}
        onShowDay={() => {}}
        gearCounts={{ b1: { booked: 6, scheduled: 2, overbooked: 1 } }}
      />,
    )
    expect(screen.getByTestId('strip-gear-badge')).toHaveTextContent('Gear: 6 booked · 2 scheduled')
    expect(screen.getByTestId('strip-gear-overbooked-marker')).toBeInTheDocument()
  })

  it('has no gear badge on a strip with no gear', () => {
    render(
      <MonthGrid
        month="2026-07"
        weeks={monthGridFor('2026-07')}
        strips={[SHOOT]}
        today="2026-07-01"
        projectStart="2026-07-01"
        projectEnd={null}
        canEdit={false}
        selectedStripId={null}
        pick={null}
        onPickRange={() => {}}
        onPickDay={() => {}}
        onOpenStrip={() => {}}
        onMove={() => {}}
        onResize={() => {}}
        onShowDay={() => {}}
        gearCounts={{ b1: { booked: 0, scheduled: 0, overbooked: 0 } }}
      />,
    )
    expect(screen.queryByTestId('strip-gear-badge')).toBeNull()
    expect(screen.queryByTestId('strip-gear-overbooked-marker')).toBeNull()
  })
})

describe('Gear → Planner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets()
  })

  it('colours each cell from the API day, overbooked in red, and a click lists who holds it', async () => {
    render(<GearPlanner categories={['Camera', 'Light']} />)
    const rows = await screen.findAllByTestId('gear-planner-row')
    const camera = within(rows[0]).getAllByTestId('gear-planner-cell')
    expect(camera.map((cell) => cell.getAttribute('data-state'))).toEqual(['free', 'used', 'used'])
    expect(camera.map((cell) => cell.textContent)).toEqual(['0/3', '1+1/3', '1/3'])
    const light = within(rows[1]).getAllByTestId('gear-planner-cell')
    expect(light.map((cell) => cell.getAttribute('data-state'))).toEqual(['overbooked', 'full', 'used'])
    expect(light[0].className).toContain('bg-status-error')
    fireEvent.click(light[0])
    const detail = screen.getByTestId('gear-planner-detail')
    expect(detail).toHaveTextContent('2 booked, 1 scheduled, -1 available of 2 — short by 1')
    expect(detail).toHaveTextContent('Doku · strip “Dreh” · booked 2')
  })

  it('sends the category', async () => {
    render(<GearPlanner categories={['Camera', 'Light']} />)
    await screen.findAllByTestId('gear-planner-row')
    fireEvent.change(screen.getByTestId('gear-planner-from'), { target: { value: '2026-07-06' } })
    fireEvent.change(screen.getByTestId('gear-planner-category'), { target: { value: 'Light' } })
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/gear/availability?from=2026-07-06&to=2026-07-12&category=Light'))
  })
})

describe('Project → Gear and the device Bookings tab', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows each strip with its total weight and the items without weight, as the API added them', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/projects/p1/gear')
        return { project_id: 'p1', counts: { scheduled: 6, booked: 0, overbooked: 0 }, total_weight_kg: '6.600', items_without_weight: 2, strips: [gearStrip([booking(), BATTERY])] }
      if (path === '/projects/p1/calendar') return { strips: [SHOOT] }
      throw new Error(`unexpected GET ${path}`)
    })
    const onOpenStrip = vi.fn()
    render(<GearTab project={{ id: 'p1' } as never} canEdit onOpenStrip={onOpenStrip} />)
    expect(await screen.findByTestId('project-gear-total')).toHaveTextContent('Total weight 6.600 kg · 2 items without weight')
    expect(screen.getByTestId('project-gear-weight')).toHaveTextContent('6.600 kg · 2 items without weight')
    expect(screen.getAllByTestId('strip-gear-row')).toHaveLength(2)
    fireEvent.click(screen.getByTestId('project-gear-open'))
    expect(onOpenStrip).toHaveBeenCalledWith('b1')
  })

  it('the device page lists its bookings in the order the API gave', async () => {
    vi.mocked(api.get).mockResolvedValue({
      device_id: 'camA',
      bookings: [booking(), booking({ id: 'g9', project_name: 'Doku', strip_name: 'Dreh', status: 'booked', dates: ['2026-07-11'] })],
    })
    render(<BookingsTab device={{ id: 'camA', name: 'Camera A' } as never} />)
    const rows = await screen.findAllByTestId('device-booking-row')
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining('1000m'),
      expect.stringContaining('Doku'),
    ])
    expect(rows[0]).toHaveTextContent('6 Jul – 8 July 2026 (3 days)')
    expect(within(rows[1]).getByTestId('device-booking-status')).toHaveTextContent('booked')
    expect(api.get).toHaveBeenCalledWith('/devices/camA/bookings')
  })
})
