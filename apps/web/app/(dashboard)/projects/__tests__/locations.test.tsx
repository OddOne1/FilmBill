import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), upload: vi.fn() },
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

import { api, ApiError } from '@/lib/api'
import { datesBetween } from '@/lib/calendar-dates'
import { useCompanyStore } from '@/stores/company-store'
import { LocationForm } from '@/components/locations/location-form'
import { LocationsTab } from '../[id]/locations-tab'
import { StripLocationSection, DayLocation } from '../[id]/calendar/strip-locations'
import type { StripActions } from '../[id]/calendar/strip-panel'
import { StripCrew } from '../[id]/calendar/strip-crew'
import type {
  BlockChange,
  CalendarStrip,
  CoordinatesParsed,
  CrewAssignment,
  HospitalSuggestions,
  Location,
  LocationList,
  LocationRef,
  LocationUsage,
  MapLinkResolved,
  Project,
  ProjectCrew,
  ProjectCrewStrip,
  ProjectLocationEntry,
  ProjectLocations,
} from '@/types'

/**
 * Locations on screen (L1, L1b).
 *
 * Rendered with the API's own shapes. What is under test is what the screens
 * SEND and which of the API's fields they draw: map links, map views,
 * directions, coordinate readings, nights and hospital distances are always
 * the API's — the links below are deliberately ones no page could build.
 */

const API_MAP_LINK = 'https://www.google.com/maps/search/?api=1&query=from-the-api-studio'
const API_EMBED = 'https://www.openstreetmap.org/export/embed.html?from-the-api-studio'
const API_ROUTE_LINK = 'https://www.google.com/maps/dir/?api=1&origin=from-the-api&destination=studio'

function seed(permissions: string[]) {
  useCompanyStore.setState({
    companies: [
      { id: 'c1', legal_name: 'YON Studio OG', trading_name: null, display_name: 'YON Studio OG', default_currency: 'EUR', role: 'owner', permissions, expires_at: null },
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
    uses: ['shoot'],
    source_link: null,
    from_openstreetmap: false,
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
    embed_link: API_EMBED,
    in_use: 2,
    created_at: '2026-11-28T10:00:00Z',
    updated_at: '2026-11-28T10:00:00Z',
    ...overrides,
  }
}

function ref(l: Location): LocationRef {
  return { id: l.id, name: l.name, uses: l.uses, city: l.city, latitude: l.latitude, longitude: l.longitude, is_active: l.is_active, map_link: l.map_link, embed_link: l.embed_link }
}

const STUDIO = location()
const BARN = location({ id: 'l-barn', name: 'Barn Tulln', city: 'Tulln', map_link: 'https://www.google.com/maps/search/?api=1&query=from-the-api-barn' })
const HOTEL = location({ id: 'l-hotel', name: 'Hotel Test', uses: ['hotel'], city: 'Wien', latitude: null, longitude: null, embed_link: null, map_link: 'https://www.google.com/maps/search/?api=1&query=from-the-api-hotel' })
const SACHER = location({ id: 'l-sacher', name: 'Hotel Sacher', uses: ['shoot', 'hotel'], embed_link: null })
const AKH = location({ id: 'l-akh', name: 'AKH Wien', uses: ['hospital'], phone: '+43 1 40400', in_use: 0, embed_link: null })
const NO_USE: LocationUsage = { location_id: 'x', count: 0, uses: [] }

function routeGets(locations: Location[]) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.includes('/usage')) return NO_USE as never
    if (path.startsWith('/locations')) {
      const use = new URLSearchParams(path.split('?')[1] ?? '').get('use')
      return { locations: locations.filter((l) => !use || l.uses.includes(use as never)), hotels_visible: true } satisfies LocationList as never
    }
    if (path === '/crew/roles') return { used: [], catalog: [], labor_items: [] } as never
    throw new Error(`unexpected GET ${path}`)
  })
}

// ─── The form ───────────────────────────────────────────────────────────────

