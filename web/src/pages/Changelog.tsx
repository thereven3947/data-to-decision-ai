import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ChangelogRow } from '../api'
import { Change, Empty, ErrorBox, KindBadge, Loading } from '../components/ui'
import { formatTs, humanize } from '../format'

export default function Changelog() {
  const [device, setDevice] = useState('')
  const log = useQuery({ queryKey: ['changelog'], queryFn: () => api<ChangelogRow[]>('/changelog') })
  const devices = [...new Set(log.data?.map((r) => r.device_id))].sort()
  const rows = log.data?.filter((r) => !device || r.device_id === device)
  return (
    <section>
      <header className="page-head">
        <h1>Semantic changelog</h1>
        <p className="muted">
          Stage 2: typed diff of consecutive device snapshots (and CDC changes), with meaning added by{' '}
          <code>semantics.toml</code>. Routine operation (idle ↔ running, small RSSI jitter) is filtered out.
        </p>
      </header>
      <div className="filters">
        <select value={device} onChange={(e) => setDevice(e.target.value)} aria-label="Device">
          <option value="">All devices</option>
          {devices.map((d) => <option key={d} value={d}>{humanize(d)}</option>)}
        </select>
        <span className="muted">{rows?.length ?? 0} rows</span>
      </div>
      {log.isLoading && <Loading />}
      {log.isError && <ErrorBox error={log.error} />}
      {rows?.length === 0 && <Empty>No changelog rows (run the pipeline first).</Empty>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table>
          <thead>
            <tr><th>Before</th><th>After</th><th>Device</th><th>Property</th><th>Change</th><th>Kind</th><th>Semantic tag</th><th>Run</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.event_id}-${r.snapshot_ts_after}`}>
                <td className="nowrap muted">{formatTs(r.snapshot_ts_before)}</td>
                <td className="nowrap">{formatTs(r.snapshot_ts_after)}</td>
                <td><Link to={`/devices/${r.device_id}`}>{humanize(r.device_id)}</Link></td>
                <td><code>{r.property}</code></td>
                <td><Change old={r.old_value} now={r.new_value} /></td>
                <td><KindBadge kind={r.change_kind} /></td>
                <td>{r.semantic_tag}</td>
                <td className="muted small">{r.snapshot_run_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
          </div>
      )}
    </section>
  )
}
