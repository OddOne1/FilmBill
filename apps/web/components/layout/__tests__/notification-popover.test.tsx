/**
 * UI-1 part 2: notifications as a small popup from the top bar.
 *
 * The REAL notification store runs here, fed by a mocked `api` that answers
 * with the shape `/me/notifications` really returns (`NotificationResponse`
 * from the generated types). So the badge is the store's own count of what
 * the API sent, and "Mark all as read" goes through the store's real
 * optimistic update to the endpoint it really calls.
 *
 * The real Header is rendered (not the bell alone) so "beside Search" is a
 * property of the top bar, and the real Sidebar is rendered for "no sidebar
 * item".
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const get = vi.fn()
const post = vi.fn()
vi.mock('@/lib/api', () => ({
  api: {
    get: (path: string) => get(path),
    post: (path: string, body?: unknown) => post(path, body),
    patch: vi.fn(() => Promise.resolve({})),
  },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))
let pathname = '/'
vi.mock('next/navigation', () => ({ usePathname: () => pathname }))
vi.mock('@/hooks/use-site-settings', () => ({
  useSiteSettings: () => ({ orgName: 'FilmBill', logoDarkUrl: null, logoLightUrl: null }),
}))
vi.mock('../company-switcher', () => ({ CompanySwitcher: () => null }))

import { Header } from '../header'
import { Sidebar } from '../sidebar'
import { badgeText } from '../notification-popover'
import { useNotificationStore } from '@/stores/notification-store'
import { useCompanyStore } from '@/stores/company-store'
import type { Notification } from '@/types'

function note(id: string, read: boolean, title = `Notice ${id}`): Notification {
  return {
    id,
    type: 'account',
    title,
    body: 'Somebody changed something on your account.',
    link: null,
    read,
    created_at: '2026-11-26T10:00:00Z',
  }
}

function serve(list: Notification[]) {
  get.mockImplementation(async (path: string) => {
    if (path === '/me/notifications') return list
    throw new Error(`unexpected GET ${path}`)
  })
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  post.mockResolvedValue(undefined)
  pathname = '/'
  useNotificationStore.setState({ notifications: [], unreadCount: 0, isLoading: false })
})

function renderHeader() {
  return render(<Header onSearchOpen={() => {}} />)
}

const bell = () => screen.getByTestId('notification-bell')

describe('the bell in the top bar', () => {
  it('sits in the top bar right after Search', async () => {
    serve([])
    renderHeader()
    const search = screen.getByText('Search').closest('button')!
    expect(search.nextElementSibling).toBe(bell())
  })

  it('shows the unread count the API reported', async () => {
    serve([note('1', false), note('2', false), note('3', true)])
    renderHeader()
    await waitFor(() => expect(screen.getByTestId('notification-badge')).toHaveTextContent('2'))
    expect(bell()).toHaveAccessibleName('Notifications, 2 unread')
  })

  it('shows "9+" above nine', async () => {
    serve(Array.from({ length: 12 }, (_, i) => note(String(i), false)))
    renderHeader()
    await waitFor(() => expect(screen.getByTestId('notification-badge')).toHaveTextContent('9+'))
  })

  it('shows no badge when everything is read', async () => {
    serve([note('1', true), note('2', true)])
    renderHeader()
    // Positive marker first: the list has arrived (two read items in the
    // store), so the missing badge is an answer and not a load still pending.
    await waitFor(() => expect(useNotificationStore.getState().notifications).toHaveLength(2))
    expect(bell()).toHaveAccessibleName('Notifications')
    expect(screen.queryByTestId('notification-badge')).toBeNull()
  })

  it('formats the badge: nothing at 0, the number to 9, "9+" above', () => {
    expect([0, 1, 9, 10, 99].map(badgeText)).toEqual([null, '1', '9', '9+', '9+'])
  })
})

describe('the popup', () => {
  it('opens under the bell on click, compact, with focus inside', async () => {
    serve([note('1', false, 'Two-factor authentication enabled')])
    renderHeader()
    await userEvent.click(bell())
    const popover = await screen.findByTestId('notification-popover')
    expect(within(popover).getByText('Two-factor authentication enabled')).toBeInTheDocument()
    // Compact, not a drawer: a fixed width and a capped height with the
    // list scrolling inside.
    expect(popover.className).toContain('w-[380px]')
    expect(screen.getByTestId('notification-panel').className).toContain('max-h-[min(480px')
    expect(screen.getByTestId('notification-list').className).toContain('overflow-y-auto')
    expect(popover.contains(document.activeElement)).toBe(true)
    expect(bell()).toHaveAttribute('aria-expanded', 'true')
  })

  it('closes on a second click of the bell', async () => {
    serve([note('1', false)])
    renderHeader()
    await userEvent.click(bell())
    await screen.findByTestId('notification-popover')
    await userEvent.click(bell())
    await waitFor(() => expect(screen.queryByTestId('notification-popover')).toBeNull())
  })

  it('closes on Escape and gives focus back to the bell', async () => {
    serve([note('1', false)])
    renderHeader()
    await userEvent.click(bell())
    await screen.findByTestId('notification-popover')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByTestId('notification-popover')).toBeNull())
    expect(document.activeElement).toBe(bell())
  })

  it('closes on a click outside it', async () => {
    serve([note('1', false)])
    render(
      <>
        <Header onSearchOpen={() => {}} />
        <p>Somewhere else on the page</p>
      </>,
    )
    await userEvent.click(bell())
    await screen.findByTestId('notification-popover')
    await userEvent.click(screen.getByText('Somewhere else on the page'))
    await waitFor(() => expect(screen.queryByTestId('notification-popover')).toBeNull())
  })

  it('closes when the page changes', async () => {
    serve([note('1', false)])
    const { rerender } = renderHeader()
    await userEvent.click(bell())
    await screen.findByTestId('notification-popover')
    pathname = '/projects'
    rerender(<Header onSearchOpen={() => {}} />)
    await waitFor(() => expect(screen.queryByTestId('notification-popover')).toBeNull())
  })

  it('opens and closes from the keyboard', async () => {
    serve([note('1', false)])
    renderHeader()
    bell().focus()
    await userEvent.keyboard('{Enter}')
    await screen.findByTestId('notification-popover')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByTestId('notification-popover')).toBeNull())
  })

  it('the Unread tab shows only the unread ones', async () => {
    serve([note('1', false, 'Unread one'), note('2', true, 'Read one')])
    renderHeader()
    await userEvent.click(bell())
    const popover = await screen.findByTestId('notification-popover')
    await within(popover).findByText('Read one')
    expect(within(popover).getAllByTestId('notification-item')).toHaveLength(2)

    await userEvent.click(within(popover).getByRole('tab', { name: 'Unread' }))
    expect(within(popover).getByRole('tab', { name: 'Unread' })).toHaveAttribute('aria-selected', 'true')
    expect(within(popover).getAllByTestId('notification-item')).toHaveLength(1)
    expect(within(popover).getByText('Unread one')).toBeInTheDocument()
    expect(within(popover).queryByText('Read one')).toBeNull()
  })

  it('"Mark all as read" calls the API and clears the badge', async () => {
    serve([note('1', false), note('2', false)])
    renderHeader()
    await waitFor(() => expect(screen.getByTestId('notification-badge')).toHaveTextContent('2'))
    await userEvent.click(bell())
    const popover = await screen.findByTestId('notification-popover')
    await userEvent.click(within(popover).getByRole('button', { name: 'Mark all as read' }))
    expect(post).toHaveBeenCalledWith('/me/notifications/read-all', undefined)
    await waitFor(() => expect(bell()).toHaveAccessibleName('Notifications'))
    expect(screen.queryByTestId('notification-badge')).toBeNull()
  })

  it('says "No updates yet" when there is nothing', async () => {
    serve([])
    renderHeader()
    await userEvent.click(bell())
    const popover = await screen.findByTestId('notification-popover')
    await waitFor(() => expect(within(popover).getByTestId('notification-empty')).toHaveTextContent('No updates yet'))
  })

  it('links to the full page and to the notification settings', async () => {
    serve([])
    renderHeader()
    await userEvent.click(bell())
    const popover = await screen.findByTestId('notification-popover')
    expect(within(popover).getByRole('link', { name: 'See all' })).toHaveAttribute('href', '/notifications')
    expect(within(popover).getByRole('link', { name: 'Notification settings' })).toHaveAttribute(
      'href',
      '/settings/notifications',
    )
  })
})

describe('the sidebar', () => {
  it('has no Notifications entry any more', () => {
    useCompanyStore.setState({
      companies: [
        {
          id: 'a',
          legal_name: 'YON Studio OG',
          trading_name: null,
          display_name: 'YON Studio OG',
          default_currency: 'EUR',
          role: 'owner',
          permissions: ['parties.view', 'projects.view'],
          expires_at: null,
        },
      ],
      activeCompanyId: 'a',
      loaded: true,
      error: null,
    })
    serve([note('1', false)])
    render(<Sidebar collapsed={false} onToggle={() => {}} />)
    // Positive marker: the sidebar did draw its entries.
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.queryByText('Notifications')).toBeNull()
    expect(screen.queryByTestId('notification-bell')).toBeNull()
  })
})
