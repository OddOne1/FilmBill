'use client'

import * as React from 'react'
import { Loader2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import type { PeriodUnit, Unit } from '@/types'
import {
  CatalogNoAccess,
  CatalogReadOnlyNotice,
  CatalogTabs,
  useCatalogAccess,
} from '../catalog-chrome'

/**
 * Units of measure.
 *
 * Nine arrive with the company (`seed_catalog`) and are marked
 * `is_system`. A system unit's **key and period are fixed** — the key is
 * what a CSV import matches on and the period is what the engine multiplies
 * by, so renaming `day` to `Tag` must not turn it into something else. The
 * labels are a company's own and can be edited.
 *
 * There is no delete. A unit in use is switched off instead, and the server
 * refuses even that while an active item points at it, naming the items.
 */

const PERIOD_UNITS: PeriodUnit[] = [
  'hour',
  'day',
  'week',
  'month',
  'piece',
  'flat',
]

export default function UnitsPage() {
  const { loaded, canEdit, refused } = useCatalogAccess()
  const [units, setUnits] = React.useState<Unit[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [adding, setAdding] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setUnits(await api.get<Unit[]>('/catalog/units?include_inactive=true'))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the units')
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    if (!loaded) return
    void load()
  }, [loaded, load])

  if (refused) return <CatalogNoAccess title="Units" />

  return (
    <div className="max-w-4xl p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Units</h1>
          <p className="mt-1 text-sm text-text-secondary">
            What a quantity is counted in. The period decides what the engine
            multiplies by.
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            data-testid="unit-new"
            onClick={() => setAdding(true)}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
          >
            New unit
          </button>
        )}
      </div>

      {loaded && !canEdit && <CatalogReadOnlyNotice />}
      <CatalogTabs />

      {adding && (
        <NewUnitForm
          onClose={() => setAdding(false)}
          onCreated={async () => {
            setAdding(false)
            await load()
          }}
          onError={setError}
        />
      )}

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {loading ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading&hellip;
        </div>
      ) : (
        <table className="mt-5 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
              <th className="py-2 pr-3 font-medium">Key</th>
              <th className="py-2 pr-3 font-medium">German</th>
              <th className="py-2 pr-3 font-medium">English</th>
              <th className="py-2 pr-3 font-medium">Period</th>
              <th className="py-2 pr-3 font-medium">Origin</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {units.map((unit) => (
              <UnitRow
                key={unit.id}
                unit={unit}
                canEdit={canEdit}
                onChanged={load}
                onError={setError}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function UnitRow({
  unit,
  canEdit,
  onChanged,
  onError,
}: {
  unit: Unit
  canEdit: boolean
  onChanged: () => Promise<void>
  onError: (message: string) => void
}) {
  const [editing, setEditing] = React.useState(false)
  const [labelDe, setLabelDe] = React.useState(unit.label_de)
  const [labelEn, setLabelEn] = React.useState(unit.label_en ?? '')
  const [busy, setBusy] = React.useState(false)

  const patch = async (body: Record<string, unknown>) => {
    setBusy(true)
    try {
      await api.patch(`/catalog/units/${unit.id}`, body)
      setEditing(false)
      await onChanged()
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : 'Could not save the unit')
    } finally {
      setBusy(false)
    }
  }

  return (
    <tr data-testid="unit-row" className="border-b border-border/60">
      <td className="py-2 pr-3 font-mono text-xs text-text-secondary">
        {unit.key}
      </td>
      <td className="py-2 pr-3">
        {editing ? (
          <Input
            aria-label="German label"
            value={labelDe}
            onChange={(event) => setLabelDe(event.target.value)}
            className="h-8 w-32"
          />
        ) : (
          unit.label_de
        )}
      </td>
      <td className="py-2 pr-3 text-text-secondary">
        {editing ? (
          <Input
            aria-label="English label"
            value={labelEn}
            onChange={(event) => setLabelEn(event.target.value)}
            className="h-8 w-32"
          />
        ) : (
          /* An em dash, not a fallback to the German: `label_en` is
             nullable because a company's own unit need not have one, and
             showing "Tag" in the English column would be a lie. */
          unit.label_en ?? '—'
        )}
      </td>
      <td className="py-2 pr-3 text-text-secondary">{unit.period_unit}</td>
      <td className="py-2 pr-3 text-xs text-text-tertiary">
        {unit.is_system ? 'system' : 'yours'}
        {unit.is_active ? '' : ' · off'}
      </td>
      <td className="py-2 text-right">
        {canEdit && (
          <span className="flex justify-end gap-2">
            {editing ? (
              <>
                <button
                  type="button"
                  disabled={busy || !labelDe.trim()}
                  onClick={() =>
                    void patch({
                      label_de: labelDe.trim(),
                      label_en: labelEn.trim() || null,
                    })
                  }
                  className="rounded bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                >
                  Save
                </button>
                <button
                  type="button"
                  onClick={() => setEditing(false)}
                  className="text-xs text-text-secondary"
                >
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="text-xs text-text-secondary hover:text-accent"
                >
                  Rename
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void patch({ is_active: !unit.is_active })}
                  className="text-xs text-text-secondary hover:text-accent disabled:opacity-50"
                >
                  {unit.is_active ? 'Switch off' : 'Switch on'}
                </button>
              </>
            )}
          </span>
        )}
      </td>
    </tr>
  )
}

function NewUnitForm({
  onClose,
  onCreated,
  onError,
}: {
  onClose: () => void
  onCreated: () => Promise<void>
  onError: (message: string) => void
}) {
  const [key, setKey] = React.useState('')
  const [labelDe, setLabelDe] = React.useState('')
  const [labelEn, setLabelEn] = React.useState('')
  const [periodUnit, setPeriodUnit] = React.useState<PeriodUnit>('piece')
  const [busy, setBusy] = React.useState(false)

  return (
    <form
      data-testid="unit-new-form"
      onSubmit={async (event) => {
        event.preventDefault()
        setBusy(true)
        try {
          await api.post('/catalog/units', {
            key: key.trim(),
            label_de: labelDe.trim(),
            // Null, not "": a unit of the company's own needs no English
            // label, and the column is nullable for exactly that.
            label_en: labelEn.trim() || null,
            period_unit: periodUnit,
          })
          await onCreated()
        } catch (err) {
          onError(err instanceof ApiError ? err.detail : 'Could not add the unit')
        } finally {
          setBusy(false)
        }
      }}
      className="mt-5 rounded-md border border-border bg-bg-secondary p-4"
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-text-secondary">
          Key
          <Input
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder="half_day"
            required
            className="mt-1 w-32 font-mono"
          />
        </label>
        <label className="text-xs text-text-secondary">
          German label
          <Input
            value={labelDe}
            onChange={(event) => setLabelDe(event.target.value)}
            placeholder="Halbtag"
            required
            className="mt-1 w-32"
          />
        </label>
        <label className="text-xs text-text-secondary">
          English label <span className="text-text-tertiary">(optional)</span>
          <Input
            value={labelEn}
            onChange={(event) => setLabelEn(event.target.value)}
            className="mt-1 w-32"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Period
          <select
            value={periodUnit}
            onChange={(event) => setPeriodUnit(event.target.value as PeriodUnit)}
            className="mt-1 block rounded-md border border-border bg-bg-primary px-2 py-1.5 text-sm text-text-primary"
          >
            {PERIOD_UNITS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          disabled={busy || !key.trim() || !labelDe.trim()}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Add
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-text-secondary"
        >
          Cancel
        </button>
      </div>
      <p className="mt-2 text-xs text-text-tertiary">
        The key is what a CSV import matches on and cannot be changed later.
      </p>
    </form>
  )
}
