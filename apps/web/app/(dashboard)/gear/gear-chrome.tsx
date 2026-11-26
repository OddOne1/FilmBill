'use client'

import { usePermissions } from '@/hooks/use-permissions'

/**
 * What every gear screen shares: who may do what, and the sentences that say
 * so (rule 17c — no dead button, no silent screen).
 */
export function useGearAccess() {
  const { can, loaded } = usePermissions()
  return {
    loaded,
    canView: can('devices.view'),
    canEdit: can('devices.edit'),
    /** Purchase prices are cost information: `budgets.view`. Presentation
     *  only — the API withholds the price itself and says `price_visible`. */
    canSeePrice: can('budgets.view'),
    refused: loaded && !can('devices.view'),
  }
}

export function GearNoAccess({ title = 'Gear' }: { title?: string }) {
  return (
    <div className="p-8">
      <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
      <p data-testid="gear-no-access" className="mt-2 text-sm text-text-secondary">
        You do not have access to this company&rsquo;s gear list. An owner or
        administrator can change that under Settings → Company → Members.
      </p>
    </div>
  )
}

export function GearReadOnlyNotice() {
  return (
    <p
      data-testid="gear-read-only"
      className="mt-4 rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary"
    >
      You can see the gear but not change it — adding devices or serial
      numbers needs the gear edit permission. An owner or administrator can
      grant it under Settings → Company → Members.
    </p>
  )
}

/** "65.000,50" or "65000,50" as typed → "65000.50" for the API, which wants a
 *  dot. A separator swap only — never arithmetic; anything else is sent as
 *  typed and the API answers with a sentence. */
export function amountForApi(text: string): string | null {
  const trimmed = text.replace(/\s/g, '').replace(/€|EUR/gi, '')
  if (!trimmed) return null
  if (trimmed.includes(',')) return trimmed.replace(/\./g, '').replace(',', '.')
  return trimmed
}

/** `1.500` is fifteen hundred to an Austrian and one and a half to the API.
 *  Refused with a sentence rather than guessed, as the CSV imports do. */
export function amountIsAmbiguous(text: string): boolean {
  return /^-?\d{1,3}(\.\d{3})+$/.test(text.replace(/\s/g, ''))
}

export const AMBIGUOUS_AMOUNT =
  'Write the price without the dot (1500) or with a decimal comma (1.500,00) — 1.500 could mean either.'
