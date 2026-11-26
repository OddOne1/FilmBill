import type { DeviceCounts } from '@/types'

/**
 * A device's two numbers in words: "3 owned · 2 serial numbers recorded ·
 * 1 not yet recorded".
 *
 * Words and never an error, because the two numbers are allowed to disagree
 * (a unit bought but not yet entered, a unit destroyed but its serial kept
 * on file). The counts are the API's; this only phrases them — nothing here
 * adds, subtracts or corrects.
 */
export function deviceCountsSentence(counts: DeviceCounts): string {
  const parts = [`${counts.owned} owned`]
  parts.push(
    counts.recorded === 0
      ? 'no serial numbers recorded'
      : `${counts.recorded} serial ${counts.recorded === 1 ? 'number' : 'numbers'} recorded`,
  )
  if (counts.needs_check) parts.push(`${counts.needs_check} to be checked`)
  if (counts.in_repair) parts.push(`${counts.in_repair} in repair`)
  if (counts.lost) parts.push(`${counts.lost} lost`)
  if (counts.retired) parts.push(`${counts.retired} retired`)
  if (counts.sold) parts.push(`${counts.sold} sold`)
  if (counts.not_yet_recorded) parts.push(`${counts.not_yet_recorded} not yet recorded`)
  if (counts.beyond_quantity) {
    parts.push(
      `${counts.beyond_quantity} more serial ${
        counts.beyond_quantity === 1 ? 'number' : 'numbers'
      } than owned`,
    )
  }
  return parts.join(' · ')
}

export const UNIT_STATUS_LABELS: Record<string, string> = {
  active: 'Active',
  needs_check: 'Needs check',
  in_repair: 'In repair',
  lost: 'Lost',
  retired: 'Retired',
  sold: 'Sold',
}

/** Gone from the company's hands; coming back asks about the quantity. */
export const REMOVED_STATUSES = ['retired', 'lost', 'sold'] as const

export function isRemoved(status: string): boolean {
  return (REMOVED_STATUSES as readonly string[]).includes(status)
}
