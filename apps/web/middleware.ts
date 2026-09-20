import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

const PUBLIC_ROUTES = ['/login', '/setup']
// No '/share/': share links went with FreeFrame's media features. Leaving a
// prefix here that matches no route is how an unauthenticated hole gets
// re-opened by accident, the day someone adds a page under that name.
const PUBLIC_PREFIXES = ['/invite/']

// Middleware runs on the SERVER, so it needs the container-to-container
// address — NEXT_PUBLIC_API_URL is what the BROWSER uses and is
// `http://localhost:8100` in dev, which from inside this container is
// nothing. The fetch below then throws, the catch swallows it, and a fresh
// install silently never gets redirected to /setup. Same rule as
// lib/site-settings-server.ts, and CLAUDE.md rule 15.
const API_URL =
  process.env.API_INTERNAL_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'http://localhost:8000'

function isPublicRoute(pathname: string): boolean {
  if (PUBLIC_ROUTES.includes(pathname)) return true
  if (PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))) return true
  return false
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl

  // Always allow public routes
  if (isPublicRoute(pathname)) {
    return NextResponse.next()
  }

  // Is this instance set up at all? Cached in a cookie so the API is not
  // asked on every request.
  //
  // `markSetupDone` rather than an early `return NextResponse.next()`. That
  // early return was an AUTH BYPASS: on any request without the cookie —
  // every first request after a deploy, a cookie clear, or its 24h expiry —
  // this branch answered `next()` and the token check below never ran. The
  // API still refused the data, so nothing leaked, but a gate that lets
  // people through on their first knock is not a gate. Found while checking
  // the route list after the port.
  let markSetupDone = false
  const setupDone = request.cookies.get('fb_setup_done')?.value
  if (!setupDone) {
    try {
      const res = await fetch(`${API_URL}/setup/status`, {
        next: { revalidate: 60 }, // Cache for 60 seconds
      })
      if (res.ok) {
        const data = await res.json()
        if (data.needs_setup) {
          return NextResponse.redirect(new URL('/setup', request.url))
        }
        markSetupDone = true
      }
    } catch {
      // API unreachable — fall through. The auth check below still applies,
      // and the page surfaces the error rather than this pretending to know.
    }
  }

  // Check for auth tokens
  const accessToken = request.cookies.get('fb_access_token')?.value
  const refreshToken = request.cookies.get('fb_refresh_token')?.value

  if (!accessToken && !refreshToken) {
    const loginUrl = new URL('/login', request.url)
    loginUrl.searchParams.set('from', pathname)
    return NextResponse.redirect(loginUrl)
  }

  const response = NextResponse.next()
  if (markSetupDone) {
    response.cookies.set('fb_setup_done', '1', { path: '/', maxAge: 60 * 60 * 24 }) // 24 hours
  }
  return response
}

export const config = {
  matcher: [
    /*
     * Match all paths except:
     * - _next/static (static files)
     * - _next/image (image optimization)
     * - favicon.ico
     * - api routes
     * - public assets (images, fonts, etc.)
     */
    '/((?!_next/static|_next/image|favicon\\.ico|api/|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|woff|woff2|ttf|otf)).*)',
  ],
}
