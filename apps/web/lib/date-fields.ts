/**
 * The second date of a pair (an end, a "to", a "paid on") starts where the
 * first one is.
 *
 * Mathias, 2026-11-25: choosing an end date opened the picker on today or
 * on the project's first month, not on the start just entered. A native
 * date picker opens on its field's value, so an EMPTY end field is given the
 * start date the moment it is focused — before the picker opens — and the
 * picker lands on the right day. Only on focus: an optional end that nobody
 * touches stays empty, which still means "not decided yet".
 */
export function fillFrom(
  value: string | null | undefined,
  from: string | null | undefined,
  set: (next: string) => void,
): () => void {
  return () => {
    if (!value && from) set(from)
  }
}
