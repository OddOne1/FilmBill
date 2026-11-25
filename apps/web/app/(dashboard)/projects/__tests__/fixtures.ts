import { useCompanyStore } from '@/stores/company-store'
import type {
  BudgetCalculated,
  BudgetLedger,
  BudgetLine,
  BudgetSheet,
  BudgetTracking,
  CalcLineResult,
  LineTracking,
  RollupTracking,
  Project,
  ProjectDay,
} from '@/types'

/**
 * Fixtures in the API's own shapes (typed against the generated schema, so a
 * field the server renames breaks the typecheck here rather than leaving a
 * test asserting on a field nothing sends).
 *
 * The budget is the S1 example's Crew and Equipment sections. The Crew
 * section's two lines are 2 550.00 and 1 650.00 — 4 200.00 together — and the
 * engine's subtotal for the section is **3 990.00**, after its 5 % discount.
 * So a screen that showed 4 200.00 under Crew added the rows up itself.
 */

export function seedCompany(permissions: string[]) {
  useCompanyStore.setState({
    companies: [
      {
        id: 'c1',
        legal_name: 'YON Studio OG',
        trading_name: null,
        display_name: 'YON Studio OG',
        default_currency: 'EUR',
        role: 'owner',
        permissions,
        expires_at: null,
      },
    ],
    activeCompanyId: 'c1',
    loaded: true,
    error: null,
  })
}

export const ALL_PERMISSIONS = [
  'projects.view',
  'projects.edit',
  'budgets.view',
  'budgets.edit',
  'parties.view',
  'catalog.view',
]

export function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    project_number: 'PRJ-2026-001',
    name: 'Spring campaign',
    client_party_id: 'party1',
    client_name: 'Acme GmbH',
    description: null,
    status: 'active',
    start_date: '2026-03-02',
    end_date: '2026-03-13',
    notes: null,
    day_counts: { shoot: 3, prep: 1 },
    budget_count: 1,
    created_at: '2026-11-24T00:00:00Z',
    updated_at: '2026-11-24T00:00:00Z',
    ...overrides,
  }
}

export function makeDay(overrides: Partial<ProjectDay> = {}): ProjectDay {
  return {
    id: 'd1',
    project_id: 'p1',
    date: '2026-03-04',
    type: 'shoot',
    location: 'Studio A',
    call_time: '07:00:00',
    wrap_time: null,
    notes: null,
    is_active: true,
    created_at: '2026-11-24T00:00:00Z',
    updated_at: '2026-11-24T00:00:00Z',
    ...overrides,
  }
}

function line(overrides: Partial<BudgetLine>): BudgetLine {
  return {
    id: 'l?',
    section_id: 's-crew',
    position: 10,
    item_id: null,
    description: '?',
    kind: 'labor',
    quantity: '1.0000',
    quantity_ref: null,
    period_value: '3.0000',
    period_ref: null,
    period_unit: 'day',
    factor: '1.0000',
    factor_source: 'manual',
    unit_price: '0.00',
    unit_cost: '0.00',
    discount_mode: null,
    discount_value: null,
    tax_ref: 'AT-20',
    tags: [],
    allow_negative: false,
    notes: null,
    is_active: true,
    catalog_difference: null,
    ...overrides,
  }
}

export function makeSheet(overrides: { locked?: boolean; closed?: boolean } = {}): BudgetSheet {
  return {
    budget: {
      id: 'b1',
      project_id: 'p1',
      name: 'Example (S1)',
      currency: 'EUR',
      notes: null,
      is_primary: true,
      is_active: true,
      locked_at: overrides.locked || overrides.closed ? '2026-11-20T10:00:00Z' : null,
      is_locked: !!overrides.locked || !!overrides.closed,
      closed_at: overrides.closed ? '2026-11-24T18:00:00Z' : null,
      closed_by: null,
      close_note: overrides.closed ? 'Approved by the producer on the wrap call.' : null,
      is_closed: !!overrides.closed,
      section_count: 2,
      line_count: 3,
      created_at: '2026-11-24T00:00:00Z',
      updated_at: '2026-11-24T00:00:00Z',
    },
    sections: [
      { id: 's-crew', budget_id: 'b1', code: '1100', name: 'Crew', parent_id: null, position: 10, is_active: true, is_extras: false },
      { id: 's-eq', budget_id: 'b1', code: '1200', name: 'Equipment', parent_id: null, position: 20, is_active: true, is_extras: false },
    ],
    lines: [
      // Follows `shoot_days` (3). Its stored period is a stale 1 — what the
      // line had before it was pointed at the global — so a cell that showed
      // the stored number instead of the charged one would say 1.
      line({ id: 'l-dop', description: 'DoP', position: 10, period_value: '1.0000', period_ref: 'shoot_days', unit_price: '850.00', unit_cost: '600.00' }),
      line({ id: 'l-gaffer', description: 'Gaffer', position: 20, unit_price: '550.00', unit_cost: '420.00' }),
      line({
        id: 'l-alexa',
        section_id: 's-eq',
        description: 'Alexa 35',
        kind: 'equipment_rental',
        item_id: 'item-alexa',
        factor: '2.8000',
        factor_source: 'catalog',
        period_value: '6.0000',
        unit_price: '1200.00',
        catalog_difference: {
          item_name: 'Alexa 35',
          catalog_unit_price: '1250.00',
          catalog_unit_cost: null,
        },
      }),
    ],
    globals: [
      {
        id: 'g-shoot',
        budget_id: 'b1',
        key: 'shoot_days',
        label: 'Shoot days',
        value: '3.0000',
        unit: 'days',
        source: 'days:shoot',
        is_computed: true,
        referenced_by: 1,
        is_active: true,
      },
    ],
    adjustments: [
      {
        id: 'a-crew',
        budget_id: 'b1',
        section_id: 's-crew',
        position: 10,
        label: 'Crew discount',
        type: 'discount',
        mode: 'percent',
        value: '5.0000',
        base: 'subtotal',
        kinds: [],
        is_active: true,
      },
    ],
  }
}

