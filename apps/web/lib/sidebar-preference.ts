/**
 * Whether this browser keeps the sidebar collapsed (UI-1 part 1).
 *
 * **Expanded is the default** for anybody who has never chosen. The choice
 * is a per-viewer convenience, so it lives in `localStorage` and every
 * access is wrapped: storage that is full, disabled or blocked in private
 * mode answers "expanded", never an error.
 *
 * **The key is versioned.** Until UI-1 the sidebar started collapsed, so any
 * "collapsed" a browser might hold under an earlier name says what it was
 * given rather than what its user chose. A new key means everybody starts
 * expanded once; the old names are listed so a test can prove they are
 * ignored, and are never read.
 */

export const SIDEBAR_COLLAPSED_KEY = 'filmbill.sidebar.collapsed.v2'

/** Names an earlier build could have used. Never read; see above. */
export const RETIRED_SIDEBAR_KEYS = ['filmbill.sidebar.collapsed', 'fb-sidebar-collapsed']

export function loadSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

export function saveSidebarCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0')
  } catch {
    // Not remembered; the choice still applies until the page is left.
  }
}
