'use client'

import * as React from 'react'
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Copy,
  Plus,
  Search,
  Trash2,
} from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAmount, NO_VALUE } from '@/lib/money'
import {
  isCalcGroupResult,
  type BudgetCalculated,
  type BudgetLine,
  type BudgetLineCreate,
  type BudgetLineUpdate,
  type BudgetSection,
  type BudgetSheet,
  type CalcChildResult,
  type CalcGroupResult,
  type CalcLineResult,
  type CalcWarning,
  type FactorExplanation,
  type ItemPage,
} from '@/types'

/**
 * The Budget sheet — a spreadsheet editor over `GET /budgets/{id}`.
 *
 * **It never computes money.** Every price, cost, margin and subtotal on this
 * screen is read out of `GET /budgets/{id}/calculated` (the engine's own
 * result, keyed by line and section id). An edit is a PATCH, and the numbers
 * change when the calculated view is fetched again — never because the
 * browser added something up. A section's subtotal is the engine's
 * `subtotal`, which already has the section's adjustments in it, and is
 * therefore NOT the sum of the lines above it; a browser sum would be wrong
 * the first time a section carried a discount.
 *
 * Inputs hold the API's strings. The only transformation is reading a German
 * decimal comma ("1.234,56") back into the API's form ("1234.56") — string
 * handling, not arithmetic.
 */

const PERIOD_UNITS = ['day', 'half_day', 'hour', 'night', 'week', 'month', 'flat', 'piece', 'km'] as const

/** Column order, also the order Enter/Tab walk. */
const COLUMNS = [
  'description',
  'quantity',
  'period',
  'unit',
  'factor',
  'unit_price',
  'unit_cost',
  'discount',
] as const
type Column = (typeof COLUMNS)[number]

/** `"3.0000"` → `"3"`, `"2.5000"` → `"2.5"`. Display of a stored string in an
 *  input; no number is constructed. */
export function trimZeros(value: string | null | undefined): string {
  if (!value) return ''
  if (!value.includes('.')) return value
  return value.replace(/0+$/, '').replace(/\.$/, '')
}

/** What somebody typed, in the API's decimal form. "1.234,56" and "1234,56"
 *  are German; "1234.56" is already right. */
export function toApiDecimal(typed: string): string {
  const text = typed.trim().replace(/\s/g, '')
  if (text.includes(',')) return text.replace(/\./g, '').replace(',', '.')
  return text
}

interface Results {
  lines: Map<string, CalcLineResult>
  groups: Map<string, CalcGroupResult>
}

/** Index the engine's result tree by id. A walk, not a calculation. */
function indexResults(calc: BudgetCalculated | null): Results {
  const lines = new Map<string, CalcLineResult>()
  const groups = new Map<string, CalcGroupResult>()
  const walk = (children: CalcChildResult[]) => {
    for (const child of children) {
      if (isCalcGroupResult(child)) {
        groups.set(child.id, child)
        walk(child.children)
      } else {
        lines.set(child.id, child)
      }
    }
  }
  if (calc) walk(calc.result.children)
  return { lines, groups }
}

function percentText(value: string | null | undefined): string {
  return value ? `${formatAmount(value)} %` : NO_VALUE
}

function factorTitle(explanation: FactorExplanation | undefined): string | undefined {
  if (!explanation) return undefined
  const from =
    explanation.matched_days != null
      ? `the ${explanation.matched_days}-day row`
      : 'no row'
  return (
    `Factor ${trimZeros(explanation.factor)} for ${explanation.days} days, from ${from}` +
    (explanation.group_name ? ` of “${explanation.group_name}”` : '') +
    ` (rule: ${explanation.rule.replace(/_/g, ' ')}).`
  )
}

export interface BudgetSheetProps {
  sheet: BudgetSheet
  calc: BudgetCalculated | null
  editable: boolean
  /** Re-fetch the sheet and the calculated view after a write. */
  onChange: () => Promise<void> | void
  onError: (message: string | null) => void
}

