import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), delete: vi.fn() },
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

import { api, ApiError } from '@/lib/api'
import { HierarchyTab } from '../hierarchy'
import { findPartyWords } from '@/test/user-words'
import type { Party } from '@/types'

/**
 * The Hierarchy tab's Add person / Add department (P1b-4 part 5), rendered
 * on its own with the API's own response shapes. The transaction and the
 * resolver inheritance are the API's and are tested there
 * (`test_party_children.py`); this file is about what the screen sends and
 * that it never navigates away.
 */

function party(overrides: Partial<Party> = {}): Party {
  return {
    id: 'org',
    type: 'organisation',
    parent_id: null,
    name: 'ORF',
    children: [],
    tax_ids: [],
    addresses: [],
    contact_points: [],
    bank_accounts: [],
    duplicates: [],
    ...overrides,
  } as Party
}

function created(overrides: Partial<Party> = {}): Party {
  return party({ id: 'new1', type: 'person', parent_id: 'org', name: 'Maria Huber', ...overrides })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue({ parties: [], total: 0, limit: 200, offset: 0 } as never)
})

describe('Add person', () => {
  it('creates the person with the organisation as parent, email, phone and crew roles', async () => {
    vi.mocked(api.post).mockResolvedValue(created() as never)
    const onSaved = vi.fn(async () => {})
    render(<HierarchyTab party={party()} canEdit onSaved={onSaved} />)
    fireEvent.click(screen.getByTestId('hierarchy-add-person'))
    expect(screen.getByTestId('hierarchy-save')).toBeDisabled()
    expect(screen.getByTestId('hierarchy-why')).toHaveTextContent('Enter a first or last name first.')

    fireEvent.change(screen.getByTestId('hierarchy-person-first'), { target: { value: 'Maria' } })
    fireEvent.change(screen.getByTestId('hierarchy-person-last'), { target: { value: 'Huber' } })
    fireEvent.change(screen.getByTestId('hierarchy-person-position'), { target: { value: 'Redaktion' } })
    fireEvent.change(screen.getByTestId('hierarchy-person-email'), { target: { value: 'maria@orf.at' } })
    fireEvent.click(screen.getByTestId('hierarchy-person-crew'))
    fireEvent.click(screen.getByTestId('hierarchy-save'))

    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/parties', {
        type: 'person',
        parent_id: 'org',
        first_name: 'Maria',
        middle_name: null,
        last_name: 'Huber',
        title: null,
        position: 'Redaktion',
        email: 'maria@orf.at',
        phone: null,
        is_crew: true,
        tags: [],
      }),
    )
    // The page is reloaded in place — no navigation — and the dialog closes.
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(screen.queryByTestId('hierarchy-dialog-person')).not.toBeInTheDocument()
  })

  it('Add another keeps the dialog open and empty for the next person', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce(created() as never)
      .mockResolvedValueOnce(created({ id: 'new2', name: 'Sam Licht' }) as never)
    render(<HierarchyTab party={party()} canEdit onSaved={async () => {}} />)
    fireEvent.click(screen.getByTestId('hierarchy-add-person'))
    for (const [first, last] of [['Maria', 'Huber'], ['Sam', 'Licht']]) {
      fireEvent.change(screen.getByTestId('hierarchy-person-first'), { target: { value: first } })
      fireEvent.change(screen.getByTestId('hierarchy-person-last'), { target: { value: last } })
      fireEvent.click(screen.getByTestId('hierarchy-save-another'))
      await waitFor(() => expect(screen.getByTestId('hierarchy-done')).toHaveTextContent(`was added`))
      expect(screen.getByTestId('hierarchy-dialog-person')).toBeInTheDocument()
      expect(screen.getByTestId('hierarchy-person-first')).toHaveValue('')
    }
    expect(vi.mocked(api.post)).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('hierarchy-done')).toHaveTextContent('Sam Licht was added.')
  })

  it('marks the new child in the list once the page reloads with it', async () => {
    vi.mocked(api.post).mockResolvedValue(created() as never)
    const { rerender } = render(<HierarchyTab party={party()} canEdit onSaved={async () => {}} />)
    fireEvent.click(screen.getByTestId('hierarchy-add-person'))
    fireEvent.change(screen.getByTestId('hierarchy-person-last'), { target: { value: 'Huber' } })
    fireEvent.click(screen.getByTestId('hierarchy-save'))
    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalled())
    rerender(
      <HierarchyTab
        party={party({
          children: [
            { id: 'new1', type: 'person', name: 'Maria Huber', position: null, party_number: null, is_active: true },
          ],
        })}
        canEdit
        onSaved={async () => {}}
      />,
    )
    expect(await screen.findByTestId('party-child-new-new1')).toHaveTextContent('just added')
    expect(screen.getByTestId('party-child-new1')).toHaveAttribute('href', '/contacts/new1')
  })

  it('shows the server’s sentence when the child is refused', async () => {
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(422, "This contact has 'x' as an email address, which is not one."),
    )
    render(<HierarchyTab party={party()} canEdit onSaved={async () => {}} />)
    fireEvent.click(screen.getByTestId('hierarchy-add-person'))
    fireEvent.change(screen.getByTestId('hierarchy-person-last'), { target: { value: 'Huber' } })
    fireEvent.click(screen.getByTestId('hierarchy-save'))
    expect(await screen.findByTestId('hierarchy-error')).toHaveTextContent('is not one')
  })
})

