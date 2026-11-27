import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
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
  usePathname: () => '/settings/company/members',
  useParams: () => ({}),
}))

import { api } from '@/lib/api'
import CompanyMembersPage from '../members/page'
import { useCompanyStore } from '@/stores/company-store'
import type { CompanyMember, TeamMember, TeamStopResult } from '@/types'

/**
 * Settings → Team (R2): the members page with its Crew column. The REAL page
 * is rendered against the API's own shapes (`/company/members`, `/team`,
 * `/crew/roles`); what is under test is what each action SENDS and the
 * sentences the screen shows.
 */

function seed() {
  useCompanyStore.setState({
    companies: [
      {
        id: 'c1',
        legal_name: 'YON Studio OG',
        trading_name: null,
        display_name: 'YON Studio OG',
        default_currency: 'EUR',
        role: 'owner',
        permissions: ['company.view', 'company.members.manage', 'parties.view', 'parties.edit'],
        expires_at: null,
      },
    ],
    activeCompanyId: 'c1',
    loaded: true,
    error: null,
  })
}

function membership(userId: string, name: string, email: string): CompanyMember {
  return {
    id: `m-${userId}`,
    user_id: userId,
    email,
    name,
    role: 'staff',
    status: 'active',
    granted_at: '2026-11-01T10:00:00Z',
    expires_at: null,
    revoked_at: null,
    is_active: true,
    two_factor_enabled: false,
  }
}

function teamMember(overrides: Partial<TeamMember> & { user_id: string; name: string; email: string }): TeamMember {
  return {
    role: 'staff',
    has_active_login: true,
    crew_state: 'not_crew',
    contact: null,
    roles: [],
    suggestion: null,
    matches: [],
    ...overrides,
  }
}

const SAM = teamMember({
  user_id: 'u-sam',
  name: 'Sam Tester',
  email: 'sam@yon.example',
  crew_state: 'needs_link',
  suggestion: { id: 'p-anna', name: 'Anna Berger', email: 'sam@yon.example' },
  matches: [{ id: 'p-anna', name: 'Anna Berger', email: 'sam@yon.example' }],
})
const NORA = teamMember({ user_id: 'u-nora', name: 'Nora Tester', email: 'nora@yon.example' })
const KIM = teamMember({
  user_id: 'u-kim',
  name: 'Kim Tester',
  email: 'kim@yon.example',
  crew_state: 'needs_link',
  matches: [
    { id: 'p-k1', name: 'Kim A.', email: 'kim@yon.example' },
    { id: 'p-k2', name: 'Kim B.', email: 'kim@yon.example' },
  ],
})
const MAX = teamMember({
  user_id: 'u-max',
  name: 'Max Tester',
  email: 'max@yon.example',
  crew_state: 'crew',
  contact: { id: 'p-max', name: 'Max Crew', email: 'max@yon.example' },
  roles: [
    { id: 'r-gaf', item_id: 'i-gaf', name: 'Gaffer', confirmed: true, set_by: 'admin' },
    { id: 'r-dop', item_id: 'i-dop', name: 'DoP', confirmed: false, set_by: 'self' },
  ],
})
const ALL = [SAM, NORA, KIM, MAX]

function routeGets(team: TeamMember[] = ALL) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/company/members') return team.map((t) => membership(t.user_id, t.name, t.email)) as never
    if (path === '/team') return { members: team } as never
    if (path === '/crew/roles') return { used: [], catalog: [], labor_items: [{ item_id: 'i-dop', name: 'DoP' }, { item_id: 'i-gaf', name: 'Gaffer' }] } as never
    throw new Error(`unexpected GET ${path}`)
  })
}

async function cell(userId: string) {
  return within(await screen.findByTestId(`crew-cell-${userId}`))
}

async function openDialog(userId: string, label: RegExp) {
  render(<CompanyMembersPage />)
  fireEvent.click((await cell(userId)).getByRole('button', { name: label }))
  const dialog = await screen.findByTestId('works-as-crew-dialog')
  // Gate on the fetched roles, not on the dialog's frame.
  await within(dialog).findByLabelText('DoP')
  return within(dialog)
}

