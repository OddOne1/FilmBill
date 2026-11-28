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
vi.mock('next/navigation', () => ({ usePathname: () => '/locations', useParams: () => ({}) }))

import { api, ApiError } from '@/lib/api'
import { datesBetween } from '@/lib/calendar-dates'
import { useCompanyStore } from '@/stores/company-store'
import LocationsPage from '../page'
import { LocationForm } from '../location-form'
import { StripLocationSection, DayLocation, StripHotels } from '../../projects/[id]/calendar/strip-locations'
import type { StripActions } from '../../projects/[id]/calendar/strip-panel'
import { StripCrew } from '../../projects/[id]/calendar/strip-crew'
import type {
  BlockChange,
  CalendarStrip,
  CoordinatesParsed,
  CrewAssignment,
  Location,
  LocationList,
  LocationRef,
  LocationUsage,
  ProjectCrewStrip,
} from '@/types'

/**
 * Locations on screen (L1 part 3).
 *
 * Rendered with the API's own shapes. What is under test is what the screens
 * SEND and which of the API's fields they draw: a map link, a directions
 * link and a coordinate reading are always the API's, never worked out on
 * the page — the links below are deliberately ones no page could build.
 */

const API_MAP_LINK = 'https://www.google.com/maps/search/?api=1&query=from-the-api-studio'
const API_ROUTE_LINK = 'https://www.google.com/maps/dir/?api=1&origin=from-the-api&destination=studio'

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
  } as never)
}

function location(overrides: Partial<Location> = {}): Location {
  return {
    id: 'l-studio',
    name: 'Studio Wien',
    kind: 'shoot',
    street: 'Hauptstraße 1',
    street2: null,
    zip: '2340',
    city: 'Mödling',
    country: 'AT',
    latitude: '48.208200',
    longitude: '16.373800',
    coordinates_display: 'Latitude 48.2082, Longitude 16.3738',
    phone: '+43 1 234',
    email: null,
    website: null,
    emergency_phone: null,
    access_notes: null,
    notes: null,
    contact_party_id: null,
    contact_party_name: null,
    contact_text: null,
    verified: false,
    verified_on: null,
    verified_by_name: null,
    verification_note: null,
    is_active: true,
    map_link: API_MAP_LINK,
    in_use: 2,
    created_at: '2026-11-28T10:00:00Z',
    updated_at: '2026-11-28T10:00:00Z',
    ...overrides,
  }
}

function ref(l: Location): LocationRef {
  return { id: l.id, name: l.name, kind: l.kind, city: l.city, latitude: l.latitude, longitude: l.longitude, is_active: l.is_active, map_link: l.map_link }
}

const STUDIO = location()
const BARN = location({ id: 'l-barn', name: 'Barn Tulln', kind: 'other', city: 'Tulln', map_link: 'https://www.google.com/maps/search/?api=1&query=from-the-api-barn' })
const HOTEL = location({ id: 'l-hotel', name: 'Hotel Test', kind: 'hotel', city: 'Wien', map_link: 'https://www.google.com/maps/search/?api=1&query=from-the-api-hotel' })
const AKH = location({ id: 'l-akh', name: 'AKH Wien', kind: 'hospital', phone: '+43 1 40400', in_use: 0 })
const NO_USE: LocationUsage = { location_id: 'x', count: 0, uses: [] }

function list(locations: Location[], hotelsVisible = true): LocationList {
  return { locations, hotels_visible: hotelsVisible }
}

function routeGets(locations: Location[], hotelsVisible = true) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.includes('/usage')) return NO_USE as never
    if (path.startsWith('/locations')) {
      const params = new URLSearchParams(path.split('?')[1] ?? '')
      const kind = params.get('kind')
      return list(locations.filter((l) => !kind || l.kind === kind), hotelsVisible) as never
    }
    if (path === '/crew/roles') return { used: [], catalog: [], labor_items: [] } as never
    throw new Error(`unexpected GET ${path}`)
  })
}

