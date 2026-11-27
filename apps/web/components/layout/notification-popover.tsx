'use client'

import * as React from 'react'
import * as Popover from '@radix-ui/react-popover'
import { Bell, ShieldCheck, Settings, Users } from 'lucide-react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useNotificationStore } from '@/stores/notification-store'
import { formatRelativeTime } from '@/lib/utils'
import { cn } from '@/lib/utils'
import type { Notification, NotificationType } from '@/types'

/**
 * Notifications as a small popup from the top bar (UI-1 part 2).
 *
 * The bell sits beside Search with the unread count on it; a click opens a
 * compact popover anchored under the bell. It used to be a full-height
 * drawer that slid out of the sidebar and covered its labels (Mathias,
 * 2026-11-26). Radix Popover supplies the behaviour a popup owes the
 * keyboard: focus moves in on open and back to the bell on close, Escape
 * and an outside click close it. Navigating closes it too (below).
 */

// One kind today; each business event that starts notifying (document
// revised, invoice overdue, receipt needs review) adds its own icon here
// alongside its backend enum case.
const notificationIcons: Record<NotificationType, React.ElementType> = {
  account: ShieldCheck,
  // R2: "<name> wants to work as crew".
  team: Users,
}

/** "9+" above nine, nothing at all at zero. */
export function badgeText(unread: number): string | null {
  if (unread <= 0) return null
  return unread > 9 ? '9+' : String(unread)
}

function NotificationItem({ notification, onClose }: { notification: Notification; onClose: () => void }) {
  const { markAsRead } = useNotificationStore()
  const Icon = notificationIcons[notification.type] ?? Bell

  async function handleClick() {
    // AWAITED before navigating. window.location.href starts a full document
    // unload, and a fetch still in flight when that happens is not
    // guaranteed to be delivered — so the read could be lost exactly for the
    // notifications that have somewhere to go. The badge already cleared
    // optimistically inside markAsRead, so this wait costs nothing visible.
    if (!notification.read) {
      try {
        await markAsRead(notification.id)
      } catch {
        // The store has already rolled the dot back. Navigating anyway is
        // right: the user asked to go somewhere, and failing to mark it read
        // is not a reason to refuse.
      }
    }
    if (notification.link) {
      window.location.href = notification.link
      onClose()
    }
  }

  return (
    <button
      type="button"
      data-testid="notification-item"
      onClick={() => void handleClick()}
      className={cn(
        'flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-bg-hover focus-visible:bg-bg-hover focus-visible:outline-none',
        !notification.read && 'bg-bg-secondary',
      )}
    >
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full mt-0.5 bg-accent-muted text-accent">
        <Icon className="h-3.5 w-3.5" />
      </div>
      <div className="flex flex-1 flex-col gap-0.5 min-w-0">
        <p className="text-sm text-text-primary font-medium">{notification.title}</p>
        {notification.body && (
          <p className="text-xs text-text-secondary line-clamp-2">{notification.body}</p>
        )}
        <p className="text-2xs text-text-tertiary mt-0.5">{formatRelativeTime(notification.created_at)}</p>
      </div>
      {!notification.read && (
        <span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-accent" />
      )}
    </button>
  )
}

