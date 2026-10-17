import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Story } from '../api'
import { formatTs, pct } from '../format'

export function Badge({ kind, children }: { kind: string; children: ReactNode }) {
  return <span className={`badge badge-${kind}`}>{children}</span>
}

export function StatusBadge({ status }: { status: Story['status'] }) {
  const label = { new: 'New', needs_review: 'Needs review', published: 'Published', closed: 'Closed' }[status]
  return <Badge kind={status}>{label}</Badge>
}

export function LabelInfo({ story }: { story: Pick<Story, 'category' | 'category_name' | 'label_source' | 'confidence'> }) {
  if (!story.category) return <span className="muted">Unlabeled</span>
  const source = { model: 'model', human: 'human', sim_reviewer: 'simulated reviewer' }[story.label_source ?? 'model']
  return (
    <span className="label-info">
      <Badge kind="category">{story.category_name ?? story.category}</Badge>
      <small className="muted">
        {source}
        {story.confidence != null && ` · ${pct(story.confidence)}`}
      </small>
    </span>
  )
}

export function StoryCard({ story }: { story: Story }) {
  return (
    <Link to={`/stories/${story.story_id}`} className="card story-card">
      <div className="row space">
        <LabelInfo story={story} />
        <StatusBadge status={story.status} />
      </div>
      <h3>{story.title}</h3>
      <p className="summary">{story.summary}</p>
      <small className="muted">
        {formatTs(story.window_start)} · {story.event_count} event{story.event_count === 1 ? '' : 's'} ·{' '}
        {story.devices.map((d) => d.name).join(', ')}
      </small>
    </Link>
  )
}

export function Loading({ what = 'Loading' }: { what?: string }) {
  return <p className="muted">{what}…</p>
}

export function ErrorBox({ error }: { error: unknown }) {
  return <p className="error">Could not load: {error instanceof Error ? error.message : String(error)}</p>
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}

export function KindBadge({ kind }: { kind: string }) {
  return <Badge kind={`kind-${kind}`}>{kind.replace('_', ' ')}</Badge>
}

export function Change({ old, now }: { old: string | null; now: string | null }) {
  return (
    <span className="change">
      <code>{old ?? '∅'}</code> → <code>{now ?? '∅'}</code>
    </span>
  )
}
