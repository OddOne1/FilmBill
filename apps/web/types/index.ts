// FilmBill API types.
//
// HAND-MAINTAINED, AND ONLY UNTIL P0b. CLAUDE.md rule 10 makes these
// generated from the API's OpenAPI schema (`pnpm gen:api`), with CI failing
// on drift — FreeFrame's copy of this file drifted from its backend enough
// times to earn that rule. Nothing new should be added here by hand.

export type UserGlobalRole = "superadmin" | "superuser" | "user";

export type UserStatus = "active" | "deactivated" | "pending_invite" | "pending_verification";

export interface User {
  id: string;
  email: string;
  name: string;
  first_name: string | null;
  last_name: string;
  avatar_url: string | null;
  status: UserStatus;
  role: UserGlobalRole;
  email_verified: boolean;
  invite_token?: string | null;
  preferences: Record<string, unknown>;
  created_at: string;
  deleted_at: string | null;
  /** the user's own second-factor state, as /auth/me reports it.
   *  Read-only: enrolling and disabling go through /auth/2fa/*, which is
   *  where the proofs those actions require are enforced. Optional so an
   *  older cached /auth/me response still validates. */
  two_factor_enabled?: boolean;
  two_factor_method?: TwoFactorMethod | null;
  /** FreeFrame §206 — whether the instance's policy forbids THIS user turning their own
   *  two-factor off. Filled by /auth/me only; `false` everywhere else, which
   *  is the safe default since the server's 403 is the actual rule. */
  two_factor_required?: boolean;
  /** FreeFrame §200 — the onboarding gate, as /auth/me reports it.
   *
   *  Both are DERIVED server-side from the stored data, never from anything
   *  this app remembers, which is why the gate can be trusted: it disappears
   *  the moment the data is real and comes back if the data is cleared.
   *  Rendering the gate from these is presentation only — every protected
   *  route returns 403 `account_setup_required` on its own, so a client that
   *  ignored them would simply be a client that cannot load anything.
   *
   *  Optional so an older cached /auth/me response still validates. Absent is
   *  read as "not gated" at the call sites, matching the server's own
   *  behaviour for a row that predates the columns. */
  must_set_password?: boolean;
  backup_email_state?: BackupEmailState;
  /** The address itself, so the gate and settings can show what is on file.
   *  Only ever returned to the account's own session. */
  backup_email?: string | null;
}

/** What GET /admin/users returns. Identical to User today; it stays its own
 *  name because P0b adds per-company role memberships to it (CLAUDE.md rule
 *  6) and that list has no business in the response every authenticated
 *  caller gets. */
export type AdminUser = User;

export interface ApiError {
  detail: string;
  status_code: number;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}

export interface SetupStatus {
  needs_setup: boolean;
}

export interface MagicCodeResponse {
  message: string;
}

export interface VerifyCodeResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  needs_password: boolean;
  /** always present and always false on this arm. The backend adds
   *  it to the SUCCESS shape as well as the 2FA one on purpose: a
   *  discriminator that appears in only one arm is one a careless client
   *  reads as `undefined` and treats as falsy by accident rather than by
   *  decision. Required here for the same reason — the union below is only
   *  narrowable if both arms declare it. */
  requires_2fa: false;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  /** Present on every TokenResponse the API returns . Optional here
   *  only because this type also describes endpoints whose response this
   *  app does not narrow on — /auth/accept-invite, /auth/refresh. Where the
   *  union matters, use LoginResponse. */
  requires_2fa?: false;
}

/** Which second factor a user enrolled with. "email" started as a fallback
 *  for a lost authenticator  and became selectable as the primary
 *  method later. */
export type TwoFactorMethod = 'totp' | 'email';

/** Password (or magic code) accepted, second factor outstanding.
 *
 *  Carries no tokens: the caller holds a `pending_token` that is inert
 *  everywhere except /auth/2fa/verify-login and the enrolment endpoints. */
export interface TwoFactorRequiredResponse {
  requires_2fa: true;
  /** True when this user has NEVER enrolled and the instance requires it —
   *  the pending token has to be redeemed through enrolment, not through
   *  /auth/2fa/verify-login. */
  setup_required: boolean;
  pending_token: string;
  /** Which code to ask for. Null exactly when `setup_required` is true: the
   *  user has not chosen a method yet, and choosing is what the enrolment
   *  screen is for. */
  method: TwoFactorMethod | null;
  /** true when a code has ALREADY been mailed as part of this
   *  response (email-primary users only), so the screen can say "we've sent
   *  you a code" instead of offering to send one that is already in
   *  flight. */
  email_code_sent: boolean;
}

