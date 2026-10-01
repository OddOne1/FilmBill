// FilmBill API types.
//
// GENERATED, as of P0b-2. `api.gen.ts` beside this file is written by
// `pnpm gen:api` from the API's own OpenAPI document and must never be
// hand-edited (CLAUDE.md rule 10); CI regenerates it and fails on any diff.
// This file is now a thin naming layer over it: friendly names, the one union
// OpenAPI cannot express, and the handful of types that describe something no
// endpoint ever sends.
//
// **Why a naming layer at all, rather than `components["schemas"]` at each
// call site.** The generated names are the Pydantic class names —
// `UserResponse`, `MemberResponse`, `SendMagicCodeResponse` — and they change
// whenever a schema is renamed server-side. One file absorbing that is one
// file to fix; forty call sites spelling it out is a rename that touches forty
// files and gets abandoned halfway.
//
// **Adding a type:** if the API sends it, add a line here pointing at the
// generated schema. If it does not, put it in the hand-written section at the
// bottom and say why. "The generator could not express it" is not one of the
// reasons — fix the schema instead. That is what `JsonObject` in
// `apps/api/schemas/auth.py` came out of: a bare `dict` generated
// `Record<string, never>`, a map that can hold nothing, and the generator was
// right.

import type { components } from './api.gen'

type Schemas = components['schemas']

// ─── Users and accounts ─────────────────────────────────────────────────────

export type UserGlobalRole = Schemas['UserGlobalRole']
export type UserStatus = Schemas['UserStatus']
export type User = Schemas['UserResponse']

/** What GET /admin/users returns. Its own schema server-side, because P0b
 *  adds per-company role memberships to it and that list has no business in
 *  the response every authenticated caller gets. */
export type AdminUser = Schemas['AdminUserResponse']

/** Which second factor a user enrolled with.
 *
 *  Derived from the field rather than re-typed: the API has no standalone
 *  schema for it, because Pydantic inlines a two-value `Literal`. Derived
 *  means that the day a third method is added, every `switch` over this stops
 *  compiling — which is the point. */
export type TwoFactorMethod = NonNullable<User['two_factor_method']>

/** "missing" | "pending" | "verified" — the password-reset address's state.
 *  Derived for the same reason as `TwoFactorMethod`. */
export type BackupEmailState = User['backup_email_state']

// ─── Setup and sign-in ──────────────────────────────────────────────────────

export type SetupStatus = Schemas['SetupStatusResponse']
export type MagicCodeResponse = Schemas['SendMagicCodeResponse']

/** Tokens. One schema server-side (`TokenResponse`) under two names here,
 *  because the two names mean different things where they are used:
 *  `VerifyCodeResponse` is the success arm of the login union below, and
 *  `AuthTokens` is what the endpoints with no union — /auth/refresh,
 *  /auth/accept-invite — return. */
export type VerifyCodeResponse = Schemas['TokenResponse']
export type AuthTokens = Schemas['TokenResponse']

export type TwoFactorRequiredResponse = Schemas['TwoFactorRequiredResponse']

/** What /auth/login and /auth/verify-magic-code both return. Narrow on
 *  `requires_2fa` — never on whether `access_token` happens to be there.
 *
 *  HAND-WRITTEN, and it has to be: FastAPI declares one `response_model` per
 *  operation, so the generated type is whichever arm was declared. Both arms
 *  above ARE generated; only the fact that they are alternatives lives here. */
export type LoginResponse = VerifyCodeResponse | TwoFactorRequiredResponse

export type SetPasswordResponse = Schemas['SetPasswordResponse']

// ─── Two-factor authentication ──────────────────────────────────────────────

export type TwoFactorSetupRequest = Schemas['TwoFactorSetupRequest']
export type TwoFactorSetupResponse = Schemas['TwoFactorSetupResponse']
export type TwoFactorVerifyRequest = Schemas['TwoFactorVerifyRequest']
export type TwoFactorConfirmRequest = Schemas['TwoFactorConfirmRequest']
export type TwoFactorConfirmResponse = Schemas['TwoFactorConfirmResponse']
export type TwoFactorReauthRequest = Schemas['TwoFactorReauthRequest']
export type TwoFactorDisableResponse = Schemas['TwoFactorDisableResponse']
export type TwoFactorBackupCodesResponse = Schemas['TwoFactorBackupCodesResponse']