/** What is inside the popup: tabs, the list, mark all read, settings. */
export function NotificationPanel({ onClose }: { onClose: () => void }) {
  const { notifications, isLoading, markAllRead, unreadCount } = useNotificationStore()
  const [tab, setTab] = React.useState<'all' | 'unread'>('all')

  const filtered = tab === 'unread' ? notifications.filter((n) => !n.read) : notifications

  return (
    <div data-testid="notification-panel" className="flex max-h-[min(480px,calc(100vh-4rem))] flex-col">
      <div className="flex items-center justify-between px-3 h-10 border-b border-border shrink-0">
        <span className="text-sm font-semibold text-text-primary">Notifications</span>
        <div className="flex items-center gap-1">
          <Link
            href="/notifications"
            onClick={onClose}
            className="rounded-md px-1.5 py-1 text-xs text-text-tertiary hover:text-text-primary hover:bg-bg-hover transition-colors"
          >
            See all
          </Link>
          <Link
            href="/settings/notifications"
            onClick={onClose}
            aria-label="Notification settings"
            title="Notification settings"
            className="flex items-center justify-center h-7 w-7 rounded-md text-text-tertiary hover:text-text-primary hover:bg-bg-hover transition-colors"
          >
            <Settings className="h-4 w-4" />
          </Link>
        </div>
      </div>

      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border shrink-0">
        <div role="tablist" aria-label="Which notifications" className="flex items-center gap-3">
          {(['all', 'unread'] as const).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={cn(
                'text-sm font-medium transition-colors',
                tab === key ? 'text-text-primary' : 'text-text-tertiary hover:text-text-secondary',
              )}
            >
              {key === 'all' ? 'All' : 'Unread'}
            </button>
          ))}
        </div>
        {unreadCount > 0 && (
          <button
            type="button"
            onClick={() => void markAllRead().catch(() => {})}
            className="text-xs text-text-tertiary hover:text-text-primary transition-colors"
          >
            Mark all as read
          </button>
        )}
      </div>

      <div data-testid="notification-list" className="min-h-0 flex-1 overflow-y-auto">
        {isLoading && notifications.length === 0 ? (
          <div className="space-y-1 p-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-14 animate-pulse rounded-lg bg-bg-secondary" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div data-testid="notification-empty" className="flex flex-col items-center justify-center py-10 text-center px-6">
            <div className="h-12 w-12 rounded-full bg-bg-tertiary flex items-center justify-center mb-3">
              <Bell className="h-6 w-6 text-text-tertiary" />
            </div>
            <p className="text-sm font-medium text-text-primary">
              {tab === 'unread' && notifications.length > 0 ? 'Nothing unread' : 'No updates yet'}
            </p>
            <p className="text-xs text-text-tertiary mt-1">
              {tab === 'unread' && notifications.length > 0
                ? 'You have read every notification. The All tab still lists them.'
                : 'New activity on your account will show here.'}
            </p>
          </div>
        ) : (
          <div className="p-1 space-y-0.5">
            {filtered.map((notification) => (
              <NotificationItem key={notification.id} notification={notification} onClose={onClose} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/** The bell in the top bar, its badge, and the popup it opens. */
export function NotificationBell() {
  const { unreadCount, fetchNotifications } = useNotificationStore()
  const [open, setOpen] = React.useState(false)
  const pathname = usePathname()
  const badge = badgeText(unreadCount)

  // Once for the badge, and again on every open so the list is current.
  React.useEffect(() => {
    void Promise.resolve(fetchNotifications()).catch(() => {})
  }, [fetchNotifications])
  React.useEffect(() => {
    if (open) void Promise.resolve(fetchNotifications()).catch(() => {})
  }, [open, fetchNotifications])

  // Navigating closes it: a popup left open over the next page belongs to
  // nothing on it.
  React.useEffect(() => {
    setOpen(false)
  }, [pathname])

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          data-testid="notification-bell"
          aria-label={badge ? `Notifications, ${unreadCount} unread` : 'Notifications'}
          className={cn(
            'relative flex h-7 w-7 items-center justify-center rounded-md border border-nav-border text-nav-text/70 transition-colors hover:border-nav-text/50 hover:text-nav-text',
            open && 'border-nav-text/50 bg-nav-text/10 text-nav-text',
          )}
        >
          <Bell className="h-3.5 w-3.5" />
          {badge && (
            <span
              data-testid="notification-badge"
              aria-hidden="true"
              className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-status-error px-1 text-[9px] font-bold leading-none text-white"
            >
              {badge}
            </span>
          )}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          side="bottom"
          sideOffset={6}
          // Never over the sidebar: keep a sidebar's width free on the left
          // (the expanded sidebar is 192 px) and a little room on the right.
          collisionPadding={{ left: 200, right: 8, top: 8, bottom: 8 }}
          aria-label="Notifications"
          data-testid="notification-popover"
          className="z-50 w-[380px] max-w-[calc(100vw-13rem)] overflow-hidden rounded-lg border border-border bg-bg-primary shadow-2xl outline-none"
        >
          <NotificationPanel onClose={() => setOpen(false)} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