describe('Settings → Team: the Crew column', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seed()
    routeGets()
    vi.mocked(api.post).mockResolvedValue({} as never)
  })

  it('shows each member’s crew state, the roles as chips and self-reported ones with Confirm', async () => {
    render(<CompanyMembersPage />)
    expect((await cell('u-sam')).getByText('Needs link')).toBeInTheDocument()
    expect((await cell('u-nora')).getByRole('button', { name: 'Works as crew' })).toBeInTheDocument()
    const max = await cell('u-max')
    expect(max.getByRole('link', { name: 'Max Crew' })).toHaveAttribute('href', '/contacts/p-max')
    const chips = max.getAllByTestId('team-role-chip')
    expect(chips.map((chip) => chip.textContent)).toEqual(['Gaffer', 'DoPself-reportedConfirm'])
    expect(within(chips[0]).queryByTestId('self-reported')).toBeNull()
  })

  it('Confirm sends the role to the confirm route', async () => {
    render(<CompanyMembersPage />)
    fireEvent.click((await cell('u-max')).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/team/u-max/crew/roles/r-dop/confirm', {}))
  })

  it('one contact has the email: offers it, and Link sends that contact with the roles picked', async () => {
    const dialog = await openDialog('u-sam', /Connect contact/)
    expect(dialog.getByTestId('suggestion-sentence')).toHaveTextContent(
      'Link to the existing contact Anna Berger (sam@yon.example)?',
    )
    fireEvent.click(dialog.getByLabelText('DoP'))
    fireEvent.click(dialog.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/team/u-sam/crew', {
        mode: 'link',
        party_id: 'p-anna',
        role_item_ids: ['i-dop'],
      }),
    )
  })

  it('“Create a new crew contact instead” sends create, and never the suggested contact', async () => {
    const dialog = await openDialog('u-sam', /Connect contact/)
    fireEvent.click(dialog.getByRole('button', { name: 'Create a new crew contact instead' }))
    fireEvent.click(dialog.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    const [path, body] = vi.mocked(api.post).mock.calls[0]
    expect(path).toBe('/team/u-sam/crew')
    expect(body).toEqual({ mode: 'create', role_item_ids: [] })
  })

  it('no contact has the email: says so and creates one', async () => {
    const dialog = await openDialog('u-nora', /Works as crew/)
    expect(dialog.getByTestId('no-match-sentence')).toHaveTextContent(
      'No contact has this email. Create a crew contact for Nora Tester?',
    )
    fireEvent.click(dialog.getByLabelText('Gaffer'))
    fireEvent.click(dialog.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/team/u-nora/crew', { mode: 'create', role_item_ids: ['i-gaf'] }),
    )
  })

  it('several contacts have the email: Save says why until one is picked, then links that one', async () => {
    const dialog = await openDialog('u-kim', /Connect contact/)
    expect(dialog.getByTestId('match-picker')).toBeInTheDocument()
    expect(dialog.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(dialog.getByTestId('save-why')).toHaveTextContent('Choose a contact, or create a new one.')
    fireEvent.click(dialog.getByLabelText('Kim B. (kim@yon.example)'))
    expect(dialog.queryByTestId('save-why')).toBeNull()
    fireEvent.click(dialog.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/team/u-kim/crew', { mode: 'link', party_id: 'p-k2', role_item_ids: [] }),
    )
  })

  it('the dialog has no rate field anywhere', async () => {
    const dialog = await openDialog('u-max', /Roles…/)
    expect(dialog.queryAllByRole('textbox')).toEqual([])
    expect(dialog.queryAllByRole('spinbutton')).toEqual([])
    expect(dialog.getByText('Day rates are set on the contact, as for any crew.')).toBeInTheDocument()
    // The held roles come ticked.
    expect(dialog.getByLabelText('Gaffer')).toBeChecked()
    expect(dialog.getByLabelText('DoP')).toBeChecked()
  })

  it('Stop working as crew says how many future bookings stay, on which projects, and that nothing was deleted', async () => {
    const result: TeamStopResult = {
      member: { ...MAX, crew_state: 'not_crew', roles: [] },
      future_bookings: [
        { assignment_id: 'a1', project_id: 'p1', project_name: 'Spot', strip_name: 'Shoot', status: 'booked', first_date: '2026-12-07', last_date: '2026-12-08' },
        { assignment_id: 'a2', project_id: 'p2', project_name: 'Doku', strip_name: 'Dreh', status: 'scheduled', first_date: '2026-12-20', last_date: '2026-12-20' },
      ],
    }
    vi.mocked(api.post).mockResolvedValue(result as never)
    render(<CompanyMembersPage />)
    fireEvent.click((await cell('u-max')).getByRole('button', { name: 'Stop working as crew' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Stop working as crew' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/team/u-max/crew/stop', {}))
    expect(await screen.findByTestId('crew-notice')).toHaveTextContent(
      'Max Crew no longer works as crew. They have 2 future bookings (Spot and Doku); those stay as they are. Nothing was deleted.',
    )
  })
})
