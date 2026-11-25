'use client'

import * as React from 'react'
import { Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAmount } from '@/lib/money'
import { ITEM_KIND_LABELS, ITEM_KINDS, kindShort } from '@/lib/item-kinds'
import type {
  BudgetAdjustment,
  BudgetAdjustmentCreate,
  BudgetCalculated,
  BudgetSheet,
  CalendarStrip,
  ItemKind,
  ProjectCalendar,
} from '@/types'
import { toApiDecimal, trimZeros } from './budget-sheet'

/**
 * The two side panels of the sheet: Globals and Adjustments.
 *
 * Globals are a closed vocabulary (D74): a number somebody typed, or a count
 * of the project's days (`days:<type>`) that this screen shows read-only.
 * A line points at one by typing `=key` into its quantity or period.
 *
 * Adjustments change the **price only** — the engine's contract. A
 * contingency that must raise the cost side is a line of kind `fee` (D78),
 * and the panel says so rather than letting somebody find out from the
 * margin.
 */

interface PanelProps {
  sheet: BudgetSheet
  calc: BudgetCalculated | null
  editable: boolean
  onChange: () => Promise<void> | void
  onError: (message: string | null) => void
}

async function run(
  call: () => Promise<unknown>,
  failure: string,
  onChange: PanelProps['onChange'],
  onError: PanelProps['onError'],
) {
  onError(null)
  try {
    await call()
    await onChange()
    return true
  } catch (err) {
    onError(err instanceof ApiError ? err.detail : failure)
    return false
  }
}

