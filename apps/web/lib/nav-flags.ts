/**
 * Sidebar entries that are switched off on purpose.
 *
 * **Archive is hidden until P5** (UI-1 part 5, SCOPE D123). P5 builds the
 * bookkeeping document archive (SCOPE §9): outgoing documents, incoming
 * bills and receipts, year / quarter / month folders, and the handover
 * packages for the tax advisor. Until then the entry opens a page with
 * nothing on it. Set this to `true` to bring the entry back — nothing else
 * changes: the `/archive` route, the `archive.view` permission and the
 * permission gate on the entry all stay in place.
 *
 * Not the per-list "Show archived" toggles: those show items archived
 * inside their own list (a contact, a project), which is a different thing.
 */
export const ARCHIVE_NAV_ENABLED = false
