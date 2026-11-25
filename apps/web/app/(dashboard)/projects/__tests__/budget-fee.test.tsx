import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

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
import { ProductionFeeControl } from '../[id]/budgets/[budgetId]/budget-fee'
import { AdjustmentsPanel } from '../[id]/budgets/[budgetId]/budget-panels'
import type { BudgetSheet, ProductionFee } from '@/types'
import { makeCalculated, makeSheet } from './fixtures'

/**
 * B1.2 part 2 — the Production fee control at the top of the sheet.
 *
 * It sends what the user chose and shows what the server says: the fee, the
 * preset, and whether they differ. The engine's applied amount is read from
 * the calculated view.
 */

function sheetWithFee(fee: Partial<ProductionFee> = {}): BudgetSheet {
  const sheet = makeSheet()
  sheet.production_fee = {
    enabled: true,
    percent: '12.0000',
    kinds: [],
    adjustment_id: 'a-fee',
    preset_percent: '8.0000',
    preset_kinds: [],
    differs_from_preset: true,
    ...fee,
  }
  sheet.adjustments.push({
    id: 'a-fee',
    budget_id: 'b1',
    section_id: null,
    position: 20,
    label: 'Production fee',
    type: 'surcharge',
    mode: 'percent',
    value: '12.0000',
    base: 'subtotal',
    kinds: [],
    role: 'production_fee',
    is_active: true,
  })
  return sheet
}

function renderControl(sheet = sheetWithFee(), editable = true) {
  const calc = makeCalculated()
  calc.result.adjustments = [
    { label: 'Production fee', type: 'surcharge', mode: 'percent', base: 'subtotal', kinds: [], base_amount: '7350.00', amount: '882.00', by_tax_ref: { 'AT-20': '882.00' } },
  ]
  const onChange = vi.fn()
  render(<ProductionFeeControl sheet={sheet} calc={calc} editable={editable} onChange={onChange} onError={vi.fn()} />)
  return { onChange }
}

beforeEach(() => {
  vi.mocked(api.put).mockReset()
  vi.mocked(api.put).mockResolvedValue({} as never)
  vi.mocked(api.post).mockReset()
  vi.mocked(api.post).mockResolvedValue({} as never)
})

describe('the Production fee control', () => {
  it('shows this budget’s fee, the preset beside it, and that they differ', () => {
    renderControl()
    expect(screen.getByLabelText('Production fee percent')).toHaveValue('12')
    expect(screen.getByTestId('production-fee-preset').textContent).toBe('preset: 8 % · all kinds')
    expect(screen.getByTestId('production-fee-differs').textContent).toBe('This budget differs from the preset.')
    expect(screen.getByTestId('production-fee-amount').textContent).toContain('882.00')
    expect(screen.getByTestId('production-fee').textContent).toContain('price only')
  })

  it('says nothing differs, and offers no reset, when the fee is the preset', () => {
    renderControl(sheetWithFee({ percent: '8.0000', differs_from_preset: false }))
    expect(screen.queryByTestId('production-fee-differs')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Reset to preset' })).toBeNull()
  })

  it('saves a new percent for this budget only', async () => {
    const { onChange } = renderControl()
    fireEvent.change(screen.getByLabelText('Production fee percent'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0]).toEqual(['/budgets/b1/production-fee', { enabled: true, percent: '10', kinds: [] }])
    await waitFor(() => expect(onChange).toHaveBeenCalled())
  })

  it('saves a fee on certain kinds', async () => {
    renderControl()
    fireEvent.change(screen.getByLabelText('Production fee on kinds'), { target: { value: 'kinds' } })
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.getByText('At least one kind, or “everything”.')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Crew & labour'))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual({ enabled: true, percent: '12', kinds: ['labor'] })
  })

  it('switches the fee off', async () => {
    renderControl()
    fireEvent.click(screen.getByLabelText('Production fee on'))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual({ enabled: false, kinds: [] })
  })

  it('Reset to preset asks the server to copy the preset', async () => {
    renderControl()
    fireEvent.click(screen.getByRole('button', { name: 'Reset to preset' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/budgets/b1/production-fee/reset'))
  })

  it('a locked budget shows the fee and changes nothing', () => {
    renderControl(sheetWithFee(), false)
    expect(screen.getByTestId('production-fee-value').textContent).toBe('12 % · all kinds')
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Reset to preset' })).toBeNull()
  })

  it('a budget with no preset says so', () => {
    renderControl(sheetWithFee({ preset_percent: null }))
    expect(screen.getByTestId('production-fee-preset').textContent).toBe('no preset')
  })
})

describe('the Adjustments panel', () => {
  it('does not list the production fee — it has its own control', () => {
    render(<AdjustmentsPanel sheet={sheetWithFee()} calc={makeCalculated()} editable onChange={vi.fn()} onError={vi.fn()} />)
    const labels = screen.getAllByTestId('adjustment-row').map((row) => row.textContent)
    expect(labels.some((text) => text?.includes('Crew discount'))).toBe(true)
    expect(labels.some((text) => text?.includes('Production fee'))).toBe(false)
  })
})
