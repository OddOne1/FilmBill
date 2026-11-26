import { describe, it, expect } from 'vitest'
// eslint-disable-next-line @typescript-eslint/no-require-imports
const nextConfig = require('../next.config.js')

/** P1b-4 part 4: the screen moved to /contacts; an old /parties bookmark
 *  must still open. Asserted on the redirect table Next.js itself reads. */
describe('old /parties addresses still open', () => {
  it('sends /parties and /parties/:id to the same place under /contacts', async () => {
    const redirects = await nextConfig.redirects()
    expect(redirects).toEqual([
      { source: '/parties', destination: '/contacts', permanent: false },
      { source: '/parties/:id', destination: '/contacts/:id', permanent: false },
    ])
  })

  it('the :id pattern carries the id across, rather than landing on the list', async () => {
    const redirects: { source: string; destination: string }[] = await nextConfig.redirects()
    const rule = redirects.find((entry) => entry.source === '/parties/:id')!
    const id = 'b2c4f7e0-0000-4000-8000-000000000001'
    const match = new RegExp(`^${rule.source.replace(':id', '([^/]+)')}$`).exec(`/parties/${id}`)
    expect(match).not.toBeNull()
    expect(rule.destination.replace(':id', match![1])).toBe(`/contacts/${id}`)
  })
})
