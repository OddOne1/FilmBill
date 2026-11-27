'use client'

import * as React from 'react'
import Link from 'next/link'
import * as Dialog from '@radix-ui/react-dialog'
import { Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import type { LaborItemChoice, TeamContact, TeamMember, TeamStopResult } from '@/types'

/**
 * R2. The Team page's Crew column: whether a member also works as crew,
 * their roles (self-reported ones with Confirm), the contact they are
 * linked to, and the two actions.
 *
 * There is never a rate field here: day rates are edited on the contact,
 * by whoever may edit crew rates, exactly as for any crew.
 */

function errorText(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.detail : fallback
}

/** "Spot (6 Jul) and Doku" — the projects of the future bookings. */
export function stopSentence(name: string, result: TeamStopResult): string {
  const bookings = result.future_bookings ?? []
  if (bookings.length === 0) {
    return `${name} no longer works as crew. They have no future bookings. Nothing was deleted.`
  }
  const projects = Array.from(new Set(bookings.map((b) => b.project_name)))
  const named = projects.length === 1 ? projects[0] : `${projects.slice(0, -1).join(', ')} and ${projects[projects.length - 1]}`
  const count = bookings.length === 1 ? 'one future booking' : `${bookings.length} future bookings`
  return `${name} no longer works as crew. They have ${count} (${named}); those stay as they are. Nothing was deleted.`
}

export function CrewCell({
  member,
  laborItems,
  onChanged,
  onNotice,
}: {
  member: TeamMember
  laborItems: LaborItemChoice[]
  onChanged: () => void | Promise<void>
  onNotice: (text: string) => void
}) {
  const [dialog, setDialog] = React.useState(false)
  const [stopping, setStopping] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const confirm = async (roleId: string) => {
    setError(null)
    try {
      await api.post(`/team/${member.user_id}/crew/roles/${roleId}/confirm`, {})
      await onChanged()
    } catch (err) {
      setError(errorText(err, 'The role could not be confirmed. Try again.'))
    }
  }

  const stop = async () => {
    setError(null)
    try {
      const result = await api.post<TeamStopResult>(`/team/${member.user_id}/crew/stop`, {})
      onNotice(stopSentence(member.contact?.name ?? member.name, result))
      await onChanged()
    } catch (err) {
      setError(errorText(err, 'That could not be changed. Try again.'))
    }
  }

  return (
    <div data-testid={`crew-cell-${member.user_id}`} className="space-y-1">
      {member.crew_state === 'crew' ? (
        <>
          <div className="text-xs text-text-secondary">
            Crew
            {member.contact && (
              <>
                {' · '}
                <Link href={`/contacts/${member.contact.id}`} className="text-accent hover:underline">
                  {member.contact.name}
                </Link>
              </>
            )}
          </div>
          <div className="flex flex-wrap gap-1">
            {(member.roles ?? []).map((role) => (
              <span
                key={role.id}
                data-testid="team-role-chip"
                className="inline-flex items-center gap-1 rounded bg-accent/10 px-1.5 py-0.5 text-xs text-text-primary"
              >
                {role.name}
                {!role.confirmed && (
                  <>
                    <span data-testid="self-reported" className="text-2xs text-text-tertiary">
                      self-reported
                    </span>
                    <button
                      type="button"
                      onClick={() => void confirm(role.id)}
                      className="rounded border border-border px-1 text-2xs text-text-primary hover:bg-bg-hover"
                    >
                      Confirm
                    </button>
                  </>
                )}
              </span>
            ))}
            {(member.roles ?? []).length === 0 && <span className="text-xs text-text-tertiary">No roles yet</span>}
          </div>
          <div className="flex gap-2">
            <button type="button" onClick={() => setDialog(true)} className="text-xs text-accent hover:underline">
              Roles…
            </button>
            {member.has_active_login && (
              <button type="button" onClick={() => setStopping(true)} className="text-xs text-text-tertiary hover:text-status-error">
                Stop working as crew
              </button>
            )}
          </div>
        </>
      ) : member.crew_state === 'needs_link' ? (
        <>
          <div className="text-xs text-text-secondary">Needs link</div>
          {member.has_active_login && (
            <button type="button" onClick={() => setDialog(true)} className="text-xs text-accent hover:underline">
              Connect contact…
            </button>
          )}
        </>
      ) : (
        <>
          <div className="text-xs text-text-secondary">
            Not crew
            {member.contact && (
              <>
                {' · '}
                <Link href={`/contacts/${member.contact.id}`} className="text-accent hover:underline">
                  {member.contact.name}
                </Link>
              </>
            )}
          </div>
          {member.has_active_login ? (
            <button type="button" onClick={() => setDialog(true)} className="text-xs text-accent hover:underline">
              Works as crew
            </button>
          ) : (
            <div className="text-2xs text-text-tertiary">No active login</div>
          )}
        </>
      )}
      {!member.has_active_login && member.crew_state === 'crew' && (
        <div className="text-2xs text-text-tertiary">No active login</div>
      )}
      {error && (
        <p role="alert" className="text-xs text-status-error">
          {error}
        </p>
      )}
      {dialog && (
        <WorksAsCrewDialog
          member={member}
          laborItems={laborItems}
          onClose={() => setDialog(false)}
          onSaved={async () => {
            setDialog(false)
            await onChanged()
          }}
        />
      )}
      <ConfirmDialog
        open={stopping}
        onOpenChange={setStopping}
        title={`Stop ${member.contact?.name ?? member.name} working as crew?`}
        description="Their bookings stay as they are and nothing is deleted. The contact stays connected to the login."
        confirmLabel="Stop working as crew"
        onConfirm={stop}
      />
    </div>
  )
}

type Choice = { mode: 'link'; partyId: string } | { mode: 'create' } | null

function initialChoice(member: TeamMember): Choice {
  if (member.contact) return { mode: 'link', partyId: member.contact.id }
  if (member.suggestion) return { mode: 'link', partyId: member.suggestion.id }
  if ((member.matches ?? []).length === 0) return { mode: 'create' }
  return null
}

function contactLabel(contact: TeamContact) {
  return contact.email ? `${contact.name} (${contact.email})` : contact.name
}

/**
 * "Works as crew" / "Connect contact…" / "Roles…". Three cases: one contact
 * has the login's email (link it, or create a new one instead), none has
 * (create one), several have (pick one, or create). Then the roles, and
 * Save. Never a rate.
 */
export function WorksAsCrewDialog({
  member,
  laborItems,
  onClose,
  onSaved,
}: {
  member: TeamMember
  laborItems: LaborItemChoice[]
  onClose: () => void
  onSaved: () => void | Promise<void>
}) {
  const [choice, setChoice] = React.useState<Choice>(() => initialChoice(member))
  const [roles, setRoles] = React.useState<Set<string>>(
    () => new Set((member.roles ?? []).map((role) => role.item_id)),
  )
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const matches = member.matches ?? []

  const save = async () => {
    if (!choice) return
    setSaving(true)
    setError(null)
    try {
      await api.post(`/team/${member.user_id}/crew`, {
        mode: choice.mode,
        ...(choice.mode === 'link' ? { party_id: choice.partyId } : {}),
        role_item_ids: Array.from(roles),
      })
      await onSaved()
    } catch (err) {
      setError(errorText(err, 'That could not be saved. Try again.'))
    } finally {
      setSaving(false)
    }
  }

  const toggle = (itemId: string, on: boolean) => {
    const next = new Set(roles)
    if (on) next.add(itemId)
    else next.delete(itemId)
    setRoles(next)
  }

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <Dialog.Content
          data-testid="works-as-crew-dialog"
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-bg-primary p-5 shadow-xl"
        >
          <Dialog.Title className="text-base font-semibold text-text-primary">
            {member.name} works as crew
          </Dialog.Title>

          <div className="mt-3 space-y-2 text-sm text-text-secondary">
            {member.contact ? (
              <p>
                Connected to the contact <strong className="text-text-primary">{member.contact.name}</strong>.
              </p>
            ) : member.suggestion ? (
              <>
                <p data-testid="suggestion-sentence">
                  Link to the existing contact{' '}
                  <strong className="text-text-primary">{member.suggestion.name}</strong>
                  {member.suggestion.email ? ` (${member.suggestion.email})` : ''}?
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant={choice?.mode === 'link' ? 'primary' : 'secondary'}
                    onClick={() => setChoice({ mode: 'link', partyId: member.suggestion!.id })}
                  >
                    Link
                  </Button>
                  <Button
                    type="button"
                    variant={choice?.mode === 'create' ? 'primary' : 'secondary'}
                    onClick={() => setChoice({ mode: 'create' })}
                  >
                    Create a new crew contact instead
                  </Button>
                </div>
              </>
            ) : matches.length === 0 ? (
              <p data-testid="no-match-sentence">
                No contact has this email. Create a crew contact for {member.name}?
              </p>
            ) : (
              <fieldset data-testid="match-picker" className="space-y-1">
                <legend className="mb-1">Several contacts have this email. Which one is {member.name}?</legend>
                {matches.map((contact) => (
                  <label key={contact.id} className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="team-contact"
                      checked={choice?.mode === 'link' && choice.partyId === contact.id}
                      onChange={() => setChoice({ mode: 'link', partyId: contact.id })}
                    />
                    {contactLabel(contact)}
                  </label>
                ))}
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="team-contact"
                    checked={choice?.mode === 'create'}
                    onChange={() => setChoice({ mode: 'create' })}
                  />
                  None of these: create a new crew contact
                </label>
              </fieldset>
            )}
          </div>

          <div className="mt-4">
            <div className="text-xs font-medium text-text-secondary">Roles</div>
            {laborItems.length === 0 ? (
              <p className="mt-1 text-xs text-text-tertiary">
                The catalog has no labor items yet. Add them in the catalog to choose roles.
              </p>
            ) : (
              <div data-testid="team-role-picker" className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {laborItems.map((item) => (
                  <label key={item.item_id} className="flex items-center gap-1.5 text-sm text-text-primary">
                    <input
                      type="checkbox"
                      checked={roles.has(item.item_id)}
                      onChange={(event) => toggle(item.item_id, event.target.checked)}
                    />
                    {item.name}
                  </label>
                ))}
              </div>
            )}
            <p className="mt-2 text-2xs text-text-tertiary">Day rates are set on the contact, as for any crew.</p>
          </div>

          {error && (
            <p role="alert" className="mt-3 text-sm text-status-error">
              {error}
            </p>
          )}

          <div className="mt-5 flex items-center justify-end gap-2">
            {!choice && (
              <span data-testid="save-why" className="mr-auto text-xs text-text-tertiary">
                Choose a contact, or create a new one.
              </span>
            )}
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="button" onClick={() => void save()} disabled={!choice || saving}>
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
