import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

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
vi.mock('next/navigation', () => ({
  usePathname: () => '/projects/p1/budgets/b1',
  useParams: () => ({ id: 'p1', budgetId: 'b1' }),
  useRouter: () => ({ push: vi.fn() }),
}))

import { api } from '@/lib/api'
import { formatAmount } from '@/lib/money'
import BudgetPage from '../[id]/budgets/[budgetId]/page'
import type { BudgetCalculated, BudgetLine, BudgetSheet } from '@/types'
import { ALL_PERMISSIONS, makeCalculated, makeLedger, makeSheet, makeTracking, seedCompany } from './fixtures'

/**
 * B1.1 part 4 — extras on the screen. Two extras in the reserved section:
 * "Drone day" accepted (500.00 / 300.00) and "Night shoot" proposed
 * (900.00 / 700.00). The totals are the API's: baseline 7 350.00, with
 * extras 7 890.00 — deliberately NOT 7 350.00 + 500.00, because the API's
 * document charges the budget's fee on the extra too, and a screen that
 * added the extra to the quote would print 7 850.00.
 */

function extraLine(overrides: Partial<BudgetLine> & { id: string; description: string }): BudgetLine {
  const base = makeSheet().lines[0]
  return {
    ...base,
    section_id: 's-x',
    item_id: null,
    quantity_ref: null,
    period_ref: null,
    period_unit: 'flat',
    catalog_difference: null,
    extra_decided_on: null,
    extra_note: null,
    ...overrides,
  } as BudgetLine
}

function sheetWithExtras({ closed = false }: { closed?: boolean } = {}): BudgetSheet {
  const sheet = makeSheet({ locked: true, closed })
  sheet.sections.push({ id: 's-x', budget_id: 'b1', code: null, name: 'Extras', parent_id: null, position: 1030, is_active: true, is_extras: true })
  sheet.lines.push(
    extraLine({ id: 'l-x1', description: 'Drone day', unit_price: '500.00', unit_cost: '300.00', extra_status: 'accepted', extra_decided_on: '2026-11-25', extra_note: 'Agreed on the phone' }),
    extraLine({ id: 'l-x2', description: 'Night shoot', unit_price: '900.00', unit_cost: '700.00', extra_status: 'proposed' }),
  )
  return sheet
}

function calcWithExtras(): BudgetCalculated {
  const calc = makeCalculated()
  calc.top_sheet.push({ section_id: 's-x', code: null, name: 'Extras', price: '500.00', cost: '300.00', margin: '200.00', margin_percent: '40.00', share_of_subtotal: '6.37' })
  calc.totals = {
    baseline: { net: '7350.00', cost: '3060.00', margin: '4290.00', margin_percent: '58.37', net_by_tax_ref: { 'AT-20': '7350.00' } },
    with_extras: { net: '7890.00', cost: '3360.00', margin: '4530.00', margin_percent: '57.41', net_by_tax_ref: { 'AT-20': '7890.00' } },
  }
  calc.extras = [
    { line_id: 'l-x1', description: 'Drone day', status: 'accepted', decided_on: '2026-11-25', note: 'Agreed on the phone', price: '500.00', cost: '300.00', margin: '200.00', margin_percent: '40.00' },
    { line_id: 'l-x2', description: 'Night shoot', status: 'proposed', decided_on: null, note: null, price: '900.00', cost: '700.00', margin: '200.00', margin_percent: '22.22' },
  ]
  return calc
}

function mockApi(sheet: BudgetSheet = sheetWithExtras()) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/budgets/b1') return sheet as never
    if (path === '/budgets/b1/calculated') return calcWithExtras() as never
    if (path === '/budgets/b1/tracking') return makeTracking() as never
    if (path.startsWith('/budgets/b1/ledger')) return makeLedger() as never
    if (path === '/budgets/b1/close-check')
      return {
        budget_id: 'b1', state: 'over_estimate', estimate_cost: '1.00', efc: '2.00', variance: '-1.00',
        lines_over: [], lines_under_not_finished: [], finished_with_open_commitment: [], open_commitments: [],
        open_commitments_total: '0.00', unallocated: [], unallocated_total: '0.00', digest: 'd',
      } as never
    return {} as never
  })
}

