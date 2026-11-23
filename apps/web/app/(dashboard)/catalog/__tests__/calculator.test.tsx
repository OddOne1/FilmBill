import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn(), upload: vi.fn() },
  setActiveCompanyId: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.name = 'ApiError'
      this.status = status
      this.detail = detail
    }
  },
}))

vi.mock('next/navigation', () => ({
  usePathname: () => '/catalog/calculator',
  useParams: () => ({}),
}))

import { api, ApiError } from '@/lib/api'
import CalculatorPage from '../calculator/page'
import { documentPayload, s1Example, taxRefsOf } from '../calculator/drafts'
import { useCompanyStore } from '@/stores/company-store'

/**
 * "Try the engine".
 *
 * **The thing these tests are about is the rule that the web never computes
 * money** (CLAUDE.md rule 2), so they are built to fail if it does. The
 * stub's numbers are deliberately *wrong*: the lines say 100.00 each and
 * the net says 999.99. A screen that rendered the API's answer shows
 * 999.99; a screen that added the lines up in the browser shows 200.00 and
 * fails. That is the mutation this file exists to catch, and it is why no
 * test here uses a response whose figures are internally consistent.
 */

function seedCompany(permissions = ['catalog.view', 'catalog.edit']) {
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

/** A response whose totals do NOT follow from its parts. See the file
 *  docstring: this is the point, not an oversight. */
function inconsistentResponse() {
  return {
    id: 'document',
    title: 'Untitled',
    children: [
      {
        type: 'line',
        id: 'a',
        label: 'A',
        net: '100.00',
        cost_net: '10.00',
        margin: '90.00',
        margin_percent: '90.00',
        tax_ref: 'AT-20',
        kind: '',
        period_unit: 'day',
        period_value: '1',
        gross: '100.00',
        discount_amount: '0.00',
      },
      {
        type: 'line',
        id: 'b',
        label: 'B',
        net: '100.00',
        cost_net: '10.00',
        margin: '90.00',
        margin_percent: '90.00',
        tax_ref: 'AT-20',
        kind: '',
        period_unit: 'day',
        period_value: '1',
        gross: '100.00',
        discount_amount: '0.00',
      },
    ],
    subtotal: '888.88',
    adjustments: [],
    net: '999.99',
    cost: '20.00',
    margin: '979.99',
    margin_percent: '98.00',
    net_by_tax_ref: { 'AT-20': '777.77' },
    warnings: [],
    vat: {
      by_tax_ref: [
        {
          tax_ref: 'AT-20',
          net: '777.77',
          rate: '20',
          vat: '123.45',
          gross: '666.66',
        },
      ],
      net: '999.99',
      vat: '123.45',
      gross: '666.66',
    },
  }
}

/** The S1 numbers, as the engine really produces them (SCOPE §12). */
function s1Response() {
  return {
    ...inconsistentResponse(),
    subtotal: '11590.00',
    net: '12517.20',
    cost: '5380.00',
    margin: '7137.20',
    margin_percent: '57.02',
    net_by_tax_ref: { 'AT-20': '12517.20' },
    vat: {
      by_tax_ref: [
        {
          tax_ref: 'AT-20',
          net: '12517.20',
          rate: '20',
          vat: '2503.44',
          gross: '15020.64',
        },
      ],
      net: '12517.20',
      vat: '2503.44',
      gross: '15020.64',
    },
  }
}

function lastRequest(): Record<string, unknown> {
  return (vi.mocked(api.post).mock.calls.at(-1)?.[1] ?? {}) as Record<
    string,
    unknown
  >
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
  vi.mocked(api.get).mockResolvedValue([] as never)
})