// ─── The Locations page ─────────────────────────────────────────────────────

describe('the Locations page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seed(['locations.view', 'locations.edit', 'parties.view'])
  })

  it('lists what the API sends, and filters by kind and search through the API', async () => {
    routeGets([HOTEL, STUDIO])
    render(<LocationsPage />)
    const rows = await screen.findAllByTestId('locations-row')
    expect(rows.map((r) => r.getAttribute('data-kind'))).toEqual(['hotel', 'shoot'])
    expect(within(rows[1]).getByText('Mödling')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Yes')).toBeInTheDocument()
    expect(screen.getAllByTestId('locations-kind-chip').map((c) => c.textContent)).toEqual(['Shoot', 'Hotel', 'Hospital', 'Other'])
    fireEvent.click(screen.getByRole('button', { name: 'Hotel' }))
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/locations?kind=hotel'))
    fireEvent.change(screen.getByLabelText('Search locations'), { target: { value: 'möd' } })
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/locations?q=m%C3%B6d&kind=hotel'))
  })

  it('a user who may not see crew gets no hotels and no Hotel chip', async () => {
    seed(['locations.view'])
    routeGets([STUDIO], false)
    render(<LocationsPage />)
    await screen.findAllByTestId('locations-row')
    expect(screen.getAllByTestId('locations-kind-chip').map((c) => c.textContent)).toEqual(['Shoot', 'Hospital', 'Other'])
    expect(screen.queryByText('Hotel Test')).toBeNull()
    expect(screen.queryByTestId('locations-new')).toBeNull()
    expect(screen.getByTestId('locations-read-only')).toHaveTextContent('that needs the location edit permission')
  })

  it('opens a location with the API’s map link and where it is used', async () => {
    routeGets([STUDIO])
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === `/locations/${STUDIO.id}/usage`)
        return {
          location_id: STUDIO.id,
          count: 1,
          uses: [{ kind: 'strip', project_id: 'p1', project_name: '1000m', project_number: 'PRJ-1000', block_id: 'b1', strip_name: 'Shoot', date: null, party_name: null }],
        } as never
      return list([STUDIO]) as never
    })
    render(<LocationsPage />)
    fireEvent.click(await screen.findByText('Studio Wien'))
    const form = await screen.findByTestId('location-form')
    expect(within(form).getByTestId('location-open-map')).toHaveAttribute('href', API_MAP_LINK)
    expect(within(form).getByTestId('location-open-map')).toHaveAttribute('target', '_blank')
    expect(await within(form).findByTestId('location-use')).toHaveTextContent("1000m · Shoot: the strip's location")
    expect(within(form).getByTestId('location-notes-help')).toHaveTextContent('Do not store gate codes or passwords here.')
  })
})

// ─── The form ───────────────────────────────────────────────────────────────

