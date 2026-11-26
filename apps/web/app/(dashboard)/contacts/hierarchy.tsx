'use client'

import * as React from 'react'
import Link from 'next/link'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { RolePicker } from '@/components/shared/role-picker'
import {
  EMPTY_PERSON_NAME,
  PersonNameFields,
  hasPersonName,
  personNamePayload,
  type PersonName,
} from '@/components/shared/person-name-fields'
import type { Party, PartyPage } from '@/types'

/**
 * The Hierarchy tab (P1b-4 part 5): what sits below this contact, and —
 * for somebody who may edit — Add person and Add department right here,
 * without leaving the page. The new child appears in the list in place,
 * marked as just added; "Add another" keeps the dialog open for the next.
 *
 * On a person's own page it also moves them under an organisation or a
 * department, and back out. The tree's rules are the server's; a refusal
 * shows its sentence.
 */
export function HierarchyTab({
  party,
  canEdit,
  onSaved,
}: {
  party: Party
  canEdit: boolean
  onSaved: () => Promise<void>
}) {
  const [adding, setAdding] = React.useState<'person' | 'department' | null>(null)
  const [justAdded, setJustAdded] = React.useState<string[]>([])
  const canHavePeople = party.type === 'organisation' || party.type === 'department'

  return (
    <div className="space-y-3">
      {party.type === 'person' ? (
        <PersonParent party={party} canEdit={canEdit} onSaved={onSaved} />
      ) : (
        party.parent_id && (
          <p className="text-sm text-text-secondary">
            Belongs to{' '}
            <Link href={`/contacts/${party.parent_id}`} className="text-accent underline">
              its organisation
            </Link>
            .
          </p>
        )
      )}

      {canEdit && canHavePeople && (
        <div className="flex gap-2">
          <button
            type="button"
            data-testid="hierarchy-add-person"
            onClick={() => setAdding('person')}
            className="rounded-md border border-border px-2.5 py-1.5 text-xs text-text-primary hover:bg-bg-hover"
          >
            Add person
          </button>
          {party.type === 'organisation' && (
            <button
              type="button"
              data-testid="hierarchy-add-department"
              onClick={() => setAdding('department')}
              className="rounded-md border border-border px-2.5 py-1.5 text-xs text-text-primary hover:bg-bg-hover"
            >
              Add department
            </button>
          )}
        </div>
      )}

      {adding && (
        <AddChildDialog
          parent={party}
          kind={adding}
          onClose={() => setAdding(null)}
          onCreated={async (created, another) => {
            setJustAdded((ids) => [...ids, created.id])
            if (!another) setAdding(null)
            await onSaved()
          }}
        />
      )}

      {party.children.length === 0 ? (
        <p data-testid="party-no-children" className="text-sm text-text-tertiary">
          Nothing below this one.
          {party.type === 'organisation' &&
            (canEdit
              ? ' Add a person or a department with the buttons above.'
              : ' Somebody who can edit contacts can add people and departments here.')}
        </p>
      ) : (
        <ul className="space-y-2">
          {party.children.map((child) => (
            <li key={child.id}>
              <Link
                href={`/contacts/${child.id}`}
                data-testid={`party-child-${child.id}`}
                className="flex items-center gap-3 rounded-md border border-border px-3 py-2 text-sm hover:bg-bg-hover/50"
              >
                <span className="w-24 text-xs text-text-tertiary">{child.type}</span>
                <span className="flex-1 text-text-primary">
                  {child.name}
                  {child.name_addition && (
                    <span className="ml-2 text-xs text-text-tertiary">{child.name_addition}</span>
                  )}
                  {child.position && (
                    <span className="ml-2 text-xs text-text-tertiary">{child.position}</span>
                  )}
                </span>
                {justAdded.includes(child.id) && (
                  <span
                    data-testid={`party-child-new-${child.id}`}
                    className="text-[11px] text-status-success"
                  >
                    just added
                  </span>
                )}
                {!child.is_active && (
                  <span className="text-[11px] text-status-warning">archived</span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface PersonDraft extends PersonName {
  title: string
  position: string
  email: string
  phone: string
  is_crew: boolean
  tags: string[]
}

const EMPTY_PERSON: PersonDraft = {
  ...EMPTY_PERSON_NAME,
  title: '',
  position: '',
  email: '',
  phone: '',
  is_crew: false,
  tags: [],
}

function AddChildDialog({
  parent,
  kind,
  onClose,
  onCreated,
}: {
  parent: Party
  kind: 'person' | 'department'
  onClose: () => void
  onCreated: (created: Party, another: boolean) => Promise<void>
}) {
  const [person, setPerson] = React.useState<PersonDraft>(EMPTY_PERSON)
  const [name, setName] = React.useState('')
  const [nameAddition, setNameAddition] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [done, setDone] = React.useState<string | null>(null)

  const ready = kind === 'person' ? hasPersonName(person) : Boolean(name.trim())

  async function save(another: boolean) {
    setBusy(true)
    setError(null)
    try {
      const body =
        kind === 'person'
          ? {
              type: 'person',
              parent_id: parent.id,
              ...personNamePayload(person),
              title: person.title.trim() || null,
              position: person.position.trim() || null,
              email: person.email.trim() || null,
              phone: person.phone.trim() || null,
              is_crew: person.is_crew,
              tags: person.is_crew ? person.tags : [],
            }
          : {
              type: 'department',
              parent_id: parent.id,
              name: name.trim(),
              name_addition: nameAddition.trim() || null,
            }
      const created = await api.post<Party>('/parties', body)
      setDone(`${created.name} was added.`)
      setPerson(EMPTY_PERSON)
      setName('')
      setNameAddition('')
      await onCreated(created, another)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'This could not be added. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-label={kind === 'person' ? 'Add person' : 'Add department'}
      data-testid={`hierarchy-dialog-${kind}`}
      className="space-y-3 rounded-md border border-accent/40 bg-bg-secondary p-3"
    >
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-text-primary">
          {kind === 'person' ? `Add a person to ${parent.name}` : `Add a department to ${parent.name}`}
        </p>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
        >
          Close
        </button>
      </div>

      {kind === 'person' ? (
        <>
          <label className="block text-xs font-medium text-text-secondary">
            Title (optional)
            <Input
              aria-label="Title"
              value={person.title}
              onChange={(event) => setPerson({ ...person, title: event.target.value })}
              className="mt-1"
            />
          </label>
          <PersonNameFields
            value={person}
            onChange={(next) => setPerson({ ...person, ...next })}
            testIdPrefix="hierarchy-person"
            autoFocus
          />
          <div className="grid gap-3 sm:grid-cols-3">
            {(
              [
                ['position', 'Position'],
                ['email', 'Email'],
                ['phone', 'Phone'],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="block text-xs font-medium text-text-secondary">
                {label}
                <Input
                  aria-label={label}
                  data-testid={`hierarchy-person-${key}`}
                  value={person[key]}
                  onChange={(event) => setPerson({ ...person, [key]: event.target.value })}
                  className="mt-1"
                />
              </label>
            ))}
          </div>
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              data-testid="hierarchy-person-crew"
              checked={person.is_crew}
              onChange={(event) => setPerson({ ...person, is_crew: event.target.checked })}
            />
            Crew — can be booked on a production
          </label>
          {person.is_crew && (
            <div className="text-xs font-medium text-text-secondary">
              <label htmlFor="hierarchy-person-roles">Crew roles</label>
              <div className="mt-1">
                <RolePicker
                  id="hierarchy-person-roles"
                  value={person.tags}
                  onChange={(tags) => setPerson({ ...person, tags })}
                  suggestions={[]}
                />
              </div>
            </div>
          )}
        </>
      ) : (
        <>
          <label className="block text-xs font-medium text-text-secondary">
            Name
            <Input
              aria-label="Department name"
              data-testid="hierarchy-department-name"
              value={name}
              autoFocus
              onChange={(event) => setName(event.target.value)}
              className="mt-1"
            />
          </label>
          <label className="block text-xs font-medium text-text-secondary">
            Additional line (optional)
            <Input
              aria-label="Additional line"
              value={nameAddition}
              onChange={(event) => setNameAddition(event.target.value)}
              className="mt-1"
            />
          </label>
        </>
      )}

      {error && (
        <p
          role="alert"
          data-testid="hierarchy-error"
          className="rounded-md border border-status-error/40 bg-status-error/10 px-2 py-1 text-xs text-status-error"
        >
          {error}
        </p>
      )}
      {done && !error && (
        <p role="status" data-testid="hierarchy-done" className="text-xs text-status-success">
          {done}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="hierarchy-save"
          disabled={busy || !ready}
          onClick={() => void save(false)}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
        >
          {busy ? 'Adding…' : 'Add'}
        </button>
        <button
          type="button"
          data-testid="hierarchy-save-another"
          disabled={busy || !ready}
          onClick={() => void save(true)}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg-hover disabled:opacity-60"
        >
          Add, then add another
        </button>
        {!ready && !busy && (
          // 17c: disabled, and says why.
          <span data-testid="hierarchy-why" className="text-xs text-text-tertiary">
            {kind === 'person' ? 'Enter a first or last name first.' : 'Enter a name first.'}
          </span>
        )}
      </div>
    </div>
  )
}

/** A person's own page: which organisation or department they belong to,
 *  and moving them in or out. */
function PersonParent({
  party,
  canEdit,
  onSaved,
}: {
  party: Party
  canEdit: boolean
  onSaved: () => Promise<void>
}) {
  const [choices, setChoices] = React.useState<Party[]>([])
  const [parentId, setParentId] = React.useState(party.parent_id ?? '')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => setParentId(party.parent_id ?? ''), [party])

  React.useEffect(() => {
    if (!canEdit) return
    void (async () => {
      try {
        const [orgs, departments] = await Promise.all([
          api.get<PartyPage>('/parties?type=organisation&limit=200'),
          api.get<PartyPage>('/parties?type=department&limit=200'),
        ])
        setChoices([...(orgs?.parties ?? []), ...(departments?.parties ?? [])])
      } catch {
        setChoices([])
      }
    })()
  }, [canEdit])

  async function move(target: string | null) {
    setBusy(true)
    setError(null)
    try {
      await api.patch(`/parties/${party.id}`, { parent_id: target })
      await onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'This could not be moved. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const current = choices.find((entry) => entry.id === party.parent_id)

  return (
    <div className="space-y-2">
      <p data-testid="hierarchy-belongs-to" className="text-sm text-text-secondary">
        {party.parent_id ? (
          <>
            Belongs to{' '}
            <Link href={`/contacts/${party.parent_id}`} className="text-accent underline">
              {current?.name ?? 'its organisation'}
            </Link>
            .
          </>
        ) : (
          'Not part of an organisation.'
        )}
      </p>
      {canEdit && (
        <div className="flex flex-wrap items-end gap-2">
          <label className="block text-xs font-medium text-text-secondary">
            Belongs to
            <select
              aria-label="Belongs to"
              data-testid="hierarchy-parent"
              value={parentId}
              onChange={(event) => setParentId(event.target.value)}
              className="mt-1 w-64 rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
            >
              <option value="">Nobody — on their own</option>
              {choices.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                  {entry.type === 'department' ? ' (department)' : ''}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            data-testid="hierarchy-move"
            disabled={busy || parentId === (party.parent_id ?? '')}
            onClick={() => void move(parentId || null)}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          >
            {parentId ? 'Move here' : 'Take out of the organisation'}
          </button>
          {parentId === (party.parent_id ?? '') && !busy && (
            <span className="text-xs text-text-tertiary">Choose where this person belongs.</span>
          )}
        </div>
      )}
      {error && (
        <p role="alert" data-testid="hierarchy-move-error" className="text-sm text-status-error">
          {error}
        </p>
      )}
    </div>
  )
}
