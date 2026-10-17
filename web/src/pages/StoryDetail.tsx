import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, getRole, post, type Meta, type StoryDetail as Detail } from '../api'
import { FeedbackButtons } from '../components/Feedback'
import { Change, ErrorBox, KindBadge, LabelInfo, Loading, StatusBadge } from '../components/ui'
import { formatTs, humanize, pct } from '../format'

export default function StoryDetail() {
  const { storyId = '' } = useParams()
  const story = useQuery({ queryKey: ['story', storyId], queryFn: () => api<Detail>(`/stories/${storyId}`) })
  if (story.isLoading) return <Loading />
  if (story.isError) return <ErrorBox error={story.error} />
  const s = story.data!
  return (
    <section>
      <Link to="/stories" className="back">← Stories</Link>
      <header className="page-head">
        <div className="row gap">
          <StatusBadge status={s.status} />
          <LabelInfo story={s} />
        </div>
        <h1>{s.title}</h1>
        <p className="lead">{s.summary}</p>
        <p className="muted small">
          {formatTs(s.window_start)} → {formatTs(s.window_end)} · story <code>{s.story_id}</code> · linked by{' '}
          {s.link_reasons.length ? s.link_reasons.join(', ') : 'single event'}
        </p>
        <FeedbackButtons target={{ type: 'story', id: s.story_id }} />
      </header>

      <div className="grid-2">
        <div>
          <h2>Change events ({s.events.length})</h2>
          <table>
            <thead>
              <tr><th>When</th><th>Device</th><th>Property</th><th>Change</th><th>Meaning</th></tr>
            </thead>
            <tbody>
              {s.events.map((e) => (
                <tr key={e.event_id}>
                  <td className="nowrap">{formatTs(e.event_ts)}</td>
                  <td><Link to={`/devices/${e.device_id}`}>{humanize(e.device_id)}</Link></td>
                  <td><code>{e.property}</code></td>
                  <td><Change old={e.old_value} now={e.new_value} /></td>
                  <td><KindBadge kind={e.change_kind} /> {humanize(e.semantic_tag)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Facts facts={s.enrichment.facts} />
        </div>
        <div>
          {s.playbook && (
            <div className="card">
              <h2>{s.playbook.name}</h2>
              <p>{s.playbook.plain}</p>
              <p className="muted small">{s.playbook.definition} Grid analogue: {s.playbook.grid_analogue}.</p>
              <h3>Checks</h3>
              <ul>{s.playbook.checks.map((c) => <li key={c}>{c}</li>)}</ul>
              <h3>Recommended actions</h3>
              <ul>{s.playbook.actions.map((c) => <li key={c}>{c}</li>)}</ul>
            </div>
          )}
          <ReviewPanel story={s} />
          {s.cases.length > 0 && (
            <div className="card">
              <h3>Cases</h3>
              <ul>{s.cases.map((c) => <li key={c.case_id}>#{c.case_id} {c.title} ({c.status})</li>)}</ul>
            </div>
          )}
          <div className="card lineage">
            <h3>Lineage</h3>
            <dl>
              <dt>Model</dt><dd>{s.model_version ?? '—'}</dd>
              <dt>Linking config</dt><dd>{s.linking_config_sha}</dd>
              <dt>Enrichment template</dt><dd>{s.enrichment.enrichment_template_sha}</dd>
            </dl>
          </div>
        </div>
      </div>
    </section>
  )
}

const FACT_LABELS: Record<string, string> = {
  kwh_shifted: 'kWh moved off-peak per day',
  usd_per_day: 'Saving per day (USD)',
  mean_rssi_gain_db: 'Average signal change (dB)',
  devices_moved: 'Devices moved',
  from_network: 'From network',
  to_network: 'To network',
  error_episodes: 'Error episodes',
  offline_episodes: 'Offline episodes',
  from_version: 'From version',
  to_version: 'To version',
}

function Facts({ facts }: { facts: Record<string, unknown> }) {
  const entries = Object.entries(facts).filter(([, v]) => typeof v === 'number' || typeof v === 'string')
  if (!entries.length) return null
  return (
    <div className="card">
      <h3>Impact</h3>
      <dl>
        {entries.map(([k, v]) => (
          <div key={k} className="fact"><dt>{FACT_LABELS[k] ?? humanize(k)}</dt><dd>{String(v)}</dd></div>
        ))}
      </dl>
    </div>
  )
}

function ReviewPanel({ story }: { story: Detail }) {
  const qc = useQueryClient()
  const meta = useQuery({ queryKey: ['meta', getRole()], queryFn: () => api<Meta>('/meta') })
  const [category, setCategory] = useState(story.category ?? story.review?.suggested_category ?? '')
  const [note, setNote] = useState('')
  const save = useMutation({
    mutationFn: () => post(`/stories/${story.story_id}/label`, { category, note: note || null }),
    onSuccess: () => qc.invalidateQueries(),
  })
  if (!meta.data?.can_label) {
    return story.review && !story.review.resolved_at ? (
      <div className="card"><p className="muted">This story is waiting for the owner's review.</p></div>
    ) : null
  }
  const open = story.review && !story.review.resolved_at
  return (
    <div className={`card review ${open ? 'attention' : ''}`}>
      <h3>{open ? `Review needed (${story.review!.reason})` : 'Label'}</h3>
      {story.review?.suggested_category && open && (
        <p className="small">
          Model suggests <strong>{humanize(story.review.suggested_category)}</strong> at {pct(story.review.suggested_confidence)}.
        </p>
      )}
      <div className="row gap wrap">
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
          <option value="" disabled>Choose a category…</option>
          {meta.data.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <input placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <button type="button" disabled={!category || save.isPending} onClick={() => save.mutate()}>
          {open ? 'Confirm label' : 'Relabel'}
        </button>
      </div>
      {save.isSuccess && <p className="ok small">Saved. The story is published with your label.</p>}
      {save.isError && <p className="error small">{String(save.error)}</p>}
      {story.labels.length > 0 && (
        <details>
          <summary className="small">Label history ({story.labels.length})</summary>
          <ul className="small">
            {story.labels.map((l) => (
              <li key={l.label_id}>
                {formatTs(l.created_at)} · {humanize(l.category)} by {l.reviewer ?? l.source}
                {l.confidence != null && ` (${pct(l.confidence)})`}
                {l.note && ` — ${l.note}`}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