export function GlobalsPanel({ sheet, editable, onChange, onError }: PanelProps) {
  const [key, setKey] = React.useState('')
  const [label, setLabel] = React.useState('')
  const [value, setValue] = React.useState('')
  const budgetId = sheet.budget.id
  const projectId = sheet.budget.project_id
  const hasCounted = sheet.globals.some((row) => row.is_computed)
  // The project's strips, for the "all strips / this strip" picker (B2a).
  // Only fetched when a counted global exists and can be changed; if the
  // calendar cannot be read the picker still offers "All strips".
  const [strips, setStrips] = React.useState<CalendarStrip[]>([])
  React.useEffect(() => {
    if (!hasCounted || !editable) return
    let live = true
    api
      .get<ProjectCalendar>(`/projects/${projectId}/calendar`)
      .then((calendar) => {
        if (live) setStrips(calendar.strips.filter((strip) => strip.kind === 'strip'))
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [hasCounted, editable, projectId])
  const missing = !key.trim() ? 'A key, e.g. crew_size.' : !label.trim() ? 'A label.' : !value.trim() ? 'A value.' : null

  return (
    <section data-testid="globals-panel" className="rounded-md border border-border p-3">
      <h2 className="text-sm font-medium text-text-primary">Globals</h2>
      <p className="mt-1 text-xs text-text-tertiary">
        Type <code>=key</code> into a line&rsquo;s quantity or period to use one.
      </p>
      <ul className="mt-2 space-y-1.5">
        {sheet.globals.map((row) => (
          <li key={row.id} data-testid="global-row" className="flex items-center gap-2 text-sm">
            <code className="w-28 shrink-0 truncate text-xs text-text-secondary">{row.key}</code>
            <span className="flex-1 truncate text-text-primary">{row.label}</span>
            {row.is_computed || !editable ? (
              <span
                className="w-16 text-right tabular-nums"
                title={row.is_computed ? `Counted from the project's ${row.source.split(':')[1]} days — change them on the Days tab.` : undefined}
              >
                {trimZeros(row.value)}
              </span>
            ) : (
              <input
                aria-label={`Value of ${row.key}`}
                defaultValue={trimZeros(row.value)}
                key={`${row.id}-${row.value}`}
                onBlur={(event) => {
                  if (event.target.value === trimZeros(row.value)) return
                  void run(
                    () => api.patch(`/budgets/${budgetId}/globals/${row.id}`, { value: toApiDecimal(event.target.value) }),
                    'The global could not be saved',
                    onChange,
                    onError,
                  )
                }}
                className="w-16 rounded border border-border bg-bg-primary px-1 text-right tabular-nums"
              />
            )}
            {row.is_computed && !editable && (
              <span className="text-[10px] text-text-tertiary">{row.block_label ? `from ${row.block_label}` : 'from Days'}</span>
            )}
            {row.is_computed && editable && (
              <select
                aria-label={`Strips counted by ${row.key}`}
                value={row.block_id ?? ''}
                onChange={(event) => {
                  const dayType = row.source.split(':')[1]
                  const source = event.target.value ? `days:${dayType}:${event.target.value}` : `days:${dayType}`
                  void run(
                    () => api.patch(`/budgets/${budgetId}/globals/${row.id}`, { source }),
                    'The global could not be changed',
                    onChange,
                    onError,
                  )
                }}
                className="max-w-[7rem] rounded border border-border bg-bg-primary px-1 text-[10px] text-text-secondary"
              >
                <option value="">All strips</option>
                {strips
                  .filter((strip) => strip.days.some((day) => day.type === row.source.split(':')[1]))
                  .map((strip) => (
                    <option key={strip.id} value={strip.id}>
                      {strip.label || `${strip.type} ${strip.start_date.slice(5)}`}
                    </option>
                  ))}
                {row.block_id && !strips.some((strip) => strip.id === row.block_id) && (
                  <option value={row.block_id}>{row.block_label ?? 'This strip'}</option>
                )}
              </select>
            )}
            {editable &&
              (row.referenced_by > 0 ? (
                // Not a dead button: the reason, where the button would be (17c).
                <span data-testid="global-in-use" className="text-[10px] text-text-tertiary">
                  used by {row.referenced_by} line{row.referenced_by === 1 ? '' : 's'}
                </span>
              ) : (
                <button
                  type="button"
                  aria-label={`Remove ${row.key}`}
                  onClick={() =>
                    void run(
                      () => api.post(`/budgets/${budgetId}/globals/${row.id}/remove`),
                      'The global could not be removed',
                      onChange,
                      onError,
                    )
                  }
                  className="text-text-tertiary hover:text-danger"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              ))}
          </li>
        ))}
        {sheet.globals
          .filter((row) => row.warning)
          .map((row) => (
            <li key={`${row.id}-warning`} data-testid="global-warning" role="alert" className="text-[11px] text-status-warning">
              {row.key}: {row.warning}
            </li>
          ))}
        {sheet.globals.length === 0 && <li className="text-xs text-text-tertiary">None yet.</li>}
      </ul>
      {editable && (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (missing) return
            void run(
              () =>
                api.post(`/budgets/${budgetId}/globals`, {
                  key: key.trim(),
                  label: label.trim(),
                  value: toApiDecimal(value),
                  source: 'manual',
                }),
              'The global could not be added',
              onChange,
              onError,
            ).then((ok) => {
              if (ok) {
                setKey('')
                setLabel('')
                setValue('')
              }
            })
          }}
          className="mt-3 flex flex-wrap items-center gap-1.5"
        >
          <input aria-label="New global key" placeholder="key" value={key} onChange={(e) => setKey(e.target.value)} className="w-24 rounded border border-border bg-bg-primary px-1 text-xs" />
          <input aria-label="New global label" placeholder="Label" value={label} onChange={(e) => setLabel(e.target.value)} className="w-28 rounded border border-border bg-bg-primary px-1 text-xs" />
          <input aria-label="New global value" placeholder="0" value={value} onChange={(e) => setValue(e.target.value)} className="w-14 rounded border border-border bg-bg-primary px-1 text-right text-xs" />
          <button type="submit" disabled={missing !== null} className="rounded bg-accent px-2 py-0.5 text-xs text-white disabled:opacity-50">
            Add
          </button>
          {missing && <span className="text-[10px] text-text-tertiary">{missing}</span>}
        </form>
      )}
    </section>
  )
}

/** The sections as the "Applies to" list shows them: the hierarchy in sheet
 *  order, sub-sections under their parent. The Extras section is not a
 *  place an adjustment goes — its lines are priced like any other, and a
 *  discount meant for one extra is a lower price on that extra. */
export function sectionOptions(sheet: BudgetSheet): { id: string; name: string; depth: number }[] {
  const live = sheet.sections.filter((section) => !section.is_extras)
  const byPosition = (a: { position: number }, b: { position: number }) => a.position - b.position
  const out: { id: string; name: string; depth: number }[] = []
  const walk = (parentId: string | null, depth: number) => {
    live
      .filter((section) => (section.parent_id ?? null) === parentId)
      .sort(byPosition)
      .forEach((section) => {
        out.push({ id: section.id, name: section.name, depth })
        walk(section.id, depth + 1)
      })
  }
  walk(null, 0)
  return out
}

/** "5 % off · equipment · whole budget". The value is shown as stored, not
 *  computed — the amount the engine applied sits beside it. */
export function adjustmentScope(row: BudgetAdjustment, sectionName: string, currency: string): string {
  const value = row.mode === 'percent' ? `${trimZeros(row.value)} %` : `${formatAmount(row.value)} ${currency}`
  const what = row.type === 'discount' ? `${value} off` : `${value} on top`
  const kinds = row.base === 'kinds' && (row.kinds ?? []).length > 0 ? (row.kinds ?? []).map(kindShort).join(', ') : 'all kinds'
  return `${what} · ${kinds} · ${sectionName}`
}

type CalcGroup = Extract<BudgetCalculated['result']['children'][number], { type: 'group' }>

function findGroup(children: BudgetCalculated['result']['children'], id: string): CalcGroup | undefined {
  for (const child of children) {
    if (child.type !== 'group') continue
    if (child.id === id) return child
    const found = findGroup(child.children as BudgetCalculated['result']['children'], id)
    if (found) return found
  }
  return undefined
}

const QUICK_ADDS: { label: string; kind: ItemKind }[] = [
  { label: 'Equipment discount', kind: 'equipment_rental' },
  { label: 'Crew discount', kind: 'labor' },
]

export function AdjustmentsPanel({ sheet, calc, editable, onChange, onError }: PanelProps) {
  const budgetId = sheet.budget.id
  const [label, setLabel] = React.useState('')
  const [type, setType] = React.useState<BudgetAdjustmentCreate['type']>('discount')
  const [mode, setMode] = React.useState<BudgetAdjustmentCreate['mode']>('percent')
  const [value, setValue] = React.useState('')
  const [sectionId, setSectionId] = React.useState('')
  const [kinds, setKinds] = React.useState<ItemKind[]>([])
  const [onlyKinds, setOnlyKinds] = React.useState(false)
  const valueInput = React.useRef<HTMLInputElement>(null)
  const options = sectionOptions(sheet)
  const sectionName = (id: string | null | undefined) =>
    id ? (sheet.sections.find((section) => section.id === id)?.name ?? '—') : 'whole budget'
  const missing = !label.trim()
    ? 'A label.'
    : !value.trim()
      ? 'A value.'
      : onlyKinds && kinds.length === 0
        ? 'At least one kind, or “everything”.'
        : null
  // The production fee has its own control at the top of the sheet (B1.2).
  const rows = sheet.adjustments.filter((row) => row.role !== 'production_fee')

  /** The engine's applied amount, read from the result — never derived. A
   *  whole-budget adjustment is in the document's list, a section's in its
   *  group's; both matched by label within that container. */
  const appliedAmount = (row: BudgetAdjustment) => {
    if (!calc) return undefined
    const list = row.section_id ? findGroup(calc.result.children, row.section_id)?.adjustments : calc.result.adjustments
    return list?.find((applied) => applied.label === row.label)?.amount
  }

  const matchesNothing = (row: BudgetAdjustment) =>
    (calc?.result.warnings ?? []).find(
      (warning) =>
        warning.code === 'kind_filter_matched_nothing' &&
        (warning.where ?? '') ===
          (row.section_id ? `group ${sectionName(row.section_id)}: ${row.label}` : `the document: ${row.label}`),
    )

  const quickAdd = (quick: (typeof QUICK_ADDS)[number]) => {
    setLabel(quick.label)
    setType('discount')
    setMode('percent')
    setSectionId('')
    setOnlyKinds(true)
    setKinds([quick.kind])
    setValue('')
    valueInput.current?.focus()
  }

  const toggleKind = (kind: ItemKind) =>
    setKinds((previous) => (previous.includes(kind) ? previous.filter((k) => k !== kind) : [...previous, kind]))

  return (
    <section data-testid="adjustments-panel" className="rounded-md border border-border p-3">
      <h2 className="text-sm font-medium text-text-primary">Adjustments</h2>
      <p className="mt-1 text-xs text-text-tertiary">
        Discounts and surcharges move the price, never the cost: margin changes, cost stays. A
        contingency or fee that is a real cost belongs in the sheet as a line of kind &ldquo;fee&rdquo;.
        They apply in the order listed.
      </p>
      <ul className="mt-2 space-y-1.5">
        {rows.map((row) => {
          const amount = appliedAmount(row)
          const warning = matchesNothing(row)
          return (
            <li key={row.id} data-testid="adjustment-row" className="text-sm">
              <div className="flex items-center gap-2">
                <span className="flex-1 truncate text-text-primary">{row.label}</span>
                <span className="text-xs text-text-secondary">{row.type === 'discount' ? '−' : '+'}</span>
                {editable ? (
                  <input
                    aria-label={`Value of ${row.label}`}
                    defaultValue={trimZeros(row.value)}
                    key={`${row.id}-${row.value}`}
                    onBlur={(event) => {
                      if (event.target.value === trimZeros(row.value)) return
                      void run(
                        () => api.patch(`/budgets/${budgetId}/adjustments/${row.id}`, { value: toApiDecimal(event.target.value) }),
                        'The adjustment could not be saved',
                        onChange,
                        onError,
                      )
                    }}
                    className="w-14 rounded border border-border bg-bg-primary px-1 text-right tabular-nums"
                  />
                ) : (
                  <span className="tabular-nums">{trimZeros(row.value)}</span>
                )}
                <span className="text-xs text-text-secondary">{row.mode === 'percent' ? '%' : sheet.budget.currency}</span>
                {amount && (
                  <span data-testid="adjustment-amount" className="w-20 text-right text-xs tabular-nums text-text-secondary">
                    {formatAmount(amount)}
                  </span>
                )}
                {editable && (
                  <button
                    type="button"
                    aria-label={`Remove ${row.label}`}
                    onClick={() =>
                      void run(
                        () => api.post(`/budgets/${budgetId}/adjustments/${row.id}/remove`),
                        'The adjustment could not be removed',
                        onChange,
                        onError,
                      )
                    }
                    className="text-text-tertiary hover:text-danger"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                )}
              </div>
              <p data-testid="adjustment-scope" className="text-xs text-text-tertiary">
                {adjustmentScope(row, sectionName(row.section_id), sheet.budget.currency)}
              </p>
              {warning && (
                <p data-testid="adjustment-warning" className="text-xs text-status-warning">
                  ⚠ No line of {(row.kinds ?? []).map(kindShort).join(', ')} here, so this takes nothing off.
                </p>
              )}
            </li>
          )
        })}
        {rows.length === 0 && <li className="text-xs text-text-tertiary">None.</li>}
      </ul>
      {editable && (
        <>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {QUICK_ADDS.map((quick) => (
              <button
                key={quick.kind}
                type="button"
                onClick={() => quickAdd(quick)}
                className="rounded border border-border px-2 py-0.5 text-xs text-text-secondary hover:text-text-primary"
              >
                {quick.label}
              </button>
            ))}
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (missing) return
              const filtered = onlyKinds && kinds.length > 0
              const body: BudgetAdjustmentCreate = {
                label: label.trim(),
                type,
                mode,
                value: toApiDecimal(value),
                base: filtered ? 'kinds' : 'subtotal',
                kinds: filtered ? kinds : [],
                section_id: sectionId || null,
              }
              void run(() => api.post(`/budgets/${budgetId}/adjustments`, body), 'The adjustment could not be added', onChange, onError).then(
                (ok) => {
                  if (ok) {
                    setLabel('')
                    setValue('')
                    setOnlyKinds(false)
                    setKinds([])
                  }
                },
              )
            }}
            className="mt-2 space-y-1.5"
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <input aria-label="New adjustment label" placeholder="Label" value={label} onChange={(e) => setLabel(e.target.value)} className="w-28 rounded border border-border bg-bg-primary px-1 text-xs" />
              <select aria-label="New adjustment type" value={type} onChange={(e) => setType(e.target.value as BudgetAdjustmentCreate['type'])} className="rounded border border-border bg-bg-primary text-xs">
                <option value="discount">Discount</option>
                <option value="surcharge">Surcharge</option>
              </select>
              <input ref={valueInput} aria-label="New adjustment value" placeholder="0" value={value} onChange={(e) => setValue(e.target.value)} className="w-12 rounded border border-border bg-bg-primary px-1 text-right text-xs" />
              <select aria-label="New adjustment mode" value={mode} onChange={(e) => setMode(e.target.value as BudgetAdjustmentCreate['mode'])} className="rounded border border-border bg-bg-primary text-xs">
                <option value="percent">%</option>
                <option value="amount">{sheet.budget.currency}</option>
              </select>
            </div>
            <label className="flex items-center gap-1.5 text-xs text-text-secondary">
              Applies to
              <select aria-label="New adjustment applies to" value={sectionId} onChange={(e) => setSectionId(e.target.value)} className="rounded border border-border bg-bg-primary text-xs">
                <option value="">Whole budget</option>
                {options.map((option) => (
                  <option key={option.id} value={option.id}>
                    {`${'  '.repeat(option.depth)}${option.depth > 0 ? '└ ' : ''}${option.name}`}
                  </option>
                ))}
              </select>
            </label>
            <fieldset className="text-xs text-text-secondary">
              <legend className="sr-only">On</legend>
              <div className="flex items-center gap-3">
                <span>On</span>
                <label className="flex items-center gap-1">
                  <input type="radio" name="adjustment-on" checked={!onlyKinds} onChange={() => setOnlyKinds(false)} />
                  everything
                </label>
                <label className="flex items-center gap-1">
                  <input type="radio" name="adjustment-on" checked={onlyKinds} onChange={() => setOnlyKinds(true)} />
                  only certain kinds
                </label>
              </div>
              {onlyKinds && (
                <div data-testid="adjustment-kinds" className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                  {ITEM_KINDS.map((kind) => (
                    <label key={kind} className="flex items-center gap-1">
                      <input type="checkbox" checked={kinds.includes(kind)} onChange={() => toggleKind(kind)} />
                      {ITEM_KIND_LABELS[kind]}
                    </label>
                  ))}
                </div>
              )}
            </fieldset>
            <div className="flex items-center gap-1.5">
              <button type="submit" disabled={missing !== null} className="rounded bg-accent px-2 py-0.5 text-xs text-white disabled:opacity-50">
                Add
              </button>
              {missing && <span className="text-[10px] text-text-tertiary">{missing}</span>}
            </div>
          </form>
        </>
      )}
    </section>
  )
}
