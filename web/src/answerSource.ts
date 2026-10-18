import type { ChatAnswer } from './api'

export type AnswerSource = { kind: 'ai' | 'recorded' | 'summary' | 'demo'; label: string; detail: string }

/** Who produced an answer, in plain words (the first line of every answer's footer). */
export function answerSource(a: Pick<ChatAnswer, 'llm_model_tag' | 'skill'>, demo: boolean): AnswerSource {
  const model = a.llm_model_tag.replace(/^ollama:/, '').replace(/\+fallback$/, '')
  if (a.skill === 'demo') {
    return { kind: 'demo', label: '💬 Demo note', detail: 'Fixed text in the static demo; no AI involved.' }
  }
  if (a.llm_model_tag === 'fallback' || a.llm_model_tag.endsWith('+fallback')) {
    return {
      kind: 'summary',
      label: '📋 Data summary, no AI',
      detail:
        'Built directly from the data by a fixed template, because the local model was unavailable or its answer failed the fact check.',
    }
  }
  if (demo) {
    return {
      kind: 'recorded',
      label: `📼 Recorded AI answer · ${model}`,
      detail: 'Generated earlier by the local language model on the Mac mini and replayed here; the static demo has no live AI.',
    }
  }
  return {
    kind: 'ai',
    label: `🤖 Live AI answer · ${model} on the Mac mini`,
    detail: 'Written just now by the local language model, from the household data it looked up with read-only tools, then fact-checked.',
  }
}
