import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api, getRole, IS_DEMO, post, type Meta } from '../api'
import { humanize } from '../format'

type Target = { type: 'story' | 'message'; id: string }

/** 👍 / 👎 with an optional reason and comment for a story or a chat answer. */
export function FeedbackButtons({ target }: { target: Target }) {
  if (IS_DEMO) return null
  return <FeedbackButtonsLive target={target} />
}

function FeedbackButtonsLive({ target }: { target: Target }) {
  const location = useLocation()
  const meta = useQuery({ queryKey: ['meta', getRole()], queryFn: () => api<Meta>('/meta') })
  const [rating, setRating] = useState<'up' | 'down' | null>(null)
  const [reason, setReason] = useState('')
  const [comment, setComment] = useState('')
  const send = useMutation({
    mutationFn: (r: 'up' | 'down') =>
      post('/feedback', {
        target_type: target.type,
        target_id: target.id,
        rating: r,
        reason: r === 'down' && reason ? reason : null,
        comment: comment || null,
        page: location.pathname,
      }),
  })

  if (send.isSuccess) return <span className="muted small">Thanks — feedback recorded.</span>

  return (
    <div className="feedback">
      <span className="muted small">Was this helpful?</span>
      <button type="button" className={`icon ${rating === 'up' ? 'on' : ''}`} aria-label="Helpful"
        onClick={() => { setRating('up'); send.mutate('up') }}>👍</button>
      <button type="button" className={`icon ${rating === 'down' ? 'on' : ''}`} aria-label="Not helpful"
        onClick={() => setRating('down')}>👎</button>
      {rating === 'down' && (
        <div className="feedback-form">
          <select value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason">
            <option value="">Reason…</option>
            {meta.data?.feedback_reasons.map((r) => (
              <option key={r} value={r}>{humanize(r)}</option>
            ))}
          </select>
          <input placeholder="What was wrong? (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <button type="button" onClick={() => send.mutate('down')} disabled={send.isPending}>Send</button>
        </div>
      )}
      {send.isError && <span className="error small">{String(send.error)}</span>}
    </div>
  )
}

/** Floating "Feedback" button for UI/UX comments on the current page. */
export function FeedbackWidget() {
  if (IS_DEMO) return null
  return <FeedbackWidgetLive />
}

function FeedbackWidgetLive() {
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [comment, setComment] = useState('')
  const send = useMutation({
    mutationFn: () =>
      post('/feedback', { target_type: 'page', target_id: location.pathname, rating: 'comment', comment, page: location.pathname }),
    onSuccess: () => { setComment(''); setTimeout(() => { setOpen(false); send.reset() }, 1200) },
  })
  return (
    <div className="feedback-widget">
      {open && (
        <div className="panel">
          <strong>Feedback on this page</strong>
          <small className="muted">{location.pathname}</small>
          <textarea rows={4} placeholder="What is confusing, missing or great here?" value={comment}
            onChange={(e) => setComment(e.target.value)} />
          <div className="row">
            <button type="button" className="secondary" onClick={() => setOpen(false)}>Close</button>
            <button type="button" disabled={!comment.trim() || send.isPending} onClick={() => send.mutate()}>
              {send.isSuccess ? 'Sent ✓' : 'Send'}
            </button>
          </div>
        </div>
      )}
      <button type="button" className="fab" onClick={() => setOpen((o) => !o)}>💬 Feedback</button>
    </div>
  )
}