describe('the location form', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets([])
  })

  it.each([['48.2082, 16.3738'], ['48.2082 16.3738'], ['48,2082; 16,3738']])(
    'the Coordinates field sends “%s” to the API and shows its reading',
    async (text) => {
      const reading: CoordinatesParsed = { ok: true, latitude: '48.208200', longitude: '16.373800', display: 'Latitude 48.2082, Longitude 16.3738', refusal: null }
      vi.mocked(api.post).mockResolvedValue(reading as never)
      render(<LocationForm canEdit onClose={vi.fn()} onSaved={vi.fn()} />)
      fireEvent.change(screen.getByLabelText('Coordinates'), { target: { value: text } })
      fireEvent.blur(screen.getByLabelText('Coordinates'))
      await waitFor(() => expect(api.post).toHaveBeenCalledWith('/locations/coordinates', { text }))
      expect(await screen.findByTestId('location-coordinates-answer')).toHaveTextContent('Latitude 48.2082, Longitude 16.3738')
    },
  )

  it('a pasted map link: the API reads it, the form takes name, address and position, and shows the API’s map view', async () => {
    const answer: MapLinkResolved = {
      ok: true, latitude: '48.220954', longitude: '16.347702', name: 'AKH Wien', address: 'Währinger Gürtel 18-20, 1090 Wien',
      provider: 'google', display: 'Latitude 48.220954, Longitude 16.347702', embed_link: 'https://www.openstreetmap.org/export/embed.html?from-the-api-akh', refusal: null,
    }
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path === '/locations/resolve-link') return answer as never
      return { location: AKH, verification_cleared: false, message: null } as never
    })
    render(<LocationForm canEdit onClose={vi.fn()} onSaved={vi.fn()} initialUses={['hospital']} />)
    const link = 'https://maps.app.goo.gl/abcDEF123'
    fireEvent.change(screen.getByLabelText('Map link'), { target: { value: link } })
    fireEvent.blur(screen.getByLabelText('Map link'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/locations/resolve-link', { url: link }))
    expect(await screen.findByTestId('location-link-answer')).toHaveTextContent('AKH Wien · Währinger Gürtel 18-20, 1090 Wien')
    expect(screen.getByLabelText('Location name')).toHaveValue('AKH Wien')
    expect(screen.getByLabelText('Street')).toHaveValue('Währinger Gürtel 18-20, 1090 Wien')
    expect(screen.getByLabelText('Coordinates')).toHaveValue('48.220954, 16.347702')
    expect(screen.getByTestId('location-map-view')).toHaveAttribute('src', answer.embed_link)
    fireEvent.click(screen.getByTestId('location-save'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/locations', expect.objectContaining({
      name: 'AKH Wien', uses: ['hospital'], source_link: link, coordinates: '48.220954, 16.347702',
    })))
  })

  it('a link the API refuses shows its sentence', async () => {
    const refusal = '“example.com” is not a Google Maps or Apple Maps link. Paste a link from one of the two, or the coordinates.'
    vi.mocked(api.post).mockResolvedValue({ ok: false, refusal } as never)
    render(<LocationForm canEdit onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Map link'), { target: { value: 'https://example.com/x' } })
    fireEvent.blur(screen.getByLabelText('Map link'))
    const shown = await screen.findByTestId('location-link-answer')
    expect(shown).toHaveTextContent(refusal)
    expect(shown).toHaveAttribute('role', 'alert')
  })

  it('several uses are ticked and sent; a project form creates into the project', async () => {
    vi.mocked(api.post).mockResolvedValue({ location: SACHER, verification_cleared: false, message: null } as never)
    render(<LocationForm canEdit projectId="p1" onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Location name'), { target: { value: 'Hotel Sacher' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Hotel' }))
    expect(screen.getAllByRole('checkbox').filter((c) => c.getAttribute('aria-checked') === 'true').map((c) => c.textContent)).toEqual(['Shooting location', 'Hotel'])
    fireEvent.click(screen.getByTestId('location-save'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/locations', expect.objectContaining({ uses: ['shoot', 'hotel'], project_id: 'p1' })))
  })

  it('a hospital: Mark as verified, then the API’s clearing sentence', async () => {
    const verified = location({ ...AKH, verified: true, verified_on: '2026-11-28', verified_by_name: 'Mathias S', verification_note: 'Called' })
    vi.mocked(api.post).mockResolvedValue({ location: verified, verification_cleared: false, message: null } as never)
    render(<LocationForm location={AKH} canEdit onClose={vi.fn()} onSaved={vi.fn()} />)
    const block = screen.getByTestId('location-verified')
    expect(block).toHaveTextContent('Not verified')
    expect(block).toHaveTextContent('Changing the address or phone clears this.')
    fireEvent.change(within(block).getByLabelText('Verification note'), { target: { value: 'Called' } })
    fireEvent.click(within(block).getByTestId('location-verify'))
    expect(await screen.findByText('Verified by Mathias S on 2026-11-28')).toBeInTheDocument()
    const message = 'This hospital is no longer marked verified, because its address or phone changed. Check the entry and mark it verified again.'
    vi.mocked(api.patch).mockResolvedValue({ location: { ...verified, verified: false, verified_on: null, verified_by_name: null }, verification_cleared: true, message } as never)
    fireEvent.change(screen.getByLabelText('Phone'), { target: { value: '+43 1 40400 0' } })
    fireEvent.click(screen.getByTestId('location-save'))
    expect(await screen.findByTestId('location-notice')).toHaveTextContent(message)
  })

  it('read-only says why and offers no Save', () => {
    render(<LocationForm location={STUDIO} canEdit={false} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(screen.getByTestId('location-read-only')).toHaveTextContent('that needs the location edit permission')
    expect(screen.queryByTestId('location-save')).toBeNull()
    expect(screen.getByTestId('location-map-view')).toHaveAttribute('src', API_EMBED)
  })
})

