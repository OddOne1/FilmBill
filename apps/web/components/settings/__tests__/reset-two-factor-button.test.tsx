/**
 * An admin can strip a locked-out user's second factor from the UI.
 *
 * PATCH /admin/users/{id}/disable-2fa has existed since FreeFrame and had no
 * button on either side of the port, so the documented recovery path was a
 * curl command. That is not acceptable in a system where a role can REQUIRE
 * 2FA (P0b's tax advisor): "they lost their phone" is ordinary support work,
 * and the person doing it is not going to open a terminal.
 *
 * The security property — that this cannot be used on YOURSELF, so a stolen
 * superadmin session cannot strip its own protection — is enforced by the
 * API and tested there (apps/api/tests/test_two_factor_disable.py). This
 * file covers what the button itself does.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const patch = vi.fn(() => Promise.resolve({}))
vi.mock('@/lib/api', () => ({ api: { patch: (...args: unknown[]) => patch(...(args as [])) } }))

import { ResetTwoFactorButton } from '@/components/settings/reset-two-factor-button'

const LOCKED_OUT = {
  id: 'u-42',
  name: 'Bob Builder',
  email: 'bob@example.com',
  two_factor_enabled: true,
} as never

beforeEach(() => {
  patch.mockClear()
  patch.mockImplementation(() => Promise.resolve({}))
})

describe("resetting another user's two-factor", () => {
  it('asks before doing anything', async () => {
    // Irreversible for the user on the other end — their authenticator and
    // every backup code stop working — so a stray click must not be enough.
    const onDone = vi.fn()
    render(<ResetTwoFactorButton user={LOCKED_OUT} onDone={onDone} />)

    await userEvent.click(screen.getByRole('button', { name: /reset 2fa/i }))

    expect(patch).not.toHaveBeenCalled()
    expect(screen.getByText(/reset two-factor for bob builder/i)).toBeTruthy()
  })

  it('names the user whose factor is about to go', async () => {
    // The dialog is the last chance to notice you clicked the wrong row.
    render(<ResetTwoFactorButton user={LOCKED_OUT} onDone={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /reset 2fa/i }))

    expect(screen.getByText(/bob builder/i)).toBeTruthy()
  })

  it('says that the action is recorded either way', async () => {
    // An unlogged way to remove someone else's second factor is
    // indistinguishable after the fact from an attacker having done it. The
    // API writes the audit row; the dialog says so, because an admin who
    // knows it is recorded behaves differently.
    render(<ResetTwoFactorButton user={LOCKED_OUT} onDone={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /reset 2fa/i }))

    expect(screen.getByText(/audit log/i)).toBeTruthy()
  })

  it('calls the admin endpoint on confirm, and refreshes the list', async () => {
    const onDone = vi.fn()
    render(<ResetTwoFactorButton user={LOCKED_OUT} onDone={onDone} />)

    await userEvent.click(screen.getByRole('button', { name: /reset 2fa/i }))
    await userEvent.click(screen.getByRole('button', { name: /^reset two-factor$/i }))

    await waitFor(() => expect(patch).toHaveBeenCalledWith('/admin/users/u-42/disable-2fa'))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
  })

  it('does not claim success when the write failed', async () => {
    // onDone refetches the user list; calling it after a failure would
    // redraw the same unchanged row and look like it worked.
    patch.mockImplementation(() => Promise.reject(new Error('nope')))
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    const onDone = vi.fn()
    render(<ResetTwoFactorButton user={LOCKED_OUT} onDone={onDone} />)

    await userEvent.click(screen.getByRole('button', { name: /reset 2fa/i }))
    await userEvent.click(screen.getByRole('button', { name: /^reset two-factor$/i }))

    await waitFor(() => expect(patch).toHaveBeenCalled())
    expect(onDone).not.toHaveBeenCalled()
  })
})
