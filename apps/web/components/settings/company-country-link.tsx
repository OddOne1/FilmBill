import Link from 'next/link'
import { TAX_WARNING_HOME_COUNTRY_MISSING } from '@/types'

/**
 * Beside "Your company has no country. Set it in Settings → Company." — the
 * place the sentence names, one click away (B1.2). Rendered for that one
 * warning only: every other tax warning is fixed somewhere else, and a link
 * to the company screen beside "The customer has no country" would send
 * people to the wrong record.
 */
export function CompanyCountryLink({ code }: { code: string }) {
  if (code !== TAX_WARNING_HOME_COUNTRY_MISSING) return null
  return (
    <>
      {' '}
      <Link href="/settings/company" data-testid="company-country-link" className="font-medium underline">
        Open Settings → Company
      </Link>
    </>
  )
}
