/**
 * The calculator's form state, and how it becomes a request.
 *
 * Separate from the screen so the serialisation can be tested without
 * rendering anything — and so it is obvious that this file contains no
 * arithmetic. Every numeric field is a **string**, kept exactly as it was
 * typed and sent exactly as it was typed. Nothing here parses money,
 * multiplies a quantity by a price, or adds a column up: that is the
 * engine's job and the engine is on the server (CLAUDE.md rule 2).
 */

import type { CalcDocumentRequest } from '@/types'

export interface LineDraft {
  node: 'line'
  id: string
  label: string
  quantity: string
  period_value: string
  period_unit: string
  unit_price: string
  unit_cost: string
  factor: string
  /** The item kind an adjustment can filter on — `labor`, `travel`, … */
  itemKind: string
  tax_ref: string
  discountMode: '' | 'percent' | 'amount'
  discountValue: string
}

export interface AdjustmentDraft {
  id: string
  label: string
  type: 'discount' | 'surcharge'
  mode: 'percent' | 'amount'
  value: string
  base: 'subtotal' | 'subtotal_incl_previous'
  /** Empty means the whole subtotal; otherwise only these item kinds. */
  kinds: string
}

export interface GroupDraft {
  node: 'group'
  id: string
  title: string
  children: ChildDraft[]
  adjustments: AdjustmentDraft[]
}

export type ChildDraft = LineDraft | GroupDraft

export function isGroupDraft(child: ChildDraft): child is GroupDraft {
  return child.node === 'group'
}

export interface DocumentDraft {
  id: string
  title: string
  children: ChildDraft[]
  adjustments: AdjustmentDraft[]
}

let counter = 0
/** Ids only have to be unique within one document; the engine refuses a
 *  duplicate with its own sentence, so this is convenience, not a rule. */
export function nextId(prefix: string): string {
  counter += 1
  return `${prefix}-${counter}`
}

export function emptyLine(): LineDraft {
  return {
    node: 'line',
    id: nextId('line'),
    label: '',
    quantity: '1',
    period_value: '1',
    period_unit: 'day',
    unit_price: '0',
    unit_cost: '0',
    factor: '1',
    itemKind: '',
    tax_ref: 'AT-20',
    discountMode: '',
    discountValue: '',
  }
}

export function emptyGroup(): GroupDraft {
  return {
    node: 'group',
    id: nextId('group'),
    title: 'New group',
    children: [emptyLine()],
    adjustments: [],
  }
}

export function emptyAdjustment(): AdjustmentDraft {
  return {
    id: nextId('adj'),
    label: 'Adjustment',
    type: 'discount',
    mode: 'percent',
    value: '0',
    base: 'subtotal',
    kinds: '',
  }
}

function adjustmentPayload(draft: AdjustmentDraft) {
  return {
    label: draft.label,
    type: draft.type,
    mode: draft.mode,
    // The string as typed. "7,5" is not this app's problem to resolve.
    value: draft.value.trim() || '0',
    base: draft.base,
    kinds: draft.kinds
      .split(',')
      .map((kind) => kind.trim())
      .filter(Boolean),
  }
}

function childPayload(child: ChildDraft): Record<string, unknown> {
  if (isGroupDraft(child)) {
    return {
      type: 'group',
      id: child.id,
      title: child.title,
      children: child.children.map(childPayload),
      adjustments: child.adjustments.map(adjustmentPayload),
    }
  }
  return {
    type: 'line',
    id: child.id,
    label: child.label,
    quantity: child.quantity.trim() || '0',
    period_value: child.period_value.trim() || '0',
    period_unit: child.period_unit,
    unit_price: child.unit_price.trim() || '0',
    unit_cost: child.unit_cost.trim() || '0',
    factor: child.factor.trim() || '1',
    kind: child.itemKind,
    tax_ref: child.tax_ref,
    discount:
      child.discountMode && child.discountValue.trim()
        ? { mode: child.discountMode, value: child.discountValue.trim() }
        : null,
  }
}

