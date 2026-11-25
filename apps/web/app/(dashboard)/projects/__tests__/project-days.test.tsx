import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn(), upload: vi.fn(), download: vi.fn() },
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
  usePathname: () => '/projects/p1',
  useParams: () => ({ id: 'p1' }),
  useRouter: () => ({ push: vi.fn() }),
}))

import { api } from '@/lib/api'
import ProjectDetailPage from '../[id]/page'
import type { BlockChange, CalendarDay, CalendarStrip, ProjectCalendar } from '@/types'
import { datesBetween } from '@/lib/calendar-dates'
import { ALL_PERMISSIONS, makeProject, seedCompany } from './fixtures'

/**
 * The Days tab as a calendar (B2a), driven the way a person drives it: by
 * pointer events (drag across days, drag a strip, drag its edge) and by the
 * keyboard and buttons that do the same without dragging.
 *
 * Responses are in the API's own shapes. **The counts are the API's**: the
 * calendar fixture says `7 shoot` while its strips hold four shoot days, so
 * a screen that counted for itself would print 4 — the mutation this file
 * exists to catch.
 */

function day(date: string, type: string, overrides: Partial<CalendarDay> = {}, main = 'shoot'): CalendarDay {
  return {
    id: `day-${date}-${type}`,
    block_id: 'b-main',
    date,
    type,
    location: null,
    call_time: null,
    wrap_time: null,
    notes: null,
    effective_location: 'Kaprun',
    effective_call_time: null,
    effective_wrap_time: null,
    is_override: type !== main,
    has_own_data: false,
    updated_at: '2026-11-25T00:00:00Z',
    ...overrides,
  }
}

function strip(
  id: string,
  start: string,
  end: string,
  overrides: Partial<CalendarStrip> = {},
  types: Record<string, string> = {},
): CalendarStrip {
  const main = overrides.type ?? 'shoot'
  const days = datesBetween(start, end).map((date) => ({
    ...day(date, types[date] ?? main, {}, main),
    id: `${id}-${date}`,
    block_id: id,
  }))
  return {
    id,
    kind: 'strip',
    type: main,
    label: null,
    start_date: start,
    end_date: end,
    location: 'Kaprun',
    default_call_time: null,
    default_wrap_time: null,
    notes: null,
    position: null,
    is_active: true,
    days,
    counts: {},
    ...overrides,
  }
}

// Monday 2 March – Thursday 5 March 2026, a travel day at the start.
const MAIN = strip('b-main', '2026-03-02', '2026-03-05', { label: 'Main shoot', counts: { travel: 1, shoot: 3 } }, { '2026-03-02': 'travel' })

function calendar(strips: CalendarStrip[] = [MAIN], counts: Record<string, number> = { shoot: 7, prep: 1 }): ProjectCalendar {
  return { project_id: 'p1', project_start: '2026-03-02', project_end: '2026-03-13', strips, counts }
}

function change(next: Partial<BlockChange> = {}): BlockChange {
  return { applied: true, needs_confirm: false, detail: 'Done.', strip: MAIN, days_with_data: [], added: 0, removed: 0, changed: 0, counts: { shoot: 7, prep: 1 }, ...next }
}

let current: ProjectCalendar

function mockApi() {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/projects/p1') return makeProject() as never
    if (path === '/projects/p1/calendar') return current as never
    return [] as never
  })
}

async function openDays() {
  render(<ProjectDetailPage />)
  fireEvent.click(await screen.findByRole('tab', { name: 'Days' }))
  await screen.findAllByTestId('calendar-strip')
}

const cell = (date: string) => document.querySelector(`[data-testid="calendar-day"][data-date="${date}"]`) as HTMLElement
const stripDay = (id: string, date: string) =>
  document.querySelector(`[data-strip-id="${id}"] [data-testid="strip-day"][data-date="${date}"]`) as HTMLElement
const bar = (id: string) => document.querySelector(`[data-testid="calendar-strip"][data-strip-id="${id}"]`) as HTMLElement

/** A drag the way the grid follows one: down on an element, moves over
 *  others (they bubble to `window`, where the grid listens), up. */