async function openExtras() {
  render(<BudgetPage />)
  await screen.findByTestId('budget-sheet')
  fireEvent.click(screen.getByRole('tab', { name: 'Extras' }))
  return screen.findByTestId('extras-tab')
}

function row(id: string): HTMLElement {
  const found = screen.getAllByTestId('extra-row').find((element) => element.getAttribute('data-line') === id)
  if (!found) throw new Error(`no extra row ${id}`)
  return found
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany(ALL_PERMISSIONS)
  mockApi()
  vi.mocked(api.post).mockResolvedValue({} as never)
  vi.mocked(api.patch).mockResolvedValue({} as never)
})

describe('the Extras tab', () => {
  it('lists every extra with its status and its own price, from the API', async () => {
    await openExtras()
    expect(within(row('l-x1')).getByTestId('extra-status').textContent).toBe('accepted')
    expect(within(row('l-x2')).getByTestId('extra-status').textContent).toBe('proposed')
    expect(within(row('l-x2')).getByTestId('extra-price').textContent).toBe(formatAmount('900.00'))
    expect(row('l-x1').textContent).toContain('Agreed on the phone')
  })

  it('proposing is one POST; the button says what is missing until it can go', async () => {
    const tab = await openExtras()
    fireEvent.click(within(tab).getByTestId('extra-add'))
    const form = within(tab).getByTestId('extra-form')
    expect(within(form).getByTestId('extra-save')).toBeDisabled()
    expect(within(form).getByTestId('extra-missing').textContent).toBe('Needs a description and a price.')
    fireEvent.change(within(form).getByLabelText('What it is'), { target: { value: 'Teleprompter' } })
    fireEvent.change(within(form).getByLabelText('Price (net)'), { target: { value: '1.250,00' } })
    fireEvent.change(within(form).getByLabelText('Cost (net)'), { target: { value: '800' } })
    fireEvent.change(within(form).getByLabelText('What the customer was told'), { target: { value: 'Offered by mail' } })
    fireEvent.click(within(form).getByTestId('extra-save'))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.post).mock.calls[0]).toEqual([
      '/budgets/b1/extras',
      { description: 'Teleprompter', unit_price: '1250.00', unit_cost: '800', note: 'Offered by mail', apply_production_fee: true },
    ])
  })

  it('“Add production fee” is ticked by default and an untick is sent (B1.2)', async () => {
    const tab = await openExtras()
    fireEvent.click(within(tab).getByTestId('extra-add'))
    const form = within(tab).getByTestId('extra-form')
    const box = within(form).getByLabelText('Add production fee') as HTMLInputElement
    expect(box.checked).toBe(true)
    fireEvent.click(box)
    fireEvent.change(within(form).getByLabelText('What it is'), { target: { value: 'Drone' } })
    fireEvent.change(within(form).getByLabelText('Price (net)'), { target: { value: '500' } })
    fireEvent.click(within(form).getByTestId('extra-save'))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    expect((vi.mocked(api.post).mock.calls[0][1] as Record<string, unknown>).apply_production_fee).toBe(false)
  })

  it('an extra without the fee says so, and its edit form starts unticked (B1.2)', async () => {
    const sheet = sheetWithExtras()
    const drone = sheet.lines.find((line) => line.id === 'l-x1')!
    drone.apply_production_fee = false
    mockApi(sheet)
    const tab = await openExtras()
    expect(within(row('l-x1')).getByTestId('extra-no-fee').textContent).toBe('no production fee')
    expect(within(row('l-x2')).queryByTestId('extra-no-fee')).toBeNull()
    fireEvent.click(within(row('l-x1')).getByTestId('extra-edit'))
    const form = within(tab).getByTestId('extra-form')
    const box = within(form).getByLabelText('Add production fee') as HTMLInputElement
    expect(box.checked).toBe(false)
    fireEvent.click(box)
    fireEvent.click(within(form).getByTestId('extra-save'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1))
    expect((vi.mocked(api.patch).mock.calls[0][1] as Record<string, unknown>).apply_production_fee).toBe(true)
  })

  it('accept is offered on a proposed extra only, and is one POST', async () => {
    await openExtras()
    expect(within(row('l-x1')).queryByTestId('extra-accept')).toBeNull()
    fireEvent.click(within(row('l-x2')).getByTestId('extra-accept'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/budgets/b1/extras/l-x2/accept', {}))
  })

  it('declining needs a note, and sends it', async () => {
    const tab = await openExtras()
    fireEvent.click(within(row('l-x2')).getByTestId('extra-decline'))
    const form = within(tab).getByTestId('decline-form')
    expect(within(form).getByTestId('decline-submit')).toBeDisabled()
    expect(form.textContent).toContain('Declining needs a note.')
    fireEvent.change(within(form).getByTestId('decline-note'), { target: { value: 'Client said no' } })
    fireEvent.click(within(form).getByTestId('decline-submit'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/budgets/b1/extras/l-x2/decline', { note: 'Client said no' }),
    )
  })

  it('editing an accepted extra says it is recorded, and PATCHes', async () => {
    const tab = await openExtras()
    fireEvent.click(within(row('l-x1')).getByTestId('extra-edit'))
    const form = within(tab).getByTestId('extra-form')
    expect(within(form).getByTestId('extra-save').textContent).toContain('changed after acceptance')
    fireEvent.change(within(form).getByLabelText('Price (net)'), { target: { value: '450,00' } })
    fireEvent.click(within(form).getByTestId('extra-save'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1))
    const [path, body] = vi.mocked(api.patch).mock.calls[0] as [string, Record<string, unknown>]
    expect(path).toBe('/budgets/b1/extras/l-x1')
    expect(body.unit_price).toBe('450.00')
  })

  it('without budgets.edit there are no buttons and a sentence says why', async () => {
    seedCompany(['projects.view', 'budgets.view'])
    const tab = await openExtras()
    expect(within(tab).getByTestId('extras-read-only')).toBeInTheDocument()
    expect(within(tab).queryByTestId('extra-add')).toBeNull()
    expect(within(tab).queryByTestId('extra-accept')).toBeNull()
    expect(within(tab).queryByTestId('extra-decline')).toBeNull()
  })

  it('on a closed budget there are no buttons and it says reopen first', async () => {
    mockApi(sheetWithExtras({ closed: true }))
    const tab = await openExtras()
    expect(within(tab).getByTestId('extras-closed').textContent).toContain('Reopen it first')
    expect(within(tab).queryByTestId('extra-add')).toBeNull()
  })
})

describe('extras elsewhere on the budget', () => {
  it('the top sheet shows the quote, the extras accepted and the total with extras — all the API’s', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Top sheet' }))
    const totals = await screen.findByTestId('extras-totals')
    expect(within(totals).getByTestId('total-baseline').textContent).toBe(formatAmount('7350.00'))
    expect(within(totals).getByTestId('total-extras-accepted').textContent).toBe(formatAmount('500.00'))
    expect(within(totals).getByTestId('total-with-extras').textContent).toBe(formatAmount('7890.00'))
    expect(within(totals).getByTestId('total-with-extras').textContent).not.toBe(formatAmount('7850.00'))
  })

  it('the Planned sheet never shows the Extras section; the cost phases show accepted extras only', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    expect(screen.getAllByTestId('section-row').map((element) => element.getAttribute('data-section'))).not.toContain('s-x')
    expect(screen.getAllByTestId('line-row').map((element) => element.getAttribute('data-line'))).not.toContain('l-x1')

    fireEvent.click(screen.getByTestId('phase-actual'))
    await screen.findByTestId('phase-sheet')
    const lines = screen.getAllByTestId('phase-line-row').map((element) => element.getAttribute('data-line'))
    expect(lines).toContain('l-x1')
    expect(lines).not.toContain('l-x2')
  })

  it('the close dialog’s “add an extra charge” opens the Extras tab', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('budget-close'))
    fireEvent.click(await screen.findByTestId('close-add-extra'))
    expect(await screen.findByTestId('extras-tab')).toBeInTheDocument()
    expect(screen.queryByTestId('close-dialog')).toBeNull()
  })
})
