/**
 * Clicking a notification must not lose the read.
 *
 * FreeFrame's drawer fired markAsRead and then set window.location.href in
 * the same tick. That starts a full document unload, and a fetch still in
 * flight when that happens is not guaranteed to be delivered — so the read
 * could be lost for exactly the notifications that have somewhere to go,
 * which is most of them.
 *
 * Asserted BEHAVIOURALLY (CLAUDE.md rule 11): markAsRead is handed a promise
 * that does not settle until the test says so, and the assertion is that no
 * navigation has happened while it is pending. An earlier version of this
 * file read the component's own source and grepped for `await` — which is a
 * test of the text, not of what the code does, and would pass on a comment.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const markAsRead = vi.fn(() => Promise.resolve())
let storeValue: Record<string, unknown> = {}
vi.mock('@/stores/notification-store', () => ({
  useNotificationStore: () => storeValue,
}))

import { NotificationDrawer } from '../notification-drawer'

const notification = {
  id: 'n1',
  type: 'account' as const,
  title: 'Two-factor authentication enabled',
  body: 'Ada turned on 2FA for your account.',
  link: '/settings/profile',
  read: false,
  created_at: '2024-01-01T00:00:00Z',
}

/** Where the component tried to navigate to, or null if it has not yet.
 *  jsdom refuses a real navigation, so window.location is replaced with a
 *  plain recorder for the duration of each test. */
let navigatedTo: string | null
let realLocation: Location

/** A promise whose settlement this test controls, so "did it wait?" is a
 *  question with a real answer rather than a race. */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  markAsRead.mockClear()
  markAsRead.mockImplementation(() => Promise.resolve())
  navigatedTo = null
  realLocation = window.location
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      get href() {
        return navigatedTo ?? 'http://localhost/'
      },
      set href(value: string) {
        navigatedTo = value
      },
    },
  })
  storeValue = {
    markAsRead,
    markAllRead: vi.fn(() => Promise.resolve()),
    notifications: [notification],
    unreadCount: 1,
    isLoading: false,
    fetchNotifications: vi.fn(),
  }
})

afterEach(() => {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: realLocation,
  })
})

describe('reading a notification', () => {
  it('marks it read on click', async () => {
    render(<NotificationDrawer open onClose={vi.fn()} />)
    await userEvent.click(screen.getByText(notification.title))
    expect(markAsRead).toHaveBeenCalledWith('n1')
  })

  it('does not navigate until the write has landed', async () => {
    const pending = deferred<void>()
    markAsRead.mockImplementation(() => pending.promise)

    render(<NotificationDrawer open onClose={vi.fn()} />)
    await userEvent.click(screen.getByText(notification.title))

    await waitFor(() => expect(markAsRead).toHaveBeenCalled())
    // The write is still in flight: navigating now is what would drop it.
    expect(navigatedTo).toBeNull()

    pending.resolve()
    await waitFor(() => expect(navigatedTo).toBe('/settings/profile'))
  })

  it('still navigates when the write fails', async () => {
    // Failing to record a read is not a reason to refuse to go where the
    // user asked to go.
    markAsRead.mockImplementation(() => Promise.reject(new Error('offline')))

    render(<NotificationDrawer open onClose={vi.fn()} />)
    await userEvent.click(screen.getByText(notification.title))

    await waitFor(() => expect(navigatedTo).toBe('/settings/profile'))
  })

  it('goes nowhere when the notification has no link', async () => {
    storeValue.notifications = [{ ...notification, link: null }]

    render(<NotificationDrawer open onClose={vi.fn()} />)
    await userEvent.click(screen.getByText(notification.title))

    await waitFor(() => expect(markAsRead).toHaveBeenCalledWith('n1'))
    expect(navigatedTo).toBeNull()
  })
})