// ─── The account-setup gate ─────────────────────────────────────────────────

export type BackupEmailResponse = Schemas['BackupEmailResponse']

/** What GET /auth/password-policy serves. The NUMBERS live on the server;
 *  `lib/password-policy.ts` holds a fallback copy only for the moment before
 *  this arrives. */
export type PasswordPolicy = Schemas['PasswordPolicyResponse']

// ─── Site and email settings ────────────────────────────────────────────────

export type SiteSettingsResponse = Schemas['SiteSettingsResponse']
export type EmailSettingsResponse = Schemas['EmailSettingsResponse']
export type EmailSettingsUpdate = Schemas['EmailSettingsUpdate']
export type TestEmailResponse = Schemas['TestEmailResponse']

// ─── Notifications ──────────────────────────────────────────────────────────

export type NotificationType = Schemas['NotificationType']
export type Notification = Schemas['NotificationResponse']

// ─── Companies ──────────────────────────────────────────────────────────────

export type CompanyRole = Schemas['CompanyRole']
export type CompanySummary = Schemas['CompanySummary']
export type Company = Schemas['CompanyResponse']
export type CompanyCreate = Schemas['CompanyCreate']
export type CompanyUpdate = Schemas['CompanyUpdate']
export type CompanyMember = Schemas['MemberResponse']

/** The accounting selectors. Generated, so the options on the Accounting
 *  screen come from the API rather than from a list somebody retyped — which
 *  is exactly why they are `Literal`s server-side and not `str`. */
export type BookkeepingMode = Company['bookkeeping_mode']
export type VatTiming = Company['vat_timing']
export type ArchiveDateBasis = Company['archive_date_basis']

export type CompanyBankAccount = Schemas['BankAccountResponse']
export type BankAccountCreate = Schemas['BankAccountCreate']
export type BankAccountUpdate = Schemas['BankAccountUpdate']

// ════════════════════════════════════════════════════════════════════════════
// Hand-written, deliberately
//
// Everything below describes something the API does not send, or something it
// sends more loosely than this app can use. Each says which.
// ════════════════════════════════════════════════════════════════════════════

/** The detail string every protected route returns while the account-setup
 *  gate is up.
 *
 *  A `const` value rather than a type, so it cannot come from a schema at all.
 *  `lib/api.ts` surfaces `detail` verbatim and the client compares against
 *  this exact token rather than against prose, which would be a translation
 *  bug waiting to happen. */
export const ACCOUNT_SETUP_REQUIRED = 'account_setup_required'

/** How SMTP encryption is configured: `starttls`, `implicit_tls` or `none`.
 *
 *  The API types this field as a plain `str`, so the generated type is
 *  `string | null` and would tell a form nothing. That looseness is
 *  deliberate server-side and worth keeping: `routers/email_settings.py`
 *  validates the value itself and answers **400 "smtp_security must be one
 *  of: starttls, implicit_tls, none"**. A Pydantic `Literal` would replace
 *  that with pydantic's 422 and a nested `loc`/`msg` blob, which is a worse
 *  thing to show an administrator who mistyped a mode.
 *
 *  So this union is narrower than the API's own type, on purpose, and the two
 *  are kept in step by hand — the values are `SMTP_SECURITY_*` in
 *  `apps/api/services/email_config.py`. */
export type SmtpSecurity = 'starttls' | 'implicit_tls' | 'none'

/** The live password meter's verdict.
 *
 *  Computed in the BROWSER by zxcvbn and never sent by any endpoint, which is
 *  exactly why it is here. Advisory only: the common-password blocklist and
 *  the personal-token rule exist server-side, so `meetsPolicy: true` still
 *  leaves a submission that can be refused. See `lib/password-policy.ts`. */
export interface PasswordStrength {
  /** zxcvbn 0-4. */
  score: number
  label: 'weak' | 'medium' | 'strong'
  /** zxcvbn's own concrete finding, e.g. "This is similar to a commonly used
   *  password". Empty when it has nothing specific to say. */
  reason: string
  /** Everything the BROWSER can check passes. Not "valid" — see above. */
  meetsPolicy: boolean
  /** Which character classes are still missing, in the server's own wording. */
  missing: string[]
}