/** What /auth/login and /auth/verify-magic-code both return. Narrow on
 *  `requires_2fa` — never on whether `access_token` happens to be there. */
export type LoginResponse = VerifyCodeResponse | TwoFactorRequiredResponse;

export interface TwoFactorSetupRequest {
  /** Mid-login enrolment; omitted when an already-signed-in user enrols
   *  from settings. */
  pending_token?: string;
  method: TwoFactorMethod;
  /** proof of the CURRENT factor, required only when the user is
   *  already enrolled and is replacing what they have. */
  reauth_code?: string;
}

export interface TwoFactorSetupResponse {
  method: TwoFactorMethod;
  /** TOTP only. */
  provisioning_uri?: string;
  /** TOTP only — already a data: URI, usable directly as an <img src>. */
  qr_code_data_uri?: string;
  /** TOTP only — the secret in plaintext, for manual entry. */
  secret?: string;
  /** Email only. False when a code was already outstanding and this call
   *  therefore did not send a second one (per-TTL idempotency). */
  email_code_sent: boolean;
}

export interface TwoFactorVerifyRequest {
  pending_token: string;
  code: string;
}

export interface TwoFactorConfirmRequest {
  pending_token?: string;
  code: string;
}

/** Proof that the caller still holds a second factor .
 *
 *  Accepts any of the three forms the login path accepts — authenticator,
 *  emailed fallback, backup code — because the user does not reliably know
 *  which kind they are holding. Required by the two self-service actions
 *  that WEAKEN an account (disable, regenerate) and, by
 *  starting a replacement enrolment. */
export interface TwoFactorReauthRequest {
  code: string;
}

/**
 * What POST /auth/set-password returns.
 *
 * Everything /auth/me's User carries, plus a replacement token pair: setting
 * or changing a password bumps `token_version`, which ends every session the
 * user holds including the one that made the call.
 *
 * It also closes a mismatch that predates FreeFrame §199. `login-form.tsx` has always
 * typed this response as `AuthTokens` and called `setTokens(res.access_token,
 * res.refresh_token)` on it, while the endpoint returned only a user — so it
 * wrote the literal string "undefined" over the tokens the magic-code step
 * had set moments earlier. Those fields now genuinely exist.
 */
