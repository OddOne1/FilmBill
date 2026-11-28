'use client'

import * as React from 'react'
import { ExternalLink, MapPin } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { CountrySelect } from '@/components/ui/country-select'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { cn } from '@/lib/utils'
import type {
  CoordinatesParsed,
  Location,
  LocationChange,
  LocationUsage,
  MapLinkResolved,
  Party,
  PartyPage,
  PlaceUse,
} from '@/types'

/**
 * One location: create or edit (L1). A panel in the page, the way Contacts
 * opens a new contact, and the same panel inline in the strip's side panel
 * ("New location…").
 *
 * **Nothing about a place is worked out here.** The Coordinates field sends
 * what was pasted to `/locations/coordinates` and shows the API's answer
 * ("Latitude 48.2082, Longitude 16.3738") or its sentence; the map link is
 * the API's `map_link`, rendered and never built; "in use" comes from
 * `/locations/{id}/usage`.
 */

export const USE_LABEL: Record<PlaceUse, string> = {
  shoot: 'Shooting location',
  hotel: 'Hotel',
  hospital: 'Hospital',
  restaurant: 'Restaurant',
  other: 'Other',
}

export const ALL_USES: PlaceUse[] = ['shoot', 'hotel', 'hospital', 'restaurant', 'other']

export function usesText(uses: PlaceUse[]): string {
  return uses.map((use) => USE_LABEL[use]).join(' · ')
}

interface Draft {
  name: string
  uses: PlaceUse[]
  source_link: string
  street: string
  street2: string
  zip: string
  city: string
  country: string | null
  coordinates: string
  phone: string
  email: string
  website: string
  emergency_phone: string
  access_notes: string
  notes: string
  contact_party_id: string | null
  contact_party_name: string | null
  contact_text: string
}

function draftFrom(location: Location | null, initialName: string, initialUses: PlaceUse[]): Draft {
  return {
    name: location?.name ?? initialName,
    uses: location?.uses ?? initialUses,
    source_link: location?.source_link ?? '',
    street: location?.street ?? '',
    street2: location?.street2 ?? '',
    zip: location?.zip ?? '',
    city: location?.city ?? '',
    country: location?.country ?? null,
    coordinates: location?.latitude && location?.longitude ? `${location.latitude}, ${location.longitude}` : '',
    phone: location?.phone ?? '',
    email: location?.email ?? '',
    website: location?.website ?? '',
    emergency_phone: location?.emergency_phone ?? '',
    access_notes: location?.access_notes ?? '',
    notes: location?.notes ?? '',
    contact_party_id: location?.contact_party_id ?? null,
    contact_party_name: location?.contact_party_name ?? null,
    contact_text: location?.contact_text ?? '',
  }
}

const TEXT_FIELDS = [
  'source_link',
  'street',
  'street2',
  'zip',
  'city',
  'phone',
  'email',
  'website',
  'emergency_phone',
  'access_notes',
  'notes',
  'contact_text',
] as const

/** What a save sends: every field, empty text as null, the pasted pair as
 *  text for the API to read. */
function payload(draft: Draft, original: Location | null): Record<string, unknown> {
  const body: Record<string, unknown> = { name: draft.name.trim(), uses: draft.uses, country: draft.country }
  for (const field of TEXT_FIELDS) body[field] = draft[field].trim() || null
  const originalPair = original?.latitude && original?.longitude ? `${original.latitude}, ${original.longitude}` : ''
  if (!original || draft.coordinates.trim() !== originalPair) body.coordinates = draft.coordinates.trim()
  body.contact_party_id = draft.contact_party_id
  return body
}

