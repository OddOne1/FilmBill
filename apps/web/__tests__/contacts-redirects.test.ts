import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
// eslint-disable-next-line @typescript-eslint/no-require-imports
const nextConfig = require('../next.config.js')

/** P1b-4 part 4: the screen moved to /contacts; an old /parties bookmark
 *  must still open. Asserted on the redirect table Next.js itself reads. */
describe('old /parties addresses still open', () => {
  it('sends /parties and /parties/:id to the same place under /contacts', async () => {
    const redirects = await nextConfig.redirects()
    expect(redirects).toEqual(
      expect.arrayContaining([
        { source: '/parties', destination: '/contacts', permanent: false },
        { source: '/parties/:id', destination: '/contacts/:id', permanent: false },
      ]),
    )
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

/** UI-1 part 3: there is no Crew page any more; crew are the Crew tab of
 *  the Contacts list, and an old /crew bookmark lands there. */
describe('an old /crew address', () => {
  it('opens Contacts with the Crew tab selected', async () => {
    const redirects: { source: string; destination: string; permanent: boolean }[] =
      await nextConfig.redirects()
    expect(redirects.find((entry) => entry.source === '/crew')).toEqual({
      source: '/crew',
      destination: '/contacts?tab=crew',
      permanent: false,
    })
  })

  it('is the only way to /crew: no page is left there to answer first', async () => {
    // Next.js applies a redirect before it looks for a page, but a page
    // left behind would be a second, stale copy of the crew list. Read off
    // the real file tree.
    const app = path.join(__dirname, '..', 'app', '(dashboard)')
    expect(fs.existsSync(path.join(app, 'contacts', 'page.tsx'))).toBe(true)
    expect(fs.existsSync(path.join(app, 'crew'))).toBe(false)
  })
})
