'use client'

import * as React from 'react'
import { AlertTriangle, Plus, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import type {
  CalcChildResult,
  CalcGroupResult,
  CalcLineResult,
  CalcPreviewResponse,
} from '@/types'
import { isCalcGroupResult } from '@/types'
import {
  CatalogNoAccess,
  CatalogTabs,
  useCatalogAccess,
} from '../catalog-chrome'
import {
  type AdjustmentDraft,
  type ChildDraft,
  type DocumentDraft,
  type GroupDraft,
  type LineDraft,
  documentPayload,
  emptyAdjustment,
  emptyGroup,
  emptyLine,
  isGroupDraft,
  s1Example,
  taxRefsOf,
} from './drafts'

/**
 * Try the engine.
 *
 * **The web never computes money; it renders the API's numbers.** Every
 * figure on this screen — a line's net, a group's subtotal, the margin
 * percent, the VAT, the gross — arrives as a string from
 * `POST /calc/preview` and is printed. There is no multiplication, no
 * addition and no rounding in this file. That is not a style preference: a
 * second implementation in the browser is a second set of answers, and the
 * one in `apps/api/billing/calc` is the one that signs invoices
 * (CLAUDE.md rule 2).
 *
 * It follows that a refused document shows the engine's own sentence. Nest
 * a third group and the screen says "group 'x' would be at nesting level 3,
 * and a document nests groups 2 deep at most (SCOPE §5.4)" — because that
 * sentence names what to fix.
 */

const PERIOD_UNITS = ['hour', 'day', 'week', 'month', 'piece', 'flat']
const ITEM_KINDS = [
  '',
  'labor',
  'equipment_rental',
  'space_rental',
  'travel',
  'per_diem',
  'storage',
  'product',
  'software_license',
  'fee',
  'other',
]

export default function CalculatorPage() {
  const { refused } = useCatalogAccess()
  const [draft, setDraft] = React.useState<DocumentDraft>(() => ({
    id: 'document',
    title: 'Untitled',
    children: [emptyGroup()],
    adjustments: [],
  }))
  const [rates, setRates] = React.useState<Record<string, string>>({
    'AT-20': '20',
  })
  const [result, setResult] = React.useState<CalcPreviewResponse | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [pending, setPending] = React.useState(false)

  const refs = taxRefsOf(draft)
  // A stable key for the effect: the rates that matter are the ones the
  // document actually mentions.
  const ratesKey = refs.map((ref) => `${ref}=${rates[ref] ?? ''}`).join('|')

  React.useEffect(() => {
    let cancelled = false
    setPending(true)
    const handle = setTimeout(() => {
      void (async () => {
        const vat_rates: Record<string, string> = {}
        for (const ref of refs) {
          const rate = (rates[ref] ?? '').trim()
          if (rate) vat_rates[ref] = rate
        }
        try {
          const answer = await api.post<CalcPreviewResponse>('/calc/preview', {
            document: documentPayload(draft),
            // Omitted entirely when no rate is filled in: asking for VAT
            // without saying what VAT is a 422, and an empty box is not a
            // question.
            vat_rates:
              Object.keys(vat_rates).length === refs.length && refs.length > 0
                ? vat_rates
                : null,
          })
          if (!cancelled) {
            setResult(answer)
            setError(null)
          }
        } catch (err) {
          if (!cancelled) {
            // The result is kept on screen next to the message: a typo in
            // one field should not blank out the numbers somebody is
            // reading.
            setError(
              err instanceof ApiError
                ? err.detail
                : 'The engine could not calculate that',
            )
          }
        } finally {
          if (!cancelled) setPending(false)
        }
      })()
    }, 250)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(documentPayload(draft)), ratesKey])

  if (refused) return <CatalogNoAccess title="Try the engine" />

  const update = (children: ChildDraft[]) =>
    setDraft({ ...draft, children })

  return (
    <div className="max-w-6xl p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">
            Try the engine
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            Build a document and watch the numbers. Every figure below comes
            from the API — this screen does no arithmetic of its own.
          </p>
        </div>
        <button
          type="button"
          data-testid="load-s1"
          onClick={() => {
            setDraft(s1Example())
            setRates({ 'AT-20': '20' })
          }}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-text-secondary"
        >
          Load S1 example
        </button>
      </div>

      <CatalogTabs />

      <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section>
          <label className="text-xs text-text-secondary">
            Document title
            <Input
              value={draft.title}
              onChange={(event) =>
                setDraft({ ...draft, title: event.target.value })
              }
              className="mt-1 w-full"
            />
          </label>

          <div className="mt-4 space-y-4">
            {draft.children.map((child, index) => (
              <ChildEditor
                key={child.id}
                child={child}
                depth={0}
                onChange={(next) =>
                  update(draft.children.map((c, i) => (i === index ? next : c)))
                }
                onRemove={() =>
                  update(draft.children.filter((_, i) => i !== index))
                }
              />
            ))}
          </div>

          <div className="mt-4 flex gap-2">
            <button
              type="button"
              data-testid="add-line"
              onClick={() => update([...draft.children, emptyLine()])}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs text-text-secondary"
            >
              <Plus className="h-3 w-3" /> Line
            </button>
            <button
              type="button"
              data-testid="add-group"
              onClick={() => update([...draft.children, emptyGroup()])}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs text-text-secondary"
            >
              <Plus className="h-3 w-3" /> Group
            </button>
          </div>

          <AdjustmentEditor
            title="Document adjustments"
            adjustments={draft.adjustments}
            onChange={(adjustments) => setDraft({ ...draft, adjustments })}
          />
        </section>

        <aside>
          <h2 className="text-sm font-semibold text-text-primary">Result</h2>

          {error && (
            <p
              role="alert"
              data-testid="calc-error"
              className="mt-2 rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-xs text-danger"
            >
              {error}
            </p>
          )}

          {result ? (
            <>
              <dl className="mt-3 space-y-1 text-sm">
                <Figure label="Subtotal" value={result.subtotal} />
                {result.adjustments.map((adjustment, index) => (
                  <Figure
                    key={index}
                    label={`${adjustment.label} (on ${adjustment.base_amount})`}
                    value={adjustment.amount}
                    muted
                  />
                ))}
                <Figure label="Net" value={result.net} testId="calc-net" strong />
                <Figure label="Cost" value={result.cost} muted />
                <Figure label="Margin" value={result.margin} muted />
                <Figure
                  label="Margin %"
                  value={result.margin_percent ?? '—'}
                  muted
                />
              </dl>

              {result.vat && (
                <dl className="mt-3 border-t border-border pt-3 space-y-1 text-sm">
                  <Figure label="Net" value={result.vat.net} muted />
                  <Figure label="VAT" value={result.vat.vat} testId="calc-vat" />
                  <Figure
                    label="Gross"
                    value={result.vat.gross}
                    testId="calc-gross"
                    strong
                  />
                  {result.vat.by_tax_ref.map((bucket) => (
                    <div
                      key={bucket.tax_ref}
                      className="flex justify-between text-xs text-text-tertiary"
                    >
                      <span>
                        {bucket.tax_ref} @ {bucket.rate ?? '—'} %
                      </span>
                      <span className="font-mono">
                        {bucket.net} + {bucket.vat ?? '—'}
                      </span>
                    </div>
                  ))}
                </dl>
              )}

              <div className="mt-3 border-t border-border pt-3">
                <h3 className="text-xs font-medium uppercase tracking-wide text-text-tertiary">
                  Net per tax ref
                </h3>
                <dl data-testid="calc-by-tax-ref" className="mt-1 space-y-1 text-xs">
                  {Object.entries(result.net_by_tax_ref).map(([ref, net]) => (
                    <div key={ref} className="flex justify-between">
                      <span className="text-text-secondary">{ref || '(none)'}</span>
                      <span className="font-mono text-text-primary">{net}</span>
                    </div>
                  ))}
                </dl>
              </div>

              <div className="mt-3 border-t border-border pt-3">
                <h3 className="text-xs font-medium uppercase tracking-wide text-text-tertiary">
                  VAT rates to apply
                </h3>
                {refs.length === 0 ? (
                  <p className="mt-1 text-xs text-text-tertiary">
                    No line carries a tax ref yet.
                  </p>
                ) : (
                  refs.map((ref) => (
                    <label
                      key={ref}
                      className="mt-1 flex items-center justify-between gap-2 text-xs text-text-secondary"
                    >
                      {ref}
                      <Input
                        aria-label={`VAT rate for ${ref}`}
                        value={rates[ref] ?? ''}
                        onChange={(event) =>
                          setRates({ ...rates, [ref]: event.target.value })
                        }
                        className="h-7 w-16 font-mono"
                      />
                    </label>
                  ))
                )}
                {refs.some((ref) => !(rates[ref] ?? '').trim()) && (
                  // 17c, applied to a figure rather than a button: the VAT
                  // block is missing and the screen says what is missing.
                  <p className="mt-2 text-xs text-text-tertiary">
                    VAT is left out until every tax ref above has a rate —
                    the engine refuses a document that asks for VAT without
                    saying what VAT.
                  </p>
                )}
              </div>

              {result.warnings.length > 0 && (
                <ul
                  data-testid="calc-warnings"
                  className="mt-3 space-y-1 border-t border-border pt-3"
                >
                  {result.warnings.map((warning, index) => (
                    <li
                      key={index}
                      className="flex gap-2 text-xs text-text-secondary"
                    >
                      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warning" />
                      <span>
                        {warning.message}
                        {warning.where ? ` (${warning.where})` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              <div className="mt-4 border-t border-border pt-3">
                <h3 className="text-xs font-medium uppercase tracking-wide text-text-tertiary">
                  Line by line
                </h3>
                <div className="mt-2 space-y-1">
                  {result.children.map((row) => (
                    <ResultRow key={row.id} row={row} depth={0} />
                  ))}
                </div>
              </div>
            </>
          ) : (
            <p className="mt-3 text-sm text-text-secondary">
              {pending ? 'Calculating…' : 'Nothing to calculate yet.'}
            </p>
          )}
        </aside>
      </div>
    </div>
  )
}

function Figure({
  label,
  value,
  testId,
  strong,
  muted,
}: {
  label: string
  value: string
  testId?: string
  strong?: boolean
  muted?: boolean
}) {
  return (
    <div className="flex justify-between gap-3">
      <dt
        className={
          muted ? 'text-xs text-text-tertiary' : 'text-sm text-text-secondary'
        }
      >
        {label}
      </dt>
      <dd
        data-testid={testId}
        className={
          strong
            ? 'font-mono text-sm font-semibold text-text-primary'
            : muted
              ? 'font-mono text-xs text-text-secondary'
              : 'font-mono text-sm text-text-primary'
        }
      >
        {value}
      </dd>
    </div>
  )
}

function ResultRow({ row, depth }: { row: CalcChildResult; depth: number }) {
  if (isCalcGroupResult(row)) {
    const group = row as CalcGroupResult
    return (
      <div style={{ paddingLeft: `${depth * 0.75}rem` }}>
        <div
          data-testid="result-group"
          className="flex justify-between gap-3 text-xs"
        >
          <span className="font-medium text-text-primary">
            {group.title || group.id}
          </span>
          <span data-testid={`group-subtotal-${group.id}`} className="font-mono">
            {group.subtotal}
          </span>
        </div>
        {group.adjustments.map((adjustment, index) => (
          <div
            key={index}
            style={{ paddingLeft: '0.75rem' }}
            className="flex justify-between gap-3 text-xs text-text-tertiary"
          >
            <span>{adjustment.label}</span>
            <span className="font-mono">{adjustment.amount}</span>
          </div>
        ))}
        <div className="space-y-1">
          {group.children.map((child) => (
            <ResultRow key={child.id} row={child} depth={depth + 1} />
          ))}
        </div>
      </div>
    )
  }

  const line = row as CalcLineResult
  return (
    <div
      data-testid="result-line"
      style={{ paddingLeft: `${depth * 0.75}rem` }}
      className="flex justify-between gap-3 text-xs"
    >
      <span className="truncate text-text-secondary">
        {line.label || line.id}
      </span>
      <span className="shrink-0 font-mono text-text-primary">
        <span data-testid={`line-net-${line.id}`}>{line.net}</span>
        <span className="ml-2 text-text-tertiary">
          cost {line.cost_net} · margin {line.margin}
        </span>
      </span>
    </div>
  )
}

function ChildEditor({
  child,
  depth,
  onChange,
  onRemove,
}: {
  child: ChildDraft
  depth: number
  onChange: (next: ChildDraft) => void
  onRemove: () => void
}) {
  if (isGroupDraft(child)) {
    return (
      <GroupEditor
        group={child}
        depth={depth}
        onChange={onChange}
        onRemove={onRemove}
      />
    )
  }
  return <LineEditor line={child} onChange={onChange} onRemove={onRemove} />
}

function GroupEditor({
  group,
  depth,
  onChange,
  onRemove,
}: {
  group: GroupDraft
  depth: number
  onChange: (next: GroupDraft) => void
  onRemove: () => void
}) {
  return (
    <section
      data-testid="group-editor"
      className="rounded-md border border-border p-3"
    >
      <div className="flex items-center gap-2">
        <Input
          aria-label="Group title"
          value={group.title}
          onChange={(event) => onChange({ ...group, title: event.target.value })}
          className="h-8 w-56"
        />
        <span className="font-mono text-xs text-text-tertiary">{group.id}</span>
        <button
          type="button"
          aria-label="Remove group"
          onClick={onRemove}
          className="ml-auto text-text-tertiary hover:text-danger"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3 space-y-3">
        {group.children.map((child, index) => (
          <ChildEditor
            key={child.id}
            child={child}
            depth={depth + 1}
            onChange={(next) =>
              onChange({
                ...group,
                children: group.children.map((c, i) =>
                  i === index ? next : c,
                ),
              })
            }
            onRemove={() =>
              onChange({
                ...group,
                children: group.children.filter((_, i) => i !== index),
              })
            }
          />
        ))}
      </div>

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() =>
            onChange({ ...group, children: [...group.children, emptyLine()] })
          }
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-text-secondary"
        >
          <Plus className="h-3 w-3" /> Line
        </button>
        {/* Offered at every level, even the one the engine refuses: the
            depth rule is the engine's and its refusal names the group. A
            button hidden here would mean the rule lives in two places. */}
        <button
          type="button"
          data-testid="add-nested-group"
          onClick={() =>
            onChange({ ...group, children: [...group.children, emptyGroup()] })
          }
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-text-secondary"
        >
          <Plus className="h-3 w-3" /> Group inside
        </button>
      </div>

      <AdjustmentEditor
        title="Group adjustments"
        adjustments={group.adjustments}
        onChange={(adjustments) => onChange({ ...group, adjustments })}
      />
    </section>
  )
}

function LineEditor({
  line,
  onChange,
  onRemove,
}: {
  line: LineDraft
  onChange: (next: LineDraft) => void
  onRemove: () => void
}) {
  const field = (
    key: keyof LineDraft,
    label: string,
    width = 'w-20',
  ) => (
    <label className="text-xs text-text-secondary">
      {label}
      <Input
        aria-label={`${label} for ${line.id}`}
        value={String(line[key] ?? '')}
        onChange={(event) => onChange({ ...line, [key]: event.target.value })}
        className={`mt-1 h-8 ${width} font-mono`}
      />
    </label>
  )

  return (
    <div
      data-testid="line-editor"
      className="rounded-md border border-border/70 bg-bg-secondary/40 p-3"
    >
      <div className="flex items-center gap-2">
        <Input
          aria-label="Line label"
          value={line.label}
          onChange={(event) => onChange({ ...line, label: event.target.value })}
          placeholder="DoP"
          className="h-8 flex-1"
        />
        <span className="font-mono text-xs text-text-tertiary">{line.id}</span>
        <button
          type="button"
          aria-label="Remove line"
          onClick={onRemove}
          className="text-text-tertiary hover:text-danger"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-end gap-3">
        {field('quantity', 'Qty')}
        {field('period_value', 'Periods')}
        <label className="text-xs text-text-secondary">
          Unit
          <select
            aria-label={`Period unit for ${line.id}`}
            value={line.period_unit}
            onChange={(event) =>
              onChange({ ...line, period_unit: event.target.value })
            }
            className="mt-1 block h-8 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
          >
            {PERIOD_UNITS.map((unit) => (
              <option key={unit} value={unit}>
                {unit}
              </option>
            ))}
          </select>
        </label>
        {field('factor', 'Factor')}
        {field('unit_price', 'Price', 'w-24')}
        {field('unit_cost', 'Cost', 'w-24')}
        <label className="text-xs text-text-secondary">
          Kind
          <select
            aria-label={`Item kind for ${line.id}`}
            value={line.itemKind}
            onChange={(event) =>
              onChange({ ...line, itemKind: event.target.value })
            }
            className="mt-1 block h-8 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
          >
            {ITEM_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind || '(none)'}
              </option>
            ))}
          </select>
        </label>
        {field('tax_ref', 'Tax ref', 'w-24')}
        <label className="text-xs text-text-secondary">
          Line discount
          <span className="mt-1 flex gap-1">
            <select
              aria-label={`Discount mode for ${line.id}`}
              value={line.discountMode}
              onChange={(event) =>
                onChange({
                  ...line,
                  discountMode: event.target.value as LineDraft['discountMode'],
                })
              }
              className="h-8 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              <option value="">none</option>
              <option value="percent">%</option>
              <option value="amount">amount</option>
            </select>
            {line.discountMode && (
              <Input
                aria-label={`Discount value for ${line.id}`}
                value={line.discountValue}
                onChange={(event) =>
                  onChange({ ...line, discountValue: event.target.value })
                }
                className="h-8 w-16 font-mono"
              />
            )}
          </span>
        </label>
      </div>
    </div>
  )
}

function AdjustmentEditor({
  title,
  adjustments,
  onChange,
}: {
  title: string
  adjustments: AdjustmentDraft[]
  onChange: (next: AdjustmentDraft[]) => void
}) {
  return (
    <div className="mt-4">
      <h3 className="text-xs font-medium uppercase tracking-wide text-text-tertiary">
        {title}
      </h3>
      <div className="mt-2 space-y-2">
        {adjustments.map((adjustment, index) => (
          <div
            key={adjustment.id}
            data-testid="adjustment-editor"
            className="flex flex-wrap items-end gap-2 rounded-md border border-border/70 p-2"
          >
            <Input
              aria-label="Adjustment label"
              value={adjustment.label}
              onChange={(event) =>
                onChange(
                  adjustments.map((current, i) =>
                    i === index
                      ? { ...current, label: event.target.value }
                      : current,
                  ),
                )
              }
              className="h-8 w-40"
            />
            <select
              aria-label="Adjustment type"
              value={adjustment.type}
              onChange={(event) =>
                onChange(
                  adjustments.map((current, i) =>
                    i === index
                      ? {
                          ...current,
                          type: event.target.value as AdjustmentDraft['type'],
                        }
                      : current,
                  ),
                )
              }
              className="h-8 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              <option value="discount">discount</option>
              <option value="surcharge">surcharge</option>
            </select>
            <select
              aria-label="Adjustment mode"
              value={adjustment.mode}
              onChange={(event) =>
                onChange(
                  adjustments.map((current, i) =>
                    i === index
                      ? {
                          ...current,
                          mode: event.target.value as AdjustmentDraft['mode'],
                        }
                      : current,
                  ),
                )
              }
              className="h-8 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              <option value="percent">percent</option>
              <option value="amount">amount</option>
            </select>
            <Input
              aria-label="Adjustment value"
              value={adjustment.value}
              onChange={(event) =>
                onChange(
                  adjustments.map((current, i) =>
                    i === index
                      ? { ...current, value: event.target.value }
                      : current,
                  ),
                )
              }
              className="h-8 w-20 font-mono"
            />
            <select
              aria-label="Adjustment base"
              value={adjustment.base}
              onChange={(event) =>
                onChange(
                  adjustments.map((current, i) =>
                    i === index
                      ? {
                          ...current,
                          base: event.target.value as AdjustmentDraft['base'],
                        }
                      : current,
                  ),
                )
              }
              className="h-8 rounded-md border border-border bg-bg-primary px-2 text-sm text-text-primary"
            >
              <option value="subtotal">on the subtotal</option>
              <option value="subtotal_incl_previous">
                on the subtotal including previous
              </option>
            </select>
            <Input
              aria-label="Adjustment kinds"
              value={adjustment.kinds}
              placeholder="labor, equipment_rental"
              onChange={(event) =>
                onChange(
                  adjustments.map((current, i) =>
                    i === index
                      ? { ...current, kinds: event.target.value }
                      : current,
                  ),
                )
              }
              className="h-8 w-48"
            />
            <button
              type="button"
              aria-label="Remove adjustment"
              onClick={() =>
                onChange(adjustments.filter((_, i) => i !== index))
              }
              className="text-text-tertiary hover:text-danger"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        data-testid="add-adjustment"
        onClick={() => onChange([...adjustments, emptyAdjustment()])}
        className="mt-2 inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-text-secondary"
      >
        <Plus className="h-3 w-3" /> Adjustment
      </button>
    </div>
  )
}
