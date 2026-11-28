'use client'

import * as React from 'react'
import { ExternalLink, Loader2, Map as MapIcon, Plus, X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { usePermissions } from '@/hooks/use-permissions'
import { dayLabel } from '@/lib/calendar-dates'
import type {
  HospitalOffer,
  HospitalSuggestions,
  Location,
  PlaceUse,
  Project,
  ProjectCrew,
  ProjectLocationEntry,
  ProjectLocations,
} from '@/types'
import { ALL_USES, LocationForm, USE_LABEL, usesText } from '@/components/locations/location-form'
import { LocationPicker } from '@/components/locations/location-picker'

/**
 * Project → Locations (L1b): every place this production uses, by use —
 * shooting locations, hotels (with their guests and dates, and the booking
 * confirmations), hospitals (with the ones OpenStreetMap suggests near the
 * shooting locations), restaurants, other. A place with several uses shows
 * in each of its sections.
 *
 * Nothing about a place is worked out here: links, map views, nights,
 * distances and the suggestions are the API's.
 */

type AttachmentDownload_ = { url: string }

const SECTIONS: { use: PlaceUse; title: string; empty: string }[] = [
  { use: 'shoot', title: 'Shooting locations', empty: 'No shooting location yet. Add one here, or pick one for a strip on the calendar.' },
  { use: 'hotel', title: 'Hotels', empty: 'No hotel yet. Add the hotels the crew stay at, then add each guest with their dates.' },
  { use: 'hospital', title: 'Hospitals', empty: 'No hospital yet. Add one of the suggestions below, or your own.' },
  { use: 'restaurant', title: 'Restaurants', empty: 'No restaurant yet.' },
  { use: 'other', title: 'Other', empty: 'Nothing else yet — parking, base camp, a station.' },
]

export function LocationsTab({ project, canEdit }: { project: Project; canEdit: boolean }) {
  const { can } = usePermissions()
  const canEditLocations = can('locations.edit')
  const base = `/projects/${project.id}`
  const [data, setData] = React.useState<ProjectLocations | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [creating, setCreating] = React.useState(false)
  const [editing, setEditing] = React.useState<Location | null>(null)
  const [crew, setCrew] = React.useState<{ party_id: string; party_name: string }[]>([])

  const load = React.useCallback(async () => {
    try {
      setData(await api.get<ProjectLocations>(`${base}/locations`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The locations could not be loaded. Try again.')
    }
  }, [base])

  React.useEffect(() => {
    void load()
  }, [load])

  React.useEffect(() => {
    if (!data?.hotels_visible) return
    api
      .get<ProjectCrew>(`${base}/crew`)
      .then((answer) => {
        const people = new Map<string, string>()
        for (const strip of answer.strips)
          for (const a of strip.assignments)
            if (a.status === 'scheduled' || a.status === 'booked') people.set(a.party_id, a.party_name)
        setCrew(Array.from(people, ([party_id, party_name]) => ({ party_id, party_name })).sort((a, b) => a.party_name.localeCompare(b.party_name)))
      })
      .catch(() => setCrew([]))
  }, [base, data?.hotels_visible])

  /** Every write answers with the whole list; a refusal is shown as said. */
  const run = async (call: () => Promise<ProjectLocations>, failure: string) => {
    setError(null)
    try {
      setData(await call())
      return true
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : failure)
      return false
    }
  }

  if (!data) {
    return error ? (
      <p role="alert" className="mt-6 text-sm text-status-error">{error}</p>
    ) : (
      <p className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading the locations…
      </p>
    )
  }

  const uses = ALL_USES.filter((u) => u !== 'hotel' || data.hotels_visible)
  const hasShootWithPin = data.locations.some((e) => e.location.uses.includes('shoot') && e.location.latitude)

  return (
    <div data-testid="project-locations" className="mt-6 space-y-6">
      {canEdit ? (
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-[16rem] flex-1">
            <LocationPicker
              label="Add a location"
              testId="project-location-add"
              value={null}
              use={null}
              projectId={project.id}
              canEdit
              onChange={(id) =>
                id
                  ? run(() => api.post<ProjectLocations>(`${base}/locations`, { location_id: id }), 'The location could not be added. Try again.')
                  : undefined
              }
            />
            <p className="mt-0.5 text-2xs text-text-tertiary">
              Places from earlier projects are found here too — pick one instead of typing it again.
            </p>
          </div>
          {canEditLocations && (
            <button
              type="button"
              data-testid="project-location-new"
              onClick={() => setCreating(true)}
              className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white"
            >
              <Plus className="h-3.5 w-3.5" /> New location
            </button>
          )}
        </div>
      ) : (
        <p data-testid="project-locations-read-only" className="text-xs text-text-tertiary">
          You can see this project&rsquo;s locations but not change them — that needs the project edit permission.
        </p>
      )}

      {creating && (
        <LocationForm
          projectId={project.id}
          uses={uses}
          canEdit
          canSeeContacts={can('parties.view')}
          onClose={() => setCreating(false)}
          onSaved={async () => {
            setCreating(false)
            await load()
          }}
        />
      )}
      {editing && (
        <LocationForm
          key={editing.id}
          location={editing}
          uses={uses}
          canEdit={canEditLocations}
          canSeeContacts={can('parties.view')}
          onClose={() => setEditing(null)}
          onSaved={async (saved) => {
            setEditing(saved)
            await load()
          }}
        />
      )}

      {error && (
        <p role="alert" data-testid="project-locations-error" className="rounded border border-status-error/40 bg-status-error/10 px-2 py-1 text-sm text-status-error">
          {error}
        </p>
      )}

      {SECTIONS.filter((section) => uses.includes(section.use)).map((section) => {
        const entries = data.locations.filter((e) => e.location.uses.includes(section.use))
        return (
          <section key={section.use} data-testid={`project-locations-${section.use}`} aria-label={section.title}>
            <h3 className="text-sm font-medium text-text-primary">{section.title}</h3>
            {entries.length === 0 && <p className="mt-1 text-xs text-text-tertiary">{section.empty}</p>}
            <div className="mt-2 space-y-2">
              {entries.map((entry) => (
                <LocationCard
                  key={entry.location.id}
                  entry={entry}
                  section={section.use}
                  base={base}
                  canEdit={canEdit}
                  crew={crew}
                  hotelsVisible={data.hotels_visible}
                  run={run}
                  onEdit={() => setEditing(entry.location)}
                />
              ))}
            </div>
            {section.use === 'hospital' && (
              <HospitalFinder base={base} canAdd={canEdit && canEditLocations} hasShootWithPin={hasShootWithPin} run={run} />
            )}
          </section>
        )
      })}
    </div>
  )
}

type Run = (call: () => Promise<ProjectLocations>, failure: string) => Promise<boolean>

function addressLine(location: Location): string {
  return [location.street, location.street2, [location.zip, location.city].filter(Boolean).join(' '), location.country]
    .filter(Boolean)
    .join(', ')
}

function LocationCard({
  entry,
  section,
  base,
  canEdit,
  crew,
  hotelsVisible,
  run,
  onEdit,
}: {
  entry: ProjectLocationEntry
  section: PlaceUse
  base: string
  canEdit: boolean
  crew: { party_id: string; party_name: string }[]
  hotelsVisible: boolean
  run: Run
  onEdit: () => void
}) {
  const { location } = entry
  const stripNames = entry.strip_names ?? []
  const dayDates = entry.day_dates ?? []
  const [showMap, setShowMap] = React.useState(false)
  const isHotelSection = section === 'hotel'
  // A place's confirmations show once: in Hotels when it is a hotel, else in
  // its first section.
  const firstSection = ALL_USES.find((u) => location.uses.includes(u) && (u !== 'hotel' || hotelsVisible))
  const showsAttachments = location.uses.includes('hotel') && hotelsVisible ? isHotelSection : section === firstSection
  const where = [
    stripNames.length ? `Strip ${stripNames.join(', ')}` : null,
    dayDates.length ? `${dayDates.length === 1 ? 'Day' : 'Days'} ${dayDates.map((d) => dayLabel(d, false)).join(', ')}` : null,
  ].filter(Boolean)
  return (
    <article data-testid="project-location" data-name={location.name} className="rounded-md border border-border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="font-medium text-text-primary">{location.name}</h4>
        {!location.is_active && <span className="rounded bg-bg-tertiary px-1 text-2xs text-text-tertiary">archived</span>}
        <span className="text-2xs text-text-tertiary">{usesText(location.uses)}</span>
        {location.from_openstreetmap && <span className="text-2xs text-text-tertiary">· from OpenStreetMap</span>}
        <div className="ml-auto flex items-center gap-3 text-xs">
          {location.map_link && (
            <a data-testid="project-location-map-link" href={location.map_link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-accent hover:underline">
              Open in maps <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          )}
          {location.embed_link && (
            <button type="button" onClick={() => setShowMap(!showMap)} aria-expanded={showMap} className="inline-flex items-center gap-0.5 text-text-secondary hover:text-text-primary">
              <MapIcon className="h-3 w-3" /> {showMap ? 'Hide map' : 'Map'}
            </button>
          )}
          <button type="button" onClick={onEdit} className="text-text-secondary hover:text-text-primary">
            {canEdit ? 'Edit' : 'Details'}
          </button>
          {canEdit && (
            <button
              type="button"
              aria-label={`Remove ${location.name} from this project`}
              onClick={() => void run(() => api.post<ProjectLocations>(`${base}/locations/${location.id}/remove`), 'The location could not be removed. Try again.')}
              className="text-text-tertiary hover:text-status-error"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
      <p className="text-xs text-text-secondary">
        {[addressLine(location), location.phone, location.emergency_phone ? `Emergency ${location.emergency_phone}` : null].filter(Boolean).join(' · ') || 'No address yet.'}
      </p>
      {where.length > 0 && <p className="text-2xs text-text-tertiary">{where.join(' · ')}</p>}
      {section === 'hospital' && (
        <p className="text-2xs text-text-tertiary">
          {location.verified ? `Verified by ${location.verified_by_name ?? 'a former member'} on ${location.verified_on}` : 'Not verified'}
        </p>
      )}
      {showMap && location.embed_link && (
        <iframe title={`Map of ${location.name}`} data-testid="project-location-map-view" src={location.embed_link} className="mt-2 h-56 w-full rounded-md border border-border" loading="lazy" />
      )}
      {isHotelSection && hotelsVisible && <Guests entry={entry} base={base} canEdit={canEdit} crew={crew} run={run} />}
      {showsAttachments && <Confirmations entry={entry} base={base} canEdit={canEdit} run={run} />}
    </article>
  )
}

function Guests({
  entry,
  base,
  canEdit,
  crew,
  run,
}: {
  entry: ProjectLocationEntry
  base: string
  canEdit: boolean
  crew: { party_id: string; party_name: string }[]
  run: Run
}) {
  const [person, setPerson] = React.useState('')
  const stays = entry.stays ?? []
  const patch = (stayId: string, body: Record<string, string>) =>
    run(() => api.patch<ProjectLocations>(`${base}/stays/${stayId}`, body), 'The stay could not be saved. Try again.')
  return (
    <div data-testid="hotel-guests" className="mt-2 border-t border-border/60 pt-2">
      <p className="text-xs font-medium text-text-secondary">Guests</p>
      {stays.length === 0 && <p className="text-2xs text-text-tertiary">Nobody stays here yet.</p>}
      <table className="mt-1 w-full text-xs">
        <tbody>
          {stays.map((stay) => (
            <tr key={stay.id} data-testid="hotel-guest">
              <td className="py-0.5 text-text-primary">{stay.party_name}</td>
              <td className="py-0.5">
                {canEdit ? (
                  <input type="date" aria-label={`Check-in of ${stay.party_name}`} defaultValue={stay.check_in} key={stay.check_in}
                    onBlur={(e) => e.target.value && e.target.value !== stay.check_in && void patch(stay.id, { check_in: e.target.value })}
                    className="h-7 rounded border border-border bg-bg-primary px-1" />
                ) : (
                  dayLabel(stay.check_in, false)
                )}
              </td>
              <td className="py-0.5">
                {canEdit ? (
                  <input type="date" aria-label={`Check-out of ${stay.party_name}`} defaultValue={stay.check_out} key={stay.check_out}
                    onBlur={(e) => e.target.value && e.target.value !== stay.check_out && void patch(stay.id, { check_out: e.target.value })}
                    className="h-7 rounded border border-border bg-bg-primary px-1" />
                ) : (
                  dayLabel(stay.check_out, false)
                )}
              </td>
              <td className="py-0.5 text-text-tertiary" data-testid="hotel-guest-nights">
                {stay.nights} {stay.nights === 1 ? 'night' : 'nights'}
              </td>
              <td className="py-0.5 text-right">
                {canEdit && (
                  <button type="button" aria-label={`Remove ${stay.party_name} from ${entry.location.name}`}
                    onClick={() => void run(() => api.post<ProjectLocations>(`${base}/stays/${stay.id}/remove`), 'The guest could not be removed. Try again.')}
                    className="text-text-tertiary hover:text-status-error">
                    <X className="h-3 w-3" />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {canEdit && (
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
          <select aria-label={`Add a guest to ${entry.location.name}`} value={person} onChange={(e) => setPerson(e.target.value)}
            className="h-7 rounded-md border border-border bg-bg-primary px-1.5">
            <option value="">Add a crew member…</option>
            {crew.map((c) => (
              <option key={c.party_id} value={c.party_id}>{c.party_name}</option>
            ))}
          </select>
          <button type="button" data-testid="hotel-guest-add" disabled={!person}
            onClick={async () => {
              const ok = await run(() => api.post<ProjectLocations>(`${base}/locations/${entry.location.id}/stays`, { party_id: person }), 'The guest could not be added. Try again.')
              if (ok) setPerson('')
            }}
            className="rounded-md border border-border px-2 py-1 disabled:opacity-50">
            Add guest
          </button>
          <span className="text-2xs text-text-tertiary">
            {crew.length === 0
              ? 'Nobody is on this project’s crew yet. Add crew to a strip first.'
              : person
                ? 'Dates start as their first and last day on this project; change them after.'
                : 'Pick a crew member to add.'}
          </span>
        </div>
      )}
    </div>
  )
}

function Confirmations({ entry, base, canEdit, run }: { entry: ProjectLocationEntry; base: string; canEdit: boolean; run: Run }) {
  const [title, setTitle] = React.useState('')
  const [text, setText] = React.useState('')
  const [open, setOpen] = React.useState<string | null>(null)
  const fileRef = React.useRef<HTMLInputElement>(null)
  const location = entry.location
  const attachments = entry.attachments ?? []
  const upload = async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    if (title.trim()) form.append('title', title.trim())
    const ok = await run(() => api.upload<ProjectLocations>(`${base}/locations/${location.id}/attachments`, form), 'The file could not be added. Try again.')
    if (ok) setTitle('')
    if (fileRef.current) fileRef.current.value = ''
  }
  const download = async (id: string) => {
    try {
      const link = await api.get<AttachmentDownload_>(`${base}/attachments/${id}/download`)
      window.open(link.url, '_blank', 'noopener')
    } catch {
      /* the list shows the file; a failed link is retried by clicking again */
    }
  }
  return (
    <div data-testid="location-confirmations" className="mt-2 border-t border-border/60 pt-2 text-xs">
      <p className="font-medium text-text-secondary">Booking confirmations</p>
      {attachments.length === 0 && <p className="text-2xs text-text-tertiary">None yet.</p>}
      <ul className="mt-1 space-y-1">
        {attachments.map((a) => (
          <li key={a.id} data-testid="location-confirmation" className="rounded border border-border/60 px-2 py-1">
            <div className="flex items-center gap-2">
              <span className="text-text-primary">{a.title}</span>
              <span className="text-2xs text-text-tertiary">
                {a.kind === 'file' ? a.file_name : 'text'}
                {a.created_by_name ? ` · ${a.created_by_name}` : ''}
              </span>
              {a.kind === 'file' ? (
                <button type="button" onClick={() => void download(a.id)} className="ml-auto text-accent hover:underline">Open</button>
              ) : (
                <button type="button" onClick={() => setOpen(open === a.id ? null : a.id)} className="ml-auto text-accent hover:underline">
                  {open === a.id ? 'Hide' : 'Show'}
                </button>
              )}
              {canEdit && (
                <button type="button" aria-label={`Remove ${a.title}`}
                  onClick={() => void run(() => api.post<ProjectLocations>(`${base}/attachments/${a.id}/remove`), 'The confirmation could not be removed. Try again.')}
                  className="text-text-tertiary hover:text-status-error">
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
            {open === a.id && a.body_text && (
              <pre data-testid="location-confirmation-text" className="mt-1 whitespace-pre-wrap font-sans text-text-secondary">{a.body_text}</pre>
            )}
          </li>
        ))}
      </ul>
      {canEdit && (
        <div className="mt-2 space-y-1">
          <Input aria-label={`Confirmation title for ${location.name}`} placeholder="Title, e.g. Booking.com 4711" value={title} onChange={(e) => setTitle(e.target.value)} className="h-8" />
          <div className="flex flex-wrap items-center gap-2">
            <input ref={fileRef} type="file" aria-label={`Attach a confirmation file to ${location.name}`}
              accept=".pdf,.eml,.msg,.txt,image/*"
              onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} className="text-2xs" />
            <span className="text-2xs text-text-tertiary">PDF, email (.eml), picture or text, up to 15 MB.</span>
          </div>
          <textarea aria-label={`Paste a confirmation for ${location.name}`} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="Or paste the email or message here" rows={3}
            className="block w-full rounded-md border border-border bg-bg-primary px-2 py-1 text-xs" />
          <button type="button" data-testid="location-confirmation-save-text" disabled={!text.trim()}
            onClick={async () => {
              const ok = await run(() => api.post<ProjectLocations>(`${base}/locations/${location.id}/attachments/text`, { title: title.trim() || null, text }), 'The text could not be saved. Try again.')
              if (ok) {
                setText('')
                setTitle('')
              }
            }}
            className="rounded-md border border-border px-2 py-1 disabled:opacity-50">
            Save pasted text
          </button>
          {!text.trim() && <span className="ml-2 text-2xs text-text-tertiary">Paste a text to save it.</span>}
        </div>
      )}
    </div>
  )
}

function HospitalFinder({ base, canAdd, hasShootWithPin, run }: { base: string; canAdd: boolean; hasShootWithPin: boolean; run: Run }) {
  const [answer, setAnswer] = React.useState<HospitalSuggestions | null>(null)
  const [loading, setLoading] = React.useState(false)

  const find = React.useCallback(async () => {
    setLoading(true)
    try {
      setAnswer(await api.get<HospitalSuggestions>(`${base}/hospital-suggestions`))
    } catch (err) {
      setAnswer({ groups: [], missing_coordinates: [], error: err instanceof ApiError ? err.detail : 'The hospital search did not answer. Try again.', source: 'OpenStreetMap' })
    } finally {
      setLoading(false)
    }
  }, [base])

  // Found automatically: as soon as a shooting location has a position.
  React.useEffect(() => {
    if (hasShootWithPin) void find()
  }, [hasShootWithPin, find])

  const add = async (offer: HospitalOffer) => {
    const ok = await run(
      () =>
        api.post<ProjectLocations>(`${base}/hospital-suggestions/add`, {
          osm_ref: offer.osm_ref, name: offer.name, latitude: offer.latitude, longitude: offer.longitude,
          phone: offer.phone, street: offer.street, zip: offer.zip, city: offer.city, country: offer.country, website: offer.website,
        }),
      'The hospital could not be added. Try again.',
    )
    if (ok) await find()
  }

  return (
    <div data-testid="hospital-suggestions" className="mt-3 rounded-md border border-dashed border-border p-3 text-xs">
      <div className="flex items-center gap-2">
        <p className="font-medium text-text-secondary">Nearest hospitals (OpenStreetMap)</p>
        <button type="button" onClick={() => void find()} disabled={loading || !hasShootWithPin} className="ml-auto text-accent hover:underline disabled:opacity-50">
          {loading ? 'Searching…' : 'Search again'}
        </button>
      </div>
      {!hasShootWithPin && (
        <p className="text-2xs text-text-tertiary">
          Give a shooting location coordinates or a map link; the nearest hospitals are then found by themselves.
        </p>
      )}
      {answer?.error && <p role="alert" className="mt-1 text-status-error">{answer.error}</p>}
      {answer && (answer.missing_coordinates ?? []).length > 0 && (
        <p className="mt-1 text-2xs text-text-tertiary">
          No position for {(answer.missing_coordinates ?? []).join(', ')}: add coordinates or a map link to search near {(answer.missing_coordinates ?? []).length === 1 ? 'it' : 'them'} too.
        </p>
      )}
      {answer?.groups.map((group) => (
        <div key={group.shooting_locations.join('|')} data-testid="hospital-group" className="mt-2">
          <p className="text-text-secondary">Near {group.shooting_locations.join(', ')}</p>
          {group.offers.length === 0 && <p className="text-2xs text-text-tertiary">OpenStreetMap knows no hospital within reach. Add one by hand.</p>}
          <ul className="mt-1 space-y-1">
            {group.offers.map((offer) => (
              <li key={offer.osm_ref} data-testid="hospital-offer" className="flex flex-wrap items-center gap-2">
                <span className="text-text-primary">{offer.name}</span>
                <span className="text-2xs text-text-tertiary">
                  {offer.distance_km} km from {offer.nearest_to} ·{' '}
                  {offer.emergency ? 'emergency department' : 'emergency department not confirmed'}
                  {offer.phone ? ` · ${offer.phone}` : ''}
                </span>
                {offer.map_link && (
                  <a href={offer.map_link} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">map</a>
                )}
                {offer.in_project ? (
                  <span data-testid="hospital-offer-added" className="ml-auto text-2xs text-text-tertiary">On this project</span>
                ) : canAdd ? (
                  <button type="button" data-testid="hospital-offer-add" onClick={() => void add(offer)} className="ml-auto rounded-md border border-border px-2 py-0.5 hover:bg-bg-hover">
                    Add
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
      <p className="mt-2 text-2xs text-text-tertiary">
        Suggestions from OpenStreetMap; check the emergency department before the shoot and mark the hospital verified.
      </p>
    </div>
  )
}
