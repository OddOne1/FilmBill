'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import {
  EMPTY_PERSON_NAME,
  PersonNameFields,
  hasPersonName,
  personNamePayload,
  type PersonName,
} from '@/components/shared/person-name-fields'
import type { Party, PartyPage, PartyType } from '@/types'

/**
 * The New-contact dialog: the fields that match the chosen type — a person's
 * name in parts, an organisation's name and its additional line (P1b-4).
 */

const TYPE_CHOICES: { value: PartyType; label: string; help: string }[] = [
  {
    value: 'organisation',
    label: 'Organisation',
    help: 'A company or institution. The top of the tree — it has no parent.',
  },
  {
    value: 'department',
    label: 'Department',
    help: 'A part of an organisation, which it needs as its parent.',
  },
  {
    value: 'person',
    label: 'Person',
    help: 'A contact inside an organisation or a department, or somebody on their own.',
  },
]

export function NewPartyPanel({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: () => Promise<void>
}) {
  const [type, setType] = React.useState<PartyType>('organisation')
  const [name, setName] = React.useState('')
  const [nameAddition, setNameAddition] = React.useState('')
  const [personName, setPersonName] = React.useState<PersonName>(EMPTY_PERSON_NAME)
  const [title, setTitle] = React.useState('')
  const [position, setPosition] = React.useState('')
  const [parentId, setParentId] = React.useState('')
  const [organisations, setOrganisations] = React.useState<Party[]>([])
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  // A department needs a parent and a person may have one, so the picker is
  // loaded whenever either is chosen rather than on every render.
  React.useEffect(() => {
    if (type === 'organisation') return
    void (async () => {
      try {
        const page = await api.get<PartyPage>('/parties?type=organisation')
        setOrganisations(page.parties)
      } catch {
        setOrganisations([])
      }
    })()
  }, [type])

  const choice = TYPE_CHOICES.find((entry) => entry.value === type)
  const needsParent = type === 'department'
  const isPerson = type === 'person'
  const hasName = isPerson ? hasPersonName(personName) : Boolean(name.trim())

  async function create() {
    setSaving(true)
    setError(null)
    try {
      await api.post(
        '/parties',
        isPerson
          ? {
              type,
              ...personNamePayload(personName),
              title: title.trim() || null,
              position: position.trim() || null,
              parent_id: parentId || null,
            }
          : {
              type,
              name,
              name_addition: nameAddition.trim() || null,
              parent_id: parentId || null,
            },
      )
      await onCreated()
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : 'This contact could not be added. Try again.',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-label="New contact"
      data-testid="parties-new-panel"
      className="mt-4 rounded-md border border-accent/40 bg-bg-secondary p-4"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-primary">New contact</h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
        >
          Cancel
        </button>
      </div>

      <div className="mt-4 space-y-3">
        <label className="block text-xs font-medium text-text-secondary">
          What kind
          <select
            aria-label="What kind"
            value={type}
            onChange={(event) => setType(event.target.value as PartyType)}
            className="mt-1 w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm text-text-primary"
          >
            {TYPE_CHOICES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        {choice && (
          <p className="text-xs text-text-tertiary">{choice.help}</p>
        )}

        {isPerson ? (
          <>
            <label className="block text-xs font-medium text-text-secondary">
              Title (optional)
              <Input
                aria-label="Title"
                value={title}
                placeholder="Dr., Mag."
                onChange={(event) => setTitle(event.target.value)}
                className="mt-1"
              />
            </label>
            <PersonNameFields
              value={personName}
              onChange={setPersonName}
              testIdPrefix="parties-new"
            />
            <label className="block text-xs font-medium text-text-secondary">
              Position (optional)
              <Input
                aria-label="Position"
                value={position}
                onChange={(event) => setPosition(event.target.value)}
                className="mt-1"
              />
            </label>
          </>
        ) : (
          <>
            <label className="block text-xs font-medium text-text-secondary">
              Name
              <Input
                aria-label="Name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="mt-1"
              />
            </label>
            <label className="block text-xs font-medium text-text-secondary">
              Additional line (optional)
              <Input
                aria-label="Additional line"
                value={nameAddition}
                placeholder="Marketing department, Studio Vienna, c/o …"
                onChange={(event) => setNameAddition(event.target.value)}
                className="mt-1"
              />
            </label>
          </>
        )}

        {type !== 'organisation' && (
          <label className="block text-xs font-medium text-text-secondary">
            {needsParent ? 'Organisation (required)' : 'Organisation (optional)'}
            <select
              aria-label="Organisation"
              value={parentId}
              onChange={(event) => setParentId(event.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm text-text-primary"
            >
              <option value="">
                {needsParent ? 'Choose an organisation…' : 'None'}
              </option>
              {organisations.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {error && (
          <p
            role="alert"
            data-testid="parties-new-error"
            className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
          >
            {error}
          </p>
        )}

        <button
          type="button"
          data-testid="parties-new-submit"
          onClick={() => void create()}
          disabled={saving || !hasName || (needsParent && !parentId)}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
        >
          {saving
            ? 'Adding…'
            : !hasName
              ? isPerson
                ? 'Enter a first or last name first'
                : 'Enter a name first'
              : needsParent && !parentId
                ? 'Choose an organisation first'
                : 'Add contact'}
        </button>
      </div>
    </div>
  )
}
