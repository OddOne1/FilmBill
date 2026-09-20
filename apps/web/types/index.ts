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

export interface TwoFactorDisableResponse {
  /** Always false after the call. Returned rather than implied so a client
   *  updates from the response instead of assuming the write landed. */
  two_factor_enabled: boolean;
}

export interface TwoFactorBackupCodesResponse {
  /** A fresh set, shown once. Replaces the previous set entirely — the old
   *  codes stop working the moment this returns. */
  backup_codes: string[];
}

export interface TwoFactorConfirmResponse {
  /** Shown ONCE. Nothing can read them back — they are hashed server-side,
   *  so a screen that skips past them has destroyed them. */
  backup_codes: string[];
  method: TwoFactorMethod;
  /** Non-null only when this completed a forced first login; an
   *  already-signed-in user enrolling from settings keeps the tokens they
   *  already hold. */
  tokens: AuthTokens | null;
}

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
