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
import { StripCrew } from '../[id]/calendar/strip-crew'
import { MonthGrid } from '../[id]/calendar/month-grid'
import { CrewAvailabilityView } from '../../contacts/crew-availability'
import { CrewTab } from '../[id]/crew-tab'
import { useCompanyStore } from '@/stores/company-store'
import type {
  CalendarStrip,
  CrewAssignment,
  CrewAvailability,
  CrewAvailabilityPerson,
  CrewClash,
  ProjectCrewStrip,
} from '@/types'

/**
 * Crew on the calendar (B2c-1 part 3).
 *
 * The components are rendered with the API's own shapes, and what is under
 * test is what they SEND and which of the API's fields they draw: the
 * picker's filters as query parameters, the people ticked as the POST body,
 * the days checklist as the PATCH, the "confirmed how" note on a status
 * change — and the clash sentence exactly as the API wrote it. Counts,
 * states and clashes are never worked out on the page.
 */

const SHOOT: CalendarStrip = {
  id: 'b1',
  kind: 'strip',
  type: 'shoot',
  label: 'Shoot',
  start_date: '2026-07-06',
  end_date: '2026-07-08',
  location: null,
  default_call_time: '08:00:00',
  default_wrap_time: null,
  notes: null,
  position: null,
  is_active: true,
  counts: { shoot: 3 },
  days: datesBetween('2026-07-06', '2026-07-08').map((date) => ({
    id: `d-${date}`,
    block_id: 'b1',
    date,
    type: date === '2026-07-07' ? 'off' : 'shoot',
    location: null,
    call_time: null,
    wrap_time: null,
    notes: null,
    effective_location: null,
    effective_call_time: '08:00:00',
    effective_wrap_time: null,
    is_override: date === '2026-07-07',
    has_own_data: false,
    updated_at: '2026-11-27T10:00:00Z',
  })),
}

const CLASH: CrewClash = {
  kind: 'assignment',
  party_id: 'anna',
  party_name: 'Anna Huber',
  dates: ['2026-07-08'],
  message: 'Anna Huber is already booked on Doku Herbst, strip “Dreh”, on 8 July 2026. Check with them, or untick those days.',
  assignment_id: 'a-other',
  project_id: 'p2',
  project_name: 'Doku Herbst',
  project_number: 'PRJ-2',
  block_id: 'b2',
  strip_name: 'Dreh',
  status: 'booked',
  unavailability_id: null,
  reason: null,
  start_date: null,
  end_date: null,
}

function assignment(overrides: Partial<CrewAssignment> = {}): CrewAssignment {
  return {
    id: 'a1',
    project_id: 'p1',
    block_id: 'b1',
    party_id: 'anna',
    party_name: 'Anna Huber',
    role_item_id: 'i-dop',
    role_name: 'DoP',
    status: 'scheduled',
    rates_visible: true,
    day_rate: '650.00',
    status_note: null,
    booked_on: null,
    booked_by_name: null,
    days: [
      { date: '2026-07-06', call_time: null, wrap_time: null, effective_call_time: '08:00:00', effective_wrap_time: null, day_type: 'shoot' },
      { date: '2026-07-08', call_time: null, wrap_time: null, effective_call_time: '08:00:00', effective_wrap_time: null, day_type: 'shoot' },
    ],
    clashes: [CLASH],
    created_at: '2026-11-27T10:00:00Z',
    ...overrides,
  }
}

function crewStrip(assignments: CrewAssignment[]): ProjectCrewStrip {
  return {
    block_id: 'b1',
    strip_name: 'Shoot',
    type: 'shoot',
    start_date: '2026-07-06',
    end_date: '2026-07-08',
    counts: {
      scheduled: assignments.filter((a) => a.status === 'scheduled').length,
      booked: assignments.filter((a) => a.status === 'booked').length,
      declined: 0,
      with_clashes: assignments.filter((a) => a.clashes.length).length,
    },
    assignments,
  }
}

function person(overrides: Partial<CrewAvailabilityPerson> = {}): CrewAvailabilityPerson {
  return {
    party_id: 'ben',
    name: 'Ben Ohr',
    roles: ['Gaffer'],
    rate: '400.00',
    free_days: 3,
    days: datesBetween('2026-07-06', '2026-07-08').map((date) => ({ date, state: 'free', clash: false, entries: [] })),
    ...overrides,
  }
}

