'use client'

import * as React from 'react'
import { Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { dayLabel } from '@/lib/calendar-dates'
import { usePermissions } from '@/hooks/use-permissions'
import type { Project, ProjectCalendar, ProjectCrew } from '@/types'
import { StripCrew } from './calendar/strip-crew'

/**
 * Project → Crew (B2c-1): one card per strip, and in each the same Crew
 * section as the strip's panel on the calendar — who is on it with role,
 * status and clashes, **Add crew** with the availability picker, the days
 * checklist, Mark booked / Decline / Cancel / Remove and **Mark selected as
 * booked**. One component in both places, so the two can never offer
 * different things. "Open on the calendar" still jumps to the strip.
 *
 * Counts, states and clashes are the API's (`GET /projects/{id}/crew`); the
 * strips' days come from the calendar endpoint, which the checklist needs
 * for each day's type. Without `projects.edit` it is read-only and says why.
 */
export function CrewTab({
  project,
  canEdit,
  onOpenStrip,
}: {
  project: Project
  canEdit: boolean
  onOpenStrip: (stripId: string) => void
}) {
  const { can, active } = usePermissions()
  const [crew, setCrew] = React.useState<ProjectCrew | null>(null)
  const [calendar, setCalendar] = React.useState<ProjectCalendar | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  const loadCrew = React.useCallback(async () => {
    try {
      setCrew(await api.get<ProjectCrew>(`/projects/${project.id}/crew`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The crew could not be loaded. Try again.')
    }
  }, [project.id])

  React.useEffect(() => {
    void loadCrew()
    api
      .get<ProjectCalendar>(`/projects/${project.id}/calendar`)
      .then(setCalendar)
      .catch((err) => setError(err instanceof ApiError ? err.detail : 'The strips could not be loaded. Try again.'))
  }, [project.id, loadCrew])

  if (error) {
    return (
      <p role="alert" className="mt-6 text-sm text-status-error">
        {error}
      </p>
    )
  }
  if (!crew || !calendar) {
    return (
      <p className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading the crew…
      </p>
    )
  }
  if (crew.strips.length === 0) {
    return (
      <p data-testid="project-crew-empty" className="mt-6 text-sm text-text-secondary">
        This project has no strips yet. Add a strip on the Days tab, then add crew to it here.
      </p>
    )
  }

  return (
    <div data-testid="project-crew" className="mt-6 space-y-5">
      {crew.strips.map((entry) => {
        const strip = calendar.strips.find((candidate) => candidate.kind === 'strip' && candidate.id === entry.block_id)
        return (
          <section key={entry.block_id} data-testid="project-crew-strip" className="rounded-md border border-border p-3">
            <div className="flex flex-wrap items-baseline gap-2">
              <h3 className="text-sm font-medium text-text-primary">{entry.strip_name}</h3>
              <span className="text-xs text-text-secondary">
                {dayLabel(entry.start_date, true)} – {dayLabel(entry.end_date, true)}
              </span>
              <button
                type="button"
                data-testid="project-crew-open"
                onClick={() => onOpenStrip(entry.block_id)}
                className="ml-auto text-xs text-accent hover:underline"
              >
                Open on the calendar
              </button>
            </div>
            {strip ? (
              <StripCrew
                embedded
                projectId={project.id}
                strip={strip}
                crew={entry}
                canEdit={canEdit}
                canSeeRates={can('budgets.view')}
                currency={active?.default_currency ?? 'EUR'}
                onChanged={loadCrew}
              />
            ) : (
              <p className="mt-2 text-xs text-text-secondary">This strip could not be loaded. Reload the page.</p>
            )}
          </section>
        )
      })}
    </div>
  )
}
