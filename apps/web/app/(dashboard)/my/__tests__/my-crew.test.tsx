import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), put: vi.fn(), post: vi.fn() },
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
vi.mock('next/navigation', () => ({ usePathname: () => '/my/crew-profile', useParams: () => ({}) }))

import { api, ApiError } from '@/lib/api'
import MyCrewProfilePage from '../crew-profile/page'
import MySchedulePage from '../schedule/page'
import { useCompanyStore } from '@/stores/company-store'
import type { MyCrewProfile, MySchedule, MyScheduleEntry } from '@/types'

/**
 * R2. My crew profile and My schedule, rendered for real against the API's
 * own shapes. What is under test: what the page SENDS (never a person, never
 * a rate), and what it shows of the answer.
 */

function seed(role: 'staff' | 'tax_advisor' = 'staff') {
  useCompanyStore.setState({
    companies: [
      {
        id: 'c1',
        legal_name: 'YON Studio OG',
        trading_name: null,
        display_name: 'YON Studio OG',
        default_currency: 'EUR',
        role,
        permissions: ['company.view', 'projects.view'],
        expires_at: null,
      },
    ],
    activeCompanyId: 'c1',
    loaded: true,
    error: null,
  })
}

const ITEMS = [
  { item_id: 'i-dop', name: 'DoP' },
  { item_id: 'i-gaf', name: 'Gaffer' },
]

function profile(overrides: Partial<MyCrewProfile> = {}): MyCrewProfile {
  return {
    is_crew: false,
    crew_state: 'not_crew',
    contact_name: null,
    roles: [],
    labor_items: ITEMS,
    message: null,
    ...overrides,
  }
}

const CREW = profile({
  is_crew: true,
  crew_state: 'crew',
  contact_name: 'Nora Tester',
  roles: [
    { id: 'r1', item_id: 'i-gaf', name: 'Gaffer', confirmed: true, set_by: 'admin' },
    { id: 'r2', item_id: 'i-dop', name: 'DoP', confirmed: false, set_by: 'self' },
  ],
})

const NEEDS_LINK = 'A contact with your email already exists. An admin has to connect it to your login.'

describe('My crew profile', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seed()
  })

  it('the switch sends only the on/off, and the roles appear once on', async () => {
    vi.mocked(api.get).mockResolvedValue(profile() as never)
    vi.mocked(api.put).mockResolvedValue({ profile: CREW, future_bookings: [] } as never)
    render(<MyCrewProfilePage />)
    const toggle = await screen.findByTestId('works-as-crew-switch')
    expect(screen.getByText('Switch on “I also work as crew” to choose your roles.')).toBeInTheDocument()
    fireEvent.click(toggle)
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/me/crew', { is_crew: true }))
    expect(await screen.findByTestId('my-role-picker')).toBeInTheDocument()
  })

  it('shows confirmed and self-reported per role, saves exactly the ticked labor items, and has no rate anywhere', async () => {
    vi.mocked(api.get).mockResolvedValue(CREW as never)
    vi.mocked(api.put).mockResolvedValue(CREW as never)
    render(<MyCrewProfilePage />)
    const picker = within(await screen.findByTestId('my-role-picker'))
    expect(screen.getByTestId('role-state-i-gaf')).toHaveTextContent('confirmed')
    expect(screen.getByTestId('role-state-i-dop')).toHaveTextContent('self-reported')
    expect(screen.getByText('An admin confirms these. Day rates are set by an admin.')).toBeInTheDocument()
    // No field to type a number into, anywhere on the page.
    expect(screen.queryAllByRole('textbox')).toEqual([])
    expect(screen.queryAllByRole('spinbutton')).toEqual([])

    fireEvent.click(picker.getByLabelText('Gaffer'))
    fireEvent.click(screen.getByRole('button', { name: 'Save roles' }))
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/me/crew/roles', { item_ids: ['i-dop'] }))
    const [, body] = vi.mocked(api.put).mock.calls[0]
    expect(Object.keys(body as object)).toEqual(['item_ids'])
  })

  it('when a contact already has my email: shows the sentence and nothing else changes', async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce(profile() as never)
      .mockResolvedValue(profile({ crew_state: 'needs_link', message: NEEDS_LINK }) as never)
    vi.mocked(api.put).mockRejectedValue(new ApiError(409, NEEDS_LINK))
    render(<MyCrewProfilePage />)
    fireEvent.click(await screen.findByTestId('works-as-crew-switch'))
    expect(await screen.findByTestId('needs-link-message')).toHaveTextContent(NEEDS_LINK)
    expect(screen.getByTestId('works-as-crew-switch')).toHaveAttribute('data-state', 'unchecked')
    expect(screen.queryByTestId('my-role-picker')).toBeNull()
  })

  it('a tax advisor is told the page is not for them', async () => {
    seed('tax_advisor')
    vi.mocked(api.get).mockRejectedValue(new ApiError(404, 'Not found'))
    render(<MyCrewProfilePage />)
    expect(await screen.findByTestId('crew-profile-closed')).toHaveTextContent(
      'Your access to YON Studio OG does not include a crew profile.',
    )
  })
})

