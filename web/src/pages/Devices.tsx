import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type Device } from '../api'
import { ErrorBox, Loading } from '../components/ui'
import { formatTs, humanize } from '../format'

export default function Devices() {
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/devices') })
  return (
    <section>
      <header className="page-head">
        <h1>Devices</h1>
        <p className="muted">Household topology from <code>household.toml</code> and the latest simulated snapshot.</p>
      </header>
      {devices.isLoading && <Loading />}
      {devices.isError && <ErrorBox error={devices.error} />}
      {devices.data && (
        <div className="table-wrap">
          <table>
          <thead>
            <tr><th>Device</th><th>Kind</th>{devices.data.some((d) => d.room) && <th>Room</th>}<th>State</th><th>Power</th><th>Firmware</th><th>Circuit</th><th>Network</th><th>Events</th><th>Stories</th><th>As of</th></tr>
          </thead>
          <tbody>
            {devices.data.map((d) => (
              <tr key={d.device_id} className={d.active ? '' : 'muted'}>
                <td><Link to={`/devices/${d.device_id}`}>{d.name}</Link>{d.tags.includes('private') && <span className="pill">private</span>}</td>
                <td>{humanize(d.kind)}</td>
                {d.room !== undefined && <td>{humanize(d.room)}</td>}
                <td><span className={`state-chip state-${d.latest?.state ?? 'unknown'}`}>{d.latest?.state ?? (d.active ? '—' : 'not installed')}</span></td>
                <td>{d.latest?.power_w != null ? `${Math.round(d.latest.power_w)} W` : '—'}</td>
                <td>{d.latest?.firmware_version ?? '—'}</td>
                <td>{d.circuit_id}</td>
                <td>{d.network_id}</td>
                <td>{d.event_count}</td>
                <td>{d.story_count}</td>
                <td className="nowrap muted small">{formatTs(d.latest?.snapshot_ts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
          </div>
      )}
    </section>
  )
}