const BUSY_CLEO = person({
  party_id: 'cleo',
  name: 'Cleo Lang',
  roles: ['DoP'],
  rate: '600.00',
  free_days: 1,
  days: [
    { date: '2026-07-06', state: 'unavailable', clash: false, entries: [{ kind: 'unavailable', reason: 'Vacation', status: null, assignment_id: null, project_id: null, project_name: null, block_id: null, strip_name: null, unavailability_id: 'u1' }] },
    { date: '2026-07-07', state: 'booked', clash: false, entries: [{ kind: 'assignment', status: 'booked', project_name: 'Doku Herbst', strip_name: 'Dreh', assignment_id: 'x', project_id: 'p2', block_id: 'b2', reason: null, unavailability_id: null }] },
    { date: '2026-07-08', state: 'free', clash: false, entries: [] },
  ],
})

function availability(people: CrewAvailabilityPerson[], ratesVisible = true): CrewAvailability {
  return { range_start: '2026-07-06', range_end: '2026-07-08', rates_visible: ratesVisible, people }
}

function routeGets(people: CrewAvailabilityPerson[], ratesVisible = true) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/crew/roles') return { used: [], catalog: [], labor_items: [{ item_id: 'i-dop', name: 'DoP' }, { item_id: 'i-gaf', name: 'Gaffer' }] }
    if (path.startsWith('/crew/availability')) return availability(people, ratesVisible)
    if (path.startsWith('/projects')) return { projects: [{ id: 'p1', name: 'Spot Sommer' }], total: 1, limit: 200, offset: 0 }
    throw new Error(`unexpected GET ${path}`)
  })
}

function renderCrew(assignments: CrewAssignment[], { canEdit = true, canSeeRates = true } = {}) {
  const onChanged = vi.fn()
  render(
    <StripCrew
      projectId="p1"
      strip={SHOOT}
      crew={crewStrip(assignments)}
      canEdit={canEdit}
      canSeeRates={canSeeRates}
      currency="EUR"
      onChanged={onChanged}
    />,
  )
  return onChanged
}