describe('the location form', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets([])
  })

  it.each([
    ['48.2082, 16.3738'],
    ['48.2082 16.3738'],
    ['48,2082; 16,3738'],
  ])('the Coordinates field sends “%s” to the API and shows its reading', async (text) => {
    const reading: CoordinatesParsed = { ok: true, latitude: '48.208200', longitude: '16.373800', display: 'Latitude 48.2082, Longitude 16.3738', refusal: null }
    vi.mocked(api.post).mockResolvedValue(reading as never)
    render(<LocationForm canEdit onClose={vi.fn()} onSaved={vi.fn()} />)
    const field = screen.getByLabelText('Coordinates')
    fireEvent.change(field, { target: { value: text } })
    fireEvent.blur(field)
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/locations/coordinates', { text }))
    expect(await screen.findByTestId('location-coordinates-answer')).toHaveTextContent('Latitude 48.2082, Longitude 16.3738')
  })

  it('shows the API’s refusal for half a pair, as an alert', async () => {
    const refusal = 'These coordinates have only one number. Paste a latitude and a longitude, for example 48.2082, 16.3738.'
    vi.mocked(api.post).mockResolvedValue({ ok: false, latitude: null, longitude: null, display: null, refusal } as never)
    render(<LocationForm canEdit onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Coordinates'), { target: { value: '48.2082' } })
    fireEvent.blur(screen.getByLabelText('Coordinates'))
    const answer = await screen.findByTestId('location-coordinates-answer')
    expect(answer).toHaveTextContent(refusal)
    expect(answer).toHaveAttribute('role', 'alert')
  })

  it('creates with the pasted text for the API to read, and says why Save is disabled', async () => {
    vi.mocked(api.post).mockResolvedValue({ location: STUDIO, verification_cleared: false, message: null } as never)
    const onSaved = vi.fn()
    render(<LocationForm canEdit onClose={vi.fn()} onSaved={onSaved} />)
    expect(screen.getByTestId('location-save')).toBeDisabled()
    expect(screen.getByText('Give the location a name to save it.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Location name'), { target: { value: 'Studio Wien' } })
    fireEvent.change(screen.getByLabelText('Coordinates'), { target: { value: '48.2082 16.3738' } })
    fireEvent.click(screen.getByTestId('location-save'))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    const [path, body] = vi.mocked(api.post).mock.calls.find((c) => c[0] === '/locations')!
    expect(path).toBe('/locations')
    expect(body).toMatchObject({ name: 'Studio Wien', kind: 'shoot', coordinates: '48.2082 16.3738' })
  })

  it('a hospital: Not verified, Mark as verified with a note, then the API’s clearing sentence', async () => {
    const verified = location({ ...AKH, verified: true, verified_on: '2026-11-28', verified_by_name: 'Mathias S', verification_note: 'Called' })
    vi.mocked(api.post).mockResolvedValue({ location: verified, verification_cleared: false, message: null } as never)
    render(<LocationForm location={AKH} canEdit onClose={vi.fn()} onSaved={vi.fn()} />)
    const block = screen.getByTestId('location-verified')
    expect(block).toHaveTextContent('Not verified')
    expect(block).toHaveTextContent('Changing the address or phone clears this.')
    fireEvent.change(within(block).getByLabelText('Verification note'), { target: { value: 'Called' } })
    fireEvent.click(within(block).getByTestId('location-verify'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(`/locations/${AKH.id}/verify`, { note: 'Called' }))
    expect(await screen.findByText('Verified by Mathias S on 2026-11-28')).toBeInTheDocument()

    const message = 'This hospital is no longer marked verified, because its address or phone changed. Check the entry and mark it verified again.'
    vi.mocked(api.patch).mockResolvedValue({ location: { ...verified, verified: false, verified_on: null, verified_by_name: null, phone: '+43 1 40400 0' }, verification_cleared: true, message } as never)
    fireEvent.change(screen.getByLabelText('Phone'), { target: { value: '+43 1 40400 0' } })
    fireEvent.click(screen.getByTestId('location-save'))
    expect(await screen.findByTestId('location-notice')).toHaveTextContent(message)
    expect(screen.getByTestId('location-verified')).toHaveTextContent('Not verified')
    expect(vi.mocked(api.patch).mock.calls[0][1]).toMatchObject({ phone: '+43 1 40400 0' })
  })

  it('read-only says why and offers no Save', () => {
    render(<LocationForm location={STUDIO} canEdit={false} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(screen.getByTestId('location-read-only')).toHaveTextContent('that needs the location edit permission')
    expect(screen.queryByTestId('location-save')).toBeNull()
    expect(screen.getByLabelText('Location name')).toBeDisabled()
  })
})

// ─── The strip panel ────────────────────────────────────────────────────────

function day(date: string, overrides: Partial<CalendarStrip['days'][number]> = {}): CalendarStrip['days'][number] {
  return {
    id: `d-${date}`,
    block_id: 'b1',
    date,
    type: 'shoot',
    location: null,
    location_id: null,
    location_ref: null,
    effective_location_ref: ref(STUDIO),
    effective_location_source: 'strip',
    call_time: null,
    wrap_time: null,
    notes: null,
    effective_location: null,
    effective_call_time: null,
    effective_wrap_time: null,
    is_override: false,
    has_own_data: false,
    updated_at: '2026-11-28T10:00:00Z',
    ...overrides,
  }
}

function strip(overrides: Partial<CalendarStrip> = {}): CalendarStrip {
  return {
    id: 'b1',
    kind: 'strip',
    type: 'shoot',
    label: 'Shoot',
    start_date: '2026-07-06',
    end_date: '2026-07-08',
    location: 'Studio, Hof 2',
    location_id: STUDIO.id,
    location_ref: ref(STUDIO),
    hotels: [ref(HOTEL)],
    hotels_visible: true,
    default_call_time: null,
    default_wrap_time: null,
    notes: null,
    position: null,
    is_active: true,
    counts: { shoot: 3 },
    days: [
      day('2026-07-06'),
      day('2026-07-07', { location_id: BARN.id, location_ref: ref(BARN), effective_location_ref: ref(BARN), effective_location_source: 'day', has_own_data: true }),
      day('2026-07-08'),
    ],
    ...overrides,
  }
}

function makeActions() {
  const run = vi.fn(async (call: () => Promise<BlockChange>) => {
    await call()
    return null
  })
  const actions: StripActions = { run: run as never, resize: vi.fn(), reload: vi.fn(async () => {}), onRemoved: vi.fn(), onError: vi.fn() }
  return actions
}

describe('the strip panel: Location and Hotels', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets([STUDIO, BARN, HOTEL, AKH])
  })

  it('shows the strip’s location with the API’s map link, and each day’s effective location', () => {
    render(<StripLocationSection strip={strip()} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} selected={new Set()} />)
    expect(screen.getByTestId('strip-location-picker-value')).toHaveTextContent('Studio Wien')
    expect(screen.getByTestId('strip-location-picker-map')).toHaveAttribute('href', API_MAP_LINK)
    const days = screen.getAllByTestId('strip-day-location')
    expect(days.map((d) => d.textContent?.includes('Barn Tulln'))).toEqual([false, true, false])
    expect(within(days[1]).getByTestId('strip-day-location-own')).toHaveTextContent('this day only')
    expect(within(days[0]).getByText('Studio Wien')).toBeInTheDocument()
  })

  it('picks a location: only shoot and other are offered, and the PATCH sends its id', async () => {
    const actions = makeActions()
    vi.mocked(api.patch).mockResolvedValue({} as never)
    render(<StripLocationSection strip={strip({ location_ref: null, location_id: null })} base="/projects/p1/blocks/b1" canEdit actions={actions} selected={new Set()} />)
    fireEvent.focus(screen.getByLabelText('Strip location'))
    const options = await screen.findAllByTestId('strip-location-picker-option')
    expect(options.map((o) => o.textContent)).toEqual(['Studio WienShoot · Mödling', 'Barn TullnOther · Tulln'])
    fireEvent.click(options[1])
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/blocks/b1', { location_id: BARN.id }))
  })

  it('New location… creates inline and then selects what was created', async () => {
    const created = location({ id: 'l-new', name: 'Halle 3' })
    vi.mocked(api.post).mockResolvedValue({ location: created, verification_cleared: false, message: null } as never)
    vi.mocked(api.patch).mockResolvedValue({} as never)
    render(<StripLocationSection strip={strip({ location_ref: null, location_id: null })} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} selected={new Set()} />)
    fireEvent.focus(screen.getByLabelText('Strip location'))
    fireEvent.change(screen.getByLabelText('Strip location'), { target: { value: 'Halle 3' } })
    fireEvent.click(await screen.findByTestId('strip-location-picker-new'))
    const form = screen.getByTestId('location-form')
    expect(within(form).getByLabelText('Location name')).toHaveValue('Halle 3')
    expect(within(form).getAllByRole('radio').map((r) => r.textContent)).toEqual(['Shoot', 'Other'])
    fireEvent.click(within(form).getByTestId('location-save'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/locations', expect.objectContaining({ name: 'Halle 3', kind: 'shoot' })))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/blocks/b1', { location_id: 'l-new' }))
  })

  it('the old text: shown as Old text, and Create location from this text opens the form with it', async () => {
    render(<StripLocationSection strip={strip()} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} selected={new Set()} />)
    expect(screen.getByTestId('strip-location-old-text')).toHaveTextContent('Old text: Studio, Hof 2')
    expect(screen.queryByTestId('location-form')).toBeNull()
    fireEvent.click(screen.getByTestId('strip-location-from-text'))
    expect(await screen.findByLabelText('Location name')).toHaveValue('Studio, Hof 2')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('Set for selected days sends the dates and the location', async () => {
    vi.mocked(api.post).mockResolvedValue({} as never)
    render(<StripLocationSection strip={strip()} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} selected={new Set(['2026-07-08', '2026-07-06'])} />)
    fireEvent.focus(screen.getByLabelText('Location for selected days'))
    const options = await screen.findAllByTestId('strip-bulk-location-option')
    fireEvent.click(options.find((o) => o.textContent?.startsWith('Barn Tulln'))!)
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b1/days/location', { dates: ['2026-07-06', '2026-07-08'], location_id: BARN.id }),
    )
    fireEvent.click(screen.getByTestId('strip-bulk-location-clear'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b1/days/location', { dates: ['2026-07-06', '2026-07-08'], location_id: null }),
    )
  })

  it('a different location for one day is a PATCH of that day; “Same as the strip” clears it', async () => {
    vi.mocked(api.patch).mockResolvedValue({} as never)
    const actions = makeActions()
    const own = strip().days[1]
    render(<DayLocation projectId="p1" day={own} canEdit actions={actions} />)
    expect(screen.getByTestId('day-location')).toHaveTextContent('A different location for this day:')
    expect(screen.getByTestId('day-location-picker-value')).toHaveTextContent('Barn Tulln')
    fireEvent.click(screen.getByRole('button', { name: /Same as the strip/ }))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/days/d-2026-07-07', { location_id: null }))
    expect(actions.reload).toHaveBeenCalled()
  })

  it('an archived location keeps showing, marked archived', () => {
    render(<StripLocationSection strip={strip({ location_ref: { ...ref(STUDIO), is_active: false } })} base="/projects/p1/blocks/b1" canEdit={false} actions={makeActions()} selected={new Set()} />)
    expect(screen.getByTestId('strip-location-picker-archived')).toHaveTextContent('archived')
    expect(screen.queryByLabelText('Strip location')).toBeNull()
  })

  it('Hotels: listed with remove and add; not drawn for somebody who may not see hotels', async () => {
    vi.mocked(api.post).mockResolvedValue({} as never)
    const { unmount } = render(<StripHotels strip={strip()} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} />)
    expect(screen.getAllByTestId('strip-hotel').map((h) => h.textContent)).toEqual(['Hotel Testmap'])
    fireEvent.click(screen.getByRole('button', { name: "Remove Hotel Test from the strip's hotels" }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(`/projects/p1/blocks/b1/hotels/${HOTEL.id}/remove`))
    fireEvent.focus(screen.getByLabelText('Add a hotel'))
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/locations?kind=hotel'))
    unmount()
    render(<StripHotels strip={strip({ hotels: [], hotels_visible: false })} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} />)
    expect(screen.queryByTestId('strip-hotels')).toBeNull()
  })
})