// ─── The project's Locations tab ────────────────────────────────────────────

const PROJECT = { id: 'p1', name: '1000m' } as Project

function entry(l: Location, more: Partial<ProjectLocationEntry> = {}): ProjectLocationEntry {
  return { location: l, strip_names: [], day_dates: [], stays: [], attachments: [], ...more }
}

const SACHER_ENTRY = entry(SACHER, {
  stays: [{ id: 's1', location_id: SACHER.id, party_id: 'anna', party_name: 'Anna Kern', check_in: '2026-07-07', check_out: '2026-07-10', nights: 3, notes: null }],
  attachments: [
    { id: 'att-pdf', location_id: SACHER.id, kind: 'file', title: 'Booking.com 4711', file_name: 'booking.pdf', content_type: 'application/pdf', size_bytes: 100, body_text: null, created_by_name: 'Mathias S', created_at: '2026-11-28T10:00:00Z' },
    { id: 'att-text', location_id: SACHER.id, kind: 'text', title: 'SMS', file_name: null, content_type: null, size_bytes: null, body_text: 'Zimmer 12 bestätigt', created_by_name: null, created_at: '2026-11-28T10:00:00Z' },
  ],
})

function projectLocations(locations: ProjectLocationEntry[], hotelsVisible = true): ProjectLocations {
  return { project_id: 'p1', hotels_visible: hotelsVisible, locations }
}

const CREW: ProjectCrew = {
  project_id: 'p1',
  rates_visible: false,
  strips: [{ block_id: 'b1', strip_name: 'Shoot', type: 'shoot', start_date: '2026-07-06', end_date: '2026-07-10', counts: { scheduled: 0, booked: 2, declined: 0, with_clashes: 0 },
    assignments: [{ party_id: 'anna', party_name: 'Anna Kern', status: 'booked' }, { party_id: 'ben', party_name: 'Ben Ohr', status: 'scheduled' }] as never }],
}

const SUGGESTIONS: HospitalSuggestions = {
  source: 'OpenStreetMap',
  missing_coordinates: ['Hotel Sacher'],
  error: null,
  groups: [{ shooting_locations: ['Studio Wien'], offers: [
    { osm_ref: 'way/1', name: 'AKH Wien', emergency: true, distance_km: 2.4, nearest_to: 'Studio Wien', latitude: '48.220900', longitude: '16.347700',
      phone: '+43 1 40400', street: 'Währinger Gürtel 18-20', zip: '1090', city: 'Wien', country: null, website: null, map_link: 'https://maps.example/from-the-api', existing_location_id: null, in_project: false },
  ] }],
}

