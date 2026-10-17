import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ChangeEvent } from '../api'
import { Change, Empty, ErrorBox, KindBadge, Loading } from '../components/ui'
import { formatTs, humanize } from '../format'

const KINDS = ['config', 'operational', 'topology', 'data_quality']

export default function Events() {
  const [kind, setKind] = useState('')
  const [device, setDevice] = useState('')
  const events = useQuery({
    queryKey: ['events', kind],
    queryFn: () => api<ChangeEvent[]>(`/events${kind ? `?change_kind=${kind}` : ''}`),
  })
  const rows = events.data?.filter((e) => !device || e.device_id.includes(device.toLowerCase().replace(/ /g, '_')))
  return (
    <section>
      <header className="page-head">
        <h1>Change events</h1>
        <p className="muted">
          Stage 3: one row per meaningful change, with an idempotent id, from the snapshot diff and the CDC stream.
        </p>
      </header>
      <div className="filters">
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Change kind">
          <option value="">All kinds</option>
          {KINDS.map((k) => <option key={k} value={k}>{humanize(k)}</option>)}
        </select>
        <input placeholder="Filter by device…" value={device} onChange={(e) => setDevice(e.target.value)} />
        <span className="muted">{rows?.length ?? 0} events</span>
      </div>
      {events.isLoading && <Loading />}
      {events.isError && <ErrorBox error={events.error} />}
      {rows?.length === 0 && <Empty>No events.</Empty>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table>
          <thead>
            <tr><th>When</th><th>Device</th><th>Property</th><th>Change</th><th>Kind</th><th>Tag</th><th>Story</th></tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.event_id}>
                <td className="nowrap">{formatTs(e.event_ts)}</td>
                <td><Link to={`/devices/${e.device_id}`}>{humanize(e.device_id)}</Link></td>
                <td><code>{e.property}</code></td>
                <td><Change old={e.old_value} now={e.new_value} /></td>
                <td><KindBadge kind={e.change_kind} /></td>
                <td>{humanize(e.semantic_tag)}</td>
                <td>{e.story_id ? <Link to={`/stories/${e.story_id}`}>open</Link> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
          </div>
      )}
    </section>
  )
}
