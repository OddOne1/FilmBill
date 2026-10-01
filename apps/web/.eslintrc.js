/**
 * Was `.eslintrc.json`, which could not carry the explanation below — JSON has
 * no comments, and a rule this specific is useless without its reason.
 */
module.exports = {
  extends: 'next/core-web-vitals',
  overrides: [
    {
      // ── No arithmetic in lib/money.ts ────────────────────────────────────
      //
      // CLAUDE.md rule 2: there is ONE calculation engine and it is on the
      // server. `lib/money.ts` is the whole of the web app's money handling
      // and it formats only — so arithmetic is banned there by the linter
      // rather than by a convention somebody has to remember, and rather
      // than by a test that greps the source (rule 11: a grep passes on a
      // comment and fails on an unrelated variable name).
      //
      // The brief for this rule asked for arithmetic on NON-STRING operands.
      // ESLint selectors are syntactic and have no type information, so "is
      // this `+` a string concatenation" cannot be decided here without
      // guessing — and a selector that guesses is wrong in both directions:
      // it lets real arithmetic through when a variable happens to look
      // stringy, and it blocks honest concatenation when it does not. So
      // every arithmetic operator is banned outright and the file uses
      // template literals where it joins strings. Strictly stronger than
      // what was asked, and it costs the file nothing: formatting needs no
      // arithmetic at all, which is the whole point.
      files: ['lib/money.ts'],
      rules: {
        'no-restricted-syntax': [
          'error',
          {
            selector: 'BinaryExpression[operator=/^[+\\-*\\/%]$/]',
            message:
              'No arithmetic in lib/money.ts. Amounts are strings from the API and the server is the only calculation engine (CLAUDE.md rule 2). Use a template literal to join strings; ask the API for any number that has to be computed.',
          },
          {
            selector: "BinaryExpression[operator='**']",
            message: 'No arithmetic in lib/money.ts. See CLAUDE.md rule 2.',
          },
          {
            selector: 'AssignmentExpression[operator=/^([+\\-*\\/%]|\\*\\*)=$/]',
            message: 'No arithmetic in lib/money.ts. See CLAUDE.md rule 2.',
          },
          {
            selector: 'UpdateExpression',
            message: 'No arithmetic in lib/money.ts. See CLAUDE.md rule 2.',
          },
          {
            selector: 'UnaryExpression[operator=/^[+\\-]$/]',
            message:
              'No numeric coercion in lib/money.ts — unary + and - turn a string amount into a double and lose the exact value. See apps/api/core/money.py.',
          },
          {
            selector:
              'CallExpression[callee.name=/^(Number|parseFloat|parseInt|BigInt)$/]',
            message:
              'Do not convert a money string to a number in lib/money.ts. The API sends strings precisely so no value passes through a binary float; Intl.NumberFormat formats a string directly.',
          },
          {
            selector: "MemberExpression[object.name='Math']",
            message:
              'No Math in lib/money.ts. Rounding happens once, on the server, in apps/api/core/money.py (CLAUDE.md rule 1).',
          },
          {
            selector: "CallExpression[callee.property.name='toFixed']",
            message:
              "toFixed rounds a double. Rounding happens on the server (apps/api/core/money.py); use Intl.NumberFormat's fraction-digit options to DISPLAY a given number of places.",
          },
        ],
      },
    },
  ],
}