// ─── Crew rows ──────────────────────────────────────────────────────────────

function assignment(overrides: Partial<CrewAssignment> = {}): CrewAssignment {
  return {
    id: 'a1',
    project_id: 'p1',
    block_id: 'b1',
    party_id: 'anna',
    party_name: 'Anna Kern',
    role_item_id: null,
    role_name: null,
    status: 'booked',
    rates_visible: false,
    day_rate: null,
    status_note: null,
    booked_on: null,
    booked_by_name: null,
    days: datesBetween('2026-07-06', '2026-07-08').map((date) => ({ date, call_time: null, wrap_time: null, effective_call_time: null, effective_wrap_time: null, day_type: 'shoot' })),
    clashes: [],
    hotel_visible: true,
    hotel: ref(HOTEL),
    route_link: API_ROUTE_LINK,
    created_at: '2026-11-28T10:00:00Z',
    ...overrides,
  }
}

function crewStrip(assignments: CrewAssignment[]): ProjectCrewStrip {
  return { block_id: 'b1', strip_name: 'Shoot', type: 'shoot', start_date: '2026-07-06', end_date: '2026-07-08', counts: { scheduled: 0, booked: assignments.length, declined: 0, with_clashes: 0 }, assignments }
}

function renderCrew(assignments: CrewAssignment[], { canEdit = true, theStrip = strip() } = {}) {
  render(<StripCrew projectId="p1" strip={theStrip} crew={crewStrip(assignments)} canEdit={canEdit} canSeeRates={false} currency="EUR" onChanged={vi.fn()} />)
}

