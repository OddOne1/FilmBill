import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

/**
 * Prepend the API origin to a relative URL; leave an absolute one alone.
 *
 * The one implementation of this rule, and it is IDEMPOTENT on purpose.
 * The rule's failure mode is two places applying it: in production
 * NEXT_PUBLIC_API_URL is itself "/api", so an already-resolved url STILL
 * starts with "/" and a plain `startsWith` guard cannot tell it from a raw
 * one. Resolve twice and you get "/api/api/files/..." and a 404. FreeFrame
 * hit that four separate times before making this function fail safe.
 *
 * Read from process.env inside the function, not into a module constant:
 * tests stub it with vi.stubEnv after this module is imported.
 *
 * Worth knowing why it only ever bites in production: in dev
 * NEXT_PUBLIC_API_URL is an absolute origin, so a resolved url no longer
 * starts with "/" and the first guard catches a second pass by accident.
 * In production it is "/api", so it does not.
 */
function withApiOrigin(url: string): string {
  const origin = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
  if (!url.startsWith('/')) return url
  // Matched at a path BOUNDARY, not as a bare prefix: with origin "/api",
  // a plain `startsWith` would also swallow a genuinely raw "/apiary/..."
  // and leave it unresolved — trading a doubled prefix for a missing one.
  if (url === origin || url.startsWith(`${origin}/`)) return url
  return `${origin}${url}`
}

/**
 * Resolve a possibly-relative file URL returned by the API (logos, avatars,
 * later: document PDFs) into a fully-qualified one. The object proxy returns
 * relative paths like "/files/object/...?token=..." — this prepends the API
 * origin so an <img> outside an API-proxied page can load it. Absolute URLs
 * are returned unchanged.
 */
export function resolveApiMediaUrl(url: string | null | undefined): string | null {
  if (!url) return url ?? null
  return withApiOrigin(url)
}

export function formatRelativeTime(date: string): string {
  const now = Date.now()
  const then = new Date(date).getTime()
  const diffMs = now - then
  const diffSecs = Math.floor(diffMs / 1000)

  if (diffSecs < 60) return 'just now'

  const diffMins = Math.floor(diffSecs / 60)
  if (diffMins < 60) return `${diffMins} minute${diffMins !== 1 ? 's' : ''} ago`

  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`

  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 30) return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`

  const diffMonths = Math.floor(diffDays / 30)
  if (diffMonths < 12) return `${diffMonths} month${diffMonths !== 1 ? 's' : ''} ago`

  const diffYears = Math.floor(diffMonths / 12)
  return `${diffYears} year${diffYears !== 1 ? 's' : ''} ago`
}

/**
 * Truncate a string to the given length, appending "..." if truncated
 */
export function truncate(str: string, length: number): string {
  if (str.length <= length) return str
  return str.slice(0, length) + '...'
}
