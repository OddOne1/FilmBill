import { describe, it, expect } from 'vitest'
import { formatRelativeTime, truncate, cn } from '../utils'

describe('formatRelativeTime', () => {
  it('returns "just now" for recent timestamps (within 60s)', () => {
    const recent = new Date(Date.now() - 30000).toISOString()
    expect(formatRelativeTime(recent)).toBe('just now')
  })

  it('returns minutes ago', () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString()
    expect(formatRelativeTime(fiveMinAgo)).toBe('5 minutes ago')
  })

  it('returns singular "minute ago"', () => {
    const oneMinAgo = new Date(Date.now() - 65 * 1000).toISOString()
    expect(formatRelativeTime(oneMinAgo)).toBe('1 minute ago')
  })

  it('returns hours ago', () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
    expect(formatRelativeTime(twoHoursAgo)).toBe('2 hours ago')
  })

  it('returns days ago', () => {
    const threeDaysAgo = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString()
    expect(formatRelativeTime(threeDaysAgo)).toBe('3 days ago')
  })
})

describe('truncate', () => {
  it('returns short string unchanged', () => {
    expect(truncate('hello', 10)).toBe('hello')
  })

  it('truncates long string with ellipsis', () => {
    expect(truncate('hello world', 5)).toBe('hello...')
  })

  it('returns string unchanged when equal to length', () => {
    expect(truncate('hello', 5)).toBe('hello')
  })
})

describe('cn', () => {
  it('merges class names', () => {
    const result = cn('foo', 'bar')
    expect(result).toContain('foo')
    expect(result).toContain('bar')
  })

  it('handles conditional classes', () => {
    const result = cn('base', false && 'conditional')
    expect(result).toContain('base')
    expect(result).not.toContain('conditional')
  })

  it('merges conflicting tailwind classes (last wins)', () => {
    const result = cn('text-red-500', 'text-blue-500')
    expect(result).toBe('text-blue-500')
  })
})