function lineResult(id: string, label: string, net: string, cost: string, marginPercent: string): CalcLineResult {
  return {
    type: 'line',
    id,
    label,
    kind: 'labor',
    net,
    gross: net,
    discount_amount: '0.00',
    cost_net: cost,
    margin: '0.00',
    margin_percent: marginPercent,
    period_unit: 'day',
    period_value: '3.0000',
    tax_ref: 'AT-20',
  }
}

export function makeCalculated(): BudgetCalculated {
  return {
    budget_id: 'b1',
    currency: 'EUR',
    factors: [
      {
        line_id: 'l-alexa',
        days: 6,
        factor: '2.8000',
        matched_days: 5,
        rule: 'next_lower',
        group_name: 'Standard-Mietfaktoren',
      },
    ],
    top_sheet: [
      { section_id: 's-crew', code: '1100', name: 'Crew', price: '3990.00', cost: '3060.00', margin: '930.00', margin_percent: '23.31', share_of_subtotal: '54.29' },
      { section_id: 's-eq', code: '1200', name: 'Equipment', price: '3360.00', cost: '0.00', margin: '3360.00', margin_percent: '100.00', share_of_subtotal: '45.71' },
    ],
    result: {
      children: [
        {
          type: 'group',
          id: 's-crew',
          title: 'Crew',
          description: '',
          children: [
            lineResult('l-dop', 'DoP', '2550.00', '1800.00', '29.41'),
            lineResult('l-gaffer', 'Gaffer', '1650.00', '1260.00', '23.64'),
          ],
          adjustments: [],
          subtotal_before_adjustments: '4200.00',
          subtotal: '3990.00',
          cost_subtotal: '3060.00',
          margin: '930.00',
          margin_percent: '23.31',
          net_by_tax_ref: { 'AT-20': '3990.00' },
          uniform_period: true,
        },
        {
          type: 'group',
          id: 's-eq',
          title: 'Equipment',
          description: '',
          children: [lineResult('l-alexa', 'Alexa 35', '3360.00', '0.00', '100.00')],
          adjustments: [],
          subtotal_before_adjustments: '3360.00',
          subtotal: '3360.00',
          cost_subtotal: '0.00',
          margin: '3360.00',
          margin_percent: '100.00',
          net_by_tax_ref: { 'AT-20': '3360.00' },
          uniform_period: true,
        },
      ],
      adjustments: [],
      subtotal: '7350.00',
      net: '7350.00',
      cost: '3060.00',
      margin: '4290.00',
      margin_percent: '58.37',
      net_by_tax_ref: { 'AT-20': '7350.00' },
      vat: null,
      warnings: [],
    },
    totals: {
      baseline: { net: '7350.00', cost: '3060.00', margin: '4290.00', margin_percent: '58.37', net_by_tax_ref: { 'AT-20': '7350.00' } },
      with_extras: { net: '7350.00', cost: '3060.00', margin: '4290.00', margin_percent: '58.37', net_by_tax_ref: { 'AT-20': '7350.00' } },
    },
    extras: [],
  }
}

// ─── B1: cost planning ──────────────────────────────────────────────────────
//
// `GET /budgets/b1/tracking` for the sheet above, with numbers that follow the
// API's own rules (billing/budget/tracking.py) so the fixture is a response
// the server could really send:
//
//   * Gaffer: estimate 1 260.00, invoiced 1 500.00 → forecast final cost 1 500.00, variance
//     −240.00 (−19.05 %), over budget.
//   * Alexa 35: estimate cost 0.00 (owned), an open order of 1 000.00 → forecast final cost
//     1 000.00, variance −1 000.00, no percentage.
//   * Crew also carries an invoice of 150.00 on the SECTION with no line.
//     So Crew's variance is −390.00 while its lines' variances add up to only
//     −240.00 — the difference a browser that summed rows would get wrong.

