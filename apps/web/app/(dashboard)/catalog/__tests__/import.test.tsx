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
  usePathname: () => '/catalog/import',
  useParams: () => ({}),
}))

import { api, ApiError } from '@/lib/api'
import ImportPage from '../import/page'
import { useCompanyStore } from '@/stores/company-store'

/**
 * The CSV import.
 *
 * **The thing under test is the digest handshake**: Apply must send the
 * digest the preview returned, or the server's "this file no longer plans
 * what you were shown" guard has nothing to check against. So the test
 * reads the FormData that actually went out rather than trusting that the
 * component meant to include it.
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

function previewResponse(overrides: Record<string, unknown> = {}) {
  return {
    rows: [
      {
        line_number: 2,
        action: 'create',
        message: '',
        sku: 'CAM-001',
        name_de: 'Alexa 35',
        kind: 'equipment_rental',
        category: 'Kamera',
        unit: 'Tag',
        bill_rate_day: '1200.00',
        cost_rate_day: null,
        tax_class: null,
        active: true,
        item_id: null,
      },
      {
        line_number: 3,
        action: 'error',
        message: 'a rate of "zwei" is not a number',
        sku: 'BAD-001',
        name_de: 'Kaputt',
        kind: null,
        category: null,
        unit: null,
        bill_rate_day: null,
        cost_rate_day: null,
        tax_class: null,
        active: null,
        item_id: null,
      },
    ],
    created: 1,
    updated: 0,
    errors: 1,
    digest: 'a1b2c3d4e5f60718',
    delimiter: ';',
    ignored_columns: ['Lieferant'],
    ...overrides,
  }
}

function csvFile(name = 'preisliste.csv') {
  return new File(['sku;name\nCAM-001;Alexa 35\n'], name, { type: 'text/csv' })
}

function uploadCalls() {
  return vi.mocked(api.upload).mock.calls
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany()
})

async function choose(file = csvFile()) {
  const input = screen.getByTestId('import-file') as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
  await waitFor(() => expect(api.upload).toHaveBeenCalled())
}

describe('the preview', () => {
  it('previews the file as soon as it is chosen', async () => {
    vi.mocked(api.upload).mockResolvedValue(previewResponse() as never)
    render(<ImportPage />)
    await choose()

    expect(String(uploadCalls()[0][0])).toBe('/catalog/items/import/preview')
    const form = uploadCalls()[0][1] as FormData
    expect((form.get('file') as File).name).toBe('preisliste.csv')
    // No digest on a preview: there is nothing yet to be stale against.
    expect(form.get('digest')).toBeNull()
  })

  it('shows every row with the verdict the API gave it', async () => {
    vi.mocked(api.upload).mockResolvedValue(previewResponse() as never)
    render(<ImportPage />)
    await choose()

    await waitFor(() =>
      expect(screen.getAllByTestId('import-row')).toHaveLength(2),
    )
    const rows = screen.getAllByTestId('import-row')
    expect(rows[0]).toHaveAttribute('data-action', 'create')
    expect(rows[0].textContent).toContain('New item')
    expect(rows[1]).toHaveAttribute('data-action', 'error')
    expect(rows[1].textContent).toContain('a rate of "zwei" is not a number')
  })

  it('identifies a refused row by its SKU and name', async () => {
    // The refused row has no values to show, but it must still be findable
    // in the file — the API carries `sku`/`name_de` on error rows for this.
    vi.mocked(api.upload).mockResolvedValue(previewResponse() as never)
    render(<ImportPage />)
    await choose()

    await waitFor(() =>
      expect(screen.getAllByTestId('import-row')[1].textContent).toContain(
        'BAD-001',
      ),
    )
    expect(screen.getAllByTestId('import-row')[1].textContent).toContain(
      'Kaputt',
    )
  })

  it('reports the separator it found and the columns it ignored', async () => {
    vi.mocked(api.upload).mockResolvedValue(previewResponse() as never)
    render(<ImportPage />)
    await choose()

    await waitFor(() =>
      expect(screen.getByText(/separator/)).toBeInTheDocument(),
    )
    expect(screen.getByText(/ignored columns: Lieferant/)).toBeInTheDocument()
  })
})

describe('applying', () => {
  it('sends the preview’s digest with the file', async () => {
    vi.mocked(api.upload).mockResolvedValue(previewResponse() as never)
    render(<ImportPage />)
    await choose()

    vi.mocked(api.upload).mockResolvedValue({
      ...previewResponse(),
      applied: 1,
    } as never)
    fireEvent.click(await screen.findByTestId('import-apply'))

    await waitFor(() => expect(uploadCalls()).toHaveLength(2))
    expect(String(uploadCalls()[1][0])).toBe('/catalog/items/import')
    const form = uploadCalls()[1][1] as FormData
    // The guard only works if this is here, and it must be the digest the
    // preview returned — not a fresh one, not an empty string.
    expect(form.get('digest')).toBe('a1b2c3d4e5f60718')
    expect((form.get('file') as File).name).toBe('preisliste.csv')
  })

  it('shows the server’s refusal when the digest no longer matches', async () => {
    vi.mocked(api.upload).mockResolvedValue(previewResponse() as never)
    render(<ImportPage />)
    await choose()

    vi.mocked(api.upload).mockRejectedValue(
      new ApiError(
        409,
        'this file no longer plans what the preview showed (preview a1b2c3d4e5f60718, file 9f8e7d6c5b4a3920); nothing was written',
      ),
    )
    fireEvent.click(await screen.findByTestId('import-apply'))

    await waitFor(() =>
      expect(screen.getByTestId('import-error')).toHaveTextContent(
        'nothing was written',
      ),
    )
  })

  it('reports how many rows landed, from the API’s own count', async () => {
    vi.mocked(api.upload).mockResolvedValue(previewResponse() as never)
    render(<ImportPage />)
    await choose()

    vi.mocked(api.upload).mockResolvedValue({
      ...previewResponse(),
      // Deliberately not created + updated: the screen reports what the
      // server says it applied.
      applied: 7,
    } as never)
    fireEvent.click(await screen.findByTestId('import-apply'))

    await waitFor(() =>
      expect(screen.getByTestId('import-counts')).toHaveTextContent(
        '7 rows imported',
      ),
    )
  })

  it('cannot be applied when every row was refused, and says why (17c)', async () => {
    vi.mocked(api.upload).mockResolvedValue(
      previewResponse({ created: 0, updated: 0, errors: 2 }) as never,
    )
    render(<ImportPage />)
    await choose()

    const button = await screen.findByTestId('import-apply')
    expect(button).toBeDisabled()
    expect(
      screen.getByText(/every row was refused/),
    ).toBeInTheDocument()
  })
})

describe('without catalog.edit', () => {
  it('offers no file input and says why', async () => {
    seedCompany(['catalog.view'])
    render(<ImportPage />)

    await waitFor(() =>
      expect(screen.getByTestId('catalog-read-only')).toBeInTheDocument(),
    )
    expect(screen.queryByTestId('import-file')).not.toBeInTheDocument()
  })
})
