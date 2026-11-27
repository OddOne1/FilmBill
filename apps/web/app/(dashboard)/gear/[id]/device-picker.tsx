"use client";

import * as React from "react";
import { api } from "@/lib/api";
import type { DevicePage } from "@/types";
import { DeviceThumb } from "../gear-chrome";

type Row = DevicePage["devices"][number];

/** The active devices of the company, for a dialog that links another
 *  device to this one. Loaded when the dialog opens. */
export function useOtherDevices(open: boolean, excludeId: string): Row[] {
  const [devices, setDevices] = React.useState<Row[]>([]);
  React.useEffect(() => {
    if (!open) return;
    api
      .get<DevicePage>("/devices?limit=2000")
      .then((page) =>
        setDevices(
          page.devices.filter((row) => row.id !== excludeId && row.is_active),
        ),
      )
      .catch(() => setDevices([]));
  }, [open, excludeId]);
  return devices;
}

/** A search box over a listbox of devices, each with its picture. */
export function DevicePicker({
  devices,
  chosen,
  onChoose,
}: {
  devices: Row[];
  chosen: string | null;
  onChoose: (id: string) => void;
}) {
  const [search, setSearch] = React.useState("");
  const needle = search.trim().toLowerCase();
  const shown = devices.filter(
    (row) => !needle || row.name.toLowerCase().includes(needle),
  );
  return (
    <>
      <input
        aria-label="Find a device"
        data-testid="device-picker-search"
        value={search}
        placeholder="Find a device"
        onChange={(event) => setSearch(event.target.value)}
        className="mt-4 h-8 w-full rounded-md border border-border bg-bg-secondary px-2 text-sm text-text-primary"
      />
      <ul
        role="listbox"
        aria-label="Devices"
        className="mt-2 max-h-48 overflow-y-auto rounded-md border border-border"
      >
        {shown.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              role="option"
              aria-selected={chosen === row.id}
              data-testid="device-picker-option"
              onClick={() => onChoose(row.id)}
              className={`flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm ${
                chosen === row.id
                  ? "bg-accent/10 text-text-primary"
                  : "text-text-secondary hover:bg-bg-tertiary"
              }`}
            >
              <DeviceThumb url={row.thumbnail_url} name={row.name} size={24} />
              {row.name}
              {row.category && (
                <span className="text-xs text-text-tertiary">
                  {row.category}
                </span>
              )}
            </button>
          </li>
        ))}
        {shown.length === 0 && (
          <li className="px-2 py-1.5 text-sm text-text-tertiary">
            No device matches.
          </li>
        )}
      </ul>
    </>
  );
}
