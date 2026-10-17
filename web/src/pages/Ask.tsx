import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { Link } from 'react-router-dom'
import { api, ApiError, IS_DEMO, LIVE_URL, post, type ChatAnswer, type Health, type Meta } from '../api'
import { FeedbackButtons } from '../components/Feedback'
import { linkCitations } from '../format'

const SUGGESTIONS = [
  'Brief me on what happened this week',
  'Why did the dishwasher start running at 2 a.m.?',
  'What should I do about the washer?',
  "What's been going on with the garage door?",
  'Which devices had data problems?',
]

type Turn = { question: string; answer?: ChatAnswer; error?: string }

const SESSION_KEY = 'd2d.chat.session'

export default function Ask() {
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/health') })
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api<Meta>('/meta') })
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(sessionStorage.getItem(SESSION_KEY))
  const bottom = useRef<HTMLDivElement>(null)

  const ask = useMutation({
    mutationFn: (q: string) => post<ChatAnswer>('/chat', { question: q, session_id: sessionId }),
    onSuccess: (a) => {
      setSessionId(a.session_id)
      sessionStorage.setItem(SESSION_KEY, a.session_id)
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, answer: a } : x)))
    },
    onError: (e) => {
      const limited = e instanceof ApiError && e.status === 429
      const error = limited ? `⏳ ${e.message}` : `Error: ${e instanceof Error ? e.message : String(e)}`
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error } : x)))
    },
  })

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns.length, ask.isPending])

  const submit = (q: string) => {
    const text = q.trim()
    if (!text || ask.isPending) return
    setTurns((t) => [...t, { question: text }])
    setQuestion('')
    ask.mutate(text)
  }

  const newChat = () => {
    sessionStorage.removeItem(SESSION_KEY)
    setSessionId(null)
    setTurns([])
  }

  return (
    <section className="chat-page">
      <header className="page-head row space">
        <div>
          <h1>Ask the Copilot</h1>
          <p className="muted">
            Read-only assistant on the local model ({health.data?.llm ?? 'no LLM'}). It explains stories and
            proposes actions; it never changes labels. Answers are checked against the data before they are shown.
          </p>
        </div>
        {turns.length > 0 && <button type="button" className="secondary" onClick={newChat}>New chat</button>}
      </header>

      {IS_DEMO && (
        <p className="notice">
          Static demo: the suggested questions replay real answers recorded from the local Copilot.{' '}
          {LIVE_URL ? (
            <>To ask your own questions, <a href={`${LIVE_URL}/ask`}>sign in to the live beta</a> (invite only).</>
          ) : (
            'Free-form questions need the live beta (invite only).'
          )}
        </p>
      )}
      {health.data && !health.data.llm_available && (
        <p className="notice">The local LLM is not reachable, so answers fall back to a data summary.</p>
      )}

      <div className="chat">
        {turns.length === 0 && (
          <div className="suggestions">
            <p className="muted">Try one of these, as role <strong>{meta.data?.role ?? '…'}</strong>:</p>
            {SUGGESTIONS.map((s) => (
              <button type="button" key={s} className="suggestion" onClick={() => submit(s)}>{s}</button>
            ))}
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className="turn">
            <div className="bubble user">{t.question}</div>
            {t.answer && <Answer a={t.answer} />}
            {t.error && <div className={`bubble assistant ${t.error.startsWith('⏳') ? 'limited' : 'error'}`}>{t.error}</div>}
            {!t.answer && !t.error && (
              <div className="bubble assistant thinking" aria-live="polite">
                <span className="spinner" /> Thinking with the local model… (usually 15–40 s)
              </div>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(question) }}>
        <input
          aria-label="Question"
          placeholder="Ask about devices, changes or stories…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={ask.isPending}
        />
        <button type="submit" disabled={!question.trim() || ask.isPending}>Ask</button>
      </form>
    </section>
  )
}

function Answer({ a }: { a: ChatAnswer }) {
  const md = linkCitations(a.answer, a.citations)
  return (
    <div className="bubble assistant">
      <Markdown
        components={{
          a: ({ href, children }) =>
            href?.startsWith('/') ? <Link to={href} className="citation">{children}</Link> : <a href={href}>{children}</a>,
        }}
      >
        {md}
      </Markdown>
      <div className="answer-meta">
        <span className={`badge ${a.checker.ok ? 'badge-published' : 'badge-needs_review'}`}>
          {a.checker.ok ? 'grounded ✓' : 'fallback'}
        </span>
        <span className="muted small">
          skill {a.skill} · {a.llm_model_tag} · {(a.latency_ms / 1000).toFixed(1)} s · tools{' '}
          {a.tool_calls.map((t) => t.name).join(', ') || '—'}
        </span>
      </div>
      {!a.checker.ok && a.checker.errors.length > 0 && (
        <details className="small"><summary>Why fallback?</summary><ul>{a.checker.errors.map((e) => <li key={e}>{e}</li>)}</ul></details>
      )}
      <FeedbackButtons target={{ type: 'message', id: String(a.message_id) }} />
    </div>
  )
}
