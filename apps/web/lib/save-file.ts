/**
 * Hand a file the API sent back (a CSV export) to the browser as a download.
 *
 * One helper for the crew and gear exports, so neither screen grows its own
 * object-URL bookkeeping.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
