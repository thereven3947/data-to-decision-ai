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

import { answerSource } from './answerSource'

describe('answer source label', () => {
  it('names who produced the answer', () => {
    expect(answerSource({ llm_model_tag: 'ollama:qwen3:14b', skill: 'general' }, false).label).toBe(
      '🤖 Live AI answer · qwen3:14b on the Mac mini',
    )
    expect(answerSource({ llm_model_tag: 'ollama:qwen3:14b+fallback', skill: 'general' }, false).kind).toBe('summary')
    expect(answerSource({ llm_model_tag: 'fallback', skill: 'general' }, false).kind).toBe('summary')
    expect(answerSource({ llm_model_tag: 'ollama:qwen3:14b', skill: 'why_question' }, true).label).toBe(
      '📼 Recorded AI answer · qwen3:14b',
    )
    expect(answerSource({ llm_model_tag: 'static demo', skill: 'demo' }, true).kind).toBe('demo')
  })
})