describe('Add department', () => {
  it('creates a department with its additional line under the organisation', async () => {
    vi.mocked(api.post).mockResolvedValue(created({ id: 'd1', type: 'department', name: 'Unterhaltung' }) as never)
    render(<HierarchyTab party={party()} canEdit onSaved={async () => {}} />)
    fireEvent.click(screen.getByTestId('hierarchy-add-department'))
    fireEvent.change(screen.getByTestId('hierarchy-department-name'), { target: { value: 'Unterhaltung' } })
    fireEvent.change(screen.getByLabelText('Additional line'), { target: { value: 'Küniglberg' } })
    fireEvent.click(screen.getByTestId('hierarchy-save'))
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/parties', {
        type: 'department',
        parent_id: 'org',
        name: 'Unterhaltung',
        name_addition: 'Küniglberg',
      }),
    )
  })

  it('a department offers Add person but not Add department; a reader gets neither', () => {
    const { unmount } = render(
      <HierarchyTab party={party({ type: 'department', parent_id: 'org' })} canEdit onSaved={async () => {}} />,
    )
    expect(screen.getByTestId('hierarchy-add-person')).toBeInTheDocument()
    expect(screen.queryByTestId('hierarchy-add-department')).not.toBeInTheDocument()
    unmount()
    const reader = render(<HierarchyTab party={party()} canEdit={false} onSaved={async () => {}} />)
    expect(screen.queryByTestId('hierarchy-add-person')).not.toBeInTheDocument()
    expect(findPartyWords(reader.container)).toEqual([])
  })
})

describe('a person’s own page: moving in and out', () => {
  const orgs = [party({ id: 'org', name: 'ORF' })]

  it('moves the person under an organisation, and takes them out again', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) =>
      ({ parties: path.includes('organisation') ? orgs : [], total: 1, limit: 200, offset: 0 }) as never,
    )
    vi.mocked(api.patch).mockResolvedValue({} as never)
    const person = party({ id: 'p1', type: 'person', name: 'Sam Licht' })
    const { rerender, container } = render(<HierarchyTab party={person} canEdit onSaved={async () => {}} />)
    expect(screen.getByTestId('hierarchy-belongs-to')).toHaveTextContent('Not part of an organisation.')
    await screen.findByRole('option', { name: 'ORF' })
    // Nothing chosen yet: the button is disabled and the line says why.
    expect(screen.getByTestId('hierarchy-move')).toBeDisabled()
    fireEvent.change(screen.getByTestId('hierarchy-parent'), { target: { value: 'org' } })
    fireEvent.click(screen.getByTestId('hierarchy-move'))
    await waitFor(() => expect(vi.mocked(api.patch)).toHaveBeenCalledWith('/parties/p1', { parent_id: 'org' }))

    rerender(<HierarchyTab party={{ ...person, parent_id: 'org' }} canEdit onSaved={async () => {}} />)
    expect(screen.getByTestId('hierarchy-belongs-to')).toHaveTextContent('Belongs to ORF')
    fireEvent.change(screen.getByTestId('hierarchy-parent'), { target: { value: '' } })
    expect(screen.getByTestId('hierarchy-move')).toHaveTextContent('Take out of the organisation')
    fireEvent.click(screen.getByTestId('hierarchy-move'))
    await waitFor(() => expect(vi.mocked(api.patch)).toHaveBeenLastCalledWith('/parties/p1', { parent_id: null }))
    expect(findPartyWords(container)).toEqual([])
  })

  it('shows the server’s refusal when a move is not allowed', async () => {
    vi.mocked(api.get).mockResolvedValue({ parties: orgs, total: 1, limit: 200, offset: 0 } as never)
    vi.mocked(api.patch).mockRejectedValue(
      new ApiError(409, 'This organisation is at the top of the tree, so it cannot sit inside another organisation.'),
    )
    render(<HierarchyTab party={party({ id: 'p1', type: 'person' })} canEdit onSaved={async () => {}} />)
    await screen.findAllByRole('option', { name: 'ORF' })
    fireEvent.change(screen.getByTestId('hierarchy-parent'), { target: { value: 'org' } })
    fireEvent.click(screen.getByTestId('hierarchy-move'))
    expect(await screen.findByTestId('hierarchy-move-error')).toHaveTextContent('top of the tree')
  })
})
