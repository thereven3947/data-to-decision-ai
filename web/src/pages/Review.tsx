import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, getRole, type Meta, type ReviewInfo, type Story } from '../api'
import { Empty, ErrorBox, Loading } from '../components/ui'
import { formatTs, humanize, pct } from '../format'

type Item = Story & { review: ReviewInfo }

export default function Review() {
  const meta = useQuery({ queryKey: ['meta', getRole()], queryFn: () => api<Meta>('/meta') })
  const queue = useQuery({ queryKey: ['review'], queryFn: () => api<Item[]>('/review') })
  return (
    <section>
      <header className="page-head">
        <h1>Review queue</h1>
        <p className="muted">
          Stage 5: stories the classifier abstained on, plus a random audit sample of confident labels. Your label
          becomes training data for the next model.
        </p>
        {meta.data && !meta.data.can_label && (
          <p className="notice">Only the owner can label stories. Switch the role to “Owner” to review.</p>
        )}
      </header>
      {queue.isLoading && <Loading />}
      {queue.isError && <ErrorBox error={queue.error} />}
      {queue.data?.length === 0 && <Empty>Nothing to review. 🎉</Empty>}
      {queue.data && queue.data.length > 0 && (
        <table>
          <thead>
            <tr><th>Story</th><th>Reason</th><th>Suggested</th><th>When</th><th>Devices</th><th /></tr>
          </thead>
          <tbody>
            {queue.data.map((s) => (
              <tr key={s.story_id}>
                <td><Link to={`/stories/${s.story_id}`}>{s.title}</Link></td>
                <td><span className={`badge badge-${s.review.reason}`}>{s.review.reason}</span></td>
                <td>{s.review.suggested_category ? `${humanize(s.review.suggested_category)} (${pct(s.review.suggested_confidence)})` : '—'}</td>
                <td className="nowrap">{formatTs(s.window_start)}</td>
                <td>{s.devices.map((d) => d.name).join(', ')}</td>
                <td><Link to={`/stories/${s.story_id}`} className="button">Review →</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