describe('the strip panel: Crew', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets([person(), BUSY_CLEO])
  })

  it('lists people with role, status chip and a clash badge carrying the API sentence', async () => {
    renderCrew([assignment(), assignment({ id: 'a2', party_id: 'ben', party_name: 'Ben Ohr', role_name: 'Gaffer', status: 'booked', clashes: [] })])
    const rows = screen.getAllByTestId('strip-crew-row')
    expect(rows.map((row) => row.getAttribute('data-status'))).toEqual(['scheduled', 'booked'])
    expect(within(rows[0]).getByText(/DoP/)).toBeInTheDocument()
    expect(within(rows[0]).getByTestId('strip-crew-clash')).toHaveAttribute('title', CLASH.message)
    expect(within(rows[1]).queryByTestId('strip-crew-clash')).toBeNull()
    expect(screen.getByTestId('strip-crew-counts')).toHaveTextContent('1 booked · 1 scheduled')
    fireEvent.click(within(rows[0]).getByRole('button', { name: /Anna Huber/ }))
    expect(within(screen.getByTestId('strip-crew-row-clashes')).getByText(CLASH.message)).toBeInTheDocument()
  })

  it('the picker sends its filters and shows what the strip dates look like for each person', async () => {
    renderCrew([assignment()])
    fireEvent.click(screen.getByTestId('strip-crew-add'))
    const rows = await screen.findAllByTestId('crew-picker-row')
    const states = rows.map((row) => within(row).getByTestId('crew-picker-availability'))
    expect(states[0]).toHaveAttribute('data-state', 'free')
    expect(states[0]).toHaveTextContent('free')
    expect(states[1]).toHaveAttribute('data-state', 'busy')
    expect(states[1]).toHaveTextContent('unavailable (Vacation), 1 day; booked on Doku Herbst · Dreh, 1 day')
    expect(within(rows[1]).getByTestId('crew-picker-rate')).toHaveTextContent('600.00')

    await waitFor(() => expect(within(screen.getByTestId('crew-picker-role')).getByText('Gaffer')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('crew-picker-role'), { target: { value: 'i-gaf' } })
    fireEvent.change(screen.getByTestId('crew-picker-max-rate'), { target: { value: '450,50' } })
    fireEvent.change(screen.getByTestId('crew-picker-search'), { target: { value: 'be' } })
    fireEvent.click(screen.getByTestId('crew-picker-available'))
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        '/crew/availability?from=2026-07-06&to=2026-07-08&role_item_id=i-gaf&max_rate=450.50&search=be&available_only=true',
      ),
    )
  })

  it('adds several people at once with the filtered role and shows the clash warnings the API returns', async () => {
    vi.mocked(api.post).mockResolvedValue({ assignments: [assignment({ id: 'n1', party_id: 'ben' }), assignment({ id: 'n2', party_id: 'cleo' })], clashes: [CLASH] })
    const onChanged = renderCrew([])
    fireEvent.click(screen.getByTestId('strip-crew-add'))
    await screen.findAllByTestId('crew-picker-row')
    // 17c: the Add button is dead and says why.
    expect(screen.getByTestId('crew-picker-add')).toBeDisabled()
    expect(screen.getByText('Tick the people to add. They start as scheduled.')).toBeInTheDocument()
    await waitFor(() => expect(within(screen.getByTestId('crew-picker-role')).getByText('DoP')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('crew-picker-role'), { target: { value: 'i-dop' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Pick Ben Ohr' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Pick Cleo Lang' }))
    fireEvent.click(screen.getByTestId('crew-picker-add'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b1/crew', {
        people: [
          { party_id: 'ben', role_item_id: 'i-dop' },
          { party_id: 'cleo', role_item_id: 'i-dop' },
        ],
      }),
    )
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(within(screen.getByTestId('strip-crew-warnings')).getByText(CLASH.message)).toBeInTheDocument()
    expect(screen.getByTestId('strip-crew-notice')).toHaveTextContent('2 people added as scheduled.')
  })

  it('a person already on the strip cannot be picked again', async () => {
    renderCrew([assignment({ party_id: 'ben', party_name: 'Ben Ohr' })])
    fireEvent.click(screen.getByTestId('strip-crew-add'))
    await screen.findAllByTestId('crew-picker-row')
    expect(screen.getByRole('checkbox', { name: 'Pick Ben Ohr' })).toBeDisabled()
    expect(screen.getAllByTestId('crew-picker-availability')[0]).toHaveTextContent('already on this strip')
  })

  it('the days checklist sends the ticked days with call and wrap of their own', async () => {
    vi.mocked(api.patch).mockResolvedValue(assignment())
    renderCrew([assignment()])
    fireEvent.click(screen.getByRole('button', { name: /Anna Huber/ }))
    const boxes = within(screen.getByTestId('strip-crew-days')).getAllByTestId('strip-crew-day')
    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([true, false, true])
    fireEvent.click(boxes[2]) // untick 8 July
    fireEvent.click(boxes[1]) // tick 7 July, the off day
    fireEvent.change(screen.getByLabelText('Call on 6 July 2026'), { target: { value: '06:30' } })
    fireEvent.click(screen.getByTestId('strip-crew-save-days'))
    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/projects/p1/crew/a1', {
        days: [
          { date: '2026-07-06', call_time: '06:30', wrap_time: null },
          { date: '2026-07-07', call_time: null, wrap_time: null },
        ],
      }),
    )
  })

  it('drops a day left outside the strip (the strip was shortened) on the next save', async () => {
    vi.mocked(api.patch).mockResolvedValue(assignment())
    const outside = assignment({
      days: [
        ...assignment().days,
        { date: '2026-07-09', call_time: null, wrap_time: null, effective_call_time: null, effective_wrap_time: null, day_type: null },
      ],
    })
    renderCrew([outside])
    fireEvent.click(screen.getByRole('button', { name: /Anna Huber/ }))
    fireEvent.click(screen.getByTestId('strip-crew-save-days'))
    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/projects/p1/crew/a1', {
        days: [
          { date: '2026-07-06', call_time: null, wrap_time: null },
          { date: '2026-07-08', call_time: null, wrap_time: null },
        ],
      }),
    )
  })

  it('marks booked with the confirmed-how note, and reopening needs a note', async () => {
    vi.mocked(api.post).mockResolvedValue(assignment({ status: 'booked' }))
    renderCrew([assignment()])
    fireEvent.click(screen.getByRole('button', { name: /Anna Huber/ }))
    fireEvent.change(screen.getByTestId('strip-crew-note'), { target: { value: 'by phone' } })
    fireEvent.click(screen.getByTestId('strip-crew-book'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects/p1/crew/a1/status', { status: 'booked', note: 'by phone' }))
  })

  it('a booked person: Reopen is dead until a note says why', async () => {
    renderCrew([assignment({ status: 'booked', status_note: 'by phone', booked_by_name: 'Mathias' })])
    fireEvent.click(screen.getByRole('button', { name: /Anna Huber/ }))
    expect(screen.getByText(/Confirmed: by phone \(Mathias\)/)).toBeInTheDocument()
    expect(screen.getByTestId('strip-crew-reopen')).toBeDisabled()
    expect(screen.getByText('Reopening needs a note saying why.')).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('strip-crew-note'), { target: { value: 'dates moved' } })
    expect(screen.getByTestId('strip-crew-reopen')).not.toBeDisabled()
  })

  it('Mark selected as booked sends the ticked scheduled people and the note', async () => {
    vi.mocked(api.post).mockResolvedValue({ changed: 2, skipped: [], assignments: [] })
    renderCrew([assignment(), assignment({ id: 'a2', party_id: 'ben', party_name: 'Ben Ohr' }), assignment({ id: 'a3', party_id: 'cleo', party_name: 'Cleo Lang', status: 'declined', clashes: [] })])
    expect(screen.getByRole('checkbox', { name: 'Select Cleo Lang' })).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Anna Huber' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Ben Ohr' }))
    fireEvent.change(screen.getByTestId('strip-crew-bulk-note'), { target: { value: 'email' } })
    fireEvent.click(screen.getByTestId('strip-crew-bulk-book'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/crew/status', { assignment_ids: ['a1', 'a2'], status: 'booked', note: 'email' }),
    )
    expect(await screen.findByTestId('strip-crew-notice')).toHaveTextContent('2 marked booked.')
  })

  it('is read-only without projects.edit and says why', () => {
    renderCrew([assignment()], { canEdit: false })
    expect(screen.getByTestId('strip-crew-read-only')).toHaveTextContent('needs the project edit permission')
    expect(screen.queryByTestId('strip-crew-add')).toBeNull()
    expect(screen.queryByRole('checkbox')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Anna Huber/ }))
    expect(screen.queryByTestId('strip-crew-book')).toBeNull()
    expect(screen.queryByTestId('strip-crew-save-days')).toBeNull()
  })

  it('shows no rate and no rate filter without budgets.view', async () => {
    routeGets([person({ rate: null }), { ...BUSY_CLEO, rate: null }], false)
    renderCrew([assignment({ rates_visible: false, day_rate: null })], { canSeeRates: false })
    fireEvent.click(screen.getByTestId('strip-crew-add'))
    await screen.findAllByTestId('crew-picker-row')
    expect(screen.queryByTestId('crew-picker-max-rate')).toBeNull()
    expect(screen.queryAllByTestId('crew-picker-rate')).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: /Anna Huber/ }))
    expect(screen.queryByTestId('strip-crew-rate')).toBeNull()
    expect(screen.getByTestId('crew-picker').textContent).not.toMatch(/€|\d+[.,]\d{2}/)
  })
})