function entry(overrides: Partial<MyScheduleEntry> = {}): MyScheduleEntry {
  return {
    assignment_id: 'a1',
    project_id: 'p1',
    project_name: 'Spot',
    strip_name: 'Shoot',
    role_name: 'DoP',
    status: 'booked',
    note: 'Confirmed by phone',
    location: 'Studio Wien',
    first_date: '2026-12-07',
    last_date: '2026-12-08',
    days: [
      { date: '2026-12-07', call_time: '08:00:00', wrap_time: '19:00:00', location: 'Studio Wien' },
      { date: '2026-12-08', call_time: '07:30:00', wrap_time: null, location: 'Studio Wien' },
    ],
    ...overrides,
  }
}

describe('My schedule', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seed()
  })

  it('lists my bookings grouped by project and strip, with role, status, call, wrap, location and the note', async () => {
    const schedule: MySchedule = {
      available: true,
      upcoming: [
        entry(),
        entry({ assignment_id: 'a2', strip_name: 'Reshoot', status: 'scheduled', note: null, first_date: '2026-12-15', last_date: '2026-12-15', days: [{ date: '2026-12-15', call_time: null, wrap_time: null, location: null }] }),
        entry({ assignment_id: 'a3', project_name: 'Doku', strip_name: 'Dreh', first_date: '2027-01-10', last_date: '2027-01-10', days: [{ date: '2027-01-10', call_time: null, wrap_time: null, location: 'Linz' }] }),
      ],
      past: [],
    }
    vi.mocked(api.get).mockResolvedValue(schedule as never)
    render(<MySchedulePage />)
    const projects = await screen.findAllByTestId('schedule-project')
    expect(projects.map((p) => p.textContent)).toEqual(['Spot', 'Doku'])
    const entries = screen.getAllByTestId('schedule-entry')
    expect(entries).toHaveLength(3)
    expect(within(entries[0]).getByText('Shoot')).toBeInTheDocument()
    expect(within(entries[0]).getByTestId('schedule-status')).toHaveTextContent('Booked')
    expect(within(entries[1]).getByTestId('schedule-status')).toHaveTextContent('Scheduled')
    const days = within(entries[0]).getAllByTestId('schedule-day').map((d) => d.textContent ?? '')
    expect(days[0]).toContain('call 08:00 · wrap 19:00 · Studio Wien')
    expect(days[1]).toContain('call 07:30 · Studio Wien')
    expect(within(entries[0]).getByText('Note: Confirmed by phone')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/me/schedule')
  })

  it('says “No bookings yet.” when there are none', async () => {
    vi.mocked(api.get).mockResolvedValue({ available: true, upcoming: [], past: [] } as never)
    render(<MySchedulePage />)
    expect(await screen.findByTestId('schedule-empty')).toHaveTextContent('No bookings yet.')
  })

  it('points to My crew profile when I do not work as crew', async () => {
    vi.mocked(api.get).mockResolvedValue({ available: false, upcoming: [], past: [] } as never)
    render(<MySchedulePage />)
    expect(await screen.findByTestId('schedule-unavailable')).toHaveTextContent(
      'Your schedule shows once you work as crew. Switch it on in My crew profile.',
    )
  })
})
