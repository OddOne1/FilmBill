"use client";

import * as React from "react";
import { useAuthStore } from "@/stores/auth-store";
import {
  ACTIVE_COMPANY_PREFERENCE_KEY,
  useCompanyStore,
} from "@/stores/company-store";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { CommandPalette } from "@/components/layout/command-palette";
import {
  AccountSetupGate,
  accountSetupOutstanding,
} from "@/components/auth/account-setup-gate";
import { cn } from "@/lib/utils";

/**
 * Everything the dashboard chrome does that needs a browser.
 *
 * Split out of app/(dashboard)/layout.tsx: that file is an async server
 * component so it can fetch branding before first paint and hand it down,
 * which it cannot do while also holding client state.
 */
export function DashboardShell({
  children,
}: {
  children: React.ReactNode;
}) {
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(true);
  const [commandOpen, setCommandOpen] = React.useState(false);
  const { fetchUser, user } = useAuthStore();
  const loadCompanies = useCompanyStore((state) => state.load);

  // Read from /auth/me, which computes it server-side from the stored
  // data. This is presentation: middleware/account_gate.py already answers
  // 403 to every protected route while it is true, so the purpose here is to
  // show the person WHY nothing loads and give them the two forms, rather
  // than to enforce anything.
  const gated = accountSetupOutstanding(user);

  React.useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  // AFTER the user, and only once they are past the gate.
  //
  // The order matters in both directions. /companies is a protected route,
  // so calling it while the account gate is up returns 403 and the switcher
  // would spend the whole gate showing an error about companies — which is
  // not the problem the person has. And the remembered company id lives in
  // `user.preferences`, so there is nothing to remember until /auth/me has
  // answered.
  const remembered = user?.preferences?.[ACTIVE_COMPANY_PREFERENCE_KEY];
  React.useEffect(() => {
    if (!user || gated) return;
    void loadCompanies(typeof remembered === "string" ? remembered : null);
    // Keyed on the user's id, not on the whole object: /auth/me is re-fetched
    // on several occasions and a new object identity each time would reload
    // the company list on every one of them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id, gated, loadCompanies]);

  // Global keyboard shortcut for command palette
  React.useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setCommandOpen((prev) => !prev);
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  if (gated && user) {
    // Rendered INSTEAD of the whole shell, not inside it. The sidebar and
    // header are navigation into an app that answers 403 to everything —
    // showing them would be an interface that does not work rather than an
    // explanation of why.
    return (
      <div className="h-screen overflow-hidden bg-bg-primary">
        <AccountSetupGate user={user} />
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-bg-primary">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((c) => !c)}
      />

      {/* Main content area */}
      <main
        className={cn(
          "flex flex-1 flex-col overflow-hidden transition-[margin] duration-200 ease-spring",
          sidebarCollapsed ? "ml-[52px]" : "ml-[192px]",
        )}
      >
        <Header onSearchOpen={() => setCommandOpen(true)} />

        <div className="relative flex-1 overflow-y-auto">{children}</div>
      </main>

      <CommandPalette open={commandOpen} onOpenChange={setCommandOpen} />
    </div>
  );
}