export interface SetPasswordResponse extends User {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface TwoFactorDisableResponse {
  /** Always false after the call. Returned rather than implied so a client
   *  updates from the response instead of assuming the write landed. */
  two_factor_enabled: boolean;
  /** FreeFrame §199 — a replacement pair for the session that made this call.
   *
   *  Disabling 2FA bumps the user's `token_version`, which ends every
   *  session they hold, including this one. Adopt these or the next request
   *  this tab makes is a 401. */
  tokens: AuthTokens | null;
}

export interface TwoFactorBackupCodesResponse {
  /** A fresh set, shown once. Replaces the previous set entirely — the old
   *  codes stop working the moment this returns. */
  backup_codes: string[];
  /** FreeFrame §199 — see TwoFactorDisableResponse.tokens; identical reasoning. */
  tokens: AuthTokens | null;
}

export interface TwoFactorConfirmResponse {
  /** Shown ONCE. Nothing can read them back — they are hashed server-side,
   *  so a screen that skips past them has destroyed them. */
  backup_codes: string[];
  method: TwoFactorMethod;
  /** FreeFrame §199 — now populated on BOTH branches, not only a forced first login.
   *  Confirming enrolment bumps `token_version`, so an already-signed-in
   *  user's existing tokens are stale as of this response and these are the
   *  replacements. Still typed nullable: a client that adopts them only when
   *  present keeps working against an older API. */
  tokens: AuthTokens | null;
}

// ─── Account security gate ─────────────────────────────────────────────

/** Where this account stands on having a usable password-reset channel.
 *
 *  "missing"  — no backup address at all.
 *  "pending"  — an address is stored but unproved. Still gated: a reset sent
 *               to an address that may not exist is worse than none, because
 *               it looks like a recovery path.
 *  "verified" — a code sent to it came back. The only state in which anything
 *               is ever mailed there. */
export type BackupEmailState = 'missing' | 'pending' | 'verified';

/** The detail string every protected route returns while the gate is up.
 *
 *  A stable token, not a sentence — `lib/api.ts` surfaces `detail` verbatim,
 *  and comparing against prose is a translation bug waiting to happen. */
export const ACCOUNT_SETUP_REQUIRED = 'account_setup_required';

export interface BackupEmailResponse {
  backup_email: string;
  state: BackupEmailState;
  /** True when this call actually sent a code. */
  code_sent: boolean;
  /** The backup address is on the same mail domain as the login address.
   *  Accepted, but worth saying out loud — one admin with domain-wide access
   *  can read both mailboxes, which is the thing the split exists to prevent.
   *  The wording lives in the UI; the server only reports the fact. */
  same_domain: boolean;
}

/** What GET /auth/password-policy serves. The NUMBERS live on the server;
 *  `lib/password-policy.ts` holds a fallback copy only for the moment before
 *  this arrives. */
export interface PasswordPolicy {
  min_length: number;
  min_strength_score: number;
  requires_upper: boolean;
  requires_lower: boolean;
  requires_digit: boolean;
  requires_special: boolean;
}

/** The live meter's verdict. Advisory — see lib/password-policy.ts. */
export interface PasswordStrength {
  /** zxcvbn 0-4. */
  score: number;
  label: 'weak' | 'medium' | 'strong';
  /** zxcvbn's own concrete finding, e.g. "This is similar to a commonly used
   *  password". Empty when it has nothing specific to say. */
  reason: string;
  /** Everything the BROWSER can check passes. Not "valid": the common-password
   *  blocklist and the personal-token rule exist only server-side, so a true
   *  here still leaves a submission that can be refused. */
  meetsPolicy: boolean;
  /** Which character classes are still missing, in the server's own wording. */
  missing: string[];
}

// ─── Site Settings ────────────────────────────────────────────────────────────

export interface SiteSettingsResponse {
  org_name: string;
  logo_dark_url: string | null;
  logo_light_url: string | null;
  logo_login_url: string | null;
  favicon_url: string | null;
  theme_colors: Record<string, unknown> | null;
  /** IANA zone deciding when the daily maintenance jobs run .
   *  Defaults to "UTC" server-side, so this is never absent in practice —
   *  optional only so an older cached response still validates. */
  timezone?: string;
  /** whether every user on this instance must have 2FA. Public: this
   *  endpoint is unauthenticated (it backs the login page's branding), and
   *  the login screen needs it to decide which sign-in method to offer. */
  require_2fa?: boolean;
}

/** How the SMTP connection is encrypted. Three modes, because there are
 *  three real arrangements — the boolean this supplements could only express
 *  two, and read `false` as implicit TLS rather than as "no TLS", which made
 *  a plaintext relay unreachable. */
export type SmtpSecurity = 'starttls' | 'implicit_tls' | 'none'

/** Mirrors EmailSettingsResponse in apps/api/schemas/email_settings.py.
 *  Note there are no password fields — secrets are reported only as
 *  `*_set` booleans and never sent to the client. */
export interface EmailSettingsResponse {
  mail_provider: string | null
  mail_from_address: string | null
  mail_from_name: string | null
  aws_mail_access_key_id: string | null
  aws_mail_secret_access_key_set: boolean
  aws_mail_region: string | null
  smtp_host: string | null
  smtp_port: number | null
  smtp_user: string | null
  smtp_password_set: boolean
  smtp_use_tls: boolean | null
  /** FreeFrame §199 — the STORED mode, or null when it has never been set explicitly. */
  smtp_security: SmtpSecurity | null
  /** And what would actually be used right now, after the env fallback and
   *  the smtp_use_tls derivation. An empty stored value must not be read as
   *  "no encryption" when the real answer is "STARTTLS, by default". */
  effective_smtp_security: SmtpSecurity | null
  /** What's actually in effect once DB-over-env precedence is applied. */
  effective_provider: string | null
  effective_from_address: string | null
  effective_smtp_host: string | null
  using_env_fallback: boolean
}

export interface EmailSettingsUpdate {
  mail_provider?: string | null
  mail_from_address?: string | null
  mail_from_name?: string | null
  aws_mail_access_key_id?: string | null
  aws_mail_secret_access_key?: string
  aws_mail_region?: string | null
  smtp_host?: string | null
  smtp_port?: number | null
  smtp_user?: string | null
  smtp_password?: string
  smtp_use_tls?: boolean | null
  smtp_security?: SmtpSecurity | null
  smtp_password_clear?: boolean
  aws_mail_secret_access_key_clear?: boolean
}

export interface TestEmailResponse {
  success: boolean
  detail: string
}

// ─── Notifications ───────────────────────────────────────────────────────────

/** Mirrors NotificationType in apps/api/models/activity.py. One case today;
 *  each business event that starts notifying adds one on both sides. */
export type NotificationType = "account";

export interface Notification {
  id: string;
  type: NotificationType;
  title: string;
  body: string | null;
  /** Relative in-app path, or null when there is nowhere to go. */
  link: string | null;
  read: boolean;
  created_at: string;
}
