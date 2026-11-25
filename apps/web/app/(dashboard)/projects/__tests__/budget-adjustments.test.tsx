import { describe, it, expect, beforeEach, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn(), upload: vi.fn(), download: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
      this.detail = detail
    }
  },
}))

import { api } from '@/lib/api'
import { AdjustmentsPanel, sectionOptions } from '../[id]/budgets/[budgetId]/budget-panels'
import type { BudgetAdjustment, BudgetCalculated, BudgetSheet } from '@/types'
import { makeCalculated, makeSheet } from './fixtures'

/**
 * B1.2 part 1 — adjustments by kind and by section, on the screen.
 *
 * The panel computes no money: what it sends is a `base` and a list of
 * `kinds`, and the amount it shows beside a row is the engine's, read from
 * the calculated view. These tests pin what is SENT (the request body is
 * the contract the API tests price) and what is SAID (the scope in words,
 * the warning when a kind filter matches nothing).
 */

function sheetWithSubSection(): BudgetSheet {
  const sheet = makeSheet()
  sheet.sections.push(
    { id: 's-bts', budget_id: 'b1', code: null, name: 'BTS shoot', parent_id: 's-eq', position: 10, is_active: true, is_extras: false },
    { id: 's-x', budget_id: 'b1', code: null, name: 'Extras', parent_id: null, position: 9999, is_active: true, is_extras: true },
  )
  return sheet
}

function adjustment(overrides: Partial<BudgetAdjustment> & { id: string; label: string }): BudgetAdjustment {
  return {
    budget_id: 'b1',
    section_id: null,
    position: 100,
    type: 'discount',
    mode: 'percent',
    value: '10.0000',
    base: 'subtotal',
    kinds: [],
    is_active: true,
    ...overrides,
  } as BudgetAdjustment
}

function renderPanel(sheet: BudgetSheet = sheetWithSubSection(), calc: BudgetCalculated | null = makeCalculated()) {
  const onChange = vi.fn()
  const onError = vi.fn()
  render(<AdjustmentsPanel sheet={sheet} calc={calc} editable onChange={onChange} onError={onError} />)
  return { onChange, onError }
}

beforeEach(() => {
  vi.mocked(api.post).mockReset()
  vi.mocked(api.post).mockResolvedValue({} as never)
})

describe('Applies to', () => {
  it('lists the sections as a hierarchy, sub-sections under their parent, and no Extras', () => {
    const options = sectionOptions(sheetWithSubSection())
    expect(options.map((option) => [option.name, option.depth])).toEqual([
      ['Crew', 0],
      ['Equipment', 0],
      ['BTS shoot', 1],
    ])
    renderPanel()
    const select = screen.getByLabelText('New adjustment applies to') as HTMLSelectElement
    const labels = Array.from(select.options).map((option) => option.textContent)
    expect(labels[0]).toBe('Whole budget')
    expect(labels).not.toContain('Extras')
    expect(labels.find((label) => label?.includes('BTS shoot'))).toMatch(/└ BTS shoot$/)
  })
})

describe('the request body', () => {
  it('sends base "subtotal" and no kinds when it applies to everything', async () => {
    renderPanel()
    fireEvent.change(screen.getByLabelText('New adjustment label'), { target: { value: 'Loyalty' } })
    fireEvent.change(screen.getByLabelText('New adjustment value'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0]).toEqual([
      '/budgets/b1/adjustments',
      { label: 'Loyalty', type: 'discount', mode: 'percent', value: '5', base: 'subtotal', kinds: [], section_id: null },
    ])
  })

  it('sends base "kinds" with the chosen kinds, on one sub-section', async () => {
    renderPanel()
    fireEvent.change(screen.getByLabelText('New adjustment label'), { target: { value: 'BTS kit' } })
    fireEvent.change(screen.getByLabelText('New adjustment value'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('New adjustment applies to'), { target: { value: 's-bts' } })
    fireEvent.click(screen.getByLabelText('only certain kinds'))
    const kinds = screen.getByTestId('adjustment-kinds')
    fireEvent.click(within(kinds).getByLabelText('Equipment rental'))
    fireEvent.click(within(kinds).getByLabelText('Space rental'))
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0][1]).toEqual({
      label: 'BTS kit',
      type: 'discount',
      mode: 'percent',
      value: '10',
      base: 'kinds',
      kinds: ['equipment_rental', 'space_rental'],
      section_id: 's-bts',
    })
  })

  it('says why Add is disabled when "only certain kinds" has none ticked', () => {
    renderPanel()
    fireEvent.change(screen.getByLabelText('New adjustment label'), { target: { value: 'X' } })
    fireEvent.change(screen.getByLabelText('New adjustment value'), { target: { value: '1' } })
    fireEvent.click(screen.getByLabelText('only certain kinds'))
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
    expect(screen.getByText('At least one kind, or “everything”.')).toBeInTheDocument()
  })
})

