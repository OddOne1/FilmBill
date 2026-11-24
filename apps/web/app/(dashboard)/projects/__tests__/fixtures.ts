import { useCompanyStore } from '@/stores/company-store'
import type {
  BudgetCalculated,
  BudgetLine,
  BudgetSheet,
  CalcLineResult,
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

export function makeSheet(overrides: { locked?: boolean } = {}): BudgetSheet {
  return {
    budget: {
      id: 'b1',
      project_id: 'p1',
      name: 'Example (S1)',
      currency: 'EUR',
      notes: null,
      is_primary: true,
      is_active: true,
      locked_at: overrides.locked ? '2026-11-20T10:00:00Z' : null,
      is_locked: !!overrides.locked,
      section_count: 2,
      line_count: 3,
      created_at: '2026-11-24T00:00:00Z',
      updated_at: '2026-11-24T00:00:00Z',
    },
    sections: [
      { id: 's-crew', budget_id: 'b1', code: '1100', name: 'Crew', parent_id: null, position: 10, is_active: true },
      { id: 's-eq', budget_id: 'b1', code: '1200', name: 'Equipment', parent_id: null, position: 20, is_active: true },
    ],
    lines: [
      line({ id: 'l-dop', description: 'DoP', position: 10, period_ref: 'shoot_days', unit_price: '850.00', unit_cost: '600.00' }),
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
  }
}
