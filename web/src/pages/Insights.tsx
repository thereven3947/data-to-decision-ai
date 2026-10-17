import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api'
import { Empty, ErrorBox, Loading } from '../components/ui'
import { formatTs, humanize, pct } from '../format'

interface Insights {
  pipeline: {
    stories: number
    events: number
    auto_label_coverage: number | null
    open_reviews: number
    audit_agreement: number | null
    audited: number
    by_label_source: Record<string, number>
  }
  review: { open_by_reason: Record<string, number>; resolved_last_7d: number; median_hours_to_review: number | null }
  copilot: {
    answers: number
    answers_last_7d: number
    grounded_rate: number | null
    fallback_answers: number
    latency_ms_median: number | null
    latency_ms_p95: number | null
    thumbs_up: number
    thumbs_down: number
    helpful_rate: number | null
    by_skill: Record<string, number>
  }
  feedback: {
    total: number
    unrouted: number
    by_target: Record<string, number>
    by_reason: Record<string, number>
    by_route: Record<string, number>
    recent: {
      feedback_id: number
      target_type: string
      target_id: string
      rating: string
      reason: string | null
      comment: string | null
      page: string | null
      username: string | null
      user_role: string
      routed_to: string | null
      created_at: string
    }[]
  }
  eval_sets: Record<string, { total: number; pending_export: number }>
  notifications: {
    by_status: Record<string, number>
    by_severity: Record<string, number>
    recent: { story_id: string; severity: string; channel: string; status: string; title: string; created_at: string; sent_at: string | null }[]
  }
}

const sec = (ms: number | null) => (ms == null ? '—' : `${(ms / 1000).toFixed(1)} s`)

function Counts({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data)
  if (!entries.length) return <span className="muted">none</span>
  return (
    <span className="counts">
      {entries.map(([k, v]) => (
        <span key={k} className="pill">{humanize(k)} {v}</span>
      ))}
    </span>
  )
}

export default function InsightsPage() {
  const q = useQuery({ queryKey: ['insights'], queryFn: () => api<Insights>('/insights'), refetchInterval: 60_000 })
  if (q.isLoading) return <Loading />
  if (q.error instanceof ApiError && q.error.status === 403) {
    return <section><h1>Insights</h1><p className="notice">Insights are for the owner. Switch “View as” back to Owner.</p></section>
  }
  if (q.isError) return <ErrorBox error={q.error} />
  const d = q.data!
  return (
    <section>
      <header className="page-head">
        <h1>Insights</h1>
        <p className="muted">
          How the loop is doing: automation, reviews, Copilot quality, feedback and notifications. Feedback is routed
          to the layer that can fix it and exported nightly into eval sets through a reviewed pull request.
        </p>
      </header>

      <div className="kpis">
        <div className="card kpi"><span className="kpi-value">{pct(d.pipeline.auto_label_coverage)}</span><span className="kpi-label">Auto-labeled</span><small className="muted">{d.pipeline.stories} stories</small></div>
        <div className="card kpi"><span className="kpi-value">{pct(d.pipeline.audit_agreement)}</span><span className="kpi-label">Audit agreement</span><small className="muted">{d.pipeline.audited} model labels reviewed</small></div>
        <div className="card kpi"><span className="kpi-value">{pct(d.copilot.grounded_rate)}</span><span className="kpi-label">Grounded answers</span><small className="muted">{d.copilot.answers} answers · p95 {sec(d.copilot.latency_ms_p95)}</small></div>
        <div className="card kpi"><span className="kpi-value">{pct(d.copilot.helpful_rate)}</span><span className="kpi-label">Helpful (👍)</span><small className="muted">{d.copilot.thumbs_up} 👍 · {d.copilot.thumbs_down} 👎</small></div>
      </div>

      <div className="grid-2">
        <div>
          <h2>Feedback inbox</h2>
          {d.feedback.recent.length === 0 && <Empty>No comments or thumbs-down yet.</Empty>}
          {d.feedback.recent.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead><tr><th>When</th><th>On</th><th>Reason</th><th>Comment</th><th>Routed to</th></tr></thead>
                <tbody>
                  {d.feedback.recent.map((f) => (
                    <tr key={f.feedback_id}>
                      <td className="nowrap">{formatTs(f.created_at)}</td>
                      <td>
                        {f.target_type === 'story' ? <Link to={`/stories/${f.target_id}`}>story</Link> : f.target_type === 'page' ? <code>{f.page}</code> : `answer #${f.target_id}`}
                        <br /><small className="muted">{f.username ?? f.user_role}</small>
                      </td>
                      <td>{f.rating === 'down' ? '👎 ' : ''}{humanize(f.reason ?? (f.rating === 'comment' ? 'comment' : '—'))}</td>
                      <td>{f.comment ?? <span className="muted">—</span>}</td>
                      <td>{f.routed_to ? humanize(f.routed_to) : <span className="muted">next run</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h2>Notifications</h2>
          <p className="small">By status: <Counts data={d.notifications.by_status} /></p>
          <p className="small">By severity: <Counts data={d.notifications.by_severity} /></p>
          {d.notifications.recent.length > 0 && (
            <ul className="small">
              {d.notifications.recent.map((n) => (
                <li key={n.story_id}>
                  <span className={`badge badge-sev-${n.severity}`}>{n.severity}</span>{' '}
                  <Link to={`/stories/${n.story_id}`}>{n.title}</Link> · {n.status}
                  {n.sent_at && ` ${formatTs(n.sent_at)}`}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <div className="card">
            <h3>Review</h3>
            <dl>
              <dt>Open</dt><dd><Counts data={d.review.open_by_reason} /></dd>
              <dt>Resolved (7 days)</dt><dd>{d.review.resolved_last_7d}</dd>
              <dt>Median time to review</dt><dd>{d.review.median_hours_to_review == null ? '—' : `${d.review.median_hours_to_review} h`}</dd>
              <dt>Label sources</dt><dd><Counts data={d.pipeline.by_label_source} /></dd>
            </dl>
          </div>
          <div className="card">
            <h3>Copilot</h3>
            <dl>
              <dt>Answers (7 days)</dt><dd>{d.copilot.answers_last_7d}</dd>
              <dt>Median latency</dt><dd>{sec(d.copilot.latency_ms_median)}</dd>
              <dt>Fallback answers</dt><dd>{d.copilot.fallback_answers}</dd>
              <dt>By skill</dt><dd><Counts data={d.copilot.by_skill} /></dd>
            </dl>
          </div>
          <div className="card">
            <h3>Feedback routing</h3>
            <dl>
              <dt>Total</dt><dd>{d.feedback.total} ({d.feedback.unrouted} waiting for the next run)</dd>
              <dt>Routed to</dt><dd><Counts data={d.feedback.by_route} /></dd>
              <dt>Thumbs-down reasons</dt><dd><Counts data={d.feedback.by_reason} /></dd>
              <dt>Eval sets</dt>
              <dd>
                {Object.keys(d.eval_sets).length === 0 ? <span className="muted">none yet</span> : (
                  <span className="counts">
                    {Object.entries(d.eval_sets).map(([k, v]) => (
                      <span key={k} className="pill">{k} {v.total}{v.pending_export ? ` (${v.pending_export} to export)` : ''}</span>
                    ))}
                  </span>
                )}
              </dd>
            </dl>
          </div>
        </div>
      </div>
    </section>
  )
}