function routeTab(data: ProjectLocations) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/projects/p1/locations') return data as never
    if (path === '/projects/p1/crew') return CREW as never
    if (path === '/projects/p1/hospital-suggestions') return SUGGESTIONS as never
    if (path.startsWith('/projects/p1/attachments/')) return { url: 'https://s3.example/presigned-from-the-api' } as never
    if (path.startsWith('/locations')) return { locations: [], hotels_visible: true } as never
    throw new Error(`unexpected GET ${path}`)
  })
}

describe('Project → Locations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seed(['projects.view', 'projects.edit', 'locations.view', 'locations.edit', 'parties.view'])
  })

  it('lists places by use — a hotel that is also a set shows in both — with the API’s links', async () => {
    routeTab(projectLocations([entry(STUDIO, { strip_names: ['Shoot'] }), SACHER_ENTRY, entry(location({ id: 'l-krone', name: 'Gasthaus Krone', uses: ['restaurant'], embed_link: null }))]))
    render(<LocationsTab project={PROJECT} canEdit />)
    const shoot = await screen.findByTestId('project-locations-shoot')
    expect(within(shoot).getAllByTestId('project-location').map((a) => a.getAttribute('data-name'))).toEqual(['Studio Wien', 'Hotel Sacher'])
    expect(within(screen.getByTestId('project-locations-hotel')).getAllByTestId('project-location').map((a) => a.getAttribute('data-name'))).toEqual(['Hotel Sacher'])
    expect(within(screen.getByTestId('project-locations-restaurant')).getByText('Gasthaus Krone')).toBeInTheDocument()
    const studio = within(shoot).getAllByTestId('project-location')[0]
    expect(within(studio).getByTestId('project-location-map-link')).toHaveAttribute('href', API_MAP_LINK)
    expect(within(studio).getByText('Strip Shoot')).toBeInTheDocument()
    fireEvent.click(within(studio).getByRole('button', { name: 'Map' }))
    expect(within(studio).getByTestId('project-location-map-view')).toHaveAttribute('src', API_EMBED)
  })

  it('hotel guests: dates per person, nights from the API, a change is a PATCH, a new guest is a POST', async () => {
    routeTab(projectLocations([SACHER_ENTRY]))
    vi.mocked(api.patch).mockResolvedValue(projectLocations([SACHER_ENTRY]) as never)
    vi.mocked(api.post).mockResolvedValue(projectLocations([SACHER_ENTRY]) as never)
    render(<LocationsTab project={PROJECT} canEdit />)
    const guests = await screen.findByTestId('hotel-guests')
    const row = within(guests).getByTestId('hotel-guest')
    expect(row).toHaveTextContent('Anna Kern')
    expect(within(row).getByTestId('hotel-guest-nights')).toHaveTextContent('3 nights')
    const checkIn = within(row).getByLabelText('Check-in of Anna Kern')
    fireEvent.change(checkIn, { target: { value: '2026-07-06' } })
    fireEvent.blur(checkIn)
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/stays/s1', { check_in: '2026-07-06' }))
    const pick = within(guests).getByLabelText('Add a guest to Hotel Sacher') as HTMLSelectElement
    await waitFor(() => expect(Array.from(pick.options).map((o) => o.textContent)).toEqual(['Add a crew member…', 'Anna Kern', 'Ben Ohr']))
    fireEvent.change(pick, { target: { value: 'ben' } })
    fireEvent.click(within(guests).getByTestId('hotel-guest-add'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects/p1/locations/l-sacher/stays', { party_id: 'ben' }))
  })

  it('booking confirmations: a file is uploaded, a text is saved, a file opens through the API’s link', async () => {
    routeTab(projectLocations([SACHER_ENTRY]))
    vi.mocked(api.upload).mockResolvedValue(projectLocations([SACHER_ENTRY]) as never)
    vi.mocked(api.post).mockResolvedValue(projectLocations([SACHER_ENTRY]) as never)
    const opened = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(<LocationsTab project={PROJECT} canEdit />)
    const box = within(await screen.findByTestId('project-locations-hotel')).getByTestId('location-confirmations')
    expect(within(box).getAllByTestId('location-confirmation').map((li) => li.textContent)).toEqual([
      expect.stringContaining('Booking.com 4711'), expect.stringContaining('SMS'),
    ])
    fireEvent.click(within(box).getByRole('button', { name: 'Show' }))
    expect(within(box).getByTestId('location-confirmation-text')).toHaveTextContent('Zimmer 12 bestätigt')
    fireEvent.click(within(box).getByRole('button', { name: 'Open' }))
    await waitFor(() => expect(opened).toHaveBeenCalledWith('https://s3.example/presigned-from-the-api', '_blank', 'noopener'))
    fireEvent.change(within(box).getByLabelText('Confirmation title for Hotel Sacher'), { target: { value: 'Direct booking' } })
    const file = new File(['%PDF-1.4'], 'direct.pdf', { type: 'application/pdf' })
    fireEvent.change(within(box).getByLabelText('Attach a confirmation file to Hotel Sacher'), { target: { files: [file] } })
    await waitFor(() => expect(api.upload).toHaveBeenCalled())
    const [path, form] = vi.mocked(api.upload).mock.calls[0] as [string, FormData]
    expect(path).toBe('/projects/p1/locations/l-sacher/attachments')
    expect((form.get('file') as File).name).toBe('direct.pdf')
    expect(form.get('title')).toBe('Direct booking')
    fireEvent.change(within(box).getByLabelText('Paste a confirmation for Hotel Sacher'), { target: { value: 'Reservierung bestätigt' } })
    fireEvent.click(within(box).getByTestId('location-confirmation-save-text'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects/p1/locations/l-sacher/attachments/text', { title: null, text: 'Reservierung bestätigt' }))
    opened.mockRestore()
  })

  it('hospitals are found by themselves near the shooting locations and added with a click', async () => {
    routeTab(projectLocations([entry(STUDIO), SACHER_ENTRY]))
    vi.mocked(api.post).mockResolvedValue(projectLocations([entry(STUDIO), SACHER_ENTRY]) as never)
    render(<LocationsTab project={PROJECT} canEdit />)
    const offer = await screen.findByTestId('hospital-offer')
    expect(api.get).toHaveBeenCalledWith('/projects/p1/hospital-suggestions')
    expect(offer).toHaveTextContent('AKH Wien')
    expect(offer).toHaveTextContent('2.4 km from Studio Wien · emergency department · +43 1 40400')
    expect(screen.getByTestId('hospital-suggestions')).toHaveTextContent('No position for Hotel Sacher')
    fireEvent.click(within(offer).getByTestId('hospital-offer-add'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/hospital-suggestions/add', expect.objectContaining({ osm_ref: 'way/1', name: 'AKH Wien', latitude: '48.220900' })),
    )
  })

  it('without crew access: no Hotels section, no guests; read-only says why', async () => {
    seed(['projects.view', 'locations.view'])
    routeTab(projectLocations([entry({ ...SACHER, uses: ['shoot'] })], false))
    render(<LocationsTab project={PROJECT} canEdit={false} />)
    await screen.findByTestId('project-locations-shoot')
    expect(screen.queryByTestId('project-locations-hotel')).toBeNull()
    expect(screen.queryByTestId('hotel-guests')).toBeNull()
    expect(screen.getByTestId('project-locations-read-only')).toHaveTextContent('that needs the project edit permission')
    expect(api.get).not.toHaveBeenCalledWith('/projects/p1/crew')
  })

  it('a refused removal shows the API’s sentence', async () => {
    routeTab(projectLocations([entry(STUDIO, { strip_names: ['Shoot'] })]))
    const sentence = '“Studio Wien” is still used by 1 strip of this project. Change those first, then remove it from the project.'
    vi.mocked(api.post).mockRejectedValue(new ApiError(422, sentence))
    render(<LocationsTab project={PROJECT} canEdit />)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Studio Wien from this project' }))
    expect(await screen.findByTestId('project-locations-error')).toHaveTextContent(sentence)
  })
})

// ─── The strip panel ────────────────────────────────────────────────────────

function day(date: string, overrides: Partial<CalendarStrip['days'][number]> = {}): CalendarStrip['days'][number] {
  return {
    id: `d-${date}`, block_id: 'b1', date, type: 'shoot', location: null, location_id: null, location_ref: null,
    effective_location_ref: ref(STUDIO), effective_location_source: 'strip', call_time: null, wrap_time: null, notes: null,
    effective_location: null, effective_call_time: null, effective_wrap_time: null, is_override: false, has_own_data: false,
    updated_at: '2026-11-28T10:00:00Z', ...overrides,
  }
}

function strip(overrides: Partial<CalendarStrip> = {}): CalendarStrip {
  return {
    id: 'b1', kind: 'strip', type: 'shoot', label: 'Shoot', start_date: '2026-07-06', end_date: '2026-07-08',
    location: 'Studio, Hof 2', location_id: STUDIO.id, location_ref: ref(STUDIO),
    default_call_time: null, default_wrap_time: null, notes: null, position: null, is_active: true, counts: { shoot: 3 },
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

describe('the strip panel: Location', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets([STUDIO, BARN, SACHER, HOTEL, AKH])
  })

  it('shows the strip’s location with the API’s map link, and each day’s effective location', () => {
    render(<StripLocationSection strip={strip()} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} selected={new Set()} />)
    expect(screen.getByTestId('strip-location-picker-value')).toHaveTextContent('Studio Wien')
    expect(screen.getByTestId('strip-location-picker-map')).toHaveAttribute('href', API_MAP_LINK)
    const days = screen.getAllByTestId('strip-day-location')
    expect(days.map((d) => d.textContent?.includes('Barn Tulln'))).toEqual([false, true, false])
    expect(within(days[1]).getByTestId('strip-day-location-own')).toHaveTextContent('this day only')
  })

  it('offers only places marked as shooting locations — a hotel that is also a set included — and PATCHes the id', async () => {
    vi.mocked(api.patch).mockResolvedValue({} as never)
    render(<StripLocationSection strip={strip({ location_ref: null, location_id: null })} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} selected={new Set()} />)
    fireEvent.focus(screen.getByLabelText('Strip location'))
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/locations?use=shoot'))
    const options = await screen.findAllByTestId('strip-location-picker-option')
    expect(options.map((o) => o.textContent?.split(/Shooting/)[0])).toEqual(['Studio Wien', 'Barn Tulln', 'Hotel Sacher'])
    fireEvent.click(options[1])
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/blocks/b1', { location_id: BARN.id }))
  })

  it('New location… creates inline as a shooting location and selects it', async () => {
    const created = location({ id: 'l-new', name: 'Halle 3' })
    vi.mocked(api.post).mockResolvedValue({ location: created, verification_cleared: false, message: null } as never)
    vi.mocked(api.patch).mockResolvedValue({} as never)
    render(<StripLocationSection strip={strip({ location_ref: null, location_id: null })} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} selected={new Set()} />)
    fireEvent.focus(screen.getByLabelText('Strip location'))
    fireEvent.change(screen.getByLabelText('Strip location'), { target: { value: 'Halle 3' } })
    fireEvent.click(await screen.findByTestId('strip-location-picker-new'))
    const form = screen.getByTestId('location-form')
    expect(within(form).getByLabelText('Location name')).toHaveValue('Halle 3')
    fireEvent.click(within(form).getByTestId('location-save'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/locations', expect.objectContaining({ name: 'Halle 3', uses: ['shoot'] })))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/blocks/b1', { location_id: 'l-new' }))
  })

  it('the old text: shown as Old text, and Create location from this text opens the form with it', async () => {
    render(<StripLocationSection strip={strip()} base="/projects/p1/blocks/b1" canEdit actions={makeActions()} selected={new Set()} />)
    expect(screen.getByTestId('strip-location-old-text')).toHaveTextContent('Old text: Studio, Hof 2')
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
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b1/days/location', { dates: ['2026-07-06', '2026-07-08'], location_id: BARN.id }))
  })

  it('a different location for one day is a PATCH of that day; “Same as the strip” clears it', async () => {
    vi.mocked(api.patch).mockResolvedValue({} as never)
    const actions = makeActions()
    render(<DayLocation projectId="p1" day={strip().days[1]} canEdit actions={actions} />)
    expect(screen.getByTestId('day-location-picker-value')).toHaveTextContent('Barn Tulln')
    fireEvent.click(screen.getByRole('button', { name: /Same as the strip/ }))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/days/d-2026-07-07', { location_id: null }))
  })

  it('an archived location keeps showing, marked archived', () => {
    render(<StripLocationSection strip={strip({ location_ref: { ...ref(STUDIO), is_active: false } })} base="/projects/p1/blocks/b1" canEdit={false} actions={makeActions()} selected={new Set()} />)
    expect(screen.getByTestId('strip-location-picker-archived')).toHaveTextContent('archived')
    expect(screen.queryByLabelText('Strip location')).toBeNull()
  })
})

