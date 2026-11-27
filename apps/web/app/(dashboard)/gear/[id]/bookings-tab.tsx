"use client";

import * as React from "react";
import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { dayLabel } from "@/lib/calendar-dates";
import { cn } from "@/lib/utils";
import type { DeviceBookings, DeviceDetail } from "@/types";

/**
 * The Bookings tab (B2c-2): where this device is booked, in date order (the
 * first day), by project and strip, with the status and a marker when the
 * booking is overbooked. Read-only here — bookings are made and changed on
 * the project's calendar or its Gear tab. The order is the API's.
 */
export function BookingsTab({ device }: { device: DeviceDetail }) {
  const [data, setData] = React.useState<DeviceBookings | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    api
      .get<DeviceBookings>(`/devices/${device.id}/bookings`)
      .then((next) => {
        setData(next);
        setError(null);
      })
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? err.detail
            : "The bookings could not be loaded. Try again.",
        ),
      );
  }, [device.id]);

  if (error) {
    return (
      <p role="alert" className="mt-5 text-sm text-danger">
        {error}
      </p>
    );
  }
  if (!data) {
    return <p className="mt-5 text-sm text-text-secondary">Loading the bookings…</p>;
  }
  if (data.bookings.length === 0) {
    return (
      <p data-testid="device-bookings-empty" className="mt-5 text-sm text-text-secondary">
        {device.name} is not booked on any project. Book it from a strip on a project&rsquo;s calendar.
      </p>
    );
  }
  return (
    <table data-testid="device-bookings" className="mt-5 w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-tertiary">
          <th className="py-2 pr-3 font-medium">Dates</th>
          <th className="py-2 pr-3 font-medium">Project</th>
          <th className="py-2 pr-3 font-medium">Strip</th>
          <th className="py-2 pr-3 font-medium">Quantity</th>
          <th className="py-2 pr-3 font-medium">Status</th>
        </tr>
      </thead>
      <tbody>
        {data.bookings.map((booking) => (
          <tr key={booking.id} data-testid="device-booking-row" className="border-b border-border/60">
            <td className="py-2 pr-3 text-text-secondary">
              {booking.dates.length === 0
                ? "no days"
                : booking.dates.length === 1
                  ? dayLabel(booking.dates[0], true)
                  : `${dayLabel(booking.dates[0])} – ${dayLabel(booking.dates[booking.dates.length - 1], true)} (${booking.dates.length} days)`}
            </td>
            <td className="py-2 pr-3">
              <Link href={`/projects/${booking.project_id}`} className="text-text-primary hover:underline">
                {booking.project_name}
              </Link>
            </td>
            <td className="py-2 pr-3 text-text-secondary">
              {booking.strip_name}
              {booking.added_via_device_name && (
                <span className="text-text-tertiary"> · with {booking.added_via_device_name}</span>
              )}
            </td>
            <td className="py-2 pr-3">{booking.quantity}</td>
            <td className="py-2 pr-3">
              <span
                data-testid="device-booking-status"
                className={cn(
                  "rounded px-1.5 py-0.5 text-2xs",
                  booking.status === "booked"
                    ? "bg-status-success/15 text-status-success"
                    : "bg-status-warning/15 text-status-warning",
                )}
              >
                {booking.status}
              </span>
              {booking.warning && (
                <span title={booking.warning.message} className="ml-1 inline-flex items-center gap-0.5 text-2xs text-status-error">
                  <AlertTriangle className="h-3 w-3" aria-hidden="true" /> overbooked
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