export function BudgetSheetEditor({ sheet, calc, editable, onChange, onError }: BudgetSheetProps) {
  const results = React.useMemo(() => indexResults(calc), [calc])
  const explanations = React.useMemo(() => {
    const map = new Map<string, FactorExplanation>()
    for (const row of calc?.factors ?? []) map.set(row.line_id, row)
    return map
  }, [calc])
  const [collapsed, setCollapsed] = React.useState<Set<string>>(new Set())
  const container = React.useRef<HTMLDivElement>(null)

  const sections = [...sheet.sections].sort((a, b) => a.position - b.position)
  const topLevel = sections.filter((section) => !section.parent_id)
  const childrenOf = (id: string) => sections.filter((section) => section.parent_id === id)
  const linesOf = (id: string) =>
    sheet.lines
      .filter((line) => line.section_id === id)
      .sort((a, b) => a.position - b.position)

  // The rows in the order they are drawn — what Enter moves down through.
  const order: string[] = []
  const visit = (section: BudgetSection) => {
    if (collapsed.has(section.id)) return
    for (const line of linesOf(section.id)) order.push(line.id)
    for (const child of childrenOf(section.id)) visit(child)
  }
  topLevel.forEach(visit)
  const rowIndex = new Map(order.map((id, index) => [id, index]))

  const write = async (call: () => Promise<unknown>, failure: string) => {
    onError(null)
    try {
      await call()
      await onChange()
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : failure)
    }
  }

  const patchLine = (line: BudgetLine, body: BudgetLineUpdate) =>
    write(() => api.patch(`/budgets/${sheet.budget.id}/lines/${line.id}`, body), 'The line could not be saved')

  /** Move a line one place up or down within its section: the two rows
   *  trade positions, two PATCHes, two `budget_line.moved` events. */
  const swapLines = (a: BudgetLine, b: BudgetLine) =>
    write(async () => {
      await api.patch(`/budgets/${sheet.budget.id}/lines/${a.id}`, { position: b.position })
      await api.patch(`/budgets/${sheet.budget.id}/lines/${b.id}`, { position: a.position })
    }, 'The line could not be moved')

  const swapSections = (a: BudgetSection, b: BudgetSection) =>
    write(async () => {
      await api.patch(`/budgets/${sheet.budget.id}/sections/${a.id}`, { position: b.position })
      await api.patch(`/budgets/${sheet.budget.id}/sections/${b.id}`, { position: a.position })
    }, 'The section could not be moved')

  const focusCell = (row: number, column: Column) => {
    const target = container.current?.querySelector<HTMLInputElement | HTMLSelectElement>(
      `[data-cell="${row}:${column}"]`,
    )
    target?.focus()
  }

  const toggle = (id: string) =>
    setCollapsed((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const warningsFor = (section: BudgetSection): CalcWarning[] =>
    (calc?.result.warnings ?? []).filter((warning) =>
      (warning.where ?? '').startsWith(`group ${section.name}`),
    )

  const renderSection = (section: BudgetSection, depth: number): React.ReactNode => {
    const group = results.groups.get(section.id)
    const siblings = depth === 0 ? topLevel : childrenOf(section.parent_id ?? '')
    const at = siblings.findIndex((row) => row.id === section.id)
    const lines = linesOf(section.id)
    const isCollapsed = collapsed.has(section.id)
    return (
      <React.Fragment key={section.id}>
        <tr data-testid="section-row" data-section={section.id} className="border-b border-border bg-bg-secondary">
          <td className="py-1.5 pr-2" colSpan={8}>
            <div className="flex items-center gap-1.5" style={{ paddingLeft: depth * 16 }}>
              <button
                type="button"
                aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${section.name}`}
                aria-expanded={!isCollapsed}
                onClick={() => toggle(section.id)}
                className="text-text-tertiary"
              >
                {isCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              </button>
              {section.code && <span className="font-mono text-xs text-text-tertiary">{section.code}</span>}
              {editable ? (
                <input
                  aria-label={`Section name ${section.name}`}
                  defaultValue={section.name}
                  key={`${section.id}-${section.name}`}
                  onBlur={(event) => {
                    const name = event.target.value.trim()
                    if (!name || name === section.name) return
                    void write(
                      () => api.patch(`/budgets/${sheet.budget.id}/sections/${section.id}`, { name }),
                      'The section could not be renamed',
                    )
                  }}
                  className="rounded border border-transparent bg-transparent px-1 text-sm font-medium text-text-primary hover:border-border focus:border-border"
                />
              ) : (
                <span className="text-sm font-medium text-text-primary">{section.name}</span>
              )}
              {editable && (
                <span className="ml-2 flex items-center gap-1 text-text-tertiary">
                  <button
                    type="button"
                    aria-label={`Move ${section.name} up`}
                    disabled={at <= 0}
                    onClick={() => void swapSections(section, siblings[at - 1])}
                    className="disabled:opacity-30"
                  >
                    <ArrowUp className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${section.name} down`}
                    disabled={at >= siblings.length - 1}
                    onClick={() => void swapSections(section, siblings[at + 1])}
                    className="disabled:opacity-30"
                  >
                    <ArrowDown className="h-3 w-3" />
                  </button>
                  {depth === 0 && (
                    <button
                      type="button"
                      onClick={() =>
                        void write(
                          () =>
                            api.post(`/budgets/${sheet.budget.id}/sections`, {
                              name: 'New sub-section',
                              parent_id: section.id,
                            }),
                          'The sub-section could not be added',
                        )
                      }
                      className="text-xs hover:text-text-primary"
                    >
                      + Sub-section
                    </button>
                  )}
                  <button
                    type="button"
                    aria-label={`Remove section ${section.name}`}
                    onClick={() =>
                      void write(
                        () => api.post(`/budgets/${sheet.budget.id}/sections/${section.id}/remove`),
                        'The section could not be removed',
                      )
                    }
                    className="hover:text-danger"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </span>
              )}
            </div>
          </td>
          {/* The engine's own subtotal for this section — after its
              adjustments, which is why it is not the sum of the rows. */}
          <td data-testid="section-subtotal" className="py-1.5 pr-2 text-right font-medium tabular-nums">
            {formatAmount(group?.subtotal)}
          </td>
          <td data-testid="section-cost" className="py-1.5 pr-2 text-right tabular-nums text-text-secondary">
            {formatAmount(group?.cost_subtotal)}
          </td>
          <td className="py-1.5 pr-2 text-right tabular-nums text-text-secondary">
            {percentText(group?.margin_percent)}
          </td>
          <td />
        </tr>
        {warningsFor(section).map((warning, index) => (
          <tr key={`w-${section.id}-${index}`}>
            <td colSpan={12} data-testid="sheet-warning" className="px-2 py-1 text-xs text-status-warning">
              ⚠ {warning.message}
            </td>
          </tr>
        ))}
        {!isCollapsed &&
          lines.map((line, index) => (
            <LineRow
              key={line.id}
              line={line}
              row={rowIndex.get(line.id) ?? 0}
              depth={depth}
              result={results.lines.get(line.id)}
              explanation={explanations.get(line.id)}
              editable={editable}
              sections={sections}
              onPatch={(body) => void patchLine(line, body)}
              onMoveUp={index > 0 ? () => void swapLines(line, lines[index - 1]) : undefined}
              onMoveDown={index < lines.length - 1 ? () => void swapLines(line, lines[index + 1]) : undefined}
              onDuplicate={() =>
                void write(
                  () => api.post(`/budgets/${sheet.budget.id}/lines/${line.id}/duplicate`),
                  'The line could not be duplicated',
                )
              }
              onRemove={() =>
                void write(
                  () => api.post(`/budgets/${sheet.budget.id}/lines/${line.id}/remove`),
                  'The line could not be removed',
                )
              }
              focusCell={focusCell}
            />
          ))}
        {!isCollapsed && childrenOf(section.id).map((child) => renderSection(child, depth + 1))}
        {!isCollapsed && editable && (
          <tr>
            <td colSpan={12} className="py-1" style={{ paddingLeft: depth * 16 + 24 }}>
              <AddLine
                budgetId={sheet.budget.id}
                sectionId={section.id}
                onAdded={onChange}
                onError={onError}
              />
            </td>
          </tr>
        )}
      </React.Fragment>
    )
  }

  return (
    <div ref={container} className="mt-4 overflow-x-auto">
      <table data-testid="budget-sheet" className="w-full min-w-[64rem] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-tertiary">
            <th className="py-2 pr-2 font-medium">Description</th>
            <th className="py-2 pr-2 text-right font-medium">Qty</th>
            <th className="py-2 pr-2 text-right font-medium">Period</th>
            <th className="py-2 pr-2 font-medium">Unit</th>
            <th className="py-2 pr-2 text-right font-medium">Factor</th>
            <th className="py-2 pr-2 text-right font-medium">Unit price</th>
            <th className="py-2 pr-2 text-right font-medium">Unit cost</th>
            <th className="py-2 pr-2 text-right font-medium">Discount</th>
            <th className="py-2 pr-2 text-right font-medium">Price</th>
            <th className="py-2 pr-2 text-right font-medium">Cost</th>
            <th className="py-2 pr-2 text-right font-medium">Margin %</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {topLevel.length === 0 && (
            <tr>
              <td colSpan={12} className="py-4 text-sm text-text-secondary">
                No sections yet.{editable && ' Add one below.'}
              </td>
            </tr>
          )}
          {topLevel.map((section) => renderSection(section, 0))}
        </tbody>
      </table>
      {editable && (
        <button
          type="button"
          onClick={() =>
            void write(
              () => api.post(`/budgets/${sheet.budget.id}/sections`, { name: 'New section' }),
              'The section could not be added',
            )
          }
          className="mt-3 inline-flex items-center gap-1 text-sm text-text-secondary hover:text-text-primary"
        >
          <Plus className="h-3.5 w-3.5" /> Section
        </button>
      )}
    </div>
  )
}