// ─── Crew rows ──────────────────────────────────────────────────────────────

function assignment(overrides: Partial<CrewAssignment> = {}): CrewAssignment {
  return {
    id: 'a1', project_id: 'p1', block_id: 'b1', party_id: 'anna', party_name: 'Anna Kern', role_item_id: null, role_name: null,
    status: 'booked', rates_visible: false, day_rate: null, status_note: null, booked_on: null, booked_by_name: null,
    days: datesBetween('2026-07-06', '2026-07-08').map((date) => ({ date, call_time: null, wrap_time: null, effective_call_time: null, effective_wrap_time: null, day_type: 'shoot' })),
    clashes: [],
    hotel_visible: true,
    hotel_stays: [{ stay_id: 's1', hotel: ref(HOTEL), check_in: '2026-07-06', check_out: '2026-07-09', route_link: API_ROUTE_LINK }],
    created_at: '2026-11-28T10:00:00Z',
    ...overrides,
  }
}

function crewStrip(assignments: CrewAssignment[]): ProjectCrewStrip {
  return { block_id: 'b1', strip_name: 'Shoot', type: 'shoot', start_date: '2026-07-06', end_date: '2026-07-08', counts: { scheduled: 0, booked: assignments.length, declined: 0, with_clashes: 0 }, assignments }
}

