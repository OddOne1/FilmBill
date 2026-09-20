import type { SiteSettingsResponse } from '@/types'

/**
 * Site branding, fetched on the SERVER so the first HTML response already
 * carries it.
 *
 * app/layout.tsx's generateMetadata (favicon), app/(auth)/layout.tsx (login
 * logo) and the dashboard sidebar all call this one function rather than
 * growing three copies of the same fetch.
 *
 * Two environment details this exists to state once:
 *
 *  - `API_INTERNAL_URL` is the container-to-container address. The browser's
 *    `/api` prefix means nothing during SSR — there is no page origin to
 *    resolve it against — so the server talks to the API directly.
 *  - `NEXT_PUBLIC_API_URL` is only guaranteed to exist at BUILD time (it is
 *    inlined into the client bundle), and a Docker multi-stage build does
 *    not carry ENV into the runner stage. `/api` is hardcoded as the
 *    fallback because that relative path, routed by Traefik to the api
 *    container, is the only value this deployment has ever used. Reading it
 *    at request time without that fallback used to point every visitor's
 *    browser at their own machine.
 *
 * Server-only by construction rather than by the `server-only` package,
 * which this app does not depend on: it reads `API_INTERNAL_URL`, which is
 * never inlined into the client bundle, so importing it from a client
 * component would fetch the wrong host rather than work by accident.
 *
 * `GET /site-settings` has no auth dependency (apps/api/routers/
 * site_settings.py), which is what makes an unauthenticated server-side
 * call legitimate here — it is the same data the login page already renders
 * to anonymous visitors.
 *
 * Never throws: an unreachable backend at render time returns null and the
 * caller falls back to the bundled default, rather than failing the page.
 */
export async function fetchSiteSettingsServer(): Promise<SiteSettingsResponse | null> {
  try {
    const internalUrl = process.env.API_INTERNAL_URL || 'http://localhost:8000'
    const res = await fetch(internalUrl + '/site-settings', { next: { revalidate: 60 } })
    if (!res.ok) return null
    return (await res.json()) as SiteSettingsResponse
  } catch {
    return null
  }
}

/**
 * The browser-usable form of one relative proxy path from those settings.
 *
 * The API hands back relative `/files/object/...` URLs (see `_to_response`
 * in routers/site_settings.py), which resolve against the Next.js origin —
 * not the API's — unless prefixed. On the client `resolveApiMediaUrl` does
 * this; on the server that helper's env assumptions do not hold, hence the
 * separate spelling.
 */
export function toPublicMediaUrl(relative: string | null | undefined): string | null {
  if (!relative) return null
  const publicPrefix = process.env.NEXT_PUBLIC_API_URL || '/api'
  return publicPrefix + relative
}
