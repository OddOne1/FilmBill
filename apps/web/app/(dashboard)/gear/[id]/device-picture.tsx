"use client";

import * as React from "react";
import { api, ApiError } from "@/lib/api";
import { resolveApiMediaUrl } from "@/lib/utils";
import type { DeviceDetail } from "@/types";

/**
 * The device's ONE cover picture (B2b-2). The API decides whether a file is
 * a picture — by its content, not its name — and answers with a sentence
 * when it is not; this screen only shows that sentence. Several pictures
 * per device come later (SCOPE §4.4.1).
 */
export function DevicePicture({
  device,
  canEdit,
  onChanged,
}: {
  device: DeviceDetail;
  canEdit: boolean;
  onChanged: (next: DeviceDetail, message: string) => void;
}) {
  const input = React.useRef<HTMLInputElement>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    try {
      onChanged(
        await api.upload<DeviceDetail>(`/devices/${device.id}/image`, form),
        "Picture saved.",
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "The picture could not be uploaded. Try again.",
      );
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      onChanged(
        await api.post<DeviceDetail>(`/devices/${device.id}/image/remove`, {}),
        "Picture removed.",
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "The picture could not be removed. Try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="device-picture" className="mt-8">
      <h2 className="text-sm font-semibold text-text-primary">Picture</h2>
      <div className="mt-2 flex flex-wrap items-end gap-4">
        {device.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- an API-proxied object, not a static asset
          <img
            data-testid="device-picture-image"
            src={resolveApiMediaUrl(device.image_url) ?? undefined}
            alt={device.name}
            className="max-h-60 max-w-xs rounded-md border border-border object-contain"
          />
        ) : (
          <div
            data-testid="device-picture-placeholder"
            className="flex h-32 w-44 items-center justify-center rounded-md border border-dashed border-border text-xs text-text-tertiary"
          >
            No picture yet
          </div>
        )}
        {canEdit && (
          <div className="flex flex-col gap-2 text-sm">
            <input
              ref={input}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              data-testid="device-picture-input"
              aria-label={
                device.image_url ? "Replace the picture" : "Upload a picture"
              }
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void upload(file);
              }}
              className="text-xs text-text-secondary"
            />
            <span className="text-xs text-text-tertiary">
              JPEG, PNG or WebP, at most 5 MB.
            </span>
            {device.image_url && (
              <button
                type="button"
                data-testid="device-picture-remove"
                disabled={busy}
                onClick={() => void remove()}
                className="self-start text-xs underline disabled:opacity-50"
              >
                Remove picture
              </button>
            )}
          </div>
        )}
      </div>
      {error && (
        <p
          role="alert"
          data-testid="device-picture-error"
          className="mt-2 text-sm text-danger"
        >
          {error}
        </p>
      )}
    </section>
  );
}
