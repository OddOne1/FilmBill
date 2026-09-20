"use client";

import * as React from "react";
import useSWR, { mutate } from "swr";
import * as Dialog from "@radix-ui/react-dialog";
import * as Popover from "@radix-ui/react-popover";
import {
  Users,
  X,
  Shield,
  Link2,
  Check,
  Search,
  Mail,
  Send,
  Clock,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ResetTwoFactorButton } from "@/components/settings/reset-two-factor-button";
import { Avatar } from "@/components/shared/avatar";
import { RequireTwoFactorSection } from "@/components/settings/require-two-factor-section";
import { EmptyState } from "@/components/shared/empty-state";
import { CollapsibleSection } from "@/components/shared/collapsible-section";
import { PlainTh, SortableTh, useSort } from "@/components/shared/sortable";
import { useAuthStore } from "@/stores/auth-store";
import { useSiteSettings } from "@/hooks/use-site-settings";
import { useEmailSettings } from "@/hooks/use-email-settings";
import { useRouter } from "next/navigation";
import type { UserStatus, AdminUser } from "@/types";

function BulkInviteDialog() {
  const [open, setOpen] = React.useState(false);
  const [emails, setEmails] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [success, setSuccess] = React.useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const emailList = emails
      .split(/[\n,]/)
      .map((e) => e.trim())
      .filter(Boolean);
    if (emailList.length === 0) return;
    setLoading(true);
    setError("");
    setSuccess("");
    let sent = 0;
    const skipped: string[] = [];
    const failed: string[] = [];
    try {
      for (const email of emailList) {
        try {
          const name = email.split("@")[0];
          await api.post("/users/invite", { email, name });
          sent++;
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : "";
          if (msg.toLowerCase().includes("already registered")) {
            skipped.push(email);
          } else {
            failed.push(email);
          }
        }
      }
      const parts: string[] = [];
      if (sent > 0) parts.push(`${sent} invite(s) sent`);
      if (skipped.length > 0)
        parts.push(`${skipped.length} already registered`);
      if (failed.length > 0) parts.push(`${failed.length} failed`);
      if (sent > 0 || skipped.length > 0) {
        setSuccess(parts.join(", "));
        if (failed.length === 0) {
          setEmails("");
          setTimeout(() => setOpen(false), 1500);
        }
      }
      if (failed.length > 0) {
        setError(`Failed to invite: ${failed.join(", ")}`);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to send invites");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button variant="secondary" size="sm">
          <Users className="h-4 w-4" />
          Bulk Invite
        </Button>
      </Dialog.Trigger>

      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-bg-secondary p-6 shadow-xl data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95">
          <Dialog.Close className="absolute right-4 top-4 text-text-tertiary hover:text-text-primary transition-colors">
            <X className="h-4 w-4" />
          </Dialog.Close>

          <Dialog.Title className="text-base font-semibold text-text-primary">
            Bulk Invite Users
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-text-secondary">
            Enter email addresses separated by commas or newlines.
          </Dialog.Description>

          <form onSubmit={handleSubmit} className="mt-4 space-y-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium text-text-secondary">
                Email addresses
              </label>
              <textarea
                value={emails}
                onChange={(e) => setEmails(e.target.value)}
                placeholder="user1@example.com&#10;user2@example.com"
                rows={5}
                className="flex w-full rounded-md border border-border bg-bg-secondary px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary transition-colors focus:outline-none focus:border-border-focus focus:ring-1 focus:ring-border-focus resize-none"
              />
            </div>
            {error && <p className="text-xs text-status-error">{error}</p>}
            {success && (
              <p className="text-xs text-status-success">{success}</p>
            )}
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setOpen(false)}
              >
                Close
              </Button>
              <Button type="submit" size="sm" loading={loading}>
                Send Invites
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function useTimeZones(current: string): string[] {
  return React.useMemo(() => {
    let zones: string[] = [];
    try {
      zones = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] })
        .supportedValuesOf?.("timeZone") ?? [];
    } catch {
      zones = [];
    }
    if (zones.length === 0) {
      zones = [
        "UTC", "Europe/Vienna", "Europe/London", "Europe/Berlin",
        "America/New_York", "America/Los_Angeles", "Asia/Kolkata", "Asia/Tokyo",
        "Australia/Sydney",
      ];
    }
    // The saved value always appears, even if this browser has never heard
    // of it — otherwise a <select> silently re-points at its first option
    // and the next Save would change a setting nobody touched.
    if (!zones.includes(current)) zones = [current, ...zones];
    if (!zones.includes("UTC")) zones = ["UTC", ...zones];
    return zones;
  }, [current]);
}

