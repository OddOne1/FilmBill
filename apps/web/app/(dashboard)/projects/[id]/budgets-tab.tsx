'use client'

import * as React from 'react'
import Link from 'next/link'
import { Copy, Loader2, Lock, Plus, Star } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import type { Budget, Project } from '@/types'
import { BudgetsNoAccess, BudgetsReadOnlyNotice, useProjectAccess } from '../projects-chrome'

/**
 * A project's budgets. Draft-only (D72): no number, no finalize — so the
 * actions here are create, duplicate, mark primary, and open.
 */
export function BudgetsTab({ project }: { project: Project }) {
  const { loaded, canViewBudgets, canEditBudgets, budgetsRefused } = useProjectAccess()
  const [budgets, setBudgets] = React.useState<Budget[] | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [name, setName] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setBudgets(await api.get<Budget[]>(`/projects/${project.id}/budgets`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the budgets')
    }
  }, [project.id])

  React.useEffect(() => {
    if (!loaded || !canViewBudgets) return
    void load()
  }, [loaded, canViewBudgets, load])

  if (budgetsRefused) return <BudgetsNoAccess />

  const act = async (call: () => Promise<unknown>, failure: string) => {
    setBusy(true)
    setError(null)
    try {
      await call()
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-5">
      {loaded && !canEditBudgets && <BudgetsReadOnlyNotice />}

      {canEditBudgets && (
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (!name.trim()) return
              void act(async () => {
                await api.post(`/projects/${project.id}/budgets`, {
                  name: name.trim(),
                  is_primary: false,
                })
                setName('')
              }, 'The budget could not be created')
            }}
            className="flex items-end gap-2"
          >
            <label className="text-xs text-text-secondary">
              New budget
              <Input
                aria-label="New budget name"
                value={name}
                placeholder="Estimate v1"
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <button
              type="submit"
              disabled={busy || !name.trim()}
              title={!name.trim() ? 'Type a name first' : undefined}
              className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              <Plus className="h-3.5 w-3.5" /> Create
            </button>
          </form>
          <button
            type="button"
            data-testid="budget-create-example"
            disabled={busy}
            onClick={() =>
              void act(
                () => api.post(`/projects/${project.id}/budgets/example`),
                'The example could not be created',
              )
            }
            className="rounded-md border border-border px-3 py-1.5 text-sm text-text-primary"
          >
            Create example (S1)
          </button>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      {budgets === null ? (
        !error && (
          <div className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading budgets…
          </div>
        )
      ) : budgets.length === 0 ? (
        <p data-testid="budgets-empty" className="mt-6 text-sm text-text-secondary">
          No budgets yet.
          {canEditBudgets &&
            ' Create one, or start from the example to see how the sheet works.'}
        </p>
      ) : (
        <ul className="mt-5 divide-y divide-border rounded-md border border-border">
          {budgets.map((budget) => (
            <li
              key={budget.id}
              data-testid="budget-row"
              className="flex items-center justify-between gap-3 px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <Link
                  href={`/projects/${project.id}/budgets/${budget.id}`}
                  className="text-sm font-medium text-text-primary hover:underline"
                >
                  {budget.name}
                </Link>
                {budget.is_primary && (
                  <span className="inline-flex items-center gap-0.5 rounded bg-bg-secondary px-1.5 py-0.5 text-[11px] text-text-secondary">
                    <Star className="h-3 w-3" /> Primary
                  </span>
                )}
                {budget.is_locked && (
                  <span className="inline-flex items-center gap-0.5 rounded bg-bg-secondary px-1.5 py-0.5 text-[11px] text-text-secondary">
                    <Lock className="h-3 w-3" /> Locked
                  </span>
                )}
                <span className="text-xs text-text-tertiary">
                  {budget.section_count} sections · {budget.line_count} lines
                </span>
              </div>
              {canEditBudgets && (
                <div className="flex items-center gap-2">
                  {!budget.is_primary && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void act(
                          () => api.post(`/budgets/${budget.id}/set-primary`),
                          'Could not mark it primary',
                        )
                      }
                      className="text-xs text-text-secondary hover:text-text-primary"
                    >
                      Make primary
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy}
                    aria-label={`Duplicate ${budget.name}`}
                    onClick={() =>
                      void act(
                        () => api.post(`/budgets/${budget.id}/duplicate`, {}),
                        'The budget could not be duplicated',
                      )
                    }
                    className="inline-flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary"
                  >
                    <Copy className="h-3 w-3" /> Duplicate
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
