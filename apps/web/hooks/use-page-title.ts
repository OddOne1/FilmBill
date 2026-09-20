'use client'

import { useEffect } from 'react'

/**
 * Sets the browser tab title. Appends " – FilmBill" suffix.
 * Pass null/undefined to reset to default "FilmBill".
 */
export function usePageTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} – FilmBill` : 'FilmBill'
    return () => { document.title = 'FilmBill' }
  }, [title])
}