function TimezoneSection() {
  const { timezone, updateTimezone } = useSiteSettings();
  const [value, setValue] = React.useState(timezone);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState(false);
  const zones = useTimeZones(timezone);

  React.useEffect(() => {
    setValue(timezone);
  }, [timezone]);

  // The point of showing this: "03:00 in Europe/Vienna" is abstract, and
  // the current local time makes it concrete enough to catch a wrong pick.
  const nowThere = React.useMemo(() => {
    try {
      return new Intl.DateTimeFormat(undefined, {
        timeZone: value, hour: "2-digit", minute: "2-digit", timeZoneName: "short",
      }).format(new Date());
    } catch {
      return null;
    }
  }, [value]);

  const handleSave = async () => {
    setError("");
    setSaving(true);
    try {
      await updateTimezone(value);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update timezone");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="rounded-lg border border-border bg-bg-secondary p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Clock className="h-4 w-4 text-text-tertiary" />
        <h2 className="text-sm font-semibold text-text-primary">Timezone</h2>
      </div>
      <p className="text-xs text-text-secondary">
        Scheduled maintenance runs overnight in this timezone — the Recently Deleted purge at
        3:00 AM and the storage reconciliation sweep at 3:45 AM. Takes effect within 15 minutes;
        no restart needed.
        {nowThere && <> It&apos;s currently <strong>{nowThere}</strong> there.</>}
      </p>
      <div className="flex items-center gap-2">
        <label
          htmlFor="platform-timezone"
          className="text-xs font-medium text-text-tertiary whitespace-nowrap"
        >
          Platform Timezone
        </label>
        <select
          id="platform-timezone"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setError("");
          }}
          className="h-8 w-64 rounded-md border border-border bg-bg-secondary px-2 text-xs text-text-primary focus:outline-none focus:border-border-focus"
        >
          {zones.map((z) => (
            <option key={z} value={z}>{z}</option>
          ))}
        </select>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleSave}
          loading={saving}
          disabled={value === timezone}
          className="h-8 px-3 text-xs"
        >
          Save
        </Button>
        {saved && <span className="text-xs text-status-success">Saved</span>}
      </div>
      {error && <p className="text-xs text-status-error">{error}</p>}
    </section>
  );
}


// ─── Email / SMTP settings (2026-07-30) ─────────────────────────────────────
// Deliberately backed by its own superadmin-only endpoint, NOT /site-settings
// -- that one is public (it serves login-page branding) and must never carry
// mail credentials. See apps/api/routers/email_settings.py.

