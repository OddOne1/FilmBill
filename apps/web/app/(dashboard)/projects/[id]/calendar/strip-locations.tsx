'use client'

import * as React from 'react'
import { api, ApiError } from '@/lib/api'
import { dayLabel, type IsoDate } from '@/lib/calendar-dates'
import type { BlockChange, CalendarDay, CalendarStrip, LocationRef } from '@/types'
import { LocationPicker } from '@/components/locations/location-picker'
import type { StripActions } from './strip-panel'

/**
 * The strip panel's Location and Hotels sections (L1).
 *
 * **Location**: the strip's location (a shooting location) with **New location…**;
 * under it the B2a free text, if any, as "Old text" with **Create location
 * from this text** (the create form opens with the name filled in — nothing
 * is created by itself); then every day with the location it happens at,
 * the API's `effective_location_ref`, and **Set for selected days**.
 *
 * Hotels and their guests live in the project's Locations tab (L1b).
 *
 * Every link is the API's `map_link`; nothing here builds one.
 */

export function StripLocationSection({
  strip,
  base,
  canEdit,
  actions,
  selected,
}: {
  strip: CalendarStrip
  /** `/projects/{id}/blocks/{strip id}` */
  base: string
  canEdit: boolean
  actions: StripActions
  /** The days ticked in the panel's day chips. */
  selected: Set<IsoDate>
}) {
  const [createName, setCreateName] = React.useState<string | null>(null)
  const [bulkName, setBulkName] = React.useState<string | null>(null)
  const setStrip = (locationId: string | null) =>
    actions.run(() => api.patch<BlockChange>(base, { location_id: locationId }), 'The strip location could not be saved')
  const setSelected = (locationId: string | null) =>
    actions.run(
      () => api.post<BlockChange>(`${base}/days/location`, { dates: Array.from(selected).sort(), location_id: locationId }),
      'The days could not be changed',
    )

  return (
    <section data-testid="strip-location" aria-label="Location" className="mt-4">
      <p className="text-xs font-medium text-text-secondary">Location</p>
      <LocationPicker
        label="Strip location"
        testId="strip-location-picker"
        value={strip.location_ref}
        use="shoot"
        canEdit={canEdit}
        onChange={setStrip}
        createName={createName}
        onCreateNameUsed={() => setCreateName(null)}
      />
      {strip.location && (
        <div data-testid="strip-location-old-text" className="mt-1.5 rounded bg-bg-secondary px-2 py-1 text-xs text-text-secondary">
          <span className="text-text-tertiary">Old text:</span> {strip.location}
          {canEdit && (
            <button
              type="button"
              data-testid="strip-location-from-text"
              onClick={() => setCreateName(strip.location ?? '')}
              className="ml-2 text-accent hover:underline"
            >
              Create location from this text
            </button>
          )}
        </div>
      )}

      {strip.days.length > 0 && (
        <ul data-testid="strip-day-locations" aria-label="Location of each day" className="mt-2 space-y-0.5 text-xs">
          {strip.days.map((day) => (
            <li key={day.id} data-testid="strip-day-location" data-date={day.date} className="flex items-center gap-2">
              <span className="w-24 shrink-0 text-text-tertiary">{dayLabel(day.date, false)}</span>
              <PlaceName place={day.effective_location_ref} />
              {day.effective_location_source === 'day' && (
                <span data-testid="strip-day-location-own" className="rounded bg-accent/10 px-1 text-2xs text-accent">
                  this day only
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {canEdit && (
        <div className="mt-2">
          <p className="text-2xs text-text-tertiary">
            {selected.size === 0
              ? 'Select days above to give them a different location.'
              : `Set a different location for the ${selected.size} selected ${selected.size === 1 ? 'day' : 'days'}:`}
          </p>
          {selected.size > 0 && (
            <div className="mt-1 flex flex-wrap items-start gap-2">
              <div className="min-w-[12rem] flex-1">
                <LocationPicker
                  label="Location for selected days"
                  testId="strip-bulk-location"
                  value={null}
                  use="shoot"
                  canEdit
                  onChange={(id) => setSelected(id)}
                  createName={bulkName}
                  onCreateNameUsed={() => setBulkName(null)}
                />
              </div>
              <button
                type="button"
                data-testid="strip-bulk-location-clear"
                onClick={() => void setSelected(null)}
                className="rounded-md border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-hover"
              >
                Back to the strip&rsquo;s location
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

export function PlaceName({ place }: { place: LocationRef | null | undefined }) {
  if (!place) return <span className="text-text-tertiary">No location</span>
  return (
    <span className="inline-flex min-w-0 items-center gap-1">
      <span className="truncate text-text-primary">{place.name}</span>
      {!place.is_active && <span className="rounded bg-bg-tertiary px-1 text-2xs text-text-tertiary">archived</span>}
      {place.map_link && (
        <a href={place.map_link} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
          map
        </a>
      )}
    </span>
  )
}

/** "Different location for this day", inside the one-day details. */
export function DayLocation({
  projectId,
  day,
  canEdit,
  actions,
}: {
  projectId: string
  day: CalendarDay
  canEdit: boolean
  actions: StripActions
}) {
  const [createName, setCreateName] = React.useState<string | null>(null)
  const save = async (locationId: string | null) => {
    actions.onError(null)
    try {
      await api.patch(`/projects/${projectId}/days/${day.id}`, { location_id: locationId })
      await actions.reload()
    } catch (err) {
      actions.onError(err instanceof ApiError ? err.detail : 'The day could not be saved')
    }
  }
  return (
    <div data-testid="day-location" className="text-xs">
      <p className="text-text-secondary">
        {day.effective_location_source === 'strip' ? (
          <>
            At the strip&rsquo;s location: <PlaceName place={day.effective_location_ref} />
          </>
        ) : day.effective_location_source === 'day' ? (
          'A different location for this day:'
        ) : (
          'No location yet.'
        )}
      </p>
      <LocationPicker
        label={`Different location for ${day.date}`}
        testId="day-location-picker"
        value={day.location_ref}
        use="shoot"
        canEdit={canEdit}
        onChange={save}
        clearLabel="Same as the strip"
        createName={createName}
        onCreateNameUsed={() => setCreateName(null)}
      />
      {day.location && (
        <p data-testid="day-location-old-text" className="mt-1 text-text-secondary">
          <span className="text-text-tertiary">Old text:</span> {day.location}
          {canEdit && (
            <button type="button" onClick={() => setCreateName(day.location ?? '')} className="ml-2 text-accent hover:underline">
              Create location from this text
            </button>
          )}
        </p>
      )}
    </div>
  )
}