function drag(from: Element, ...over: Element[]) {
  fireEvent.pointerDown(from, { button: 0 })
  for (const element of over) fireEvent.pointerMove(element)
  fireEvent.pointerUp(over[over.length - 1] ?? from)
  // The click a browser fires after the drag: must not open anything.
  fireEvent.click(over[over.length - 1] ?? from)
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany(ALL_PERMISSIONS)
  current = calendar()
  mockApi()
})

describe('the calendar', () => {
  it('prints the API’s counts, not the number of days it was sent', async () => {
    await openDays()
    expect(screen.getByTestId('day-counts').textContent).toBe('1 prep · 7 shoot')
  })

  it('opens on the month of the first strip and draws the strip across its days', async () => {
    await openDays()
    expect(screen.getByTestId('calendar-month').textContent).toBe('March 2026')
    const days = Array.from(bar('b-main').querySelectorAll('[data-testid="strip-day"]'))
    expect(days.map((element) => element.getAttribute('data-date'))).toEqual(['2026-03-02', '2026-03-03', '2026-03-04', '2026-03-05'])
    // The travel day is marked as an override and carries the plane.
    expect(stripDay('b-main', '2026-03-02').getAttribute('data-override')).toBe('true')
    expect(within(stripDay('b-main', '2026-03-02')).getByLabelText('Travel day')).toBeInTheDocument()
    expect(stripDay('b-main', '2026-03-03').getAttribute('data-override')).toBeNull()
  })

  it('wraps a strip across week rows like a multi-day event', async () => {
    // Thursday 12 – Tuesday 17 March: two week rows.
    current = calendar([strip('b-long', '2026-03-12', '2026-03-17', { label: 'Long' })])
    await openDays()
    expect(document.querySelectorAll('[data-strip-id="b-long"]')).toHaveLength(2)
  })

  it('stacks overlapping strips in lanes and shows "+N" for the ones that do not fit', async () => {
    current = calendar([
      MAIN,
      strip('b-2', '2026-03-04', '2026-03-04', { label: 'BTS' }),
      strip('b-3', '2026-03-04', '2026-03-06', { label: 'Second unit', type: 'prep' }),
      strip('b-4', '2026-03-04', '2026-03-04', { label: 'Recce', type: 'prep' }),
    ])
    await openDays()

    const lanes = ['b-main', 'b-2', 'b-3'].map((id) => bar(id).getAttribute('data-lane'))
    expect(new Set(lanes).size).toBe(3)
    expect(bar('b-4')).toBeNull()
    const more = within(cell('2026-03-04')).getByTestId('day-more')
    expect(more.textContent).toBe('+1')

    fireEvent.click(more)
    const list = await screen.findByTestId('day-strips')
    expect(within(list).getAllByRole('button').map((b) => b.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining('Recce')]),
    )
    fireEvent.click(within(list).getByText('Recce'))
    expect(within(await screen.findByTestId('strip-panel')).getAllByText('Recce').length).toBeGreaterThan(0)
  })

  it('marks today and the project’s start and end', async () => {
    await openDays()
    expect(within(cell('2026-03-02')).getByTestId('project-start-mark')).toBeInTheDocument()
    expect(within(cell('2026-03-13')).getByTestId('project-end-mark')).toBeInTheDocument()
  })
})

