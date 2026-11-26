'use client'

import * as React from 'react'
import Link from 'next/link'
import { api, ApiError } from '@/lib/api'
import { RolePicker } from '@/components/shared/role-picker'
import type { CrewCreateResult } from '@/types'

/**
 * Quick add: a name, and whatever else is known. No address, no tax data,
 * no number — those belong to the full party record, one click away.
 *
 * The form stays open after a save, emptied, because the case it exists for
 * is typing in "a bunch" of people one after another.
 */
export function QuickAdd({
  suggestions,
  onAdded,
  onClose,
}: {
  suggestions: string[]
  onAdded: () => void
  onClose: () => void
}) {
  const [name, setName] = React.useState('')
  const [roles, setRoles] = React.useState<string[]>([])
  const [phone, setPhone] = React.useState('')
  const [email, setEmail] = React.useState('')
  const [notes, setNotes] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [last, setLast] = React.useState<CrewCreateResult | null>(null)
  const nameRef = React.useRef<HTMLInputElement>(null)

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!name.trim()) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<CrewCreateResult>('/crew', {
        name,
        roles,
        phone: phone || null,
        email: email || null,
        notes: notes || null,
      })
      setLast(result)
      setName('')
      setRoles([])
      setPhone('')
      setEmail('')
      setNotes('')
      onAdded()
      nameRef.current?.focus()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'This person could not be saved. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const field =
    'mt-1 h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary'

  return (
    <form
      onSubmit={(event) => void save(event)}
      data-testid="crew-quick-add"
      className="mt-5 rounded-md border border-border bg-bg-secondary p-4"
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-text-secondary">
          Name
          <input
            ref={nameRef}
            autoFocus
            data-testid="crew-add-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className={field}
          />
        </label>
        <div className="text-xs text-text-secondary">
          <label htmlFor="crew-roles">Roles</label>
          <div className="mt-1">
            <RolePicker value={roles} onChange={setRoles} suggestions={suggestions} />
          </div>
        </div>
        <label className="text-xs text-text-secondary">
          Phone
          <input
            data-testid="crew-add-phone"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            className={field}
          />
        </label>
        <label className="text-xs text-text-secondary">
          Email
          <input
            type="email"
            data-testid="crew-add-email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className={field}
          />
        </label>
        <label className="text-xs text-text-secondary sm:col-span-2">
          Notes
          <input
            data-testid="crew-add-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            className={field}
          />
        </label>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="submit"
          data-testid="crew-add-save"
          disabled={busy || !name.trim()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Add to crew
        </button>
        <button type="button" onClick={onClose} className="text-sm text-text-secondary hover:text-text-primary">
          Close
        </button>
        {!name.trim() && !busy && (
          // 17c: the button is disabled and this says why.
          <span data-testid="crew-add-why" className="text-xs text-text-tertiary">
            Type a name to add this person. Everything else is optional.
          </span>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
      {last && (
        <div role="status" data-testid="crew-add-done" className="mt-3 text-sm text-text-secondary">
          <span className="text-success">{last.member.name} was added.</span>{' '}
          <Link href={`/parties/${last.member.id}`} className="underline">
            Open the full record
          </Link>
          {(last.duplicates ?? []).length > 0 && (
            <ul data-testid="crew-add-duplicates" className="mt-1 list-disc pl-5 text-xs text-warning">
              {(last.duplicates ?? []).map((entry) => (
                <li key={entry.party_id}>
                  {entry.detail}{' '}
                  <Link href={`/parties/${entry.party_id}`} className="underline">
                    Check {entry.name}
                  </Link>{' '}
                  — if it is the same person, archive one of the two records.
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </form>
  )
}
