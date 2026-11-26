'use client'

/**
 * The document archive's address, kept while there is nothing behind it.
 *
 * UI-1 hid the Archive entry from the sidebar until P5 builds the
 * bookkeeping document archive (SCOPE §9, `lib/nav-flags.ts`). The route
 * stays so an old bookmark does not 404 and so P5 has its place; somebody
 * who opens it by URL gets one plain sentence rather than an empty frame
 * (rule 17c). Nothing is fetched and nothing is gated here: there is no
 * data to protect yet. When P5 fills it in, its endpoints enforce
 * `require("archive.view")` on their own and this stays presentation.
 */
export default function ArchivePage() {
  return (
    <div className="p-8">
      <h1 className="text-lg font-semibold text-text-primary">Archive</h1>
      <p data-testid="archive-not-yet" className="mt-2 text-sm text-text-secondary">
        The document archive is not available yet — it comes with a later
        release.
      </p>
    </div>
  )
}
