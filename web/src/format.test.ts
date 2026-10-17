import { describe, expect, it } from 'vitest'
import { formatTs, humanize, linkCitations, pct, splitCitations } from './format'

describe('format', () => {
  it('formats timestamps in the home time zone', () => {
    expect(formatTs('2026-10-01T10:00:00Z', 'America/Los_Angeles')).toBe('Thu, Oct 1, 03:00')
    expect(formatTs(null)).toBe('—')
  })

  it('humanizes ids and percentages', () => {
    expect(humanize('smart_plug_tv')).toBe('Smart plug tv')
    expect(pct(0.733)).toBe('73%')
    expect(pct(null)).toBe('—')
  })

  it('splits and links story citations', () => {
    const text = 'Shifted [story:bfe961fd6bb47399] and more.'
    expect(splitCitations(text)).toEqual([
      { type: 'text', text: 'Shifted ' },
      { type: 'story', id: 'bfe961fd6bb47399' },
      { type: 'text', text: ' and more.' },
    ])
    expect(linkCitations(text, { bfe961fd6bb47399: 'Load shift' })).toBe(
      'Shifted [↗ Load shift](/stories/bfe961fd6bb47399) and more.',
    )
    expect(linkCitations('[story:0123456789abcdef]')).toBe('[story 012345](/stories/0123456789abcdef)')
  })
})
