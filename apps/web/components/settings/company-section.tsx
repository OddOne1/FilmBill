'use client'

import * as React from 'react'
import { Loader2 } from 'lucide-react'
import { usePermissions } from '@/hooks/use-permissions'

/**
 * The frame every Settings → Company screen shares.
 *
 * Four screens that each need the same three things — a title, the active
 * company's name so nobody edits the wrong company's VAT ID, and one honest
 * answer for "you may not do this" — and four copies of that is four places
 * for the answer to drift. The permission check in particular: a screen that
 * forgot it would render its form, let someone fill it in and then 404 on
 * save.
 *
 * It renders the refusal as a SENTENCE, never a blank page or a redirect
 * (CLAUDE.md rule 17c). A redirect makes a permission problem look like a
 * broken link; a blank screen is the dead-and-silent state this codebase has
 * agreed never to produce.
 */
export function CompanySection({
  title,
  description,
  permission = 'company.settings.edit',
  children,
}: {
  title: string
  description?: string
  /** The permission this screen's endpoints enforce. Stated per screen rather
   *  than assumed, so a screen whose API gate differs cannot silently inherit
   *  the wrong one. */
  permission?: string
  children: React.ReactNode
}) {
  const { can, loaded, active } = usePermissions()

  if (!loaded) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-text-tertiary">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading…
      </div>
    )
  }

  if (!active) {
    // An install whose switcher came back empty, or a company whose access was
    // revoked in another tab. Says which, because "no company" and "that
    // company is gone" lead to different next steps.
    return (
      <div className="p-8">
        <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
        <p className="mt-2 text-sm text-text-secondary">
          No company is active. An installation administrator can create one
          from the company menu in the top bar.
        </p>
      </div>
    )
  }

  if (!can(permission)) {
    return (
      <div className="p-8">
        <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role in {active.display_name} does not include this.
        </p>
      </div>
    )
  }

  return (
    <div className="max-w-3xl p-8">
      <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
      <p className="mt-1 text-sm text-text-secondary">
        {description ? `${description} ` : ''}
        <span className="text-text-tertiary">{active.display_name}</span>
      </p>
      <div className="mt-6">{children}</div>
    </div>
  )
}

/** A labelled field. Its own component only so that forty of them on the
 *  General screen stay readable. */
export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-text-secondary">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-text-tertiary">{hint}</span>}
    </label>
  )
}

/** The save row. One place, so "Saved" and the error banner read the same on
 *  every company screen.
 *
 *  `disabled` is only ever "a request is in flight", and it says so on the
 *  button — rule 17c: a control is never dead while the screen is silent, and
 *  a value that is merely unknown never blocks a save, because the server
 *  enforces the rule regardless. */
export function SaveRow({
  saving,
  saved,
  error,
  label = 'Save changes',
}: {
  saving: boolean
  saved: boolean
  error: string | null
  label?: string
}) {
  return (
    <div className="flex items-center gap-3">
      <button
        type="submit"
        disabled={saving}
        className="inline-flex h-9 items-center gap-2 rounded-md bg-accent px-4 text-sm font-medium text-accent-foreground transition-all hover:bg-accent-hover disabled:opacity-50"
      >
        {saving && <Loader2 className="h-4 w-4 animate-spin" />}
        {saving ? 'Saving…' : label}
      </button>
      {saved && !error && (
        <span className="text-xs text-text-secondary">Saved.</span>
      )}
      {error && (
        <span className="text-xs text-status-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}