/** The draft as the request body the API expects. */
export function documentPayload(draft: DocumentDraft): CalcDocumentRequest {
  return {
    id: draft.id,
    title: draft.title,
    children: draft.children.map(childPayload),
    adjustments: draft.adjustments.map(adjustmentPayload),
  } as unknown as CalcDocumentRequest
}

/** Every `tax_ref` the document mentions, in the order it first appears.
 *
 *  Used to ask for a VAT rate per bucket. Collecting them is not computing
 *  money — no rate is assumed and no VAT is derived here; the rates go to
 *  `POST /calc/preview` and the server returns the VAT. */
export function taxRefsOf(draft: DocumentDraft): string[] {
  const refs: string[] = []
  const walk = (children: ChildDraft[]) => {
    for (const child of children) {
      if (isGroupDraft(child)) walk(child.children)
      else if (child.tax_ref && !refs.includes(child.tax_ref)) {
        refs.push(child.tax_ref)
      }
    }
  }
  walk(draft.children)
  return refs
}

/** The S1 quote from SCOPE §12, as this form's state.
 *
 *  The same numbers as `apps/api/tests/fixtures/calc/s1-quote.json`, so
 *  pressing the button and reading the total is a check against SCOPE:
 *  net 12,517.20 · VAT 20 % 2,503.44 · gross 15,020.64. */
export function s1Example(): DocumentDraft {
  const line = (
    id: string,
    label: string,
    quantity: string,
    periodValue: string,
    periodUnit: string,
    unitPrice: string,
    unitCost: string,
    itemKind: string,
  ): LineDraft => ({
    ...emptyLine(),
    id,
    label,
    quantity,
    period_value: periodValue,
    period_unit: periodUnit,
    unit_price: unitPrice,
    unit_cost: unitCost,
    itemKind,
    tax_ref: 'AT-20',
  })

  return {
    id: 's1',
    title: 'S1 — domestic AT B2B quote',
    children: [
      {
        node: 'group',
        id: 'crew',
        title: 'Crew',
        children: [
          line('dop', 'DoP (cost 600/day)', '1', '3', 'day', '850', '600', 'labor'),
          line('gaffer', 'Gaffer (cost 420/day)', '1', '3', 'day', '550', '420', 'labor'),
        ],
        adjustments: [
          {
            id: 'crew-discount',
            label: 'Crew discount',
            type: 'discount',
            mode: 'percent',
            value: '5',
            base: 'subtotal',
            kinds: '',
          },
        ],
      },
      {
        node: 'group',
        id: 'equipment',
        title: 'Equipment',
        children: [
          line(
            'alexa',
            'Alexa 35 (owned, cost 0)',
            '1',
            '3',
            'day',
            '1200',
            '0',
            'equipment_rental',
          ),
          line(
            'lenses',
            'Lens set (owned, cost 0)',
            '2',
            '3',
            'day',
            '180',
            '0',
            'equipment_rental',
          ),
        ],
        adjustments: [],
      },
      {
        node: 'group',
        id: 'studio',
        title: 'Studio',
        children: [
          line(
            'studio-a',
            'Studio A (sub-rented at 1 100/day)',
            '1',
            '2',
            'day',
            '1400',
            '1100',
            'space_rental',
          ),
        ],
        adjustments: [],
      },
      {
        node: 'group',
        id: 'travel',
        title: 'Travel',
        children: [
          line('taxi', 'Taxi (rebilled at cost)', '1', '1', 'flat', '120', '120', 'travel'),
        ],
        adjustments: [],
      },
    ],
    adjustments: [
      {
        id: 'production-fee',
        label: 'Production fee',
        type: 'surcharge',
        mode: 'percent',
        value: '8',
        base: 'subtotal',
        kinds: '',
      },
    ],
  }
}