function EmailSettingsSection() {
  const { settings, isLoading, update, sendTest } = useEmailSettings();

  const [provider, setProvider] = React.useState("smtp");
  const [fromAddress, setFromAddress] = React.useState("");
  const [fromName, setFromName] = React.useState("");
  const [smtpHost, setSmtpHost] = React.useState("");
  const [smtpPort, setSmtpPort] = React.useState("");
  const [smtpUser, setSmtpUser] = React.useState("");
  const [smtpUseTls, setSmtpUseTls] = React.useState(true);
  const [awsKeyId, setAwsKeyId] = React.useState("");
  const [awsRegion, setAwsRegion] = React.useState("");

  // Secrets are write-only: always blank on load, never pre-filled from the
  // server (which never sends them), and only transmitted when the admin
  // actually types something. Same convention as the profile page's own
  // password field.
  const [smtpPassword, setSmtpPassword] = React.useState("");
  const [awsSecret, setAwsSecret] = React.useState("");

  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState(false);

  const [testTo, setTestTo] = React.useState("");
  const [testing, setTesting] = React.useState(false);
  const [testResult, setTestResult] = React.useState<{ ok: boolean; detail: string } | null>(null);

  React.useEffect(() => {
    if (!settings) return;
    setProvider(settings.mail_provider ?? settings.effective_provider ?? "smtp");
    setFromAddress(settings.mail_from_address ?? "");
    setFromName(settings.mail_from_name ?? "");
    setSmtpHost(settings.smtp_host ?? "");
    setSmtpPort(settings.smtp_port ? String(settings.smtp_port) : "");
    setSmtpUser(settings.smtp_user ?? "");
    setSmtpUseTls(settings.smtp_use_tls ?? true);
    setAwsKeyId(settings.aws_mail_access_key_id ?? "");
    setAwsRegion(settings.aws_mail_region ?? "");
    // Secret boxes intentionally not repopulated.
  }, [settings]);

  const handleSave = async () => {
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      await update({
        mail_provider: provider,
        mail_from_address: fromAddress,
        mail_from_name: fromName,
        smtp_host: smtpHost,
        smtp_port: smtpPort.trim() ? parseInt(smtpPort, 10) : null,
        smtp_user: smtpUser,
        smtp_use_tls: smtpUseTls,
        aws_mail_access_key_id: awsKeyId,
        aws_mail_region: awsRegion,
        // Only sent when non-empty -- an untouched box leaves the stored
        // credential alone rather than wiping it.
        ...(smtpPassword ? { smtp_password: smtpPassword } : {}),
        ...(awsSecret ? { aws_mail_secret_access_key: awsSecret } : {}),
      });
      setSmtpPassword("");
      setAwsSecret("");
      setSaved(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save email settings");
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTestResult(null);
    setTesting(true);
    try {
      const res = await sendTest(testTo.trim());
      setTestResult({ ok: res.success, detail: res.detail });
    } catch (err: unknown) {
      setTestResult({
        ok: false,
        detail: err instanceof Error ? err.message : "Could not send test email",
      });
    } finally {
      setTesting(false);
    }
  };

  const inputClass =
    "h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-xs text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-border-focus";

  return (
    <section className="rounded-lg border border-border bg-bg-secondary p-4 space-y-4">
      <div className="flex items-center gap-2">
        <Mail className="h-4 w-4 text-text-tertiary" />
        <h2 className="text-sm font-semibold text-text-primary">Email &amp; SMTP</h2>
      </div>

      <p className="text-xs text-text-secondary">
        {isLoading
          ? "Loading…"
          : settings?.using_env_fallback
            ? `Currently using the server environment configuration${
                settings?.effective_smtp_host ? ` (${settings.effective_smtp_host})` : ""
              }. Anything you set here overrides it.`
            : "Using the settings saved here. Any field you leave empty falls back to the server environment."}
      </p>

      {/* The sentence above says WHICH config is active; this says which
          address actually results from it. Unconditional — the saved-
          settings branch never showed a From address at all, and the env
          branch only ever showed the SMTP host. */}
      {!isLoading && settings?.effective_from_address && (
        <p className="text-xs text-text-secondary">
          Currently sending as{" "}
          <span className="font-medium text-text-primary">
            {settings.effective_from_address}
          </span>
        </p>
      )}

      <div className="flex items-center gap-2">
        <label className="text-xs font-medium text-text-tertiary w-28 shrink-0">Provider</label>
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          className="h-8 rounded-md border border-border bg-bg-secondary px-2 text-xs text-text-primary focus:outline-none focus:border-border-focus"
        >
          <option value="smtp">SMTP</option>
          <option value="ses">AWS SES</option>
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-text-tertiary">From address</label>
          <input className={inputClass} value={fromAddress} onChange={(e) => setFromAddress(e.target.value)} placeholder="noreply@example.com" />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-text-tertiary">From name</label>
          <input className={inputClass} value={fromName} onChange={(e) => setFromName(e.target.value)} placeholder="FilmBill" />
        </div>
      </div>

      {provider === "smtp" ? (
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-text-tertiary">SMTP host</label>
            <input className={inputClass} value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} placeholder="smtp.office365.com" />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-text-tertiary">Port</label>
            <input className={inputClass} type="number" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} placeholder="587" />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-text-tertiary">Username</label>
            <input className={inputClass} value={smtpUser} onChange={(e) => setSmtpUser(e.target.value)} autoComplete="off" />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-text-tertiary">
              Password {settings?.smtp_password_set && <span className="text-text-tertiary">(saved)</span>}
            </label>
            <input
              className={inputClass}
              type="password"
              value={smtpPassword}
              onChange={(e) => setSmtpPassword(e.target.value)}
              placeholder={settings?.smtp_password_set ? "Leave blank to keep current" : "Not set"}
              autoComplete="new-password"
            />
          </div>
          <label className="col-span-2 flex items-center gap-2 text-xs text-text-secondary">
            <input type="checkbox" checked={smtpUseTls} onChange={(e) => setSmtpUseTls(e.target.checked)} />
            Use STARTTLS
          </label>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-text-tertiary">Access key ID</label>
            <input className={inputClass} value={awsKeyId} onChange={(e) => setAwsKeyId(e.target.value)} autoComplete="off" />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-text-tertiary">
              Secret access key {settings?.aws_mail_secret_access_key_set && <span className="text-text-tertiary">(saved)</span>}
            </label>
            <input
              className={inputClass}
              type="password"
              value={awsSecret}
              onChange={(e) => setAwsSecret(e.target.value)}
              placeholder={settings?.aws_mail_secret_access_key_set ? "Leave blank to keep current" : "Not set"}
              autoComplete="new-password"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-text-tertiary">Region</label>
            <input className={inputClass} value={awsRegion} onChange={(e) => setAwsRegion(e.target.value)} placeholder="ap-south-1" />
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button variant="secondary" size="sm" onClick={handleSave} loading={saving} className="h-8 px-3 text-xs">
          Save
        </Button>
        {saved && <span className="text-xs text-status-success">Saved.</span>}
        {error && <span className="text-xs text-status-error">{error}</span>}
      </div>

      <div className="border-t border-border pt-3 space-y-2">
        <label className="text-xs font-medium text-text-tertiary">
          Send a test email using the saved settings
        </label>
        <div className="flex items-center gap-2">
          <input
            className={inputClass + " max-w-xs"}
            type="email"
            value={testTo}
            onChange={(e) => setTestTo(e.target.value)}
            placeholder="you@example.com"
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={handleTest}
            loading={testing}
            disabled={!testTo.trim()}
            className="h-8 px-3 text-xs"
          >
            <Send className="h-3.5 w-3.5" />
            Send test
          </Button>
        </div>
        {testResult && (
          <p className={cn("text-xs", testResult.ok ? "text-status-success" : "text-status-error")}>
            {testResult.detail}
          </p>
        )}
      </div>
    </section>
  );
}

// ─── Delete confirmation dialog (task 1, 2026-07-23) ───────────────────────
// Permanent, irreversible -- gated behind typing the literal text "DELETE".
function userStatusBadge(status: UserStatus) {
  const map: Record<UserStatus, { label: string; className: string }> = {
    active: {
      label: "Active",
      className: "bg-status-success/15 text-status-success",
    },
    deactivated: {
      label: "Deactivated",
      className: "bg-status-error/15 text-status-error",
    },
    pending_invite: {
      label: "Pending",
      className: "bg-status-warning/15 text-status-warning",
    },
    pending_verification: {
      label: "Unverified",
      className: "bg-bg-tertiary text-text-secondary",
    },
  };
  const cfg = map[status] ?? map.active;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        cfg.className,
      )}
    >
      {cfg.label}
    </span>
  );
}

