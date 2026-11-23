import type { ItemCategory } from '@/types'

/**
 * Nesting a flat category list.
 *
 * Its own module and not a function inside `page.tsx`, because a Next.js
 * page file may export nothing but the page and its route config — a named
 * export there compiles under `tsc` and under vitest and then fails
 * `next build` with "is not a valid Page export field". That is the gap
 * `pnpm ci:web` exists to close, and this is the second time it has caught
 * something macOS and the unit tests both let through (CLAUDE.md, local
 * development).
 */

export interface CategoryNode {
  category: ItemCategory
  children: CategoryNode[]
}

/** Nest by `parent_id`, keeping the server's order within each level.
 *
 *  **By `parent_id`, never by splitting `path`.** The API sends both, and
 *  `path` ("Equipment › Camera › Cinema") is the right thing to show on an
 *  item — but a category name may itself contain the separator, so reading
 *  the hierarchy out of the display string would invent levels.
 *
 *  A parent that is not in the list (switched off while its child is on)
 *  leaves its child at the top rather than dropping it: a category you
 *  cannot see is a category you cannot fix. */
export function buildTree(categories: ItemCategory[]): CategoryNode[] {
  const nodes = new Map<string, CategoryNode>()
  for (const category of categories) {
    nodes.set(category.id, { category, children: [] })
  }
  const roots: CategoryNode[] = []
  for (const category of categories) {
    const node = nodes.get(category.id)!
    const parent = category.parent_id ? nodes.get(category.parent_id) : null
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  return roots
}