describe('crew rows: Hotel and Route', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets([STUDIO, HOTEL])
  })

  it('offers the strip’s hotels, renders the API’s Route link, and PUTs a change', async () => {
    vi.mocked(api.put).mockResolvedValue({} as never)
    renderCrew([assignment()])
    const row = screen.getByTestId('strip-crew-hotel')
    const select = within(row).getByLabelText('Hotel of Anna Kern') as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['No hotel', 'Hotel Test'])
    expect(select.value).toBe(HOTEL.id)
    expect(within(row).getByTestId('strip-crew-route')).toHaveAttribute('href', API_ROUTE_LINK)
    fireEvent.change(select, { target: { value: '' } })
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/projects/p1/crew/a1/hotel', { location_id: null }))
  })

  it('with no hotels on the strip, any hotel is offered', async () => {
    renderCrew([assignment({ hotel: null, route_link: null })], { theStrip: strip({ hotels: [] }) })
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/locations?kind=hotel'))
    const select = screen.getByLabelText('Hotel of Anna Kern') as HTMLSelectElement
    await waitFor(() => expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['No hotel', 'Hotel Test']))
    expect(screen.queryByTestId('strip-crew-route')).toBeNull()
  })

  it('read-only: the hotel and the Route, no choice, and the section says why', () => {
    renderCrew([assignment()], { canEdit: false })
    expect(screen.queryByLabelText('Hotel of Anna Kern')).toBeNull()
    expect(screen.getByTestId('strip-crew-hotel-name')).toHaveTextContent('Hotel Test')
    expect(screen.getByTestId('strip-crew-route')).toHaveAttribute('href', API_ROUTE_LINK)
    expect(screen.getByTestId('strip-crew-read-only')).toHaveTextContent('that needs the project edit permission')
  })

  it('somebody who may not see hotels gets no Hotel control at all', () => {
    renderCrew([assignment({ hotel_visible: false, hotel: null, route_link: null })], { theStrip: strip({ hotels: [], hotels_visible: false }) })
    expect(screen.getByTestId('strip-crew-row')).toBeInTheDocument()
    expect(screen.queryByTestId('strip-crew-hotel')).toBeNull()
  })

  it('an archived hotel stays on the row, marked archived', () => {
    renderCrew([assignment({ hotel: { ...ref(HOTEL), is_active: false } })], { theStrip: strip({ hotels: [] }) })
    expect(screen.getByTestId('strip-crew-hotel-archived')).toHaveTextContent('archived')
    const select = screen.getByLabelText('Hotel of Anna Kern') as HTMLSelectElement
    expect(select.value).toBe(HOTEL.id)
  })
})

void ApiError
