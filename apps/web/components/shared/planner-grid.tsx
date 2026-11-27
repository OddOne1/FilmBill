'use client'

import * as React from 'react'
import { weekdayLetter, type IsoDate } from '@/lib/calendar-dates'

/**
 * The planner grid (B2c-1, shared with gear in B2c-2): rows down the side,
 * days across the top, one cell per row and day. It only lays things out —
 * what a cell says and how it is coloured is the caller's, from the API's
 * numbers. Contacts → Crew → Availability and Gear → Planner both use it, so
 * the two planners can never drift apart in how they read.
 */
export function PlannerGrid<Row>({
  dates,
  rows,
  rowKey,
  rowLabel,
  rowHeader,
  extraColumns = [],
  cell,
  rowTestId,
  cellWidth = 'w-7',
}: {
  dates: IsoDate[]
  rows: Row[]
  rowKey: (row: Row) => string
  /** The first column's heading ("Person", "Device"). */
  rowLabel: string
  rowHeader: (row: Row) => React.ReactNode
  extraColumns?: { label: string; render: (row: Row) => React.ReactNode }[]
  cell: (row: Row, index: number) => React.ReactNode
  rowTestId?: string
  cellWidth?: string
}) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="border-collapse text-xs">
        <thead>
          <tr>
            <th className="sticky left-0 bg-bg-primary py-1 pr-3 text-left font-medium text-text-tertiary">{rowLabel}</th>
            {extraColumns.map((column) => (
              <th key={column.label} className="py-1 pr-2 text-left font-medium text-text-tertiary">
                {column.label}
              </th>
            ))}
            {dates.map((date) => (
              <th key={date} className={`${cellWidth} px-0.5 py-1 text-center font-normal text-text-tertiary`}>
                <div>{weekdayLetter(date)}</div>
                <div>{Number(date.slice(8, 10))}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} data-testid={rowTestId}>
              <td className="sticky left-0 bg-bg-primary py-0.5 pr-3">{rowHeader(row)}</td>
              {extraColumns.map((column) => (
                <td key={column.label} className="pr-2 text-text-secondary">
                  {column.render(row)}
                </td>
              ))}
              {dates.map((date, index) => (
                <td key={date} className="px-0.5 py-0.5">
                  {cell(row, index)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