describe('creating a strip', () => {
  it('by dragging across empty days, then Create', async () => {
    vi.mocked(api.post).mockResolvedValue(
      change({ strip: strip('b-new', '2026-03-10', '2026-03-12', { label: 'BTS shoot' }), counts: { shoot: 10, prep: 1 }, detail: '3 days added.' }) as never,
    )
    await openDays()

    drag(cell('2026-03-10'), cell('2026-03-11'), cell('2026-03-12'))
    const form = await screen.findByTestId('create-strip-form')
    expect((within(form).getByLabelText('New strip from') as HTMLInputElement).value).toBe('2026-03-10')
    expect((within(form).getByLabelText('New strip to') as HTMLInputElement).value).toBe('2026-03-12')

    fireEvent.change(within(form).getByLabelText('New strip label'), { target: { value: 'BTS shoot' } })
    fireEvent.click(within(form).getByLabelText('Weekends off'))
    fireEvent.click(within(form).getByText('Create'))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0]).toEqual([
      '/projects/p1/blocks',
      { type: 'shoot', label: 'BTS shoot', location: null, start_date: '2026-03-10', end_date: '2026-03-12', weekends: 'off' },
    ])
    // The header follows the API's answer, and the new strip is drawn and open.
    await waitFor(() => expect(screen.getByTestId('day-counts').textContent).toBe('1 prep · 10 shoot'))
    expect(bar('b-new')).not.toBeNull()
    expect(screen.getByTestId('strip-panel')).toBeInTheDocument()
  })

  it('by clicking a day and shift-clicking another', async () => {
    await openDays()
    fireEvent.click(cell('2026-03-16'))
    fireEvent.click(cell('2026-03-19'), { shiftKey: true })
    const form = await screen.findByTestId('create-strip-form')
    expect((within(form).getByLabelText('New strip from') as HTMLInputElement).value).toBe('2026-03-16')
    expect((within(form).getByLabelText('New strip to') as HTMLInputElement).value).toBe('2026-03-19')
  })

  it('from the keyboard with the Add strip button and date fields', async () => {
    await openDays()
    fireEvent.click(screen.getByText('Add strip'))
    const form = await screen.findByTestId('create-strip-form')
    expect(within(form).getByText('Pick the first day.')).toBeInTheDocument()
    expect(within(form).getByText('Create')).toBeDisabled()
    fireEvent.change(within(form).getByLabelText('New strip from'), { target: { value: '2026-03-20' } })
    fireEvent.change(within(form).getByLabelText('New strip to'), { target: { value: '2026-03-20' } })
    expect(within(form).getByText('Create')).not.toBeDisabled()
  })
})

describe('the strip panel', () => {
  it('opens by click and by Enter, and Escape closes it', async () => {
    await openDays()
    fireEvent.click(bar('b-main'))
    expect(await screen.findByTestId('strip-panel')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('strip-panel')).toBeNull())

    bar('b-main').focus()
    fireEvent.keyDown(bar('b-main'), { key: 'Enter' })
    expect(await screen.findByTestId('strip-panel')).toBeInTheDocument()
  })

  it('marks a middle day as travel and shows the counts the API sends back', async () => {
    const after = strip('b-main', '2026-03-02', '2026-03-05', { label: 'Main shoot', counts: { travel: 2, shoot: 2 } }, { '2026-03-02': 'travel', '2026-03-04': 'travel' })
    vi.mocked(api.put).mockResolvedValue(change({ strip: after, counts: { shoot: 6, travel: 2, prep: 1 } }) as never)
    await openDays()
    fireEvent.click(bar('b-main'))
    const panel = await screen.findByTestId('strip-panel')

    expect(within(panel).getByText('Mark as travel')).toBeDisabled()
    expect(within(panel).getByTestId('selection-hint')).toBeInTheDocument()
    fireEvent.click(within(panel).getByLabelText('4 March 2026, Shoot'))
    fireEvent.click(within(panel).getByText('Mark as travel'))

    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0]).toEqual(['/projects/p1/blocks/b-main/days', { days: [{ date: '2026-03-04', type: 'travel' }] }])
    await waitFor(() => expect(screen.getByTestId('day-counts').textContent).toBe('1 prep · 6 shoot · 2 travel'))
    expect(within(panel).getByTestId('strip-counts').textContent).toBe('2 shoot · 2 travel')
    expect(stripDay('b-main', '2026-03-04').getAttribute('data-type')).toBe('travel')
  })

  it('selects a range with shift-click and adds one with ctrl-click', async () => {
    vi.mocked(api.put).mockResolvedValue(change() as never)
    await openDays()
    fireEvent.click(bar('b-main'))
    const panel = await screen.findByTestId('strip-panel')
    fireEvent.click(within(panel).getByLabelText('3 March 2026, Shoot'))
    fireEvent.click(within(panel).getByLabelText('5 March 2026, Shoot'), { shiftKey: true })
    fireEvent.click(within(panel).getByLabelText('2 March 2026, Travel'), { ctrlKey: true })
    fireEvent.click(within(panel).getByText('Back to shoot'))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual({
      days: ['2026-03-02', '2026-03-03', '2026-03-04', '2026-03-05'].map((date) => ({ date, type: null })),
    })
  })

  it('first day and last day = travel are one click each', async () => {
    vi.mocked(api.put).mockResolvedValue(change() as never)
    await openDays()
    fireEvent.click(bar('b-main'))
    const panel = await screen.findByTestId('strip-panel')
    fireEvent.click(within(panel).getByText('Last day = travel'))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual({ days: [{ date: '2026-03-05', type: 'travel' }] })
  })

  it('moves and resizes through its fields', async () => {
    vi.mocked(api.post).mockResolvedValue(change() as never)
    await openDays()
    fireEvent.click(bar('b-main'))
    const panel = await screen.findByTestId('strip-panel')

    fireEvent.change(within(panel).getByLabelText('Move to start on'), { target: { value: '2026-03-16' } })
    fireEvent.click(within(panel).getByText('Move'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b-main/move', { start_date: '2026-03-16' }))

    const end = within(panel).getByLabelText('Strip end')
    fireEvent.change(end, { target: { value: '2026-03-06' } })
    fireEvent.blur(end)
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/projects/p1/blocks/b-main/resize', { start_date: '2026-03-02', end_date: '2026-03-06', confirm: false }),
    )
  })

  it('shows a day’s own details with the strip’s default as the placeholder', async () => {
    vi.mocked(api.patch).mockResolvedValue({} as never)
    await openDays()
    fireEvent.click(bar('b-main'))
    const panel = await screen.findByTestId('strip-panel')
    fireEvent.click(within(panel).getByLabelText('3 March 2026, Shoot'))
    const location = within(screen.getByTestId('day-details')).getByLabelText('Location 2026-03-03') as HTMLInputElement
    expect(location.placeholder).toBe('Kaprun')
    fireEvent.change(location, { target: { value: 'Zell am See' } })
    fireEvent.blur(location)
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/days/b-main-2026-03-03', { location: 'Zell am See' }))
  })
})