// ─── User group block: independent bordered table per group, collapsible ──

/** Accessors, one per sortable column. Deliberately absent: **Actions**,
 *  which is controls, not a value — it stays a plain header rather than
 *  being given a sort that would not mean what the column shows. */
const USER_SORT = {
  user: (u: AdminUser) => u.name,
  role: (u: AdminUser) => u.role ?? null,
  status: (u: AdminUser) => u.status ?? null,
  joined: (u: AdminUser) => u.created_at,
};

function UserGroupBlock({
  title,
  users,
  storageKey,
  defaultCollapsed,
  renderRow,
}: {
  title: string;
  users: AdminUser[];
  /** Suffix of the localStorage key, so collapse state survives a reload. */
  storageKey: string;
  defaultCollapsed?: boolean;
  renderRow: (u: AdminUser) => React.ReactNode;
}) {
  // One sort per block, so sorting Admins leaves Members alone.
  const { sorted, sort } = useSort(users, USER_SORT, { key: "user" });

  if (users.length === 0) return null;

  return (
    <CollapsibleSection
      tone="block"
      title={title}
      count={users.length}
      storageKey={storageKey}
      defaultCollapsed={defaultCollapsed}
    >
      <table className="w-full text-sm min-w-[640px]">
        <thead>
          <tr className="border-b border-t border-border bg-bg-tertiary">
            <SortableTh label="User" sortKey="user" sort={sort} />
            <SortableTh label="Role" sortKey="role" sort={sort} />
            <SortableTh label="Status" sortKey="status" sort={sort} />
            <SortableTh label="Joined" sortKey="joined" sort={sort} />
            <PlainTh label="Actions" align="right" />
          </tr>
        </thead>
        <tbody>{sorted.map(renderRow)}</tbody>
      </table>
    </CollapsibleSection>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────
// Instance administration: who can sign in, whether 2FA is mandatory, what
// timezone scheduled jobs run in, and where mail goes. Per-company settings
// (number series, roles, tax profile) arrive in P0b on their own pages.

export default function AdminPage() {
  const { user, isSuperAdmin } = useAuthStore();
  const router = useRouter();

  const { data: usersResp, isLoading: loadingUsers } = useSWR<AdminUser[]>(
    "/admin/users",
    () => api.get<AdminUser[]>("/admin/users"),
  );

  React.useEffect(() => {
    if (user && !isSuperAdmin) {
      router.replace("/");
    }
  }, [user, isSuperAdmin, router]);

  const handleDeactivate = async (userId: string) => {
    try {
      await api.patch(`/admin/users/${userId}/deactivate`);
      mutate("/admin/users");
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to deactivate user";
      alert(message);
    }
  };

  const handleReactivate = async (userId: string) => {
    try {
      await api.patch(`/admin/users/${userId}/reactivate`);
      mutate("/admin/users");
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to reactivate user";
      alert(message);
    }
  };

  const [copiedId, setCopiedId] = React.useState<string | null>(null);

  const handleCopyInviteLink = (u: AdminUser) => {
    if (!u.invite_token) return;
    const link = `${window.location.origin}/invite/${u.invite_token}`;
    navigator.clipboard.writeText(link);
    setCopiedId(u.id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleToggleAdmin = async (
    userId: string,
    isCurrentlyAdmin: boolean,
  ) => {
    try {
      await api.patch(`/admin/users/${userId}/role`, {
        is_admin: !isCurrentlyAdmin,
      });
      mutate("/admin/users");
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to update user role";
      alert(message);
    }
  };

  const [search, setSearch] = React.useState("");

  const filteredUsers = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return usersResp ?? [];
    return (usersResp ?? []).filter(
      (u) =>
        u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q),
    );
  }, [usersResp, search]);

  // Search filters within each group; it doesn't collapse the admin/member
  // split. Deactivated users (any role) are pulled out into their own group
  // entirely rather than just sorted to the bottom.
  //
  // Ordering is not done here: each block sorts itself from its own column
  // headers.
  const admins = React.useMemo(
    () =>
      filteredUsers
        .filter((u) => u.role === "superadmin" && u.status !== "deactivated"),
    [filteredUsers],
  );
  const members = React.useMemo(
    () =>
      filteredUsers
        .filter((u) => u.role !== "superadmin" && u.status !== "deactivated"),
    [filteredUsers],
  );
  const deactivated = React.useMemo(
    () => filteredUsers.filter((u) => u.status === "deactivated"),
    [filteredUsers],
  );

  if (!isSuperAdmin) {
    return null;
  }

  const renderRow = (u: AdminUser) => (
    <tr
      key={u.id}
      className="border-b border-border last:border-0 hover:bg-bg-tertiary transition-colors"
    >
      <td className="px-4 py-3">
        <div className="flex items-center gap-2.5">
          <Avatar src={u.avatar_url} name={u.name} size="sm" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-text-primary truncate">
              {u.name}
            </p>
            <p className="text-xs text-text-tertiary truncate">{u.email}</p>
          </div>
        </div>
      </td>
      <td className="px-4 py-3">
        {u.role === "superadmin" ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
            <Shield className="h-3 w-3" />
            Admin
          </span>
        ) : (
          <span className="text-xs text-text-tertiary">User</span>
        )}
      </td>
      <td className="px-4 py-3">{userStatusBadge(u.status)}</td>
      <td className="px-4 py-3 text-xs text-text-tertiary">
        {u.created_at ? new Date(u.created_at).toLocaleDateString() : "—"}
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center justify-end gap-2">
          {u.status === "pending_invite" && u.invite_token && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleCopyInviteLink(u)}
              className="gap-1"
            >
              {copiedId === u.id ? (
                <>
                  <Check className="h-3.5 w-3.5 text-status-success" />{" "}
                  Copied
                </>
              ) : (
                <>
                  <Link2 className="h-3.5 w-3.5" /> Copy Invite Link
                </>
              )}
            </Button>
          )}
          {u.id !== user?.id && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleToggleAdmin(u.id, u.role === "superadmin")}
            >
              {u.role === "superadmin" ? "Remove Admin" : "Make Admin"}
            </Button>
          )}
          {u.id !== user?.id && u.two_factor_enabled && (
            <ResetTwoFactorButton user={u} onDone={() => mutate("/admin/users")} />
          )}
          {u.id !== user?.id && u.status === "active" ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleDeactivate(u.id)}
              className="text-status-error hover:text-status-error"
            >
              Deactivate
            </Button>
          ) : u.id !== user?.id && u.status === "deactivated" ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleReactivate(u.id)}
            >
              Reactivate
            </Button>
          ) : u.id === user?.id ? (
            <span className="text-xs text-text-tertiary italic">You</span>
          ) : null}
        </div>
      </td>
    </tr>
  );

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-muted">
          <Shield className="h-5 w-5 text-accent" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-text-primary">
            Admin Dashboard
          </h1>
          <p className="text-sm text-text-secondary">
            Manage platform users.
          </p>
        </div>
      </div>

      <TimezoneSection />
      <RequireTwoFactorSection />

      <EmailSettingsSection />

      <section className="space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="text-sm font-semibold text-text-primary">
            Platform Users
          </h2>
          <div className="flex items-center gap-2">
            <Input
              icon={<Search className="h-3.5 w-3.5" />}
              placeholder="Search by name or email"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-8 w-56 text-xs"
            />
            <BulkInviteDialog />
          </div>
        </div>

        {loadingUsers ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="h-12 animate-pulse rounded-lg bg-bg-tertiary"
              />
            ))}
          </div>
        ) : !usersResp || usersResp.length === 0 ? (
          <div className="rounded-lg border border-border bg-bg-secondary">
            <EmptyState
              icon={Users}
              title="No users"
              description="Users will appear here once they register or are invited."
            />
          </div>
        ) : admins.length === 0 && members.length === 0 && deactivated.length === 0 ? (
          <div className="rounded-lg border border-border bg-bg-secondary">
            <EmptyState
              icon={Search}
              title="No matching users"
              description="Try a different name or email."
            />
          </div>
        ) : (
          <div className="space-y-4">
            <UserGroupBlock
              title="Admins"
              users={admins}
              storageKey="admin-admins"
              renderRow={renderRow}
            />
            <UserGroupBlock
              title="Members"
              users={members}
              storageKey="admin-members"
              renderRow={renderRow}
            />
            <UserGroupBlock
              title="Deactivated"
              users={deactivated}
              storageKey="admin-deactivated"
              // Unchanged: this one has always started closed.
              defaultCollapsed
              renderRow={renderRow}
            />
          </div>
        )}
      </section>
    </div>
  );
}
