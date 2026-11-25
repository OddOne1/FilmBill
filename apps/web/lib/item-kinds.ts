import type { ItemKind } from '@/types'

/**
 * The item kinds (SCOPE §4.1) and their plain names, for screens that show
 * or pick a kind. The vocabulary itself is the API's
 * (`apps/api/billing/catalog/kinds.py`); this file only names it.
 *
 * `ITEM_KIND_SHORT` is the one-word form an adjustment's scope is written
 * in — "10 % off · equipment · whole budget" — where "Equipment rental"
 * would make the sentence longer than the thing it describes.
 */
export const ITEM_KIND_LABELS: Record<ItemKind, string> = {
  labor: 'Crew & labour',
  equipment_rental: 'Equipment rental',
  space_rental: 'Space rental',
  travel: 'Travel',
  per_diem: 'Per diem',
  storage: 'Storage',
  product: 'Product',
  software_license: 'Software licence',
  fee: 'Fee',
  other: 'Other',
}

export const ITEM_KINDS = Object.keys(ITEM_KIND_LABELS) as ItemKind[]

export const ITEM_KIND_SHORT: Record<ItemKind, string> = {
  labor: 'crew',
  equipment_rental: 'equipment',
  space_rental: 'space',
  travel: 'travel',
  per_diem: 'per diem',
  storage: 'storage',
  product: 'product',
  software_license: 'software',
  fee: 'fees',
  other: 'other',
}

export function kindShort(kind: string): string {
  return ITEM_KIND_SHORT[kind as ItemKind] ?? kind
}