describe('the quick-adds', () => {
  it.each([
    ['Equipment discount', 'equipment_rental'],
    ['Crew discount', 'labor'],
  ])('%s prefills a percent discount on the whole budget for %s; the user types the value', async (name, kind) => {
    renderPanel()
    // A section chosen earlier must not survive the quick-add.
    fireEvent.change(screen.getByLabelText('New adjustment applies to'), { target: { value: 's-crew' } })
    fireEvent.click(screen.getByRole('button', { name }))
    expect(screen.getByLabelText('New adjustment label')).toHaveValue(name)
    expect(screen.getByLabelText('New adjustment value')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('New adjustment value'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0][1]).toEqual({
      label: name,
      type: 'discount',
      mode: 'percent',
      value: '10',
      base: 'kinds',
      kinds: [kind],
      section_id: null,
    })
  })
})

describe('each adjustment says its scope in words', () => {
  it('kind and whole budget, all kinds and a section', () => {
    const sheet = sheetWithSubSection()
    sheet.adjustments = [
      adjustment({ id: 'a1', label: 'Equipment discount', value: '5.0000', base: 'kinds', kinds: ['equipment_rental'] }),
      adjustment({ id: 'a2', label: 'BTS discount', section_id: 's-bts' }),
      adjustment({ id: 'a3', label: 'Rush', type: 'surcharge', value: '8.0000' }),
    ]
    renderPanel(sheet)
    expect(screen.getAllByTestId('adjustment-scope').map((node) => node.textContent)).toEqual([
      '5 % off · equipment · whole budget',
      '10 % off · all kinds · BTS shoot',
      '8 % on top · all kinds · whole budget',
    ])
  })

  it('shows the amount the engine applied, for a section adjustment too', () => {
    renderPanel(makeSheet(), makeCalculated())
    const row = screen.getByTestId('adjustment-row')
    const group = makeCalculated().result.children.find((child) => child.type === 'group' && child.id === 's-crew')
    // The fixture's Crew group carries no applied adjustment, so nothing is shown.
    expect(group && group.type === 'group' ? group.adjustments : []).toEqual([])
    expect(within(row).queryByTestId('adjustment-amount')).toBeNull()

    const calc = makeCalculated()
    const crew = calc.result.children.find((child) => child.type === 'group' && child.id === 's-crew')
    if (crew && crew.type === 'group') {
      crew.adjustments = [
        { label: 'Crew discount', type: 'discount', mode: 'percent', base: 'subtotal', kinds: [], base_amount: '4200.00', amount: '-210.00', by_tax_ref: { 'AT-20': '-210.00' } },
      ]
    }
    cleanup()
    renderPanel(makeSheet(), calc)
    expect(screen.getByTestId('adjustment-amount').textContent).toContain('210.00')
  })

  it('warns, on the row, when a kind filter matches nothing', () => {
    const sheet = sheetWithSubSection()
    sheet.adjustments = [adjustment({ id: 'a1', label: 'Storage discount', base: 'kinds', kinds: ['storage'] })]
    const calc = makeCalculated()
    calc.result.warnings = [
      { code: 'kind_filter_matched_nothing', message: 'matched nothing', where: 'the document: Storage discount' },
    ]
    renderPanel(sheet, calc)
    expect(screen.getByTestId('adjustment-warning').textContent).toBe(
      '⚠ No line of storage here, so this takes nothing off.',
    )
  })

  it('tells the user adjustments move price and not cost', () => {
    renderPanel()
    expect(screen.getByTestId('adjustments-panel').textContent).toContain('move the price, never the cost')
  })
})
