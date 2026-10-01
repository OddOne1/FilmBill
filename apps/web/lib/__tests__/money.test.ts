import { describe, it, expect } from 'vitest'
import {
  NO_VALUE,
  formatAmount,
  formatMoney,
  formatQuantity,
  isNegativeAmount,
} from '../money'

/**
 * `lib/money.ts` formats and never computes. These assert the formatting; the
 * "never computes" half is enforced by the ESLint override in `.eslintrc.js`
 * and demonstrated in the P0b-2 report, not by a test that greps the source
 * (CLAUDE.md rule 11).
 *
 * The locale is pinned on every call. Without it `Intl` uses the runtime's,
 * which differs between a developer's Mac, CI and a browser — and a test whose
 * expected string depends on that is a test that fails for the wrong reason.
 *
 * Non-breaking and narrow-no-break spaces: `Intl` separates a currency symbol
 * from its number with U+00A0 in de-AT and groups thousands with U+202F. The
 * assertions normalise those rather than pasting invisible characters into
 * source, which is how this kind of test becomes unmaintainable.
 */

/** Every Unicode space to a plain one, so an assertion is readable. */
function plain(value: string): string {
  return value.replace(/[   ]/g, ' ')
}

describe('formatMoney', () => {
  it('formats with the currency the company uses', () => {
    expect(plain(formatMoney('1250.00', 'EUR', 'de-AT'))).toBe('€ 1.250,00')
    // de-CH groups with a straight apostrophe. One installation can host
    // companies in two currencies, which is why `currency` is always passed
    // and never defaulted to EUR.
    expect(plain(formatMoney('1250.00', 'CHF', 'de-CH'))).toBe("CHF 1'250.00")
  })

  it('always shows two decimal places', () => {
    // "12.3" from an API that got lazy, and a whole-euro amount. An invoice
    // line reading €12,3 is a bug report waiting to be filed.
    expect(plain(formatMoney('12.3', 'EUR', 'de-AT'))).toBe('€ 12,30')
    expect(plain(formatMoney('12', 'EUR', 'de-AT'))).toBe('€ 12,00')
  })

  it('is exact for a value a double would mangle', () => {
    // The reason the whole chain is strings. As a JS number this is
    // 12345678901234.56 → 12345678901234.56 but one more digit loses it;
    // Intl formats the string without ever constructing a number.
    expect(plain(formatMoney('12345678901234.56', 'EUR', 'de-AT'))).toBe(
      '€ 12.345.678.901.234,56',
    )
  })

  it('formats a negative amount', () => {
    expect(plain(formatMoney('-99.95', 'EUR', 'de-AT'))).toBe('-€ 99,95')
  })

  it('shows a dash rather than a zero for a missing value', () => {
    // NOT "0,00". A total that failed to load must not render as a real
    // amount of zero euros — somebody would believe it.
    expect(formatMoney(null, 'EUR', 'de-AT')).toBe(NO_VALUE)
    expect(formatMoney(undefined, 'EUR', 'de-AT')).toBe(NO_VALUE)
    expect(formatMoney('', 'EUR', 'de-AT')).toBe(NO_VALUE)
  })

  it('refuses anything that is not a plain decimal string', () => {
    for (const junk of ['1,250.00', '€12.30', 'NaN', '1e3', 'twelve', '12.3.4']) {
      expect(formatMoney(junk, 'EUR', 'de-AT')).toBe(NO_VALUE)
    }
  })

  it('falls back readably on an unknown currency code', () => {
    // Intl throws RangeError on these. Showing the raw value with the code
    // beside it is worse than a formatted amount and far better than a
    // crashed render.
    expect(formatMoney('10.00', 'XYZZY', 'de-AT')).toBe('10.00 XYZZY')
  })
})

describe('formatAmount', () => {
  it('formats without a currency symbol, for a column that has one in its header', () => {
    // A space group separator, not a dot — `de-AT` groups a plain decimal with
    // U+202F and a CURRENCY amount with a full stop. Two different separators
    // in one locale, which is exactly the kind of thing that makes rolling
    // your own formatter a mistake.
    expect(plain(formatAmount('1250.5', 'de-AT'))).toBe('1 250,50')
  })
})

describe('formatQuantity', () => {
  it('trims trailing zeros, unlike an amount', () => {
    // "3 days" reads better than "3,0000 days", and unlike money a quantity's
    // scale carries no meaning the reader needs.
    expect(plain(formatQuantity('3.0000', 'de-AT'))).toBe('3')
    expect(plain(formatQuantity('1.5000', 'de-AT'))).toBe('1,5')
  })

  it('keeps up to four places', () => {
    expect(plain(formatQuantity('0.1234', 'de-AT'))).toBe('0,1234')
  })

  it('shows a dash for a missing value', () => {
    expect(formatQuantity(null, 'de-AT')).toBe(NO_VALUE)
  })
})

describe('isNegativeAmount', () => {
  it('reads the sign off the string', () => {
    expect(isNegativeAmount('-1.00')).toBe(true)
    expect(isNegativeAmount('1.00')).toBe(false)
  })

  it('treats a credit that rounded to nothing as not negative', () => {
    // "-0.00" is zero. Colouring it red and prefixing a minus would be a
    // statement about an amount that is not there.
    expect(isNegativeAmount('-0.00')).toBe(false)
  })

  it('is false for anything unreadable', () => {
    expect(isNegativeAmount(null)).toBe(false)
    expect(isNegativeAmount('-')).toBe(false)
  })
})