function LineRow({
  line,
  row,
  depth,
  result,
  explanation,
  editable,
  sections,
  onPatch,
  onMoveUp,
  onMoveDown,
  onDuplicate,
  onRemove,
  focusCell,
}: {
  line: BudgetLine
  row: number
  depth: number
  result: CalcLineResult | undefined
  explanation: FactorExplanation | undefined
  editable: boolean
  sections: BudgetSection[]
  onPatch: (body: BudgetLineUpdate) => void
  onMoveUp?: () => void
  onMoveDown?: () => void
  onDuplicate: () => void
  onRemove: () => void
  focusCell: (row: number, column: Column) => void
}) {
  const difference = line.catalog_difference
  const differsTitle = difference
    ? `Differs from the catalog item “${difference.item_name}”` +
      (difference.catalog_unit_price ? ` — catalog price ${difference.catalog_unit_price}` : '') +
      (difference.catalog_unit_cost ? `, catalog cost ${difference.catalog_unit_cost}` : '') +
      '. The budget keeps its own rate; the catalog is never written back.'
    : undefined

  const quantityShown = line.quantity_ref ? `=${line.quantity_ref}` : trimZeros(line.quantity)
  const periodShown = line.period_ref ? `=${line.period_ref}` : trimZeros(line.period_value)
  const discountShown = !line.discount_mode
    ? ''
    : line.discount_mode === 'percent'
      ? `${trimZeros(line.discount_value)}%`
      : (line.discount_value ?? '')

  /** One field typed → one PATCH body. */
  const bodyFor = (column: Column, typed: string): BudgetLineUpdate | null => {
    const text = typed.trim()
    switch (column) {
      case 'description':
        return text ? { description: text } : null
      case 'quantity':
        if (text.startsWith('=')) return { quantity_ref: text.slice(1).trim() }
        return line.quantity_ref
          ? { quantity: toApiDecimal(text), clear: ['quantity_ref'] }
          : { quantity: toApiDecimal(text) }
      case 'period':
        if (text.startsWith('=')) return { period_ref: text.slice(1).trim() }
        return line.period_ref
          ? { period_value: toApiDecimal(text), clear: ['period_ref'] }
          : { period_value: toApiDecimal(text) }
      case 'factor':
        if (text.toLowerCase() === 'catalog') return { factor_source: 'catalog' }
        return { factor: toApiDecimal(text), factor_source: 'manual' }
      case 'unit_price':
        return { unit_price: toApiDecimal(text) }
      case 'unit_cost':
        return { unit_cost: toApiDecimal(text) }
      case 'discount':
        if (!text) return { clear: ['discount'] }
        if (text.endsWith('%'))
          return { discount_mode: 'percent', discount_value: toApiDecimal(text.slice(0, -1)) }
        return { discount_mode: 'amount', discount_value: toApiDecimal(text) }
      default:
        return null
    }
  }

  const cell = (column: Column, shown: string, label: string, align: 'left' | 'right' = 'right', title?: string) => {
    if (!editable) {
      return (
        <span title={title} className={align === 'right' ? 'block text-right' : undefined}>
          {shown || NO_VALUE}
        </span>
      )
    }
    return (
      <input
        aria-label={`${label} — ${line.description}`}
        data-cell={`${row}:${column}`}
        defaultValue={shown}
        key={`${column}-${shown}`}
        title={title}
        onBlur={(event) => {
          if (event.target.value === shown) return
          const body = bodyFor(column, event.target.value)
          if (body) onPatch(body)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            ;(event.target as HTMLInputElement).blur()
            focusCell(row + 1, column)
          } else if (event.key === 'Escape') {
            ;(event.target as HTMLInputElement).value = shown
            ;(event.target as HTMLInputElement).blur()
          }
        }}
        className={`w-full rounded border border-transparent bg-transparent px-1 py-0.5 hover:border-border focus:border-border ${
          align === 'right' ? 'text-right tabular-nums' : ''
        }`}
      />
    )
  }

  return (
    <tr data-testid="line-row" data-line={line.id} className="border-b border-border/60">
      <td className="py-0.5 pr-2" style={{ paddingLeft: depth * 16 + 20 }}>
        <div className="flex items-center gap-1">
          {cell('description', line.description, 'Description', 'left')}
          {difference && (
            <span
              data-testid="differs-from-catalog"
              title={differsTitle}
              className="shrink-0 rounded border border-status-warning px-1 text-[10px] text-status-warning"
            >
              ≠ catalog
            </span>
          )}
        </div>
      </td>
      <td className="w-20 py-0.5 pr-2">{cell('quantity', quantityShown, 'Quantity')}</td>
      <td className="w-20 py-0.5 pr-2">{cell('period', periodShown, 'Period')}</td>
      <td className="w-24 py-0.5 pr-2">
        {editable ? (
          <select
            aria-label={`Unit — ${line.description}`}
            data-cell={`${row}:unit`}
            value={line.period_unit}
            onChange={(event) =>
              onPatch({ period_unit: event.target.value as BudgetLineUpdate['period_unit'] })
            }
            className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-sm hover:border-border"
          >
            {PERIOD_UNITS.map((unit) => (
              <option key={unit} value={unit}>
                {unit.replace('_', ' ')}
              </option>
            ))}
          </select>
        ) : (
          line.period_unit.replace('_', ' ')
        )}
      </td>
      <td className="w-20 py-0.5 pr-2" data-testid="line-factor">
        {line.factor_source === 'catalog' ? (
          <span
            data-testid="factor-explained"
            title={factorTitle(explanation) ?? 'Factor from the catalog'}
            className="block cursor-help text-right tabular-nums underline decoration-dotted"
          >
            {explanation ? trimZeros(explanation.factor) : trimZeros(line.factor)}
          </span>
        ) : (
          cell('factor', trimZeros(line.factor), 'Factor')
        )}
      </td>
      <td className="w-28 py-0.5 pr-2">{cell('unit_price', line.unit_price, 'Unit price')}</td>
      <td className="w-28 py-0.5 pr-2">{cell('unit_cost', line.unit_cost, 'Unit cost')}</td>
      <td className="w-20 py-0.5 pr-2">{cell('discount', discountShown, 'Discount')}</td>
      <td data-testid="line-price" className="w-28 py-0.5 pr-2 text-right tabular-nums">
        {formatAmount(result?.net)}
      </td>
      <td data-testid="line-cost" className="w-28 py-0.5 pr-2 text-right tabular-nums text-text-secondary">
        {formatAmount(result?.cost_net)}
      </td>
      <td className="w-20 py-0.5 pr-2 text-right tabular-nums text-text-secondary">
        {percentText(result?.margin_percent)}
      </td>
      <td className="w-36 py-0.5 text-right">
        {editable && (
          <span className="inline-flex items-center gap-1 text-text-tertiary">
            <button type="button" aria-label={`Move ${line.description} up`} disabled={!onMoveUp} onClick={onMoveUp} className="disabled:opacity-30">
              <ArrowUp className="h-3 w-3" />
            </button>
            <button type="button" aria-label={`Move ${line.description} down`} disabled={!onMoveDown} onClick={onMoveDown} className="disabled:opacity-30">
              <ArrowDown className="h-3 w-3" />
            </button>
            <select
              aria-label={`Move ${line.description} to section`}
              value=""
              onChange={(event) => event.target.value && onPatch({ section_id: event.target.value })}
              className="w-6 bg-transparent text-xs"
            >
              <option value="">↦</option>
              {sections
                .filter((section) => section.id !== line.section_id)
                .map((section) => (
                  <option key={section.id} value={section.id}>
                    {section.code ? `${section.code} ` : ''}
                    {section.name}
                  </option>
                ))}
            </select>
            <button type="button" aria-label={`Duplicate ${line.description}`} onClick={onDuplicate}>
              <Copy className="h-3 w-3" />
            </button>
            <button type="button" aria-label={`Remove ${line.description}`} onClick={onRemove} className="hover:text-danger">
              <Trash2 className="h-3 w-3" />
            </button>
          </span>
        )}
      </td>
    </tr>
  )
}