describe('the strip badge on the calendar', () => {
  it('reads "1 booked · 2 scheduled" with a marker when somebody clashes', () => {
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
        crewCounts={{ b1: { booked: 1, scheduled: 2, declined: 3, with_clashes: 1 } }}
      />,
    )
    expect(screen.getByTestId('strip-crew-badge')).toHaveTextContent('1 booked · 2 scheduled')
    expect(screen.getByTestId('strip-crew-clash-marker')).toBeInTheDocument()
  })

  it('has no badge on a strip with nobody on it', () => {
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
        crewCounts={{ b1: { booked: 0, scheduled: 0, declined: 2, with_clashes: 0 } }}
      />,
    )
    expect(screen.queryByTestId('strip-crew-badge')).toBeNull()
  })
})

describe('Contacts → Crew → Availability', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeGets([person(), BUSY_CLEO])
  })

  it('colours each cell by the API state and says what a clicked cell is', async () => {
    render(<CrewAvailabilityView laborItems={[{ item_id: 'i-dop', name: 'DoP' }]} canSeeRates currency="EUR" />)
    const rows = await screen.findAllByTestId('crew-availability-row')
    const cells = within(rows[1]).getAllByTestId('crew-availability-cell')
    expect(cells.map((cell) => cell.getAttribute('data-state'))).toEqual(['unavailable', 'booked', 'free'])
    fireEvent.click(cells[1])
    expect(screen.getByTestId('crew-availability-detail')).toHaveTextContent('Booked on Doku Herbst, strip “Dreh”')
    fireEvent.click(cells[0])
    expect(screen.getByTestId('crew-availability-detail')).toHaveTextContent('Not available: Vacation')
  })

  it('sends the role, rate and project filters', async () => {
    render(<CrewAvailabilityView laborItems={[{ item_id: 'i-dop', name: 'DoP' }]} canSeeRates currency="EUR" />)
    await screen.findAllByTestId('crew-availability-row')
    fireEvent.change(screen.getByTestId('crew-availability-from'), { target: { value: '2026-07-06' } })
    fireEvent.change(screen.getByTestId('crew-availability-role'), { target: { value: 'i-dop' } })
    fireEvent.change(screen.getByTestId('crew-availability-max-rate'), { target: { value: '620' } })
    await waitFor(() => expect(within(screen.getByTestId('crew-availability-project')).getByText('Spot Sommer')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('crew-availability-project'), { target: { value: 'p1' } })
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/crew/availability?from=2026-07-06&to=2026-07-12&role_item_id=i-dop&max_rate=620&project_id=p1'),
    )
  })

  it('has no rate column and no rate filter without budgets.view', async () => {
    routeGets([person({ rate: null })], false)
    render(<CrewAvailabilityView laborItems={[]} canSeeRates={false} currency="EUR" />)
    await screen.findAllByTestId('crew-availability-row')
    expect(screen.queryByTestId('crew-availability-max-rate')).toBeNull()
    expect(screen.queryAllByTestId('crew-availability-rate')).toEqual([])
  })
})

