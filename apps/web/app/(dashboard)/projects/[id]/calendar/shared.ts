import type { CalendarDay, CalendarStrip } from '@/types'
import { DAY_TYPE_LABELS } from '../../projects-chrome'

/** One colour per day type, the same family the Days tab has always used. */
export const TYPE_COLOURS: Record<string, string> = {
  prep: 'bg-sky-500/80',
  shoot: 'bg-accent',
  travel: 'bg-amber-500/85',
  wrap: 'bg-violet-500/80',
  post: 'bg-emerald-500/80',
  office: 'bg-slate-400/80',
  off: 'bg-bg-tertiary',
}

/** Text that reads on the colour above. `off` is a pale chip, so dark text. */
export const TYPE_TEXT: Record<string, string> = {
  off: 'text-text-secondary',
}

export function typeColour(type: string): string {
  return TYPE_COLOURS[type] ?? 'bg-text-tertiary'
}

export function typeText(type: string): string {
  return TYPE_TEXT[type] ?? 'text-white'
}

export function typeLabel(type: string): string {
  return DAY_TYPE_LABELS[type] ?? type
}

/** What a strip is called on screen: its label, or its type. */
export function stripName(strip: CalendarStrip): string {
  return strip.label || typeLabel(strip.type)
}

/** `3 prep · 8 shoot` from the API's counts, in the vocabulary's order. The
 *  numbers are printed, never computed here. */
export function countsSentence(counts: Record<string, number>, order: string[]): string {
  return order
    .filter((type) => counts[type])
    .map((type) => `${counts[type]} ${typeLabel(type).toLowerCase()}`)
    .join(' · ')
}

export function dayByDate(strip: CalendarStrip): Map<string, CalendarDay> {
  return new Map(strip.days.map((day) => [day.date, day]))
}

/** Which date the pointer is over: the element under it in a real browser
 *  (a strip's day-span or a grid cell, both carry `data-date`), the event's
 *  own target where there is no layout (tests). */
export function dateAtPointer(event: { clientX: number; clientY: number; target: EventTarget | null }): string | null {
  let element: Element | null = null
  if (typeof document !== 'undefined' && typeof document.elementFromPoint === 'function') {
    element = document.elementFromPoint(event.clientX, event.clientY)
  }
  if (!element && event.target instanceof Element) element = event.target
  const holder = element?.closest('[data-date]')
  return holder?.getAttribute('data-date') ?? null
}
