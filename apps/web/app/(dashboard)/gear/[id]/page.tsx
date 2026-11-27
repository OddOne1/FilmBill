"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { deviceCountsSentence } from "@/lib/device-counts";
import { formatMoney } from "@/lib/money";
import { usePermissions } from "@/hooks/use-permissions";
import type { DeviceDetail } from "@/types";
import { RentalPrices } from "./rental-prices";
import { UnitsTab } from "./units-tab";
import { DependenciesTab } from "./dependencies-tab";
import { AlternativesTab } from "./alternatives-tab";
import { DevicePicture } from "./device-picture";
import {
  AMBIGUOUS_AMOUNT,
  amountForApi,
  amountIsAmbiguous,
  DeviceThumb,
  GearNoAccess,
  GearReadOnlyNotice,
  useGearAccess,
} from "../gear-chrome";

/**
 * One device: its two numbers, its units, and what can be done to each.
 *
 * **The quantity owned is changed in exactly three ways**, all visible:
 * typed by hand in the form; "also reduce the quantity owned by 1" when a
 * unit is removed (retired, lost or sold — always with what happened, in
 * words); "also raise it by 1" when a removed unit comes back. The last two
 * are ticked by default and can be unticked. Adding serial numbers never
 * touches it — the API guarantees that, and this screen says so.
 *
 * **What a unit cost and who sold it** (its own price when it differs from
 * the device's, the seller, the invoice number) is shown and edited only by
 * somebody who may see purchase prices — the API withholds it otherwise.
 */
export default function DevicePage() {
  const params = useParams<{ id: string }>();
  const { can, active } = usePermissions();
  const currency = active?.default_currency ?? "EUR";
  const { loaded, canView, canEdit, refused } = useGearAccess();
  const [device, setDevice] = React.useState<DeviceDetail | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [tab, setTab] = React.useState<TabKey>("details");

  const load = React.useCallback(async () => {
    try {
      setDevice(await api.get<DeviceDetail>(`/devices/${params.id}`));
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "This device could not be loaded. Try again.",
      );
    }
  }, [params.id]);

  React.useEffect(() => {
    if (!loaded || !canView) return;
    void load();
  }, [loaded, canView, load]);

  if (refused) return <GearNoAccess title="Device" />;

  const show = (next: DeviceDetail, message: string) => {
    setDevice(next);
    setNotice(message);
  };

  const archiveToggle = async () => {
    if (!device) return;
    setError(null);
    try {
      setDevice(
        await api.post<DeviceDetail>(
          `/devices/${device.id}/${device.is_active ? "archive" : "reactivate"}`,
          {},
        ),
      );
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "That did not work. Try again.",
      );
    }
  };

  return (
    <div className="max-w-5xl p-8">
      <Link
        href="/gear"
        className="inline-flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary"
      >
        <ArrowLeft className="h-3 w-3" /> Gear
      </Link>

      {error && (
        <p
          role="alert"
          data-testid="device-error"
          className="mt-4 text-sm text-danger"
        >
          {error}
        </p>
      )}

      {!device ? (
        !error && (
          <p className="mt-4 text-sm text-text-secondary">
            Loading the device…
          </p>
        )
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <DeviceThumb
                url={device.thumbnail_url}
                name={device.name}
                size={48}
              />
              <div>
                <h1 className="text-lg font-semibold text-text-primary">
                  {device.name}
                  {!device.is_active && (
                    <span className="ml-2 rounded bg-bg-tertiary px-1.5 py-0.5 text-xs text-text-secondary">
                      archived
                    </span>
                  )}
                </h1>
                <p
                  data-testid="device-counts"
                  className="mt-1 text-sm text-text-secondary"
                >
                  {deviceCountsSentence(device.counts)}
                </p>
              </div>
            </div>
            {canEdit && (
              <button
                type="button"
                data-testid="device-archive-toggle"
                onClick={() => void archiveToggle()}
                className="rounded-md border border-border px-3 py-1.5 text-sm text-text-primary"
              >
                {device.is_active ? "Archive" : "Reactivate"}
              </button>
            )}
          </div>

          {!canEdit && <GearReadOnlyNotice />}
          {notice && (
            <p
              role="status"
              data-testid="device-notice"
              className="mt-4 text-sm text-success"
            >
              {notice}
            </p>
          )}

          <nav
            aria-label="Device"
            role="tablist"
            className="mt-5 flex gap-1 border-b border-border"
          >
            {TABS.map((entry) => (
              <button
                key={entry.key}
                type="button"
                role="tab"
                data-testid={`device-tab-${entry.key}`}
                aria-selected={tab === entry.key}
                onClick={() => setTab(entry.key)}
                className={
                  tab === entry.key
                    ? "border-b-2 border-accent px-3 py-2 text-sm font-medium text-text-primary"
                    : "border-b-2 border-transparent px-3 py-2 text-sm text-text-secondary hover:text-text-primary"
                }
              >
                {entry.label}
              </button>
            ))}
          </nav>

          {tab === "details" && (
            <>
              {canEdit ? (
                <DeviceForm
                  device={device}
                  onSaved={(next) => {
                    setDevice(next);
                    setNotice("Saved.");
                  }}
                />
              ) : (
                <dl className="mt-5 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                  <dt className="text-text-tertiary">Category</dt>
                  <dd className="text-text-primary">
                    {device.category ?? "—"}
                  </dd>
                  <dt className="text-text-tertiary">Quantity owned</dt>
                  <dd className="text-text-primary">{device.quantity_owned}</dd>
                  {device.price_visible && (
                    <>
                      <dt className="text-text-tertiary">Purchase price</dt>
                      <dd className="text-text-primary">
                        {formatMoney(device.purchase_price, currency)}
                      </dd>
                    </>
                  )}
                  <dt className="text-text-tertiary">Weight (kg)</dt>
                  <dd
                    data-testid="device-weight-read"
                    className="text-text-primary"
                  >
                    {device.weight_kg ?? "—"}
                  </dd>
                  <dt className="text-text-tertiary">Size (L × W × H, cm)</dt>
                  <dd
                    data-testid="device-size-read"
                    className="text-text-primary"
                  >
                    {sizeText(device)}
                  </dd>
                  <dt className="text-text-tertiary">Notes</dt>
                  <dd className="text-text-primary">{device.notes ?? "—"}</dd>
                </dl>
              )}

              <DevicePicture
                device={device}
                canEdit={canEdit}
                onChanged={show}
              />

              <RentalPrices
                device={device}
                currency={currency}
                onChanged={show}
              />
            </>
          )}

          {tab === "units" && (
            <UnitsTab
              device={device}
              canEdit={canEdit}
              canPickSeller={can("parties.view")}
              currency={currency}
              onDevice={show}
            />
          )}

          {tab === "dependencies" && (
            <DependenciesTab device={device} canEdit={canEdit} />
          )}
          {tab === "alternatives" && (
            <AlternativesTab device={device} canEdit={canEdit} />
          )}
        </>
      )}
    </div>
  );
}

