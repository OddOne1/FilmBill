"use client";

import * as React from "react";
import { ShieldOff } from "lucide-react";

import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { AdminUser } from "@/types";

/**
 * Strip another user's second factor when they have lost every one of them.
 *
 * The endpoint has existed since FreeFrame and had no UI on either side of
 * the port, which made the documented recovery path a curl command. In a
 * system where a role can REQUIRE 2FA, "the tax advisor lost their phone" is
 * an ordinary Tuesday, not an edge case.
 *
 * Confirmed rather than one-click, and deliberately not offered for
 * yourself: the API refuses that, because otherwise a stolen superadmin
 * session could strip that superadmin's own protection with no code at all.
 * A superadmin who has genuinely lost everything is reset by another one.
 */
export function ResetTwoFactorButton({ user, onDone }: { user: AdminUser; onDone: () => void }) {
  const [open, setOpen] = React.useState(false);

  async function handleConfirm() {
    try {
      await api.patch(`/admin/users/${user.id}/disable-2fa`);
      onDone();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to reset two-factor authentication");
      throw err;
    }
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen(true)}
        className="gap-1"
        title="Remove this user's second factor so they can enrol again"
      >
        <ShieldOff className="h-3.5 w-3.5" /> Reset 2FA
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        variant="danger"
        title={`Reset two-factor for ${user.name}?`}
        description={
          "Their authenticator, backup codes and enrolment are removed, and " +
          "their next sign-in needs only their password until they enrol " +
          "again. Do this only when you are sure who you are talking to — it " +
          "is recorded in the audit log either way."
        }
        confirmLabel="Reset two-factor"
        onConfirm={handleConfirm}
      />
    </>
  );
}