function lineTracking(overrides: Partial<LineTracking> & { id: string }): LineTracking {
  return {
    estimate_cost: '0.00',
    committed_gross: '0.00',
    committed_open: '0.00',
    actual: '0.00',
    exposure: '0.00',
    remaining: '0.00',
    remaining_rule: 'default',
    efc: '0.00',
    variance: '0.00',
    variance_percent: null,
    over_budget: false,
    etc: null,
    finished: false,
    ...overrides,
  }
}

function rollup(overrides: Partial<RollupTracking> & { id: string }): RollupTracking {
  return {
    estimate_cost: '0.00',
    committed_gross: '0.00',
    committed_open: '0.00',
    actual: '0.00',
    exposure: '0.00',
    remaining: '0.00',
    efc: '0.00',
    variance: '0.00',
    variance_percent: null,
    over_budget: false,
    unallocated: '0.00',
    unallocated_actual: '0.00',
    unallocated_committed_open: '0.00',
    price: null,
    forecast_margin: null,
    forecast_margin_percent: null,
    estimate_margin: null,
    estimate_margin_percent: null,
    ...overrides,
  }
}

export function makeTracking(): BudgetTracking {
  return {
    lines: {
      'l-dop': lineTracking({
        id: 'l-dop',
        estimate_cost: '1800.00',
        remaining: '1800.00',
        efc: '1800.00',
        variance_percent: '0.00',
      }),
      'l-gaffer': lineTracking({
        id: 'l-gaffer',
        estimate_cost: '1260.00',
        actual: '1500.00',
        exposure: '1500.00',
        efc: '1500.00',
        variance: '-240.00',
        variance_percent: '-19.05',
        over_budget: true,
      }),
      'l-alexa': lineTracking({
        id: 'l-alexa',
        committed_gross: '1000.00',
        committed_open: '1000.00',
        exposure: '1000.00',
        remaining: '1000.00',
        efc: '1000.00',
        variance: '-1000.00',
        over_budget: true,
      }),
    },
    sections: {
      's-crew': rollup({
        id: 's-crew',
        estimate_cost: '3060.00',
        actual: '1650.00',
        exposure: '1650.00',
        remaining: '1800.00',
        efc: '3450.00',
        variance: '-390.00',
        variance_percent: '-12.75',
        over_budget: true,
        unallocated: '150.00',
        unallocated_actual: '150.00',
        price: '3990.00',
        forecast_margin: '540.00',
        forecast_margin_percent: '13.53',
        estimate_margin: '930.00',
        estimate_margin_percent: '23.31',
      }),
      's-eq': rollup({
        id: 's-eq',
        committed_gross: '1000.00',
        committed_open: '1000.00',
        exposure: '1000.00',
        remaining: '1000.00',
        efc: '1000.00',
        variance: '-1000.00',
        over_budget: true,
        price: '3360.00',
        forecast_margin: '2360.00',
        forecast_margin_percent: '70.24',
        estimate_margin: '3360.00',
        estimate_margin_percent: '100.00',
      }),
    },
    budget: rollup({
      id: 'b1',
      estimate_cost: '3060.00',
      committed_gross: '1000.00',
      committed_open: '1000.00',
      actual: '1650.00',
      exposure: '2650.00',
      remaining: '2800.00',
      efc: '4450.00',
      variance: '-1390.00',
      variance_percent: '-45.42',
      over_budget: true,
      unallocated: '150.00',
      unallocated_actual: '150.00',
      price: '7350.00',
      forecast_margin: '2900.00',
      forecast_margin_percent: '39.46',
      estimate_margin: '4290.00',
      estimate_margin_percent: '58.37',
    }),
  }
}

/** One invoice on the Gaffer line, as `GET /budgets/b1/ledger` returns it. */
export function makeLedger(): BudgetLedger {
  return {
    commitments: [],
    actuals: [
      {
        id: 'a-gaffer',
        budget_id: 'b1',
        section_id: 's-crew',
        line_id: 'l-gaffer',
        party_id: null,
        payee_text: 'Licht GmbH',
        description: 'Gaffer, 3 days',
        reference: 'RE-7',
        amount: '1500.00',
        notes: null,
        voided_at: null,
        void_reason: null,
        created_at: '2026-11-24T00:00:00Z',
        updated_at: '2026-11-24T00:00:00Z',
        section_name: 'Crew',
        section_code: '1100',
        line_description: 'Gaffer',
        payee: 'Licht GmbH',
        is_voided: false,
        commitment_id: null,
        incurred_on: '2026-06-03',
        paid_on: null,
        commitment_reference: null,
      },
    ],
  }
}
