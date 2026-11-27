"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { addDays, dayLabel, todayIso, type IsoDate } from "@/lib/calendar-dates";
import { PlannerGrid } from "@/components/shared/planner-grid";
import type { DeviceAvailability, GearAvailability, GearDay } from "@/types";
import { DeviceThumb } from "./gear-chrome";

/**
 * Gear → Planner (B2c-2): devices as rows, days as columns, on B2c-1's
 * planner grid. Each cell shows booked plus scheduled against owned
 * ("2+1/3") — the API's numbers for the day, not added up here — and an
 * overbooked day is red; a click on a
 * cell lists the projects and strips that hold the device. Nothing is
 * counted here: every number is `GET /gear/availability`'s.
 */

const SPANS = [7, 14, 31];

export function cellState(day: GearDay): "overbooked" | "full" | "used" | "free" {
  if (day.overbooked) return "overbooked";
  if (day.available === 0 && day.owned > 0) return "full";
  if (day.available < day.owned) return "used";
  return "free";
}

const CELL: Record<string, string> = {
  overbooked: "bg-status-error/70 text-white",
  full: "bg-status-warning/50 text-text-primary",
  used: "bg-status-success/30 text-text-primary",
  free: "bg-bg-secondary text-text-tertiary",
};
const WORD: Record<string, string> = {
  overbooked: "overbooked",
  full: "all in use",
  used: "partly in use",
  free: "free",
};

export function GearPlanner({ categories }: { categories: string[] }) {
  const [from, setFrom] = React.useState<IsoDate>(() => todayIso());
  const [span, setSpan] = React.useState(7);
  const [category, setCategory] = React.useState("");
  const [data, setData] = React.useState<GearAvailability | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [open, setOpen] = React.useState<{ device: DeviceAvailability; day: GearDay } | null>(null);
  const to = addDays(from, span - 1);

  React.useEffect(() => {
    const params = new URLSearchParams({ from, to });
    if (category) params.set("category", category);
    const handle = setTimeout(() => {
      api
        .get<GearAvailability>(`/gear/availability?${params.toString()}`)
        .then((loaded) => {
          setData(loaded);
          setError(null);
        })
        .catch((err) =>
          setError(err instanceof ApiError ? err.detail : "The planner could not be loaded. Try again."),
        );
    }, 150);
    return () => clearTimeout(handle);
  }, [from, to, category]);

  return (
    <div data-testid="gear-planner" className="mt-5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button type="button" aria-label="Earlier" onClick={() => setFrom(addDays(from, -span))} className="rounded border border-border p-1">
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <input
          type="date"
          aria-label="From"
          data-testid="gear-planner-from"
          value={from}
          onChange={(event) => event.target.value && setFrom(event.target.value)}
          className="h-8 rounded-md border border-border bg-bg-secondary px-2"
        />
        <button type="button" aria-label="Later" onClick={() => setFrom(addDays(from, span))} className="rounded border border-border p-1">
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
        <select aria-label="Days shown" value={span} onChange={(event) => setSpan(Number(event.target.value))} className="h-8 rounded-md border border-border bg-bg-secondary px-1">
          {SPANS.map((n) => (
            <option key={n} value={n}>
              {n} days
            </option>
          ))}
        </select>
        <select
          aria-label="Category"
          data-testid="gear-planner-category"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          className="h-8 rounded-md border border-border bg-bg-secondary px-1"
        >
          <option value="">All categories</option>
          {categories.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <span className="ml-auto flex gap-2 text-text-tertiary">
          {Object.entries(WORD).map(([state, word]) => (
            <span key={state} className="inline-flex items-center gap-1">
              <span className={cn("h-2.5 w-3 rounded-sm", CELL[state])} />
              {word}
            </span>
          ))}
        </span>
      </div>
      <p className="mt-1 text-2xs text-text-tertiary">Each day: booked (+ scheduled) / owned, as the API counted it.</p>

      {error && (
        <p role="alert" className="mt-3 text-sm text-status-error">
          {error}
        </p>
      )}
      {data && data.devices.length === 0 && (
        <p data-testid="gear-planner-empty" className="mt-4 text-sm text-text-secondary">
          No devices in this category. Choose another category, or add gear first.
        </p>
      )}
      {data && data.devices.length > 0 && (
        <PlannerGrid
          dates={(data.devices[0]?.days ?? []).map((day) => day.date)}
          rows={data.devices}
          rowKey={(device) => device.device_id}
          rowLabel="Device"
          rowTestId="gear-planner-row"
          cellWidth="w-10"
          rowHeader={(device) => (
            <span className="inline-flex items-center gap-1.5">
              <DeviceThumb url={device.thumbnail_url} name={device.name} size={20} />
              <Link href={`/gear/${device.device_id}`} className="text-text-primary hover:underline">
                {device.name}
              </Link>
            </span>
          )}
          cell={(device, index) => {
            const day = device.days[index];
            const state = cellState(day);
            return (
              <button
                type="button"
                data-testid="gear-planner-cell"
                data-state={state}
                aria-label={`${device.name}, ${dayLabel(day.date, true)}: ${day.booked} booked, ${day.scheduled} scheduled of ${day.owned}`}
                onClick={() => setOpen({ device, day })}
                className={cn("block h-5 w-9 rounded-sm text-[10px] leading-5", CELL[state])}
              >
                {day.scheduled > 0 ? `${day.booked}+${day.scheduled}` : day.booked}/{day.owned}
              </button>
            );
          }}
        />
      )}
      {open && (
        <div data-testid="gear-planner-detail" role="status" className="mt-3 rounded-md border border-border bg-bg-secondary p-2 text-xs">
          <p className="font-medium text-text-primary">
            {open.device.name}, {dayLabel(open.day.date, true)}: {open.day.booked} booked, {open.day.scheduled} scheduled,{" "}
            {open.day.available} available of {open.day.owned}
            {open.day.overbooked ? ` — short by ${open.day.shortage}` : ""}
          </p>
          {open.day.holders.length === 0 ? (
            <p className="mt-1 text-text-secondary">Nothing holds it on this day.</p>
          ) : (
            <ul className="mt-1 space-y-0.5 text-text-secondary">
              {open.day.holders.map((holder) => (
                <li key={holder.booking_id}>
                  <Link href={`/projects/${holder.project_id}`} className="text-text-primary hover:underline">
                    {holder.project_name}
                  </Link>{" "}
                  · strip “{holder.strip_name}” · {holder.status} {holder.quantity}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
