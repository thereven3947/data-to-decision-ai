import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type Device, type Kpis, type Story } from '../api'
import { Empty, ErrorBox, Loading, StoryCard } from '../components/ui'
import { humanize, pct } from '../format'

export default function Overview() {
  const kpis = useQuery({ queryKey: ['kpis'], queryFn: () => api<Kpis>('/kpis') })
  const stories = useQuery({ queryKey: ['stories'], queryFn: () => api<Story[]>('/stories') })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/devices') })

  const k = kpis.data
  return (
    <section>
      <header className="page-head">
        <h1>Overview</h1>
        <p className="muted">
          What changed in the simulated home, grouped into stories, labeled by the classifier or a reviewer.
        </p>
      </header>

      {kpis.isError && <ErrorBox error={kpis.error} />}
      {k && (
        <div className="kpis">
          <Kpi label="Change stories" value={k.stories} hint={`${k.events} change events`} />
          <Kpi label="Auto-labeled" value={pct(k.auto_label_coverage)} hint="labeled by the model" />
          <Kpi label="Open reviews" value={k.open_reviews} hint={`${k.abstained_open} abstained`} to="/review" />
          <Kpi label="Audit agreement" value={pct(k.audit_agreement)} hint={`${k.audited} model labels reviewed`} />
        </div>
      )}

      <div className="grid-2">
        <div>
          <h2>Latest stories</h2>
          {stories.isLoading && <Loading />}
          {stories.isError && <ErrorBox error={stories.error} />}
          {stories.data?.length === 0 && <Empty>No stories visible for this role.</Empty>}
          <div className="stack">
            {stories.data?.slice(0, 6).map((s) => <StoryCard key={s.story_id} story={s} />)}
          </div>
          {stories.data && stories.data.length > 6 && (
            <Link to="/stories" className="more">All {stories.data.length} stories →</Link>
          )}
        </div>
        <div>
          <h2>Home at a glance</h2>
          {devices.isLoading && <Loading />}
          <div className="device-grid">
            {devices.data?.map((d) => (
              <Link key={d.device_id} to={`/devices/${d.device_id}`} className={`device-tile state-${d.latest?.state ?? 'unknown'}`}>
                <strong>{d.name}</strong>
                <small>{d.room ? humanize(d.room) : humanize(d.kind)}</small>
                <span className="state">{d.latest?.state ?? (d.active ? '—' : 'not installed')}</span>
                {d.story_count > 0 && <span className="pill">{d.story_count} stor{d.story_count === 1 ? 'y' : 'ies'}</span>}
              </Link>
            ))}
          </div>
          {k && (
            <>
              <h2>By category</h2>
              <ul className="bars">
                {Object.entries(k.by_category).map(([c, n]) => (
                  <li key={c}>
                    <Link to={`/stories?category=${c}`}>{humanize(c)}</Link>
                    <span className="bar" style={{ width: `${(100 * n) / Math.max(...Object.values(k.by_category))}%` }} />
                    <span>{n}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

function Kpi({ label, value, hint, to }: { label: string; value: string | number; hint: string; to?: string }) {
  const body = (
    <>
      <span className="kpi-value">{value}</span>
      <span className="kpi-label">{label}</span>
      <small className="muted">{hint}</small>
    </>
  )
  return to ? <Link to={to} className="card kpi">{body}</Link> : <div className="card kpi">{body}</div>
}
