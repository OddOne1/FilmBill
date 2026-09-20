"use client";

import * as React from "react";
import { Bell, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";

/**
 * P0a offers the email-frequency control and nothing else.
 *
 * FreeFrame's six per-category switches were about comments, uploads and
 * asset status; FilmBill has none of those events. Keeping the switches with
 * billing-sounding labels would recreate the exact bug the preference gate
 * exists to prevent — a control that gates nothing is worse than no control,
 * because it is a false statement to the user. Each category arrives here and
 * in apps/api/services/notification_prefs.py in the same change as the event
 * it describes (SCOPE §5.5, §9, §14).
 */

interface NotifPrefs {
  email_frequency: string;
  [key: string]: string;
}

const defaults: NotifPrefs = {
  email_frequency: "instant",
};

export default function NotificationsPage() {
  const { user } = useAuthStore();
  const [prefs, setPrefs] = React.useState<NotifPrefs>(defaults);
  const [saving, setSaving] = React.useState(false);

  // Load from user preferences
  React.useEffect(() => {
    if (!user?.preferences) return;
    const notif = (user.preferences.notifications ?? {}) as Record<
      string,
      unknown
    >;
    const merged: NotifPrefs = { ...defaults };
    Object.entries(notif).forEach(([key, value]) => {
      if (typeof value === "string") {
        merged[key] = value;
      }
    });
    setPrefs(merged);
  }, [user?.preferences]);

  async function updatePref(key: string, value: string) {
    const updated = { ...prefs, [key]: value };
    setPrefs(updated);
    setSaving(true);
    try {
      await api.patch("/auth/me/preferences", { notifications: updated });
    } catch {}
    setSaving(false);
  }

  return (
    <div className="p-6 max-w-3xl space-y-8">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-muted">
          <Bell className="h-5 w-5 text-accent" />
        </div>
        <div className="flex-1">
          <h1 className="text-lg font-semibold text-text-primary">
            Notifications
          </h1>
          <p className="text-sm text-text-secondary">
            Manage your notification preferences
          </p>
        </div>
        {saving && (
          <Loader2 className="h-4 w-4 animate-spin text-text-tertiary" />
        )}
      </div>

      {/* Email Frequency */}
      <section className="space-y-4">
        <div className="flex items-start gap-3 p-4 rounded-lg border border-border bg-bg-secondary">
          <Bell className="h-5 w-5 text-text-secondary mt-0.5" />
          <div className="flex-1">
            <h3 className="text-sm font-medium text-text-primary">
              Email Notifications Frequency
            </h3>
            <p className="text-xs text-text-tertiary mt-1">
              Email updates will be sent to your email address
            </p>
            <select
              value={prefs.email_frequency}
              onChange={(e) => updatePref("email_frequency", e.target.value)}
              className="mt-3 w-40 rounded-md border border-border bg-bg-tertiary px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="instant">Instantly</option>
              <option value="15min">15 Minutes</option>
              <option value="hourly">Hourly</option>
              <option value="daily">Daily</option>
              <option value="never">Never</option>
            </select>
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-text-primary">
          What you get notified about
        </h2>
        <div className="p-4 rounded-lg border border-border bg-bg-secondary">
          <p className="text-sm text-text-secondary">
            Right now the only notifications FilmBill sends are about your own
            account — sign-in codes, invites and security changes. You always
            receive those.
          </p>
          <p className="text-xs text-text-tertiary mt-2">
            Per-event controls (quote accepted, invoice overdue, document
            revised, receipt needs review) appear here as those features ship.
          </p>
        </div>
      </section>

      <div className="p-4 rounded-lg bg-bg-tertiary border border-border">
        <p className="text-xs text-text-secondary">
          You will always get important administrative emails, such as sign-in
          and password-reset codes, whatever this page says.
        </p>
      </div>
    </div>
  );
}