export function LocationForm({
  location = null,
  initialName = '',
  initialUses = ['shoot'],
  uses = ALL_USES,
  projectId = null,
  canEdit,
  canSeeContacts = false,
  onClose,
  onSaved,
}: {
  /** Null for a new location. */
  location?: Location | null
  initialName?: string
  initialUses?: PlaceUse[]
  /** The uses this form offers (Hotel only for who may see crew). */
  uses?: PlaceUse[]
  /** A new location is also added to this project's list. */
  projectId?: string | null
  canEdit: boolean
  canSeeContacts?: boolean
  onClose: () => void
  onSaved: (location: Location, message: string | null) => void | Promise<void>
}) {
  const [draft, setDraft] = React.useState<Draft>(() => draftFrom(location, initialName, initialUses))
  const [linkReading, setLinkReading] = React.useState<MapLinkResolved | null>(null)
  const [reading, setReading] = React.useState(false)
  const [parsed, setParsed] = React.useState<CoordinatesParsed | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [saving, setSaving] = React.useState(false)
  const [usage, setUsage] = React.useState<LocationUsage | null>(null)
  const [confirmArchive, setConfirmArchive] = React.useState(false)
  const [verifyNote, setVerifyNote] = React.useState('')
  const [current, setCurrent] = React.useState<Location | null>(location)

  React.useEffect(() => {
    setCurrent(location)
    setDraft(draftFrom(location, initialName, initialUses))
    setLinkReading(null)
    setParsed(null)
    setError(null)
    setNotice(null)
    setUsage(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location, initialName])

  React.useEffect(() => {
    if (!current) return
    api
      .get<LocationUsage>(`/locations/${current.id}/usage`)
      .then(setUsage)
      .catch(() => setUsage(null))
  }, [current])

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((d) => ({ ...d, [key]: value }))

  const readCoordinates = async (text: string) => {
    if (!text.trim()) {
      setParsed(null)
      return
    }
    try {
      setParsed(await api.post<CoordinatesParsed>('/locations/coordinates', { text }))
    } catch {
      setParsed(null)
    }
  }

  /** A pasted Google / Apple Maps link: the API reads it (opening a short
   *  link once) and the form takes what it carries — the position always,
   *  the name and the address only into empty fields. */
  const readLink = async (url: string) => {
    if (!url.trim()) {
      setLinkReading(null)
      return
    }
    setReading(true)
    try {
      const answer = await api.post<MapLinkResolved>('/locations/resolve-link', { url })
      setLinkReading(answer)
      if (answer.ok) {
        setDraft((d) => ({
          ...d,
          coordinates: answer.latitude && answer.longitude ? `${answer.latitude}, ${answer.longitude}` : d.coordinates,
          name: d.name.trim() ? d.name : answer.name ?? d.name,
          street: d.street.trim() || d.city.trim() ? d.street : answer.address ?? d.street,
        }))
        if (answer.display) {
          setParsed({ ok: true, latitude: answer.latitude, longitude: answer.longitude, display: answer.display, refusal: null })
        }
      }
    } catch {
      setLinkReading(null)
    } finally {
      setReading(false)
    }
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const body = payload(draft, current)
      const change = current
        ? await api.patch<LocationChange>(`/locations/${current.id}`, body)
        : await api.post<LocationChange>('/locations', projectId ? { ...body, project_id: projectId } : body)
      setCurrent(change.location)
      setNotice(change.message ?? null)
      await onSaved(change.location, change.message ?? null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The location could not be saved. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const act = async (path: string, body: unknown, failure: string) => {
    setError(null)
    try {
      const change = await api.post<LocationChange>(path, body)
      setCurrent(change.location)
      await onSaved(change.location, change.message ?? null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : failure)
    }
  }

  const nameMissing = !draft.name.trim()
  const title = current ? current.name : 'New location'
  const isHospital = draft.uses.includes('hospital')

  return (
    <div
      role="dialog"
      aria-label={current ? `Location ${current.name}` : 'New location'}
      data-testid="location-form"
      className="mt-3 rounded-md border border-accent/40 bg-bg-secondary p-3 text-sm"
    >
      <div className="flex items-center gap-2">
        <MapPin className="h-4 w-4 text-text-tertiary" aria-hidden="true" />
        <h2 className="min-w-0 flex-1 truncate font-semibold text-text-primary">{title}</h2>
        {current && !current.is_active && (
          <span data-testid="location-archived" className="rounded bg-bg-tertiary px-1.5 py-0.5 text-2xs text-text-tertiary">
            archived
          </span>
        )}
        {current?.map_link && (
          <a
            data-testid="location-open-map"
            href={current.map_link}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
          >
            Open in maps <ExternalLink className="h-3 w-3" aria-hidden="true" />
          </a>
        )}
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
        >
          {canEdit ? 'Cancel' : 'Close'}
        </button>
      </div>
      {!canEdit && (
        <p data-testid="location-read-only" className="mt-1 text-xs text-text-tertiary">
          You can see this location but not change it — that needs the location edit permission.
        </p>
      )}

      <fieldset disabled={!canEdit} className="mt-3 grid grid-cols-2 gap-2">
        <label className="col-span-2 text-xs text-text-secondary">
          Name
          <Input aria-label="Location name" value={draft.name} onChange={(e) => set('name', e.target.value)} />
        </label>
        <div className="col-span-2 text-xs text-text-secondary">
          Used as
          <div role="group" aria-label="Used as" className="mt-1 flex flex-wrap gap-1">
            {uses.map((use) => {
              const on = draft.uses.includes(use)
              return (
                <button
                  key={use}
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  onClick={() =>
                    set('uses', on ? draft.uses.filter((u) => u !== use) : ALL_USES.filter((u) => u === use || draft.uses.includes(u)))
                  }
                  className={cn(
                    'rounded-md border px-2 py-1 text-xs',
                    on ? 'border-accent bg-accent/10 text-text-primary' : 'border-border text-text-secondary',
                  )}
                >
                  {USE_LABEL[use]}
                </button>
              )
            })}
          </div>
          <span className="mt-0.5 block text-2xs text-text-tertiary">
            A hotel or hospital is a shooting location only when “Shooting location” is ticked too.
          </span>
        </div>
        <label className="col-span-2 text-xs text-text-secondary">
          Map link
          <Input
            aria-label="Map link"
            placeholder="Paste a link from Google Maps or Apple Maps"
            value={draft.source_link}
            onChange={(e) => {
              set('source_link', e.target.value)
              setLinkReading(null)
            }}
            onBlur={(e) => void readLink(e.target.value)}
          />
          {reading && <span className="mt-0.5 block text-2xs text-text-tertiary">Reading the link…</span>}
          {linkReading && (
            <span
              data-testid="location-link-answer"
              role={linkReading.ok ? 'status' : 'alert'}
              className={cn('mt-0.5 block text-2xs', linkReading.ok ? 'text-text-secondary' : 'text-status-error')}
            >
              {linkReading.ok
                ? [linkReading.name, linkReading.address, linkReading.display].filter(Boolean).join(' · ') || 'Read.'
                : linkReading.refusal}
            </span>
          )}
        </label>
        <label className="col-span-2 text-xs text-text-secondary">
          Street
          <Input aria-label="Street" value={draft.street} onChange={(e) => set('street', e.target.value)} />
        </label>
        <label className="col-span-2 text-xs text-text-secondary">
          Second line
          <Input aria-label="Second address line" value={draft.street2} onChange={(e) => set('street2', e.target.value)} />
        </label>
        <label className="text-xs text-text-secondary">
          Postcode
          <Input aria-label="Postcode" value={draft.zip} onChange={(e) => set('zip', e.target.value)} />
        </label>
        <label className="text-xs text-text-secondary">
          City
          <Input aria-label="City" value={draft.city} onChange={(e) => set('city', e.target.value)} />
        </label>
        <div className="col-span-2 text-xs text-text-secondary">
          Country
          <CountrySelect aria-label="Country" allowEmpty value={draft.country} onChange={(code) => set('country', code)} disabled={!canEdit} />
        </div>
        <label className="col-span-2 text-xs text-text-secondary">
          Coordinates
          <Input
            aria-label="Coordinates"
            placeholder="Paste a pair, e.g. 48.2082, 16.3738"
            value={draft.coordinates}
            onChange={(e) => {
              set('coordinates', e.target.value)
              setParsed(null)
            }}
            onBlur={(e) => void readCoordinates(e.target.value)}
          />
          {parsed && (
            <span
              data-testid="location-coordinates-answer"
              role={parsed.ok ? 'status' : 'alert'}
              className={cn('mt-0.5 block text-2xs', parsed.ok ? 'text-text-secondary' : 'text-status-error')}
            >
              {parsed.ok ? parsed.display : parsed.refusal}
            </span>
          )}
          {!parsed && current?.coordinates_display && draft.coordinates.trim() && (
            <span className="mt-0.5 block text-2xs text-text-tertiary">{current.coordinates_display}</span>
          )}
        </label>
        <label className="text-xs text-text-secondary">
          Phone
          <Input aria-label="Phone" value={draft.phone} onChange={(e) => set('phone', e.target.value)} />
        </label>
        <label className="text-xs text-text-secondary">
          Email
          <Input aria-label="Email" value={draft.email} onChange={(e) => set('email', e.target.value)} />
        </label>
        <label className="text-xs text-text-secondary">
          Website
          <Input aria-label="Website" value={draft.website} onChange={(e) => set('website', e.target.value)} />
        </label>
        <label className="text-xs text-text-secondary">
          Emergency phone
          <Input aria-label="Emergency phone" value={draft.emergency_phone} onChange={(e) => set('emergency_phone', e.target.value)} />
        </label>
        <div className="col-span-2 text-xs text-text-secondary">
          On-site contact
          {canSeeContacts && (
            <ContactPicker
              value={draft.contact_party_id ? { id: draft.contact_party_id, name: draft.contact_party_name ?? '' } : null}
              disabled={!canEdit}
              onChange={(party) => setDraft((d) => ({ ...d, contact_party_id: party?.id ?? null, contact_party_name: party?.name ?? null }))}
            />
          )}
          <Input
            aria-label="On-site contact text"
            placeholder="Or type it: Frau Berger, Hausmeister, 0664 …"
            value={draft.contact_text}
            onChange={(e) => set('contact_text', e.target.value)}
            className="mt-1"
          />
        </div>
        <label className="col-span-2 text-xs text-text-secondary">
          Access
          <textarea
            aria-label="Access notes"
            value={draft.access_notes}
            onChange={(e) => set('access_notes', e.target.value)}
            placeholder="Parking, entrances, which door"
            className="mt-0.5 block w-full rounded-md border border-border bg-bg-primary px-2 py-1 text-sm"
            rows={2}
          />
        </label>
        <label className="col-span-2 text-xs text-text-secondary">
          Notes
          <textarea
            aria-label="Notes"
            value={draft.notes}
            onChange={(e) => set('notes', e.target.value)}
            className="mt-0.5 block w-full rounded-md border border-border bg-bg-primary px-2 py-1 text-sm"
            rows={2}
          />
          <span data-testid="location-notes-help" className="mt-0.5 block text-2xs text-text-tertiary">
            Do not store gate codes or passwords here.
          </span>
        </label>
      </fieldset>

      {(linkReading?.embed_link || current?.embed_link) && (
        <iframe
          title={`Map of ${draft.name || 'the location'}`}
          data-testid="location-map-view"
          src={(linkReading?.embed_link ?? current?.embed_link) as string}
          className="mt-3 h-48 w-full rounded-md border border-border"
          loading="lazy"
        />
      )}

      {isHospital && current && (
        <div data-testid="location-verified" className="mt-3 rounded-md border border-border p-2 text-xs">
          <p className="font-medium text-text-primary">
            {current.verified
              ? `Verified by ${current.verified_by_name ?? 'a former member'} on ${current.verified_on}`
              : 'Not verified'}
          </p>
          {current.verified && current.verification_note && (
            <p className="text-text-secondary">{current.verification_note}</p>
          )}
          <p className="text-text-tertiary">Changing the address or phone clears this.</p>
          <p className="text-text-tertiary">Verified means a person checked this entry, not that it is the nearest hospital.</p>
          {canEdit && current.is_active && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <Input
                aria-label="Verification note"
                placeholder="How was it checked? e.g. called the switchboard"
                value={verifyNote}
                onChange={(e) => setVerifyNote(e.target.value)}
                className="h-8 flex-1"
              />
              <button
                type="button"
                data-testid="location-verify"
                onClick={() => void act(`/locations/${current.id}/verify`, { note: verifyNote.trim() || null }, 'The hospital could not be marked verified. Try again.')}
                className="rounded-md border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-hover"
              >
                Mark as verified
              </button>
            </div>
          )}
        </div>
      )}
      {isHospital && !current && (
        <p className="mt-2 text-2xs text-text-tertiary">Save the hospital first; then it can be marked verified.</p>
      )}

      {notice && (
        <p role="status" data-testid="location-notice" className="mt-2 rounded bg-status-warning/10 px-2 py-1 text-xs text-status-warning">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" data-testid="location-error" className="mt-2 rounded border border-status-error/40 bg-status-error/10 px-2 py-1 text-xs text-status-error">
          {error}
        </p>
      )}

      {canEdit && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="location-save"
            disabled={saving || nameMissing}
            onClick={() => void save()}
            className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          >
            {saving ? 'Saving…' : current ? 'Save' : 'Create location'}
          </button>
          {nameMissing && <span className="text-2xs text-text-tertiary">Give the location a name to save it.</span>}
          {current && current.is_active && (
            <button
              type="button"
              data-testid="location-archive"
              onClick={() => setConfirmArchive(true)}
              className="ml-auto text-xs text-status-error hover:underline"
            >
              Archive
            </button>
          )}
          {current && !current.is_active && (
            <button
              type="button"
              data-testid="location-restore"
              onClick={() => void act(`/locations/${current.id}/restore`, undefined, 'The location could not be restored. Try again.')}
              className="ml-auto text-xs text-accent hover:underline"
            >
              Restore
            </button>
          )}
        </div>
      )}

      {current && usage && (
        <div data-testid="location-usage" className="mt-3 border-t border-border pt-2 text-xs">
          <p className="font-medium text-text-secondary">
            {usage.count === 0 ? 'Not used anywhere yet.' : `Used in ${usage.count} ${usage.count === 1 ? 'place' : 'places'}`}
          </p>
          <ul className="mt-1 space-y-0.5">
            {usage.uses.map((use, index) => (
              <li key={index} data-testid="location-use" className="text-text-secondary">
                {usageText(use)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {current && (
        <ConfirmDialog
          open={confirmArchive}
          onOpenChange={setConfirmArchive}
          title={`Archive ${current.name}?`}
          description={
            usage && usage.count > 0
              ? `It is used in ${usage.count} ${usage.count === 1 ? 'place' : 'places'}: ${usage.uses.map(usageText).join('; ')}. They keep showing it, marked archived. It can no longer be picked for anything new.`
              : 'It is not used anywhere. It can be restored later.'
          }
          confirmLabel="Archive"
          variant="danger"
          onConfirm={() => act(`/locations/${current.id}/archive`, undefined, 'The location could not be archived. Try again.')}
        />
      )}
    </div>
  )
}

export function usageText(use: LocationUsage['uses'][number]): string {
  const where = [use.project_name, use.strip_name].filter(Boolean).join(' · ')
  switch (use.kind) {
    case 'strip':
      return `${where}: the strip's location`
    case 'day':
      return `${where}: ${use.date}`
    case 'project':
      return `${where}: on the project's list`
    case 'hotel_stay':
      return `${where}: ${use.party_name ?? 'a crew member'} from ${use.date}`
  }
}

/** Pick an on-site contact from Contacts — search by name, `GET /parties`. */
function ContactPicker({
  value,
  disabled,
  onChange,
}: {
  value: { id: string; name: string } | null
  disabled?: boolean
  onChange: (party: { id: string; name: string } | null) => void
}) {
  const [query, setQuery] = React.useState('')
  const [results, setResults] = React.useState<Party[]>([])

  React.useEffect(() => {
    if (!query.trim()) {
      setResults([])
      return
    }
    const handle = setTimeout(() => {
      api
        .get<PartyPage>(`/parties?search=${encodeURIComponent(query.trim())}&limit=8`)
        .then((page) => setResults(page.parties ?? []))
        .catch(() => setResults([]))
    }, 200)
    return () => clearTimeout(handle)
  }, [query])

  if (value) {
    return (
      <div data-testid="location-contact" className="mt-1 flex items-center gap-2">
        <span className="text-sm text-text-primary">{value.name}</span>
        {!disabled && (
          <button type="button" onClick={() => onChange(null)} className="text-2xs text-text-tertiary hover:underline">
            Remove
          </button>
        )}
      </div>
    )
  }
  return (
    <div className="mt-1">
      <Input aria-label="Find a contact" placeholder="Pick from Contacts" value={query} onChange={(e) => setQuery(e.target.value)} disabled={disabled} />
      {results.length > 0 && (
        <ul className="mt-1 rounded-md border border-border bg-bg-primary">
          {results.map((party) => (
            <li key={party.id}>
              <button
                type="button"
                onClick={() => {
                  onChange({ id: party.id, name: party.name })
                  setQuery('')
                }}
                className="w-full px-2 py-1 text-left text-xs hover:bg-bg-hover"
              >
                {party.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
