'use client'

import * as React from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { dayLabel } from '@/lib/calendar-dates'
import { cn } from '@/lib/utils'
import type { Project, ProjectCrew } from '@/types'
import { crewCountsText } from './calendar/strip-crew'

/**
 * Project → Crew (B2c-1): per strip, who is scheduled, booked or declined,
 * with the API's counts and clash warnings, and a way to open the strip on
 * the calendar where the people are changed. Read-only by design: every
 * change happens in the strip's panel.
 */
export function CrewTab({ project, onOpenStrip }: { project: Project; onOpenStrip: (stripId: string) => void }) {
  const [crew, setCrew] = React.useState<ProjectCrew | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    api
      .get<ProjectCrew>(`/projects/${project.id}/crew`)
      .then((loaded) => {
        setCrew(loaded)
        setError(null)
      })
      .catch((err) => setError(err instanceof ApiError ? err.detail : 'The crew could not be loaded. Try again.'))
  }, [project.id])

  if (error) {
    return (
      <p role="alert" className="mt-6 text-sm text-status-error">
        {error}
      </p>
    )
  }
  if (!crew) {
    return (
      <p className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading the crew…
      </p>
    )
  }
  if (crew.strips.length === 0) {
    return (
      <p data-testid="project-crew-empty" className="mt-6 text-sm text-text-secondary">
        This project has no strips yet. Add a strip on the Days tab, then add crew to it.
      </p>
    )
  }

  return (
    <div data-testid="project-crew" className="mt-6 space-y-5">
      {crew.strips.map((strip) => (
        <section key={strip.block_id} data-testid="project-crew-strip" className="rounded-md border border-border p-3">
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 className="text-sm font-medium text-text-primary">{strip.strip_name}</h3>
            <span className="text-xs text-text-secondary">
              {dayLabel(strip.start_date, true)} – {dayLabel(strip.end_date, true)}
            </span>
            <span data-testid="project-crew-counts" className="text-xs text-text-tertiary">
              {crewCountsText(strip.counts) || 'Nobody yet'}
              {strip.counts.declined > 0 && ` · ${strip.counts.declined} declined`}
            </span>
            {strip.counts.with_clashes > 0 && (
              <span className="inline-flex items-center gap-1 text-xs text-status-warning">
                <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                {strip.counts.with_clashes} with a clash
              </span>
            )}
            <button
              type="button"
              data-testid="project-crew-open"
              onClick={() => onOpenStrip(strip.block_id)}
              className="ml-auto text-xs text-accent hover:underline"
            >
              Open on the calendar
            </button>
          </div>
          {strip.assignments.length > 0 && (
            <ul className="mt-2 divide-y divide-border/60 text-sm">
              {strip.assignments.map((assignment) => (
                <li key={assignment.id} data-testid="project-crew-row" className="py-1.5">
                  <div className="flex items-center gap-2">
                    <span className="text-text-primary">{assignment.party_name}</span>
                    {assignment.role_name && <span className="text-xs text-text-secondary">{assignment.role_name}</span>}
                    <span className="text-xs text-text-tertiary">
                      {assignment.days.length} {assignment.days.length === 1 ? 'day' : 'days'}
                    </span>
                    <span
                      className={cn(
                        'ml-auto rounded px-1.5 py-0.5 text-2xs',
                        assignment.status === 'booked'
                          ? 'bg-status-success/15 text-status-success'
                          : assignment.status === 'scheduled'
                            ? 'bg-status-warning/15 text-status-warning'
                            : 'bg-bg-tertiary text-text-tertiary',
                      )}
                    >
                      {assignment.status}
                    </span>
                  </div>
                  {assignment.clashes.map((clash, index) => (
                    <p key={index} data-testid="project-crew-clash" className="mt-0.5 text-xs text-status-warning">
                      {clash.message}
                    </p>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  )
}
