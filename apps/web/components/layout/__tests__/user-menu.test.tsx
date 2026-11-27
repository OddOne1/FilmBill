import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const get = vi.fn()
vi.mock('@/lib/api', () => ({
  api: { get: (path: string) => get(path) },
  setActiveCompanyId: vi.fn(),
  ApiError: class extends Error {},
}))

import { UserMenu } from '../user-menu'
import { useCompanyStore } from '@/stores/company-store'
import type { MyCrewProfile } from '@/types'

/**
 * R2. The user menu: Profile always; My crew profile when the company has
 * one for me; My schedule only when I am linked and work as crew. Gated on
 * the fetched answer: each case waits for `/me/crew` to have been asked.
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
        role: 'staff',
        permissions: ['company.view'],
        expires_at: null,
      },
    ],
    activeCompanyId: 'c1',
    loaded: true,
    error: null,
  })
}

function profile(isCrew: boolean): MyCrewProfile {
  return {
    is_crew: isCrew,
    crew_state: isCrew ? 'crew' : 'not_crew',
    contact_name: isCrew ? 'Nora Tester' : null,
    roles: [],
    labor_items: [],
    message: null,
  }
}

async function open() {
  render(<UserMenu />)
  fireEvent.click(screen.getByTestId('user-menu'))
  await waitFor(() => expect(get).toHaveBeenCalledWith('/me/crew'))
}

describe('the user menu', () => {
  beforeEach(() => {
    get.mockReset()
    seed()
  })

  it('shows My crew profile and My schedule to somebody who works as crew', async () => {
    get.mockResolvedValue(profile(true))
    await open()
    expect(await screen.findByRole('menuitem', { name: /My schedule/ })).toHaveAttribute('href', '/my/schedule')
    expect(screen.getByRole('menuitem', { name: /My crew profile/ })).toHaveAttribute('href', '/my/crew-profile')
    expect(screen.getByRole('menuitem', { name: /Profile/ })).toHaveAttribute('href', '/settings/profile')
  })

  it('hides My schedule when I am not crew, and keeps My crew profile to switch it on', async () => {
    get.mockResolvedValue(profile(false))
    await open()
    expect(await screen.findByRole('menuitem', { name: /My crew profile/ })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /My schedule/ })).toBeNull()
  })

  it('shows only Profile when the company has no crew profile for me (a tax advisor)', async () => {
    get.mockRejectedValue(new Error('Not found'))
    await open()
    expect(screen.getByRole('menuitem', { name: /^Profile/ })).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByRole('menuitem')).toHaveLength(1))
  })
})
