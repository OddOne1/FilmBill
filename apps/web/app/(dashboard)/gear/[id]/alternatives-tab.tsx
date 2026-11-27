"use client";

import * as React from "react";
import Link from "next/link";
import * as Dialog from "@radix-ui/react-dialog";
import { api, ApiError } from "@/lib/api";
import type {
  DeviceAlternative,
  DeviceAlternatives,
  DeviceDetail,
} from "@/types";
import { DeviceThumb } from "../gear-chrome";
import { DevicePicker, useOtherDevices } from "./device-picker";

/**
 * The Alternatives tab (B2b-2, SCOPE §4.4.5): an ordered list of devices
 * that can stand in for this one when it is overbooked or somebody wants
 * something similar — offered by B2c's overbooking popup, never booked by
 * themselves. Links are one-directional; "Also add the reverse" is an
 * explicit tick, unticked by default.
 */
export function AlternativesTab({
  device,
  canEdit,
}: {
  device: DeviceDetail;
  canEdit: boolean;
}) {
  const [data, setData] = React.useState<DeviceAlternatives | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [adding, setAdding] = React.useState(false);

  React.useEffect(() => {
    api
      .get<DeviceAlternatives>(`/devices/${device.id}/alternatives`)
      .then((next) => {
        setData(next);
        setError(null);
      })
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? err.detail
            : "The alternatives could not be loaded. Try again.",
        ),
      );
  }, [device.id]);

  const send = async (request: Promise<DeviceAlternatives>) => {
    setError(null);
    setNotice(null);
    try {
      setData(await request);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "That did not work. Try again.",
      );
    }
  };
  const base = `/devices/${device.id}/alternatives`;
  const rows = data?.alternatives ?? [];

  return (
    <section data-testid="alternatives-tab" className="mt-5">
      <p className="text-xs text-text-tertiary">
        Offered in this order when {device.name} is overbooked or somebody wants
        something similar. Never booked by themselves.
      </p>
      {error && (
        <p
          role="alert"
          data-testid="alternatives-error"
          className="mt-3 text-sm text-danger"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          role="status"
          data-testid="alternatives-notice"
          className="mt-3 text-sm text-success"
        >
          {notice}
        </p>
      )}
      <h2 className="mt-4 text-sm font-semibold text-text-primary">
        Alternatives for {device.name}
      </h2>
      {!data ? (
        <p className="mt-2 text-sm text-text-secondary">Loading…</p>
      ) : rows.length === 0 ? (
        <p
          data-testid="alternatives-empty"
          className="mt-2 text-sm text-text-secondary"
        >
          No alternatives yet.
        </p>
      ) : (
        <ol className="mt-2 space-y-2">
          {rows.map((row, index) => (
            <AlternativeRow
              key={row.id}
              row={row}
              index={index}
              last={index === rows.length - 1}
              canEdit={canEdit}
              onMove={(direction) =>
                void send(api.post(`${base}/${row.id}/move`, { direction }))
              }
              onChange={(body) =>
                void send(api.patch(`${base}/${row.id}`, body))
              }
              onRemove={() =>
                void send(api.post(`${base}/${row.id}/remove`, {}))
              }
            />
          ))}
        </ol>
      )}
      {canEdit && device.is_active && (
        <button
          type="button"
          data-testid="alternative-add-open"
          onClick={() => setAdding(true)}
          className="mt-3 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
        >
          Add alternative
        </button>
      )}

      <h2 className="mt-8 text-sm font-semibold text-text-primary">
        Offered as an alternative by
      </h2>
      {data && data.offered_by.length === 0 ? (
        <p
          data-testid="offered-by-empty"
          className="mt-2 text-sm text-text-secondary"
        >
          No other device offers {device.name} as an alternative.
        </p>
      ) : (
        <ul data-testid="offered-by" className="mt-2 space-y-1 text-sm">
          {data?.offered_by.map((row) => (
            <li key={row.id} className="flex items-center gap-2">
              <DeviceThumb
                url={row.device.thumbnail_url}
                name={row.device.name}
                size={24}
              />
              <Link
                href={`/gear/${row.device.id}`}
                className="text-text-primary underline"
              >
                {row.device.name}
              </Link>
            </li>
          ))}
        </ul>
      )}

      <AddAlternativeDialog
        open={adding}
        onOpenChange={setAdding}
        device={device}
        taken={new Set(rows.map((row) => row.alternative.id))}
        onAdded={(next, name, reverse) => {
          setData(next);
          setAdding(false);
          setNotice(
            reverse === true
              ? `${name} added, and ${device.name} is now an alternative for ${name} too.`
              : reverse === false
                ? `${name} added. It already listed ${device.name}, so nothing else changed.`
                : `${name} added. ${name}'s own list is unchanged.`,
          );
        }}
      />
    </section>
  );
}

