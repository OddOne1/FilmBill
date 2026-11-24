'use client'

import * as React from 'react'
import { usePermissions } from '@/hooks/use-permissions'

/**
 * What every project and budget screen shares: the access answers and the
 * sentences that explain a missing button.
 *
 * Rule 17c — a disabled control must always say why. The shape that rule
 * keeps catching is a screen where somebody without the edit permission sees
 * buttons that do nothing and no explanation, so here the buttons are simply
 * not drawn and one of these notices is, always together.
 */

export const STATUS_LABELS: Record<string, string> = {
  planning: 'Planning',
  active: 'Active',
  wrapped: 'Wrapped',
  archived: 'Archived',
}

export const DAY_TYPE_LABELS: Record<string, string> = {
  prep: 'Prep',
  shoot: 'Shoot',
  travel: 'Travel',
  wrap: 'Wrap',
  post: 'Post',
  office: 'Office',
  off: 'Off',
}

export const DAY_TYPES = Object.keys(DAY_TYPE_LABELS)

/** `loaded && !can(...)` in one place, so no screen renders the refusal
 *  while the permissions are still loading. */
export function useProjectAccess() {
  const { can, loaded } = usePermissions()
  return {
    loaded,
    canView: can('projects.view'),
    canEdit: can('projects.edit'),
    canViewBudgets: can('budgets.view'),
    canEditBudgets: can('budgets.edit'),
    canViewParties: can('parties.view'),
    refused: loaded && !can('projects.view'),
    budgetsRefused: loaded && !can('budgets.view'),
  }
}

function Notice({ testId, children }: { testId: string; children: React.ReactNode }) {
  return (
    <p
      data-testid={testId}
      className="mt-4 rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary"
    >
      {children}
    </p>
  )
}

export function ProjectsReadOnlyNotice() {
  return (
    <Notice testId="projects-read-only">
      You can see projects and their days but not change them — creating a
      project, editing it or planning its days needs the project edit
      permission. An owner or administrator can grant it under Settings →
      Company → Members.
    </Notice>
  )
}

export function BudgetsReadOnlyNotice() {
  return (
    <Notice testId="budgets-read-only">
      You can read this budget but not change it — editing lines, sections,
      globals or adjustments, entering costs, locking it or importing into
      it needs the budget edit permission. An owner or administrator can grant it under
      Settings → Company → Members.
    </Notice>
  )
}

/** Why a budget cannot be edited although the person may: it is locked. */
export function BudgetLockedNotice({ lockedAt }: { lockedAt?: string | null }) {
  return (
    <Notice testId="budget-locked">
      This budget is locked{lockedAt ? ` since ${lockedAt.slice(0, 10)}` : ''}:
      its lines, sections, globals and adjustments cannot be changed. Its
      name still can. Unlock it to edit — unlocking is recorded in the audit
      log. Costs are still open: orders, invoices and ETCs can be entered
      against a locked estimate, because the locked estimate is what they
      are measured against.
    </Notice>
  )
}

/** Budgets carry cost rates and margins, so they are closed to roles without
 *  `budgets.view` (staff, the tax advisor) — said in a sentence rather than
 *  by an empty tab. */
export function BudgetsNoAccess() {
  return (
    <Notice testId="budgets-no-access">
      Budgets show cost rates and margins, so they are only open to owners,
      administrators, accountants and producers. An owner or administrator
      can change that under Settings → Company → Members.
    </Notice>
  )
}

export function ProjectsNoAccess({ title = 'Projects' }: { title?: string }) {
  return (
    <div className="p-8">
      <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
      <p className="mt-2 text-sm text-text-secondary">
        You do not have access to this company&rsquo;s projects. An owner or
        administrator can change that under Settings → Company → Members.
      </p>
    </div>
  )
}
