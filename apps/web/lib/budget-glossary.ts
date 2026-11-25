/**
 * Every word the budget screens use for a cost figure, in one place.
 *
 * Keyed by stable ids so a later language pass translates this one file and
 * nothing else. The API's field names (`estimate_cost`, `committed_open`,
 * `efc`, `remaining`, `variance`, `forecast_margin`, `unallocated`) do not
 * change: they are the contract with the server, and this is only what a
 * person reads.
 *
 * **Two abbreviations are banned from the screen**: the production-accounting
 * shorthand for "estimate at completion" and "estimate to complete". Nobody
 * at the acceptance walk knew what they meant, and a label a person has to
 * look up is not a label. `budget-glossary.test.tsx` renders every budget view
 * and fails if either appears in the page text or in any attribute a screen
 * reader would speak.
 *
 * Nothing here computes money (CLAUDE.md rule 2). The worked example's
 * figures come from `budget-glossary-example.json`, and the API's
 * `test_budget_glossary_example.py` runs that file's inputs through
 * `billing/budget/tracking.py` and fails if a figure here is not what the
 * real module says.
 */

import example from './budget-glossary-example.json'
import { formatAmount, NO_VALUE } from './money'

export type GlossaryId =
  | 'planned'
  | 'ordered'
  | 'ordered_open'
  | 'spent'
  | 'still_to_spend'
  | 'forecast'
  | 'variance'
  | 'forecast_profit'
  | 'planned_profit'
  | 'price_agreed'
  | 'not_on_a_line'
  | 'finished'
  | 'price'
  | 'cost'
  | 'margin'
  | 'margin_percent'
  | 'share'
  | 'extras_accepted'
  | 'total_quote'
  | 'total_with_extras'

export interface GlossaryEntry {
  label: string
  /** One sentence, shown behind the info icon. */
  explanation: string
}

export const GLOSSARY: Record<GlossaryId, GlossaryEntry> = {
  planned: {
    label: 'Planned',
    explanation: 'The cost this was estimated at when the budget was made — the yardstick everything else is measured against.',
  },
  ordered: {
    label: 'Ordered',
    explanation: 'Orders and bookings placed with suppliers that have not been invoiced yet.',
  },
  ordered_open: {
    label: 'Ordered, not yet invoiced',
    explanation: 'What is still owed on open orders: each order less the invoices already received for it, never below zero.',
  },
  spent: {
    label: 'Spent',
    explanation: 'Every supplier invoice or receipt booked here, paid or not.',
  },
  still_to_spend: {
    label: 'Still to spend',
    explanation:
      'What is still expected to go out: unless you set it yourself, whatever is left of the planned cost, but never less than what is still on order — and nothing at all once the line is marked finished.',
  },
  forecast: {
    label: 'Forecast final cost',
    explanation: 'What this is expected to cost in the end: what has been spent plus what is still to spend.',
  },
  variance: {
    label: 'Under / over budget',
    explanation: 'Planned cost compared with the forecast final cost: “under” means it should cost less than planned, “over” means more.',
  },
  forecast_profit: {
    label: 'Forecast profit',
    explanation:
      'The price the client agreed minus the forecast final cost; the price does not move when costs do, so this is what is really left.',
  },
  planned_profit: {
    label: 'Planned profit',
    explanation: 'The price the client agreed minus the planned cost, as the budget was made.',
  },
  price_agreed: {
    label: 'Price agreed',
    explanation: 'The client price this budget was calculated at, including accepted extras.',
  },
  not_on_a_line: {
    label: 'Not on a line',
    explanation: 'Costs booked on a section but on none of its lines — something nobody planned a line for.',
  },
  finished: {
    label: 'Finished',
    explanation: 'Nothing more is expected on this line, so its forecast final cost is what has been spent.',
  },
  price: {
    label: 'Price',
    explanation: 'What the client pays for this section, after its discounts and surcharges.',
  },
  cost: {
    label: 'Cost',
    explanation: 'The planned cost of this section.',
  },
  margin: {
    label: 'Margin',
    explanation: 'Price minus planned cost.',
  },
  margin_percent: {
    label: 'Margin %',
    explanation: 'The margin as a share of the price.',
  },
  share: {
    label: 'Share',
    explanation: 'This section’s part of the price of all sections together.',
  },
  extras_accepted: {
    label: 'Extras accepted',
    explanation:
      'Extra charges the client accepted after the quote, at their own price; the budget’s surcharges, such as a production fee, apply to them like to any line. Proposed and declined extras are not counted.',
  },
  total_quote: {
    label: 'Quote (without extras)',
    explanation: 'The net price of everything except extras — what the locked quote said.',
  },
  total_with_extras: {
    label: 'With accepted extras',
    explanation: 'The net price including every extra the client has accepted.',
  },
}

