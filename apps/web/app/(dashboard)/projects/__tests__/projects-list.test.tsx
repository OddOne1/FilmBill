import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), upload: vi.fn(), download: vi.fn() },
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
  usePathname: () => '/projects',
  useParams: () => ({}),
}))

import { api } from '@/lib/api'
import ProjectsPage from '../page'
import type { Project, ProjectPage } from '@/types'
import { ALL_PERMISSIONS, makeProject, seedCompany } from './fixtures'

/**
 * The project list. The filters are the server's, so the tests assert what
 * went out in the query string; the day counts are the API's `day_counts`,
 * so the fixture's numbers are ones no client could derive (there are no
 * days in it to count).
 */

function mockList(projects: Project[] = [makeProject()]) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.startsWith('/projects')) {
      return { projects, total: projects.length, limit: 100, offset: 0 } satisfies ProjectPage as never
    }
    if (path.startsWith('/parties')) {
      return { parties: [], total: 0, limit: 200, offset: 0 } as never
    }
    return [] as never
  })
}

function projectPaths(): string[] {
  return vi
    .mocked(api.get)
    .mock.calls.map((call) => String(call[0]))
    .filter((path) => path.startsWith('/projects'))
}

beforeEach(() => {
  vi.clearAllMocks()
  seedCompany(ALL_PERMISSIONS)
})

describe('the project list', () => {
  it('shows number, name, client, status and the API’s day counts', async () => {
    mockList()
    render(<ProjectsPage />)
    const row = await screen.findByTestId('project-row')
    expect(row.textContent).toContain('PRJ-2026-001')
    expect(row.textContent).toContain('Spring campaign')
    expect(row.textContent).toContain('Acme GmbH')
    expect(row.textContent).toContain('Active')
    expect(row.textContent).toContain('1 prep · 3 shoot')
  })

  it('sends the status filter to the server', async () => {
    mockList()
    render(<ProjectsPage />)
    await screen.findByTestId('project-row')
    fireEvent.change(screen.getByLabelText('Filter by status'), { target: { value: 'wrapped' } })
    await waitFor(() =>
      expect(projectPaths().some((path) => path.includes('status=wrapped'))).toBe(true),
    )
  })

  it('creates a project with what was typed, and says why the button waits', async () => {
    mockList([])
    vi.mocked(api.post).mockResolvedValue(makeProject({ id: 'p2' }) as never)
    render(<ProjectsPage />)
    fireEvent.click(await screen.findByTestId('projects-new'))

    expect(screen.getByTestId('project-new-why').textContent).toContain('needs a name')
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Autumn spot' } })
    expect(screen.getByTestId('project-new-why').textContent).toContain('start date')
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2026-09-01' } })
    expect(screen.queryByTestId('project-new-why')).toBeNull()

    fireEvent.click(screen.getByText('Create project'))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe('/projects')
    expect(vi.mocked(api.post).mock.calls[0][1]).toMatchObject({
      name: 'Autumn spot',
      start_date: '2026-09-01',
      client_party_id: null,
    })
  })

  it('without projects.edit there is no New button and a sentence says why', async () => {
    seedCompany(['projects.view'])
    mockList()
    render(<ProjectsPage />)
    await screen.findByTestId('project-row')
    expect(screen.queryByTestId('projects-new')).toBeNull()
    expect(screen.getByTestId('projects-read-only').textContent).toContain('project edit permission')
  })

  it('without projects.view the screen says so and asks for nothing', async () => {
    seedCompany(['catalog.view'])
    mockList()
    render(<ProjectsPage />)
    expect(screen.getByText(/do not have access to this company’s projects/)).toBeInTheDocument()
    expect(projectPaths()).toEqual([])
  })
})
