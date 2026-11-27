'use client'

import * as React from 'react'
import { Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { dayLabel } from '@/lib/calendar-dates'
import type { Project, ProjectCalendar, ProjectGear } from '@/types'
import { StripGear } from './calendar/strip-gear'

/**
 * Project → Gear (B2c-2): one card per strip with the same Gear section as
 * the strip's panel on the calendar — bookings with warnings, Add gear, the
 * days checklist, Mark booked, Cancel — and the strip's **total weight**
 * with "n items without weight". The weights and counts are the API's
 * (`GET /projects/{id}/gear`); nothing is added up here. No prices.
 */
export function weightText(totalKg: string, missing: number): string {
  // The API's string as it is, as the device page shows a weight.
  const total = `${totalKg} kg`
  return missing > 0 ? `${total} · ${missing} ${missing === 1 ? 'item' : 'items'} without weight` : total
}

export function GearTab({
  project,
  canEdit,
  onOpenStrip,
}: {
  project: Project
  canEdit: boolean
  onOpenStrip: (stripId: string) => void
}) {
  const [gear, setGear] = React.useState<ProjectGear | null>(null)
  const [calendar, setCalendar] = React.useState<ProjectCalendar | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  const loadGear = React.useCallback(async () => {
    try {
      setGear(await api.get<ProjectGear>(`/projects/${project.id}/gear`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The gear could not be loaded. Try again.')
    }
  }, [project.id])

  React.useEffect(() => {
    void loadGear()
    api
      .get<ProjectCalendar>(`/projects/${project.id}/calendar`)
      .then(setCalendar)
      .catch((err) => setError(err instanceof ApiError ? err.detail : 'The strips could not be loaded. Try again.'))
  }, [project.id, loadGear])

  if (error) {
    return (
      <p role="alert" className="mt-6 text-sm text-status-error">
        {error}
      </p>
    )
  }
  if (!gear || !calendar) {
    return (
      <p className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading the gear…
      </p>
    )
  }
  if (gear.strips.length === 0) {
    return (
      <p data-testid="project-gear-empty" className="mt-6 text-sm text-text-secondary">
        This project has no strips yet. Add a strip on the Days tab, then add gear to it here.
      </p>
    )
  }

  return (
    <div data-testid="project-gear" className="mt-6 space-y-5">
      <p data-testid="project-gear-total" className="text-sm text-text-secondary">
        Total weight {weightText(gear.total_weight_kg, gear.items_without_weight)}
      </p>
      {gear.strips.map((entry) => {
        const strip = calendar.strips.find((candidate) => candidate.kind === 'strip' && candidate.id === entry.block_id)
        return (
          <section key={entry.block_id} data-testid="project-gear-strip" className="rounded-md border border-border p-3">
            <div className="flex flex-wrap items-baseline gap-2">
              <h3 className="text-sm font-medium text-text-primary">{entry.strip_name}</h3>
              <span className="text-xs text-text-secondary">
                {dayLabel(entry.start_date, true)} – {dayLabel(entry.end_date, true)}
              </span>
              <span data-testid="project-gear-weight" className="text-xs text-text-tertiary">
                {weightText(entry.total_weight_kg, entry.items_without_weight)}
              </span>
              <button
                type="button"
                data-testid="project-gear-open"
                onClick={() => onOpenStrip(entry.block_id)}
                className="ml-auto text-xs text-accent hover:underline"
              >
                Open on the calendar
              </button>
            </div>
            {strip ? (
              <StripGear embedded projectId={project.id} strip={strip} gear={entry} canEdit={canEdit} onChanged={loadGear} />
            ) : (
              <p className="mt-2 text-xs text-text-secondary">This strip could not be loaded. Reload the page.</p>
            )}
          </section>
        )
      })}
    </div>
  )
}
