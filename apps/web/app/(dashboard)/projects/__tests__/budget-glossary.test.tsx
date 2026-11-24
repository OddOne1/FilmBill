import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

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
import type { LineTracking } from '@/types'
import { ALL_PERMISSIONS, makeCalculated, makeLedger, makeSheet, makeTracking, seedCompany } from './fixtures'

/**
 * B1.1 part 1: plain labels.
 *
 * The labels and sentences are asserted as LITERALS here, not read back out
 * of `lib/budget-glossary.ts` — a test that compared the screen with the
 * glossary would pass whatever the glossary said, including the
 * abbreviations this part removes.
 */

const BANNED = /\b(EFC|ETC)\b/

/** The fixture tracking, with one line whose still-to-spend was set by hand
 *  — so the marker that names it is on screen for the abbreviation check. */
function trackingWithHandSetLine() {
  const tracking = makeTracking()
  tracking.lines!['l-dop'] = { ...tracking.lines!['l-dop'], remaining_rule: 'etc', etc: '500.00' } as LineTracking
  return tracking
}

function mockApi() {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/budgets/b1') return makeSheet() as never
    if (path === '/budgets/b1/calculated') return makeCalculated() as never
    if (path === '/budgets/b1/tracking') return trackingWithHandSetLine() as never
    if (path.startsWith('/budgets/b1/ledger')) return makeLedger() as never
    if (path.startsWith('/parties')) return { parties: [], total: 0, limit: 200, offset: 0 } as never
    return {} as never
  })
}

/** Every string a person could read or hear: each text node ON ITS OWN, and
 *  every attribute except the test ids (which are not rendered and keep the
 *  API's names). Text nodes one by one, because `body.textContent` runs
 *  neighbours together — "DoP" next to a chip reading "ETC 500" becomes
 *  "DoPETC 500", where `\b` finds no word boundary and the check passes. */
function spokenStrings(): string[] {
  const out: string[] = []
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) out.push(node.nodeValue ?? '')
  document.body.querySelectorAll('*').forEach((element) => {
    for (const attribute of Array.from(element.attributes)) {
      if (attribute.name === 'data-testid' || attribute.name === 'class' || attribute.name === 'style') continue
      out.push(attribute.value)
    }
  })
  return out
}

function expectNoAbbreviations(where: string) {
  const found = spokenStrings().filter((value) => BANNED.test(value))
  expect(found, `“EFC”/“ETC” rendered on ${where}`).toEqual([])
}

function openAllInfoTips() {
  for (const button of screen.queryAllByRole('button', { name: /^What “/ })) {
    if (button.getAttribute('aria-expanded') !== 'true') fireEvent.click(button)
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany(ALL_PERMISSIONS)
  mockApi()
})

describe('the phase switch', () => {
  const expected: [string, string, string][] = [
    ['estimate', 'Planned', 'yardstick everything else is measured against'],
    ['committed', 'Ordered', 'not been invoiced yet'],
    ['actual', 'Spent', 'supplier invoice or receipt'],
    ['efc', 'Forecast final cost', 'what has been spent plus what is still to spend'],
    ['variance', 'Under / over budget', '“under” means it should cost less than planned'],
  ]

  it.each(expected)('the %s button reads “%s” and its info icon explains it', async (key, label, sentence) => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    expect(screen.getByTestId(`phase-${key}`).textContent).toBe(label)

    const info = screen.getByRole('button', { name: `What “${label}” means` })
    expect(screen.queryByRole('note')).toBeNull()
    fireEvent.click(info)
    expect(info.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('note').textContent).toContain(sentence)
  })

  it('the info icon is reachable with the keyboard and opens with Enter', async () => {
    const user = userEvent.setup()
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    const info = screen.getByRole('button', { name: 'What “Forecast final cost” means' })
    info.focus()
    expect(document.activeElement).toBe(info)
    await user.keyboard('{Enter}')
    expect(screen.getByRole('note').textContent).toContain('expected to cost in the end')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('the sentence under the switch is plain words', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-efc'))
    expect(screen.getByTestId('phase-explained').textContent).toBe(
      'Each line shows what it is expected to cost in the end: what has been spent, plus what is still to spend. ' +
        'Click a line to see its orders and invoices, or to mark it finished.',
    )
  })
})

