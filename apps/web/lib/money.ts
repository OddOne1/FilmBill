/**
 * Displaying money and quantities. **Formatting only — no arithmetic, ever.**
 *
 * CLAUDE.md rule 2: there is one calculation engine and it is on the server.
 * The web app never computes money; it asks the API for a preview and renders
 * what comes back. This file is the whole of the web's money handling, and it
 * is deliberately small enough to read in one sitting.
 *
 * The rule is enforced by an ESLint `no-restricted-syntax` override scoped to
 * this path (see `.eslintrc.json`), not by a test that greps the source —
 * CLAUDE.md rule 11. Adding `a + b` here fails `pnpm lint`.
 *
 * **Everything is a string, start to finish.** The API sends amounts as
 * strings precisely so they never pass through a binary float
 * (`apps/api/core/money.py` explains why), and turning one into a `number`
 * here to format it would throw that away at the last possible moment —
 * which is the version of this bug that is hardest to see, because it only
 * shows up on the values with enough digits to matter.
 *
 * `Intl.NumberFormat.prototype.format` accepts a **string** argument
 * (Intl.NumberFormat v3: Chrome 106+, Safari 15.4+, Firefox 116+, Node 18.14+
 * — CI runs Node 20) and formats it exactly, without going through a double.
 * That is the single fact this file is built on. A number is never
 * constructed.
 */

/** Places the API uses for money (`NUMERIC(18,2)`). */
const AMOUNT_PLACES = 2
/** Places the API uses for quantities and rates (`NUMERIC(18,4)`). */
const QTY_PLACES = 4

/**
 * What to show when a value is missing or unreadable.
 *
 * An em-dash rather than "0.00", and the difference is not cosmetic: a total
 * that failed to load must not render as a real amount of zero euros. Someone
 * would believe it.
 */
export const NO_VALUE = '—'

/**
 * `Intl.NumberFormat.prototype.format` accepts a string at RUNTIME and does
 * not in TypeScript's type for it.
 *
 * The lib declares `format(value: number | bigint | StringNumericLiteral)`,
 * and `StringNumericLiteral` is a template-literal type: it matches string
 * *literals* that look numeric and rejects a `string` whose value is only
 * known at run time — which is every amount this file is given. The runtime
 * behaviour is Intl.NumberFormat v3 and is exactly what this file is built on
 * (see the module docstring).
 *
 * So the cast is here, once, named, with the reason attached — rather than at
 * each of the three call sites, where the next reader would have to work out
 * whether it was load-bearing or a shortcut. It is not a `Number()` in
 * disguise: nothing is converted, the string is passed through and Intl parses
 * it exactly.
 */
function asIntlValue(decimal: string): number {
  return decimal as unknown as number
}

/**
 * Whether a string is something `Intl` can format.
 *
 * A character check, not a parse: this is exactly the set of shapes the API's
 * `Money` serialiser can emit (optional sign, digits, optional fraction).
 * Anything else — an empty string, `null` arriving as `"null"`, scientific
 * notation, a stray currency symbol — falls through to `NO_VALUE` rather than
 * to whatever `Intl` decides to do with it.
 */
function isDecimalString(value: unknown): value is string {
  return typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value.trim())
}

/**
 * Format an amount with its currency.
 *
 * `currency` is the company's ISO-4217 code — always passed, never defaulted
 * to EUR. One installation can host companies in two currencies (see
 * `Company.default_currency`), and a default here would render Swiss francs
 * with a euro sign on the day the second company is created.
 */
export function formatMoney(
  amount: string | null | undefined,
  currency: string,
  locale?: string,
): string {
  if (!isDecimalString(amount)) return NO_VALUE
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency,
      minimumFractionDigits: AMOUNT_PLACES,
      maximumFractionDigits: AMOUNT_PLACES,
    }).format(asIntlValue(amount.trim()))
  } catch {
    // An unknown currency code throws RangeError. Showing the raw value with
    // the code beside it is worse than a formatted amount and much better
    // than a crashed render.
    return `${amount} ${currency}`
  }
}

/**
 * Format an amount with no currency symbol — for a column that carries the
 * currency in its header rather than on every row.
 */
export function formatAmount(
  amount: string | null | undefined,
  locale?: string,
): string {
  if (!isDecimalString(amount)) return NO_VALUE
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: AMOUNT_PLACES,
    maximumFractionDigits: AMOUNT_PLACES,
  }).format(asIntlValue(amount.trim()))
}

/**
 * Format a quantity or rate.
 *
 * Trailing zeros are **trimmed** here and nowhere else: "3 days" reads better
 * than "3.0000 days", and unlike an amount, a quantity's scale carries no
 * meaning a reader needs. `maximumFractionDigits` does the trimming, so no
 * string surgery and no arithmetic.
 */
export function formatQuantity(
  quantity: string | null | undefined,
  locale?: string,
): string {
  if (!isDecimalString(quantity)) return NO_VALUE
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: QTY_PLACES,
  }).format(asIntlValue(quantity.trim()))
}

/**
 * Whether an amount is negative — for choosing a colour or a sign, never for
 * deciding a number.
 *
 * A string test, because that is all it needs to be: the API's serialiser
 * emits a leading `-` and nothing else. `"-0.00"` reads as not negative,
 * which is the right answer for a credit that rounded to nothing.
 */
export function isNegativeAmount(amount: string | null | undefined): boolean {
  if (!isDecimalString(amount)) return false
  return amount.trim().startsWith('-') && /[1-9]/.test(amount)
}