function AlternativeRow({
  row,
  index,
  last,
  canEdit,
  onMove,
  onChange,
  onRemove,
}: {
  row: DeviceAlternative;
  index: number;
  last: boolean;
  canEdit: boolean;
  onMove: (direction: "up" | "down") => void;
  onChange: (body: Record<string, unknown>) => void;
  onRemove: () => void;
}) {
  const [note, setNote] = React.useState(row.note ?? "");
  const [hint, setHint] = React.useState(row.price_hint ?? "");
  React.useEffect(() => setNote(row.note ?? ""), [row.note]);
  React.useEffect(() => setHint(row.price_hint ?? ""), [row.price_hint]);
  const field =
    "h-7 rounded-md border border-border bg-bg-secondary px-1.5 text-sm text-text-primary";
  const name = row.alternative.name;

  return (
    <li
      data-testid="alternative-row"
      className="flex flex-wrap items-center gap-3 rounded-md border border-border px-3 py-2 text-sm"
    >
      <span className="w-5 text-right text-xs text-text-tertiary">
        {index + 1}.
      </span>
      <DeviceThumb url={row.alternative.thumbnail_url} name={name} size={28} />
      <Link
        href={`/gear/${row.alternative.id}`}
        className="font-medium text-text-primary underline"
      >
        {name}
      </Link>
      {canEdit ? (
        <>
          <input
            aria-label={`Note on ${name}`}
            data-testid="alternative-note"
            value={note}
            placeholder="Note, such as needs a different mount"
            onChange={(event) => setNote(event.target.value)}
            onBlur={() =>
              note !== (row.note ?? "") && onChange({ note: note || null })
            }
            className={`${field} min-w-[12rem] flex-1`}
          />
          <input
            aria-label={`Price hint for ${name}`}
            data-testid="alternative-hint"
            value={hint}
            placeholder="Price hint, such as about +150 per day"
            onChange={(event) => setHint(event.target.value)}
            onBlur={() =>
              hint !== (row.price_hint ?? "") &&
              onChange({ price_hint: hint || null })
            }
            className={`${field} w-48`}
          />
          <span className="ml-auto flex gap-2 text-xs">
            <button
              type="button"
              data-testid="alternative-up"
              aria-label={`Move ${name} up`}
              disabled={index === 0}
              onClick={() => onMove("up")}
              className="underline disabled:opacity-40"
            >
              Up
            </button>
            <button
              type="button"
              data-testid="alternative-down"
              aria-label={`Move ${name} down`}
              disabled={last}
              onClick={() => onMove("down")}
              className="underline disabled:opacity-40"
            >
              Down
            </button>
            <button
              type="button"
              data-testid="alternative-remove"
              onClick={onRemove}
              className="underline"
            >
              Remove
            </button>
          </span>
        </>
      ) : (
        <span className="text-xs text-text-secondary">
          {[row.note, row.price_hint].filter(Boolean).join(" · ")}
        </span>
      )}
    </li>
  );
}

function AddAlternativeDialog({
  open,
  onOpenChange,
  device,
  taken,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  device: DeviceDetail;
  taken: Set<string>;
  onAdded: (
    next: DeviceAlternatives,
    name: string,
    reverse: boolean | null | undefined,
  ) => void;
}) {
  const devices = useOtherDevices(open, device.id);
  const [chosen, setChosen] = React.useState<string | null>(null);
  const [note, setNote] = React.useState("");
  const [hint, setHint] = React.useState("");
  const [reverse, setReverse] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setChosen(null);
    setNote("");
    setHint("");
    setReverse(false);
    setError(null);
  }, [open]);

  const name = devices.find((row) => row.id === chosen)?.name ?? "";
  const why = !chosen
    ? "Pick the device to offer instead."
    : taken.has(chosen)
      ? `${name} is already an alternative for ${device.name}.`
      : null;

  const save = async () => {
    if (why || !chosen) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.post<DeviceAlternatives>(
        `/devices/${device.id}/alternatives`,
        {
          alternative_device_id: chosen,
          note: note || null,
          price_hint: hint || null,
          also_reverse: reverse,
        },
      );
      onAdded(next, name, next.reverse_added);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "The alternative could not be added. Try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  const field =
    "mt-1 h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary";
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content
          data-testid="alternative-dialog"
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border bg-bg-secondary p-6 shadow-xl"
        >
          <Dialog.Title className="text-sm font-semibold text-text-primary">
            Add an alternative for {device.name}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-text-tertiary">
            It goes to the end of the list; move it up afterwards.
          </Dialog.Description>
          <DevicePicker
            devices={devices}
            chosen={chosen}
            onChoose={setChosen}
          />
          <label className="mt-3 block text-xs text-text-secondary">
            Note
            <input
              data-testid="alternative-add-note"
              value={note}
              placeholder="needs a different mount"
              onChange={(event) => setNote(event.target.value)}
              className={field}
            />
          </label>
          <label className="mt-3 block text-xs text-text-secondary">
            Price hint
            <input
              data-testid="alternative-add-hint"
              value={hint}
              placeholder="about +150 per day"
              onChange={(event) => setHint(event.target.value)}
              className={field}
            />
          </label>
          <label className="mt-3 flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              data-testid="alternative-add-reverse"
              checked={reverse}
              onChange={(event) => setReverse(event.target.checked)}
            />
            Also add the reverse
            {name ? ` — ${device.name} as an alternative for ${name}` : ""}
          </label>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid="alternative-add-save"
              disabled={busy || why !== null}
              onClick={() => void save()}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Add alternative
            </button>
            <Dialog.Close asChild>
              <button
                type="button"
                className="text-sm text-text-secondary hover:text-text-primary"
              >
                Cancel
              </button>
            </Dialog.Close>
            {why && (
              <span
                data-testid="alternative-add-why"
                className="text-xs text-text-tertiary"
              >
                {why}
              </span>
            )}
          </div>
          {error && (
            <p role="alert" className="mt-3 text-sm text-danger">
              {error}
            </p>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
