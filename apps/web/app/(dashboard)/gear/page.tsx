"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Download, Plus, Search, Upload } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { deviceCountsSentence } from "@/lib/device-counts";
import { formatMoney } from "@/lib/money";
import { saveBlob } from "@/lib/save-file";
import { usePermissions } from "@/hooks/use-permissions";
import { useCompanyStore } from "@/stores/company-store";
import type { DeviceDetail, DevicePage } from "@/types";
import { GearImport } from "./gear-import";
import {
  AMBIGUOUS_AMOUNT,
  amountForApi,
  amountIsAmbiguous,
  DeviceThumb,
  GearNoAccess,
  GearReadOnlyNotice,
  useGearAccess,
} from "./gear-chrome";

/**
 * Gear — the devices the company owns.
 *
 * Each row shows the two numbers in words: how many are owned (what planning
 * will book against) and how many serial numbers are on file. They may
 * disagree, and that is said, never flagged as an error.
 */
export default function GearPage() {
  const activeCompanyId = useCompanyStore((state) => state.activeCompanyId);
  const { active } = usePermissions();
  const currency = active?.default_currency ?? "EUR";
  const { loaded, canView, canEdit, canSeePrice, refused } = useGearAccess();

  const [search, setSearch] = React.useState("");
  const [category, setCategory] = React.useState("");
  const [includeInactive, setIncludeInactive] = React.useState(false);
  const [page, setPage] = React.useState<DevicePage | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [panel, setPanel] = React.useState<"add" | "import" | null>(null);

  const refresh = React.useCallback(async () => {
    const params = new URLSearchParams();
    if (search.trim()) params.set("search", search.trim());
    if (category) params.set("category", category);
    if (includeInactive) params.set("include_inactive", "true");
    try {
      const query = params.toString();
      setPage(await api.get<DevicePage>(`/devices${query ? `?${query}` : ""}`));
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "The gear list could not be loaded. Try again.",
      );
    }
  }, [search, category, includeInactive]);

  React.useEffect(() => {
    if (!loaded || !canView) return;
    const handle = setTimeout(() => void refresh(), 200);
    return () => clearTimeout(handle);
  }, [loaded, canView, activeCompanyId, refresh]);

  if (refused) return <GearNoAccess />;

  const exportCsv = async () => {
    setError(null);
    try {
      const query = includeInactive ? "?include_inactive=true" : "";
      saveBlob(await api.download(`/devices/export.csv${query}`), "gear.csv");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "The export failed. Try again.",
      );
    }
  };

  const showPrice =
    page?.devices.some((device) => device.price_visible) ?? false;

  return (
    <div className="max-w-6xl p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Gear</h1>
          <p className="mt-1 text-sm text-text-secondary">
            The devices you own. The quantity is what productions will book;
            serial numbers are kept per unit, and the two do not have to match.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canEdit && (
            <>
              <button
                type="button"
                data-testid="gear-add-open"
                onClick={() => setPanel(panel === "add" ? null : "add")}
                className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
              >
                <Plus className="h-3.5 w-3.5" /> Add device
              </button>
              <button
                type="button"
                data-testid="gear-import-open"
                onClick={() => setPanel(panel === "import" ? null : "import")}
                className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-text-primary"
              >
                <Upload className="h-3.5 w-3.5" /> Import CSV
              </button>
            </>
          )}
          <button
            type="button"
            data-testid="gear-export"
            onClick={() => void exportCsv()}
            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-text-primary"
          >
            <Download className="h-3.5 w-3.5" /> Export CSV
          </button>
        </div>
      </div>

      {loaded && canView && !canEdit && <GearReadOnlyNotice />}

      {panel === "add" && canEdit && (
        <AddDevice
          categories={page?.categories ?? []}
          canSeePrice={canSeePrice}
          onClose={() => setPanel(null)}
        />
      )}
      {panel === "import" && canEdit && (
        <GearImport onApplied={() => void refresh()} />
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <label className="relative">
          <span className="sr-only">Search gear</span>
          <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-text-tertiary" />
          <input
            data-testid="gear-search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Name, category or serial number"
            className="h-8 w-72 rounded-md border border-border bg-bg-secondary pl-8 pr-2 text-sm text-text-primary placeholder:text-text-tertiary"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Category{" "}
          <select
            data-testid="gear-category-filter"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            className="ml-1 h-8 rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
          >
            <option value="">All categories</option>
            {(page?.categories ?? []).map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-flex items-center gap-1.5 text-xs text-text-secondary">
          <input
            type="checkbox"
            data-testid="gear-include-inactive"
            checked={includeInactive}
            onChange={(event) => setIncludeInactive(event.target.checked)}
          />
          Show archived devices
        </label>
      </div>

      {error && (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      )}

      {page === null ? (
        !error && (
          <p className="mt-6 text-sm text-text-secondary">
            Loading the gear list…
          </p>
        )
      ) : page.devices.length === 0 ? (
        <p
          data-testid="gear-empty"
          className="mt-6 text-sm text-text-secondary"
        >
          {search || category
            ? "No device matches that. Clear the search or the category to see everything."
            : canEdit
              ? "No gear yet. Add the first device, or import a CSV file."
              : "No gear yet."}
        </p>
      ) : (
        <table className="mt-5 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
              <th className="py-2 pr-3 font-medium">Device</th>
              <th className="py-2 pr-3 font-medium">Category</th>
              <th className="py-2 pr-3 font-medium">Owned and recorded</th>
              {showPrice && (
                <th className="py-2 pr-3 text-right font-medium">
                  Purchase price
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {page.devices.map((device) => (
              <tr
                key={device.id}
                data-testid="gear-row"
                className="border-b border-border/60 align-top"
              >
                <td className="py-2 pr-3">
                  <span className="inline-flex items-center gap-2 align-middle">
                    <DeviceThumb
                      url={device.thumbnail_url}
                      name={device.name}
                      size={28}
                    />
                    <Link
                      href={`/gear/${device.id}`}
                      className="font-medium text-text-primary hover:underline"
                    >
                      {device.name}
                    </Link>
                  </span>
                  {!device.is_active && (
                    <span className="ml-2 rounded bg-bg-tertiary px-1.5 py-0.5 text-2xs text-text-secondary">
                      archived
                    </span>
                  )}
                </td>
                <td className="py-2 pr-3 text-text-secondary">
                  {device.category ?? "—"}
                </td>
                <td
                  data-testid="gear-counts"
                  className="py-2 pr-3 text-text-secondary"
                >
                  {deviceCountsSentence(device.counts)}
                </td>
                {showPrice && (
                  <td className="py-2 pr-3 text-right font-mono text-xs">
                    {device.price_visible
                      ? formatMoney(device.purchase_price, currency)
                      : ""}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AddDevice({
  categories,
  canSeePrice,
  onClose,
}: {
  categories: string[];
  canSeePrice: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const [name, setName] = React.useState("");
  const [category, setCategory] = React.useState("");
  const [quantity, setQuantity] = React.useState("1");
  const [price, setPrice] = React.useState("");
  const [purchaseDate, setPurchaseDate] = React.useState("");
  const [notes, setNotes] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const quantityValid = /^\d+$/.test(quantity.trim());
  const why = !name.trim()
    ? "Type a name, such as Alexa 35."
    : !quantityValid
      ? "The quantity owned is a whole number — 0 is fine."
      : canSeePrice && amountIsAmbiguous(price)
        ? AMBIGUOUS_AMOUNT
        : null;

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (why) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<DeviceDetail>("/devices", {
        name,
        category: category || null,
        quantity_owned: Number.parseInt(quantity.trim(), 10),
        purchase_price: canSeePrice ? amountForApi(price) : null,
        purchase_date: purchaseDate || null,
        notes: notes || null,
      });
      router.push(`/gear/${created.id}`);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "The device could not be saved. Try again.",
      );
      setBusy(false);
    }
  };

  const field =
    "mt-1 h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary";

  return (
    <form
      onSubmit={(event) => void save(event)}
      data-testid="gear-add"
      className="mt-5 rounded-md border border-border bg-bg-secondary p-4"
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-xs text-text-secondary sm:col-span-2">
          Name
          <input
            autoFocus
            data-testid="gear-add-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={field}
          />
        </label>
        <label className="text-xs text-text-secondary">
          Category
          <input
            data-testid="gear-add-category"
            list="gear-categories"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className={field}
          />
          <datalist id="gear-categories">
            {categories.map((entry) => (
              <option key={entry} value={entry} />
            ))}
          </datalist>
        </label>
        <label className="text-xs text-text-secondary">
          Quantity owned
          <input
            data-testid="gear-add-quantity"
            inputMode="numeric"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            className={field}
          />
        </label>
        {canSeePrice && (
          <label className="text-xs text-text-secondary">
            Purchase price (net, each)
            <input
              data-testid="gear-add-price"
              inputMode="decimal"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="65000,00"
              className={field}
            />
          </label>
        )}
        <label className="text-xs text-text-secondary">
          Purchased on
          <input
            type="date"
            data-testid="gear-add-date"
            value={purchaseDate}
            onChange={(e) => setPurchaseDate(e.target.value)}
            className={field}
          />
        </label>
        <label className="text-xs text-text-secondary sm:col-span-3">
          Notes
          <input
            data-testid="gear-add-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className={field}
          />
        </label>
      </div>
      <p className="mt-2 text-xs text-text-tertiary">
        Serial numbers are added on the device&rsquo;s page, one by one or as a
        pasted list. Adding them never changes the quantity owned.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="submit"
          data-testid="gear-add-save"
          disabled={busy || why !== null}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Add device
        </button>
        <button
          type="button"
          onClick={onClose}
          className="text-sm text-text-secondary hover:text-text-primary"
        >
          Close
        </button>
        {why && !busy && (
          <span
            data-testid="gear-add-why"
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
    </form>
  );
}
