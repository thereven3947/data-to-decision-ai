import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, type ChangeEvent, type Device, type Story } from '../api'
import { Change, Empty, ErrorBox, KindBadge, Loading, StoryCard } from '../components/ui'
import { formatTs, humanize } from '../format'

interface History {
  device: Device
  events: ChangeEvent[]
  stories: Story[]
}

export default function DeviceDetail() {
  const { deviceId = '' } = useParams()
  const h = useQuery({ queryKey: ['device', deviceId], queryFn: () => api<History>(`/devices/${deviceId}`) })
  if (h.isLoading) return <Loading />
  if (h.isError) return <ErrorBox error={h.error} />
  const { device: d, events, stories } = h.data!
  return (
    <section>
      <Link to="/devices" className="back">← Devices</Link>
      <header className="page-head">
        <h1>{d.name}</h1>
        <p className="muted">
          {humanize(d.kind)}{d.room ? ` · ${humanize(d.room)}` : ''} · {d.circuit_id} · {d.network_id} · rated {d.rated_w} W
        </p>
        {d.latest && (
          <div className="row gap wrap">
            <span className={`state-chip state-${d.latest.state}`}>{d.latest.state}</span>
            {d.latest.power_w != null && <span className="pill">{Math.round(d.latest.power_w)} W</span>}
            {d.latest.firmware_version && <span className="pill">fw {d.latest.firmware_version}</span>}
            {d.latest.rssi_dbm != null && <span className="pill">{d.latest.rssi_dbm} dBm</span>}
            {d.latest.setpoint != null && <span className="pill">setpoint {d.latest.setpoint}</span>}
            <span className="muted small">as of {formatTs(d.latest.snapshot_ts)}</span>
          </div>
        )}
      </header>
      <div className="grid-2">
        <div>
          <h2>Stories ({stories.length})</h2>
          {stories.length === 0 && <Empty>No stories for this device.</Empty>}
          <div className="stack">{stories.map((s) => <StoryCard key={s.story_id} story={s} />)}</div>
        </div>
        <div>
          <h2>Change events ({events.length})</h2>
          {events.length === 0 && <Empty>No change events.</Empty>}
          <ol className="timeline">
            {events.map((e) => (
              <li key={e.event_id}>
                <span className="muted small">{formatTs(e.event_ts)}</span>
                <div>
                  <KindBadge kind={e.change_kind} /> <code>{e.property}</code> <Change old={e.old_value} now={e.new_value} />
                </div>
                <small>{humanize(e.semantic_tag)}{e.story_id && <> · <Link to={`/stories/${e.story_id}`}>story</Link></>}</small>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  )
}