describe('the numbers on screen', () => {
  it('are the API’s, even when they do not add up', async () => {
    vi.mocked(api.post).mockResolvedValue(inconsistentResponse() as never)
    render(<CalculatorPage />)

    await waitFor(() =>
      expect(screen.getByTestId('calc-net')).toHaveTextContent('999.99'),
    )
    // The two lines are 100.00 each. A browser that summed them would show
    // 200.00 here.
    expect(screen.getByTestId('calc-net')).not.toHaveTextContent('200.00')
    expect(screen.getByTestId('calc-vat')).toHaveTextContent('123.45')
    // 20 % of 999.99 is 199.998. The screen shows the API's 123.45 because
    // it does not derive VAT from a net.
    expect(screen.getByTestId('calc-vat')).not.toHaveTextContent('199.99')
    // And gross is the API's, not net + vat (which would be 1123.44).
    expect(screen.getByTestId('calc-gross')).toHaveTextContent('666.66')
    expect(screen.getByTestId('calc-gross')).not.toHaveTextContent('1123.44')
  })

  it('shows the net per tax ref as the API grouped it', async () => {
    vi.mocked(api.post).mockResolvedValue(inconsistentResponse() as never)
    render(<CalculatorPage />)

    await waitFor(() =>
      expect(screen.getByTestId('calc-by-tax-ref')).toHaveTextContent('777.77'),
    )
  })

  it('shows each line’s own net, cost and margin from the response', async () => {
    vi.mocked(api.post).mockResolvedValue(inconsistentResponse() as never)
    render(<CalculatorPage />)

    await waitFor(() =>
      expect(screen.getByTestId('line-net-a')).toHaveTextContent('100.00'),
    )
    expect(screen.getAllByTestId('result-line')).toHaveLength(2)
    expect(screen.getAllByTestId('result-line')[0].textContent).toContain(
      'cost 10.00',
    )
  })

  it('renders a group subtotal from the response, not from its lines', async () => {
    vi.mocked(api.post).mockResolvedValue({
      ...inconsistentResponse(),
      children: [
        {
          type: 'group',
          id: 'crew',
          title: 'Crew',
          description: '',
          children: inconsistentResponse().children,
          subtotal_before_adjustments: '200.00',
          adjustments: [],
          // Not 200.00, and not 190.00 either: whatever the server says.
          subtotal: '555.55',
          cost_subtotal: '20.00',
          margin: '535.55',
          margin_percent: '96.40',
          uniform_period: true,
          net_by_tax_ref: { 'AT-20': '555.55' },
        },
      ],
    } as never)
    render(<CalculatorPage />)

    await waitFor(() =>
      expect(screen.getByTestId('group-subtotal-crew')).toHaveTextContent(
        '555.55',
      ),
    )
  })

  it('shows the engine’s warnings', async () => {
    vi.mocked(api.post).mockResolvedValue({
      ...inconsistentResponse(),
      warnings: [
        {
          code: 'mixed_periods',
          message: 'this group mixes day and flat lines',
          where: 'crew',
        },
      ],
    } as never)
    render(<CalculatorPage />)

    await waitFor(() =>
      expect(screen.getByTestId('calc-warnings')).toHaveTextContent(
        'this group mixes day and flat lines',
      ),
    )
  })
})

