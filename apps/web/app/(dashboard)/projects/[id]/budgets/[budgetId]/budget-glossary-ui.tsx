'use client'

import * as React from 'react'
import { HelpCircle, Info } from 'lucide-react'
import { formatAmount } from '@/lib/money'
import { glossary, varianceWords, WORKED_EXAMPLE, type GlossaryId } from '@/lib/budget-glossary'

/**
 * The info icon beside a budget term. A real button, so it is reachable with
 * Tab and opens with Enter or Space — not a hover-only tooltip, which a
 * keyboard and a touch screen never see. The explanation is the glossary's
 * one sentence, nothing composed here.
 */
export function InfoTip({ id }: { id: GlossaryId }) {
  const [open, setOpen] = React.useState(false)
  const entry = glossary(id)
  const noteId = React.useId()
  return (
    <span className="relative inline-flex align-middle">
      <button
        type="button"
        data-testid={`info-${id}`}
        aria-label={`What “${entry.label}” means`}
        aria-expanded={open}
        aria-controls={open ? noteId : undefined}
        onClick={(event) => {
          event.stopPropagation()
          setOpen((previous) => !previous)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false)
        }}
        onBlur={() => setOpen(false)}
        className="ml-0.5 inline-flex rounded text-text-tertiary hover:text-text-primary focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
      >
        <Info className="h-3 w-3" aria-hidden="true" />
      </button>
      {open && (
        <span
          id={noteId}
          role="note"
          data-testid={`info-${id}-text`}
          className="absolute left-0 top-full z-30 mt-1 w-64 rounded-md border border-border bg-bg-primary p-2 text-left text-xs font-normal normal-case text-text-secondary shadow-lg"
        >
          {entry.explanation}
        </span>
      )}
    </span>
  )
}

/** "finished" beside a line, in every phase (B1.1). The words are the
 *  glossary's; whether a line is finished is the API's `finished` flag. */
export function FinishedMarker() {
  return (
    <span
      data-testid="line-finished"
      title={glossary('finished').explanation}
      className="ml-1.5 shrink-0 rounded bg-status-success/10 px-1 text-[10px] font-medium text-status-success"
    >
      finished
    </span>
  )
}

/** A term's label with its info icon. */
export function Term({ id, suffix }: { id: GlossaryId; suffix?: string }) {
  return (
    <span className="inline-flex items-center whitespace-nowrap">
      {glossary(id).label}
      {suffix ?? ''}
      <InfoTip id={id} />
    </span>
  )
}

/** "How to read this": one worked example, every figure from the JSON the
 *  API's test checks against the real tracking module. */
export function HowToRead() {
  const [open, setOpen] = React.useState(false)
  return (
    <div className="mt-3">
      <button
        type="button"
        data-testid="how-to-read"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary"
      >
        <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" /> How to read this
      </button>
      {open && (
        <div data-testid="how-to-read-panel" className="mt-2 max-w-xl rounded-md border border-border p-3 text-xs text-text-secondary">
          <p>{WORKED_EXAMPLE.intro} The columns then show:</p>
          <table className="mt-2 w-full">
            <tbody>
              {WORKED_EXAMPLE.rows.map(([id, value, why]) => (
                <tr key={id} data-testid="how-to-read-row" data-term={id} className="border-b border-border/60">
                  <td className="py-1 pr-2 font-medium text-text-primary">{glossary(id).label}</td>
                  <td className="py-1 pr-2 text-right tabular-nums text-text-primary">
                    {id === 'variance' ? varianceWords(value) : formatAmount(value)}
                  </td>
                  <td className="py-1">{why}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2">{WORKED_EXAMPLE.finished}</p>
        </div>
      )}
    </div>
  )
}
