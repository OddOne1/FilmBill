'use client'

import * as React from 'react'
import Link from 'next/link'
import { Bell, Settings, Brush } from 'lucide-react'
import { useAuthStore } from '@/stores/auth-store'
import { usePageTitle } from '@/hooks/use-page-title'

function getGreeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

interface Shortcut {
  href: string
  label: string
  description: string
  icon: React.ElementType
}

const shortcuts: Shortcut[] = [
  {
    href: '/notifications',
    label: 'Notifications',
    description: 'Everything that happened on your account',
    icon: Bell,
  },
  {
    href: '/settings/profile',
    label: 'Your profile',
    description: 'Name, password and two-factor authentication',
    icon: Settings,
  },
  {
    href: '/settings/branding',
    label: 'Branding',
    description: 'Logo, colours and workspace name',
    icon: Brush,
  },
]

/**
 * The dashboard, as far as P0a has one.
 *
 * It says plainly that the business features are not here yet rather than
 * showing empty widgets for quotes and invoices that no endpoint can fill.
 * The real dashboard arrives with the documents it summarises (P2).
 */
export default function HomePage() {
  usePageTitle('Dashboard')
  const { user } = useAuthStore()

  return (
    <div className="p-6 space-y-8 max-w-3xl">
      <div>
        <h1 className="text-xl font-semibold text-text-primary">
          {getGreeting()},{' '}
          <span className="text-accent">
            {user?.first_name ?? user?.name?.split(' ')[0] ?? 'there'}
          </span>
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Your FilmBill workspace is set up. Quotes, invoices and your catalog
          arrive in the next phases.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {shortcuts.map((item) => {
          const Icon = item.icon
          return (
            <Link
              key={item.href}
              href={item.href}
              className="group flex items-start gap-3 rounded-lg border border-border bg-bg-secondary p-4 hover:border-border-focus hover:bg-bg-tertiary transition-colors"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-muted text-accent">
                <Icon className="h-4 w-4" />
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-text-primary group-hover:text-accent transition-colors">
                  {item.label}
                </p>
                <p className="text-xs text-text-tertiary mt-0.5">
                  {item.description}
                </p>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