describe('the S1 example', () => {
  it('sends the S1 document and shows the SCOPE totals', async () => {
    vi.mocked(api.post).mockResolvedValue(s1Response() as never)
    render(<CalculatorPage />)
    await waitFor(() => expect(api.post).toHaveBeenCalled())

    fireEvent.click(screen.getByTestId('load-s1'))

    await waitFor(() => {
      const document = lastRequest().document as Record<string, unknown>
      expect(document.id).toBe('s1')
    })

    const document = lastRequest().document as {
      children: { id: string; children?: { id: string }[] }[]
      adjustments: { label: string; value: string }[]
    }
    expect(document.children.map((child) => child.id)).toEqual([
      'crew',
      'equipment',
      'studio',
      'travel',
    ])
    expect(document.adjustments[0]).toMatchObject({
      label: 'Production fee',
      value: '8',
    })
    expect(lastRequest().vat_rates).toEqual({ 'AT-20': '20' })

    await waitFor(() =>
      expect(screen.getByTestId('calc-net')).toHaveTextContent('12517.20'),
    )
    expect(screen.getByTestId('calc-vat')).toHaveTextContent('2503.44')
    expect(screen.getByTestId('calc-gross')).toHaveTextContent('15020.64')
  })

  it('re-asks the engine when the crew discount changes', async () => {
    vi.mocked(api.post).mockResolvedValue(s1Response() as never)
    render(<CalculatorPage />)
    fireEvent.click(screen.getByTestId('load-s1'))

    await waitFor(() => {
      const document = lastRequest().document as { id: string }
      expect(document.id).toBe('s1')
    })
    const before = vi.mocked(api.post).mock.calls.length

    const values = screen.getAllByLabelText('Adjustment value')
    // The crew group's discount is the first one on the page.
    fireEvent.change(values[0], { target: { value: '10' } })

    await waitFor(() =>
      expect(vi.mocked(api.post).mock.calls.length).toBeGreaterThan(before),
    )
    const document = lastRequest().document as {
      children: { adjustments?: { value: string }[] }[]
    }
    expect(document.children[0].adjustments?.[0].value).toBe('10')
  })
})

describe('a refused document', () => {
  it('shows the engine’s own sentence', async () => {
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(
        422,
        "group 'g3' would be at nesting level 3, and a document nests groups 2 deep at most (SCOPE §5.4)",
      ),
    )
    render(<CalculatorPage />)

    await waitFor(() =>
      expect(screen.getByTestId('calc-error')).toHaveTextContent(
        'nesting level 3',
      ),
    )
  })

  it('offers the nested-group button anyway, so the engine can refuse it', async () => {
    vi.mocked(api.post).mockResolvedValue(inconsistentResponse() as never)
    render(<CalculatorPage />)

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    // The depth rule lives in the engine. A button hidden here would be a
    // second copy of it, and the two would drift.
    expect(screen.getAllByTestId('add-nested-group').length).toBeGreaterThan(0)
  })
})

describe('the request body', () => {
  it('sends every number as the string it was typed as', async () => {
    vi.mocked(api.post).mockResolvedValue(inconsistentResponse() as never)
    render(<CalculatorPage />)
    await waitFor(() => expect(api.post).toHaveBeenCalled())

    // A value a float round-trip would mangle. It must arrive verbatim.
    fireEvent.change(screen.getAllByLabelText(/^Price for /)[0], {
      target: { value: '1200.005' },
    })

    await waitFor(() => {
      const document = lastRequest().document as {
        children: { children: { unit_price: string }[] }[]
      }
      expect(document.children[0].children[0].unit_price).toBe('1200.005')
    })
  })

  it('leaves vat_rates out until every tax ref has a rate', async () => {
    vi.mocked(api.post).mockResolvedValue(inconsistentResponse() as never)
    render(<CalculatorPage />)
    await waitFor(() => expect(api.post).toHaveBeenCalled())

    fireEvent.change(screen.getByLabelText('VAT rate for AT-20'), {
      target: { value: '' },
    })

    await waitFor(() => expect(lastRequest().vat_rates).toBeNull())
    expect(
      screen.getByText(/VAT is left out until every tax ref/),
    ).toBeInTheDocument()
  })
})

describe('the draft model', () => {
  it('collects the tax refs a document mentions, in order', () => {
    expect(taxRefsOf(s1Example())).toEqual(['AT-20'])
  })

  it('serialises the S1 draft to the engine’s own field names', () => {
    const payload = documentPayload(s1Example()) as unknown as {
      children: {
        type: string
        id: string
        children: { type: string; unit_price: string; kind: string }[]
      }[]
    }
    const crew = payload.children[0]
    expect(crew.type).toBe('group')
    expect(crew.children[0]).toMatchObject({
      type: 'line',
      unit_price: '850',
      kind: 'labor',
    })
  })
})
