import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ArchivePage from '../page'

/** UI-1 part 5: the route stays; opened by URL it says, in one sentence,
 *  that the document archive is not there yet — not an empty frame. */
describe('the archive page before P5', () => {
  it('says the document archive is not available yet', () => {
    render(<ArchivePage />)
    expect(screen.getByRole('heading', { name: 'Archive' })).toBeInTheDocument()
    expect(screen.getByTestId('archive-not-yet')).toHaveTextContent(
      'The document archive is not available yet',
    )
  })
})