/** "+ Line" (free text) and "+ From catalog" (search, pick — the API copies
 *  the item's rates into the line at that moment). */
function AddLine({
  budgetId,
  sectionId,
  onAdded,
  onError,
}: {
  budgetId: string
  sectionId: string
  onAdded: () => Promise<void> | void
  onError: (message: string | null) => void
}) {
  const [searching, setSearching] = React.useState(false)
  const [query, setQuery] = React.useState('')
  const [items, setItems] = React.useState<ItemPage['items']>([])

  React.useEffect(() => {
    if (!searching || !query.trim()) {
      setItems([])
      return
    }
    const handle = setTimeout(() => {
      void (async () => {
        try {
          const page = await api.get<ItemPage>(
            `/catalog/items?search=${encodeURIComponent(query.trim())}&limit=10`,
          )
          setItems(page?.items ?? [])
        } catch {
          setItems([])
        }
      })()
    }, 250)
    return () => clearTimeout(handle)
  }, [searching, query])

  const add = async (body: Partial<BudgetLineCreate>) => {
    onError(null)
    const payload: BudgetLineCreate = {
      section_id: sectionId,
      quantity: '1',
      period_value: '1',
      factor: '1',
      allow_negative: false,
      ...body,
    }
    try {
      await api.post(`/budgets/${budgetId}/lines`, payload)
      setSearching(false)
      setQuery('')
      await onAdded()
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : 'The line could not be added')
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-text-secondary">
      <button
        type="button"
        onClick={() => void add({ description: 'New line' })}
        className="inline-flex items-center gap-1 hover:text-text-primary"
      >
        <Plus className="h-3 w-3" /> Line
      </button>
      <button
        type="button"
        onClick={() => setSearching(!searching)}
        className="inline-flex items-center gap-1 hover:text-text-primary"
      >
        <Search className="h-3 w-3" /> From catalog
      </button>
      {searching && (
        <span className="relative">
          <input
            autoFocus
            aria-label="Search the catalog"
            value={query}
            placeholder="SKU or name"
            onChange={(event) => setQuery(event.target.value)}
            className="w-56 rounded border border-border bg-bg-primary px-2 py-0.5 text-sm"
          />
          {items.length > 0 && (
            <ul className="absolute z-10 mt-1 w-72 rounded-md border border-border bg-bg-primary shadow">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => void add({ item_id: item.id })}
                    className="block w-full px-2 py-1 text-left text-sm hover:bg-bg-secondary"
                  >
                    <span className="font-mono text-xs text-text-tertiary">{item.sku}</span>{' '}
                    {item.name_de}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </span>
      )}
    </div>
  )
}

