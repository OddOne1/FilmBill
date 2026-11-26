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
  if (counts.in_repair) parts.push(`${counts.in_repair} in repair`)
  if (counts.retired) parts.push(`${counts.retired} retired`)
  if (counts.not_yet_recorded) parts.push(`${counts.not_yet_recorded} not yet recorded`)
  if (counts.beyond_quantity) {
    parts.push(
      `${counts.beyond_quantity} more usable serial ${
        counts.beyond_quantity === 1 ? 'number' : 'numbers'
      } than owned`,
    )
  }
  return parts.join(' · ')
}

export const RETIRED_REASON_LABELS: Record<string, string> = {
  destroyed: 'Destroyed',
  lost: 'Lost',
  sold: 'Sold',
  kept: 'Kept (not usable)',
}

export const UNIT_STATUS_LABELS: Record<string, string> = {
  active: 'Active',
  in_repair: 'In repair',
  retired: 'Retired',
}