describe('Project → Crew: adding and booking from the tab', () => {
  const PREP: CalendarStrip = { ...SHOOT, id: 'b0', label: 'Prep', start_date: '2026-07-01', end_date: '2026-07-02', days: [] }
  const project = { id: 'p1' } as never

  beforeEach(() => {
    vi.clearAllMocks()
    useCompanyStore.setState({
      companies: [
        {
          id: 'c1',
          legal_name: 'YON Studio OG',
          trading_name: null,
          display_name: 'YON Studio OG',
          default_currency: 'EUR',
          role: 'owner',
          permissions: ['projects.view', 'projects.edit', 'parties.view', 'budgets.view'],
          expires_at: null,
        },
      ],
      activeCompanyId: 'c1',
      loaded: true,
      error: null,
    })
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/projects/p1/crew')
        return {
          project_id: 'p1',
          rates_visible: true,
          strips: [
            { ...crewStrip([]), block_id: 'b0', strip_name: 'Prep', start_date: '2026-07-01', end_date: '2026-07-02' },
            crewStrip([assignment()]),
          ],
        }
      if (path === '/projects/p1/calendar')
        return { project_id: 'p1', project_start: '2026-07-01', project_end: null, strips: [PREP, SHOOT], counts: {} }
      if (path === '/crew/roles') return { used: [], catalog: [], labor_items: [{ item_id: 'i-dop', name: 'DoP' }] }
      if (path.startsWith('/crew/availability')) return availability([person()])
      throw new Error(`unexpected GET ${path}`)
    })
  })

  it('every strip card has Add crew, and the picker asks for that strip’s dates', async () => {
    render(<CrewTab project={project} canEdit onOpenStrip={() => {}} />)
    const cards = await screen.findAllByTestId('project-crew-strip')
    expect(cards).toHaveLength(2)
    expect(within(cards[0]).getByText('Prep')).toBeInTheDocument()
    fireEvent.click(within(cards[0]).getByTestId('strip-crew-add'))
    await within(cards[0]).findAllByTestId('crew-picker-row')
    expect(api.get).toHaveBeenCalledWith('/crew/availability?from=2026-07-01&to=2026-07-02')
    vi.mocked(api.post).mockResolvedValue({ assignments: [assignment({ id: 'n1', party_id: 'ben', block_id: 'b0' })], clashes: [] })
    fireEvent.click(within(cards[0]).getByRole('checkbox', { name: 'Pick Ben Ohr' }))
    fireEvent.click(within(cards[0]).getByTestId('crew-picker-add'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b0/crew', { people: [{ party_id: 'ben' }] }))
    // The tab reloads the crew from the API after the add.
    await waitFor(() => expect(vi.mocked(api.get).mock.calls.filter(([path]) => path === '/projects/p1/crew')).toHaveLength(2))
  })

  it('books a scheduled person from the tab with the confirmed-how note', async () => {
    vi.mocked(api.post).mockResolvedValue(assignment({ status: 'booked' }))
    render(<CrewTab project={project} canEdit onOpenStrip={() => {}} />)
    const cards = await screen.findAllByTestId('project-crew-strip')
    fireEvent.click(within(cards[1]).getByRole('button', { name: /Anna Huber/ }))
    fireEvent.change(within(cards[1]).getByTestId('strip-crew-note'), { target: { value: 'by phone' } })
    fireEvent.click(within(cards[1]).getByTestId('strip-crew-book'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects/p1/crew/a1/status', { status: 'booked', note: 'by phone' }))
  })

  it('is read-only without projects.edit and says why', async () => {
    render(<CrewTab project={project} canEdit={false} onOpenStrip={() => {}} />)
    const cards = await screen.findAllByTestId('project-crew-strip')
    expect(within(cards[1]).queryByTestId('strip-crew-add')).toBeNull()
    expect(within(cards[1]).getByTestId('strip-crew-read-only')).toHaveTextContent('needs the project edit permission')
  })
})