describe('column headers', () => {
  it.each([
    ['committed', 'Ordered, not yet invoiced', 'each order less the invoices already received'],
    ['actual', 'Spent', 'paid or not'],
    ['efc', 'Forecast final cost', 'expected to cost in the end'],
    ['variance', 'Under / over budget', 'compared with the forecast final cost'],
  ])('the %s phase column is “%s” with an explanation', async (key, label, sentence) => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId(`phase-${key}`))
    const head = await screen.findByTestId('phase-head-value')
    expect(head.textContent).toBe(label)
    fireEvent.click(within(head).getByRole('button'))
    expect(within(head).getByRole('note').textContent).toContain(sentence)
    expect(screen.getByTestId('phase-head-planned').textContent).toBe('Planned')
  })

  it('the top sheet’s cost forecast uses the plain names, each with an explanation', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByRole('tab', { name: 'Top sheet' }))
    await screen.findByTestId('cost-forecast')
    const heads: [string, string][] = [
      ['estimate_cost', 'Planned'],
      ['committed_open', 'Ordered, not yet invoiced'],
      ['actual', 'Spent'],
      ['efc', 'Forecast final cost'],
      ['variance', 'Under / over budget'],
    ]
    for (const [key, label] of heads) {
      const head = screen.getByTestId(`forecast-head-${key}`)
      expect(head.textContent).toBe(label)
      expect(within(head).getByRole('button', { name: `What “${label}” means` })).toBeInTheDocument()
    }
    expect(screen.getByTestId('forecast-unallocated').textContent).toContain('Not on a line')
    expect(screen.getByRole('button', { name: 'What “Forecast profit” means' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'What “Planned profit” means' })).toBeInTheDocument()
  })
})

describe('under / over reads as a word', () => {
  it('negative is “over”, with the word and not only a sign', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('phase-variance'))
    const values = screen.getAllByTestId('phase-line-value').map((cell) => cell.textContent)
    // Gaffer −240.00 (over) in the fixture.
    expect(values).toContain(`${formatAmount('240.00')} over`)
    expect(values.every((text) => !(text ?? '').startsWith('-'))).toBe(true)
  })
})

describe('How to read this', () => {
  it('shows the worked example: planned 1,000, 600 invoiced, 200 ordered not invoiced', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('how-to-read'))
    const panel = screen.getByTestId('how-to-read-panel')
    const row = (term: string) =>
      within(panel)
        .getAllByTestId('how-to-read-row')
        .find((element) => element.getAttribute('data-term') === term)?.textContent ?? ''
    expect(row('planned')).toContain(`Planned${formatAmount('1000.00')}`)
    expect(row('ordered_open')).toContain(`Ordered, not yet invoiced${formatAmount('200.00')}`)
    expect(row('spent')).toContain(`Spent${formatAmount('600.00')}`)
    expect(row('still_to_spend')).toContain(`Still to spend${formatAmount('400.00')}`)
    expect(row('forecast')).toContain(`Forecast final cost${formatAmount('1000.00')}`)
    expect(row('variance')).toContain(`Under / over budget${formatAmount('0.00')} on budget`)
    expect(panel.textContent).toContain(`drops to ${formatAmount('600.00')}`)
    expect(panel.textContent).toContain(`${formatAmount('400.00')} under`)
  })
})

describe('the abbreviations never reach the screen', () => {
  it('not on any phase, tab, info note, the drawer or the worked example', async () => {
    render(<BudgetPage />)
    await screen.findByTestId('budget-sheet')
    fireEvent.click(screen.getByTestId('how-to-read'))
    openAllInfoTips()
    expectNoAbbreviations('the estimate phase')

    for (const key of ['committed', 'actual', 'efc', 'variance']) {
      fireEvent.click(screen.getByTestId(`phase-${key}`))
      await screen.findByTestId('phase-sheet')
      openAllInfoTips()
      expectNoAbbreviations(`the ${key} phase`)
    }

    fireEvent.click(screen.getAllByTestId('phase-line-row')[0].querySelector('button') as HTMLElement)
    await screen.findByTestId('line-drawer')
    openAllInfoTips()
    expectNoAbbreviations('the line drawer')

    for (const tab of ['Costs', 'Top sheet']) {
      fireEvent.click(screen.getByRole('tab', { name: tab }))
      openAllInfoTips()
      expectNoAbbreviations(`the ${tab} tab`)
    }
  })

  it('nor on a locked budget’s notice', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/budgets/b1') return makeSheet({ locked: true }) as never
      if (path === '/budgets/b1/calculated') return makeCalculated() as never
      if (path === '/budgets/b1/tracking') return makeTracking() as never
      return {} as never
    })
    render(<BudgetPage />)
    await screen.findByTestId('budget-locked')
    expectNoAbbreviations('the locked notice')
  })
})