function renderCrew(assignments: CrewAssignment[], canEdit = true) {
  render(<StripCrew projectId="p1" strip={strip()} crew={crewStrip(assignments)} canEdit={canEdit} canSeeRates={false} currency="EUR" onChanged={vi.fn()} />)
}

describe('crew rows: the hotel stays and the Route', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets([])
  })

  it('shows the stay read-only with the API’s Route link', () => {
    renderCrew([assignment()])
    const stay = screen.getByTestId('strip-crew-stay')
    expect(stay).toHaveTextContent('Hotel Test')
    expect(within(stay).getByTestId('strip-crew-route')).toHaveAttribute('href', API_ROUTE_LINK)
    expect(screen.queryByRole('combobox', { name: /Hotel of/ })).toBeNull()
  })

  it('no stay yet says where to add one', () => {
    renderCrew([assignment({ hotel_stays: [] })])
    expect(screen.getByTestId('strip-crew-hotel-none')).toHaveTextContent('add guests to a hotel on the project’s Locations tab')
  })

  it('somebody who may not see hotels gets no hotel line at all', () => {
    renderCrew([assignment({ hotel_visible: false, hotel_stays: [] })])
    expect(screen.getByTestId('strip-crew-row')).toBeInTheDocument()
    expect(screen.queryByTestId('strip-crew-hotel')).toBeNull()
  })

  it('an archived hotel stays on the row, marked archived', () => {
    renderCrew([assignment({ hotel_stays: [{ stay_id: 's1', hotel: { ...ref(HOTEL), is_active: false }, check_in: '2026-07-06', check_out: '2026-07-09', route_link: null }] })], false)
    expect(screen.getByTestId('strip-crew-hotel-archived')).toHaveTextContent('archived')
  })
})
