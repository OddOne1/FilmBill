/**
 * The guard behind "the screen says Contacts, the code says party" (P1b-4).
 *
 * It reads what a person can SEE or hear — rendered text, and the
 * `aria-label`, `placeholder`, `title` and `alt` attributes — never the
 * source files. A source-text guard would trip over code identifiers
 * (`party.id`, `/parties` API paths) and over comments, which is exactly
 * the false-pass/false-fail shape CLAUDE.md rule 11 forbids.
 */
const WORD = /\bpart(y|ies)\b/i
const ATTRIBUTES = ['aria-label', 'placeholder', 'title', 'alt']

export function findPartyWords(root: ParentNode): string[] {
  const found: string[] = []
  const walker = document.createTreeWalker(root as Node, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent ?? ''
    if (WORD.test(text)) found.push(text.trim())
  }
  root.querySelectorAll('*').forEach((element) => {
    for (const name of ATTRIBUTES) {
      const value = element.getAttribute(name)
      if (value && WORD.test(value)) found.push(`${name}="${value}"`)
    }
  })
  return found
}
