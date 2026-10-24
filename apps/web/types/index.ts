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

// ─── Number series ──────────────────────────────────────────────────────────
//
// The number is always the API's. `NumberPreview` is the whole reason the
// preview endpoint exists: the web app never formats or increments a document
// number (CLAUDE.md rule 2), so the screen asks what the next one would be
// rather than working it out.

export type NumberSeries = Schemas['NumberSeriesResponse']
export type NumberSeriesCreate = Schemas['NumberSeriesCreate']
export type NumberSeriesUpdate = Schemas['NumberSeriesUpdate']
export type NumberPreview = Schemas['NumberPreviewResponse']
export type NumberPreviewRequest = Schemas['NumberPreviewRequest']
export type NumberWarning = Schemas['NumberWarningResponse']
/** How a series treats the turn of the year. `yearly` | `never`. */
export type ResetMode = NumberSeries['reset']

// ─── Audit log ──────────────────────────────────────────────────────────────

export type AuditEvent = Schemas['AuditEventResponse']
export type AuditEventPage = Schemas['AuditEventPage']

/** The warning code a back-dated document carries.
 *
 *  Compared as a token, never as prose: the server's sentence is what gets
 *  shown and this is what gets branched on, so a reworded message cannot
 *  silently turn the warning off. Same reasoning as
 *  `ACCOUNT_SETUP_REQUIRED` below. */
export const DOCUMENT_DATE_BEFORE_LATEST = 'document_date_before_latest'

// ─── Tax (P1a) ──────────────────────────────────────────────────────────────
//
// The screen shows a rate; it never works one out. Every rate and every
// proposal on Settings → Company → Tax comes from the API, because the rule
// evaluator is one engine (CLAUDE.md rule 2) and a second one in TypeScript
// would be the one nobody tested against a real pack.
//
// `rate` is a STRING in every one of these, and that is rule 1 arriving at
// the type level: a `number` here would be a float, and the whole chain from
// `NUMERIC(18,4)` through `Decimal` exists to keep it out.

export type TaxCode = Schemas['TaxCodeResponse']
export type TaxCodeUpdate = Schemas['TaxCodeUpdate']
export type TaxClass = Schemas['TaxClassResponse']
export type TaxRule = Schemas['TaxRuleResponse']
export type TaxProposal = Schemas['TaxProposalResponse']
export type TaxProposeRequest = Schemas['TaxProposeRequest']
export type TaxWarning = Schemas['TaxWarningResponse']
export type TaxOverrideRequest = Schemas['TaxOverrideRequest']
export type TaxClassUpdate = Schemas['TaxClassUpdate']
export type TaxRateCreate = Schemas['TaxRateCreate']
export type TaxRateCreated = Schemas['TaxRateResponse']
export type TaxNoTaxCaseCreate = Schemas['TaxNoTaxCaseCreate']
export type NoTaxCategoryOption = Schemas['NoTaxCategoryOption']
export type CountryChangePlan = Schemas['CountryChangePlanResponse']
export type CountryChangeRequest = Schemas['CountryChangeRequest']

/** One of the six no-tax categories a user may pick. Never `S` — a rated code
 *  is a RATE, added the other way — and never `L`/`M`, which carry real rates
 *  and need a region pack rather than a dropdown. */
export type NoTaxCategory = TaxNoTaxCaseCreate['category']

/** What an override does. `code` pins one code; `domestic_for_foreign_business`
 *  is SCOPE §6.3's "use the domestic rate for foreign business buyers". */
export type TaxOverrideKind = TaxOverrideRequest['kind']

/** Branched on, never matched as prose — same reasoning as
 *  `DOCUMENT_DATE_BEFORE_LATEST`. The server owns the sentence; these are the
 *  tokens, and `billing/tax/vocabulary.py::WARNING_CODES` is the list. */
export const TAX_WARNING_VAT_ID_NOT_VERIFIED = 'buyer_vat_id_not_verified'
export const TAX_WARNING_NOTE_NOT_REVIEWED = 'legal_note_not_reviewed'
export const TAX_WARNING_CODE_INACTIVE = 'code_inactive'
export const TAX_WARNING_HOME_COUNTRY_MISSING = 'home_country_missing'
export const TAX_WARNING_BUYER_COUNTRY_MISSING = 'buyer_country_missing'

// ─── Parties (P1b) ──────────────────────────────────────────────────────────
//
// Every resolved field is a value AND its source level, so the screen can
// grey an inherited value and name where it came from. A value with no
// provenance is one somebody has to guess the origin of, and on an invoice
// that guess is expensive.

export type Party = Schemas['PartyResponse']
export type PartyPage = Schemas['PartyPage']
export type PartyCreate = Schemas['PartyCreate']
export type PartyWrite = Schemas['PartyWrite']
export type PartyChild = Schemas['PartyChild']
export type PartyAddress = Schemas['PartyAddressResponse']
export type PartyAddressWrite = Schemas['PartyAddressWrite']
export type PartyContactPoint = Schemas['PartyContactPointResponse']
export type PartyContactPointWrite = Schemas['PartyContactPointWrite']
export type PartyBankAccount = Schemas['PartyBankAccountResponse']
export type PartyBankAccountWrite = Schemas['PartyBankAccountWrite']
export type PartyVatCheck = Schemas['PartyVatCheckResponse']
export type PartyTaxId = Schemas['PartyTaxIdResponse']
export type PartyTaxIdCreate = Schemas['PartyTaxIdCreate']
export type PartyTaxIdUpdate = Schemas['PartyTaxIdUpdate']
export type TaxIdCheckPolicy = Schemas['TaxIdCheckPolicyResponse']
export type TaxIdCheckPolicyUpdate = Schemas['TaxIdCheckPolicyUpdate']
export type ResolvedRecipient = Schemas['ResolvedRecipientResponse']
export type ResolvedField = Schemas['ResolvedFieldResponse']
export type DuplicateWarning = Schemas['DuplicateWarning']
export type PartyTaxPreviewRequest = Schemas['PartyTaxPreviewRequest']

/** `organisation` | `department` | `person`. */
export type PartyType = PartyCreate['type']
/** `auto` | `business` | `private`. */
export type BusinessStatus = NonNullable<PartyWrite['business_status']>

/** The three levels, outermost last — the resolver's own lookup order. */
export const PARTY_LEVELS = ['person', 'department', 'organisation'] as const

/** Branched on, never matched as prose. `invalid` means VIES positively said
 *  so; everything else that is not `valid` is a failure to get an answer. */
export const VAT_VALID = 'valid'
export const VAT_INVALID = 'invalid'
export const VAT_UNAVAILABLE = 'unavailable'
export const VAT_NOT_APPLICABLE = 'not_applicable'
/** P1b-2. Neither is a shade of `invalid`: `not_configured` says the server
 *  lacks a credential this registry needs, and `not_checkable` that no
 *  register exists for this kind at all. Both are facts about us or the
 *  world, not about somebody's number. */
export const VAT_NOT_CONFIGURED = 'not_configured'
export const VAT_NOT_CHECKABLE = 'not_checkable'

/** `vat` | `uid_ch` | `abn` | `ein` | `tax_number` | `company_register` |
 *  `other`. From the generated schema, so a kind added on the server cannot
 *  be missed here. */
export type TaxIdKind = PartyTaxIdCreate['kind']

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