/** The phase switch's buttons: which glossary entry each phase reads. */
export const PHASE_GLOSSARY = {
  estimate: 'planned',
  committed: 'ordered',
  actual: 'spent',
  efc: 'forecast',
  variance: 'variance',
} as const satisfies Record<string, GlossaryId>

/** The column a phase shows, when it differs from the button's word. */
export const PHASE_COLUMN = {
  committed: 'ordered_open',
  actual: 'spent',
  efc: 'forecast',
  variance: 'variance',
} as const satisfies Record<string, GlossaryId>

/** The sentence under the phase switch, per phase, in plain words. */
export const PHASE_SENTENCE: Record<keyof typeof PHASE_COLUMN, string> = {
  committed: 'Each line shows what is still owed on its open orders — ordered, but not invoiced yet.',
  actual: 'Each line shows what has been invoiced so far, paid or not.',
  efc: 'Each line shows what it is expected to cost in the end: what has been spent, plus what is still to spend.',
  variance: 'Each line shows how far its forecast final cost is under or over what was planned.',
}

export const PHASE_CLICK_HINT = 'Click a line to see its orders and invoices, or to mark it finished.'

export function glossary(id: GlossaryId): GlossaryEntry {
  return GLOSSARY[id]
}

/**
 * An under/over amount in words: "240.00 over", "150.00 under", "0.00 on
 * budget". String handling only — the sign is read off the API's string and
 * dropped because the word now carries it; nothing is negated.
 */
export function varianceWords(value: string | null | undefined): string {
  if (typeof value !== 'string' || !/^-?\d+(\.\d+)?$/.test(value.trim())) return NO_VALUE
  const text = value.trim()
  if (text.startsWith('-')) return `${formatAmount(text.slice(1))} over`
  if (/^0+(\.0+)?$/.test(text)) return `${formatAmount(text)} on budget`
  return `${formatAmount(text)} under`
}

/** A percentage without its sign, for beside `varianceWords`. */
export function unsignedPercent(value: string | null | undefined): string | null {
  if (typeof value !== 'string' || !value) return null
  return formatAmount(value.startsWith('-') ? value.slice(1) : value)
}

/** The "How to read this" panel's worked example. */
export const WORKED_EXAMPLE = {
  inputs: example.inputs,
  outputs: example.outputs,
  intro: `A line planned at ${formatAmount(example.inputs.planned)}. ${formatAmount(
    example.inputs.spent,
  )} has been invoiced, and another ${formatAmount(example.inputs.ordered_not_invoiced)} is ordered but not invoiced yet.`,
  rows: [
    ['planned', example.outputs.planned, 'what the budget said'],
    ['ordered_open', example.outputs.ordered_open, 'the order nobody has invoiced yet'],
    ['spent', example.outputs.spent, 'the invoices so far'],
    ['still_to_spend', example.outputs.still_to_spend, 'what is left of the plan — more than the open order, so the plan wins'],
    ['forecast', example.outputs.forecast, 'spent plus still to spend'],
    ['variance', example.outputs.variance, 'the plan is still expected to hold'],
  ] as [GlossaryId, string, string][],
  finished: `If the line is marked finished because nothing more will come, still to spend becomes 0.00, the forecast final cost drops to ${formatAmount(
    example.outputs.finished_forecast,
  )} and the line shows ${varianceWords(example.outputs.finished_variance)}: the forecast profit rises by ${formatAmount(
    example.outputs.finished_variance,
  )}. (With an order still open, FilmBill asks before finishing the line.)`,
}
