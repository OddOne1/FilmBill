"use client";

import * as React from "react";
import { useAuthStore } from "@/stores/auth-store";
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

  // Read from /auth/me, which computes it server-side from the stored
  // data. This is presentation: middleware/account_gate.py already answers
  // 403 to every protected route while it is true, so the purpose here is to
  // show the person WHY nothing loads and give them the two forms, rather
  // than to enforce anything.
  const gated = accountSetupOutstanding(user);

  React.useEffect(() => {
    fetchUser();
  }, [fetchUser]);

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