describe('dragging strips', () => {
  it('drags the body to move the strip by the days the pointer travelled', async () => {
    vi.mocked(api.post).mockResolvedValue(change() as never)
    await openDays()

    // Grab the strip on the 3rd, let go over the 10th: one week later.
    drag(stripDay('b-main', '2026-03-03'), cell('2026-03-05'), cell('2026-03-10'))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0]).toEqual(['/projects/p1/blocks/b-main/move', { start_date: '2026-03-09' }])
    // The click that follows the drop did not open the panel.
    expect(screen.queryByTestId('strip-panel')).toBeNull()
  })

  it('drags the right edge to resize, and asks first when days with details would go', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce(
        change({
          applied: false,
          needs_confirm: true,
          detail: '1 day that would be taken out has notes, a location or times of its own: 5 March 2026. Confirm to take it out anyway.',
          days_with_data: [day('2026-03-05', 'shoot', { notes: 'Drone permit', has_own_data: true })],
        }) as never,
      )
      .mockResolvedValueOnce(change({ removed: 1 }) as never)
    await openDays()

    const handle = within(bar('b-main')).getByTestId('strip-handle-end')
    drag(handle, cell('2026-03-04'))

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.post).mock.calls[0]).toEqual([
      '/projects/p1/blocks/b-main/resize',
      { start_date: '2026-03-02', end_date: '2026-03-04', confirm: false },
    ])
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('5 March 2026')

    fireEvent.click(within(dialog).getByText('Take them out'))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.post).mock.calls[1][1]).toEqual({ start_date: '2026-03-02', end_date: '2026-03-04', confirm: true })
  })

  it('drags the left edge to make the strip start earlier', async () => {
    vi.mocked(api.post).mockResolvedValue(change() as never)
    await openDays()
    drag(within(bar('b-main')).getByTestId('strip-handle-start'), cell('2026-02-27'))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0][1]).toEqual({ start_date: '2026-02-27', end_date: '2026-03-05', confirm: false })
  })
})