const MEASURE_FIELDS = [
  ["weight", "weight_kg"],
  ["length", "length_cm"],
  ["width", "width_cm"],
  ["height", "height_cm"],
] as const;

/** "12.345" from the API → "12,345" in the field, as prices are shown. */
function decimalForInput(value: string | null | undefined): string {
  return value ? value.replace(".", ",") : "";
}

/** A typed "12,5" → "12.5" for the API; empty → null. No arithmetic: the API
 *  checks the value and answers with a sentence. */
function decimalForApi(text: string): string | null {
  const trimmed = text.replace(/\s/g, "");
  return trimmed ? trimmed.replace(",", ".") : null;
}

function sizeText(device: DeviceDetail): string {
  const parts = [device.length_cm, device.width_cm, device.height_cm];
  return parts.some(Boolean)
    ? parts.map((part) => part ?? "?").join(" × ")
    : "—";
}

type TabKey = "details" | "units" | "dependencies" | "alternatives";

const TABS: { key: TabKey; label: string }[] = [
  { key: "details", label: "Details" },
  { key: "units", label: "Units" },
  { key: "dependencies", label: "Dependencies" },
  { key: "alternatives", label: "Alternatives" },
];

// ─── The form ────────────────────────────────────────────────────────────────

function DeviceForm({
  device,
  onSaved,
}: {
  device: DeviceDetail;
  onSaved: (next: DeviceDetail) => void;
}) {
  const initial = React.useMemo(
    () => ({
      name: device.name,
      category: device.category ?? "",
      quantity: String(device.quantity_owned),
      price: device.purchase_price
        ? device.purchase_price.replace(".", ",")
        : "",
      purchaseDate: device.purchase_date ?? "",
      weight: decimalForInput(device.weight_kg),
      length: decimalForInput(device.length_cm),
      width: decimalForInput(device.width_cm),
      height: decimalForInput(device.height_cm),
      notes: device.notes ?? "",
    }),
    [device],
  );
  const [draft, setDraft] = React.useState(initial);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => setDraft(initial), [initial]);

  const quantityValid = /^\d+$/.test(draft.quantity.trim());
  const why = !draft.name.trim()
    ? "A device needs a name."
    : !quantityValid
      ? "The quantity owned is a whole number — 0 is fine."
      : device.price_visible && amountIsAmbiguous(draft.price)
        ? AMBIGUOUS_AMOUNT
        : null;

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (why) return;
    const changes: Record<string, unknown> = {};
    if (draft.name !== initial.name) changes.name = draft.name;
    if (draft.category !== initial.category)
      changes.category = draft.category || null;
    if (draft.quantity.trim() !== initial.quantity)
      changes.quantity_owned = Number.parseInt(draft.quantity.trim(), 10);
    if (device.price_visible && draft.price !== initial.price)
      changes.purchase_price = amountForApi(draft.price);
    if (draft.purchaseDate !== initial.purchaseDate)
      changes.purchase_date = draft.purchaseDate || null;
    for (const [key, field] of MEASURE_FIELDS) {
      if (draft[key] !== initial[key])
        changes[field] = decimalForApi(draft[key]);
    }
    if (draft.notes !== initial.notes) changes.notes = draft.notes || null;
    if (Object.keys(changes).length === 0) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.patch<DeviceDetail>(`/devices/${device.id}`, changes));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "The device could not be saved. Try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  const field =
    "mt-1 h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary";
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);

  return (
    <form
      onSubmit={(event) => void save(event)}
      data-testid="device-form"
      className="mt-5"
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-xs text-text-secondary sm:col-span-2">
          Name
          <input
            data-testid="device-name"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            className={field}
          />
        </label>
        <label className="text-xs text-text-secondary">
          Category
          <input
            data-testid="device-category"
            value={draft.category}
            onChange={(e) => setDraft({ ...draft, category: e.target.value })}
            className={field}
          />
        </label>
        <label className="text-xs text-text-secondary">
          Quantity owned
          <input
            data-testid="device-quantity"
            inputMode="numeric"
            value={draft.quantity}
            onChange={(e) => setDraft({ ...draft, quantity: e.target.value })}
            className={field}
          />
        </label>
        {device.price_visible && (
          <label className="text-xs text-text-secondary">
            Purchase price (net, each)
            <input
              data-testid="device-price"
              inputMode="decimal"
              value={draft.price}
              onChange={(e) => setDraft({ ...draft, price: e.target.value })}
              className={field}
            />
          </label>
        )}
        <label className="text-xs text-text-secondary">
          Purchased on
          <input
            type="date"
            data-testid="device-date"
            value={draft.purchaseDate}
            onChange={(e) =>
              setDraft({ ...draft, purchaseDate: e.target.value })
            }
            className={field}
          />
        </label>
        <label className="text-xs text-text-secondary">
          Weight (kg)
          <input
            data-testid="device-weight"
            inputMode="decimal"
            value={draft.weight}
            onChange={(e) => setDraft({ ...draft, weight: e.target.value })}
            className={field}
          />
        </label>
        <fieldset className="text-xs text-text-secondary sm:col-span-2">
          <legend>Size: length × width × height (cm)</legend>
          <div className="mt-1 flex items-center gap-1">
            <input
              aria-label="Length (cm)"
              data-testid="device-length"
              inputMode="decimal"
              value={draft.length}
              onChange={(e) => setDraft({ ...draft, length: e.target.value })}
              className={`${field} mt-0`}
            />
            <span>×</span>
            <input
              aria-label="Width (cm)"
              data-testid="device-width"
              inputMode="decimal"
              value={draft.width}
              onChange={(e) => setDraft({ ...draft, width: e.target.value })}
              className={`${field} mt-0`}
            />
            <span>×</span>
            <input
              aria-label="Height (cm)"
              data-testid="device-height"
              inputMode="decimal"
              value={draft.height}
              onChange={(e) => setDraft({ ...draft, height: e.target.value })}
              className={`${field} mt-0`}
            />
          </div>
        </fieldset>
        <label className="text-xs text-text-secondary sm:col-span-3">
          Notes
          <input
            data-testid="device-notes"
            value={draft.notes}
            onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
            className={field}
          />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="submit"
          data-testid="device-save"
          disabled={busy || !dirty || why !== null}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Save
        </button>
        {why ? (
          <span
            data-testid="device-save-why"
            className="text-xs text-text-tertiary"
          >
            {why}
          </span>
        ) : !dirty ? (
          <span className="text-xs text-text-tertiary">
            Nothing changed yet.
          </span>
        ) : null}
      </div>
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  );
}