describe('read-only mode', () => {
  it('without projects.edit: nothing to drag or press, and a sentence says why', async () => {
    seedCompany(['projects.view'])
    await openDays()
    expect(screen.queryByText('Add strip')).toBeNull()
    expect(screen.getByTestId('calendar-read-only').textContent).toContain('project edit permission')
    expect(screen.getByTestId('projects-read-only')).toBeInTheDocument()
    expect(screen.queryByTestId('strip-handle-end')).toBeNull()

    // A drag does nothing.
    drag(stripDay('b-main', '2026-03-03'), cell('2026-03-10'))
    drag(cell('2026-03-16'), cell('2026-03-18'))
    expect(api.post).not.toHaveBeenCalled()
    expect(screen.queryByTestId('create-strip-form')).toBeNull()

    // The panel opens, but shows values instead of fields and no buttons.
    fireEvent.click(bar('b-main'))
    const panel = await screen.findByTestId('strip-panel')
    expect(within(panel).queryByLabelText('Strip label')).toBeNull()
    expect(within(panel).queryByText('Mark as travel')).toBeNull()
    expect(within(panel).queryByText('Remove strip')).toBeNull()
  })

  it('a staff member sees the Budgets tab explain why it is closed', async () => {
    seedCompany(['projects.view'])
    render(<ProjectDetailPage />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Budgets' }))
    expect(screen.getByTestId('budgets-no-access').textContent).toContain('cost rates and margins')
    expect(vi.mocked(api.get).mock.calls.some((call) => String(call[0]).includes('/budgets'))).toBe(false)
  })
})

describe('editing one day', () => {
  it('clicking a day inside a strip opens the panel on that day', async () => {
    await openDays()
    fireEvent.click(stripDay('b-main', '2026-03-04'))
    const details = await screen.findByTestId('day-details')
    expect(details.textContent).toContain('4 March 2026')
    expect((within(details).getByLabelText('Type 2026-03-04') as HTMLSelectElement).value).toBe('shoot')
    expect(within(details).getByLabelText('Start time 2026-03-04')).toBeInTheDocument()
    expect(within(details).getByLabelText('End time 2026-03-04')).toBeInTheDocument()
  })

  it('changes any day’s type there, and back to the strip’s main type', async () => {
    vi.mocked(api.put).mockResolvedValue(change() as never)
    await openDays()
    fireEvent.click(stripDay('b-main', '2026-03-04'))
    const details = await screen.findByTestId('day-details')
    fireEvent.change(within(details).getByLabelText('Type 2026-03-04'), { target: { value: 'office' } })
    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.put).mock.calls[0]).toEqual(['/projects/p1/blocks/b-main/days', { days: [{ date: '2026-03-04', type: 'office' }] }])

    // The travel day back to shoot (the main type) is sent as null.
    fireEvent.click(stripDay('b-main', '2026-03-02'))
    const travel = await screen.findByTestId('day-details')
    fireEvent.change(within(travel).getByLabelText('Type 2026-03-02'), { target: { value: 'shoot' } })
    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.put).mock.calls[1][1]).toEqual({ days: [{ date: '2026-03-02', type: null }] })
  })

  it('sets a start and end time on a travel day like on any other', async () => {
    vi.mocked(api.patch).mockResolvedValue({} as never)
    await openDays()
    fireEvent.click(stripDay('b-main', '2026-03-02'))
    const details = await screen.findByTestId('day-details')
    const start = within(details).getByLabelText('Start time 2026-03-02')
    fireEvent.change(start, { target: { value: '06:15' } })
    fireEvent.blur(start)
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/days/b-main-2026-03-02', { call_time: '06:15' }))
    const end = within(details).getByLabelText('End time 2026-03-02')
    fireEvent.change(end, { target: { value: '21:00' } })
    fireEvent.blur(end)
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/days/b-main-2026-03-02', { wrap_time: '21:00' }))
  })

  it('a day added on its own (outside any strip) is edited and removed from its panel', async () => {
    const loose: CalendarStrip = {
      ...strip('d-loose', '2026-03-20', '2026-03-20', { kind: 'day', type: 'office', label: null, location: null }),
    }
    loose.days = [{ ...loose.days[0], id: 'd-loose', block_id: null, is_override: false }]
    current = calendar([MAIN, loose])
    vi.mocked(api.patch).mockResolvedValue({} as never)
    vi.mocked(api.post).mockResolvedValue({} as never)
    await openDays()
    fireEvent.click(stripDay('d-loose', '2026-03-20'))
    const details = await screen.findByTestId('day-details')
    fireEvent.change(within(details).getByLabelText('Type 2026-03-20'), { target: { value: 'prep' } })
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/days/d-loose', { type: 'prep' }))
    fireEvent.click(screen.getByText('Remove this day'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/projects/p1/days/d-loose/remove'))
  })

  it('there is no List view any more', async () => {
    await openDays()
    expect(screen.queryByText('List')).toBeNull()
    expect(screen.queryByTestId('day-list')).toBeNull()
  })
})
