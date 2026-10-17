import { useQuery } from '@tanstack/react-query'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api, ROLES, type Health, type Meta, type Role } from './api'
import { ErrorBoundary } from './components/ErrorBoundary'
import { FeedbackWidget } from './components/Feedback'
import { humanize } from './format'
import Ask from './pages/Ask'
import Changelog from './pages/Changelog'
import DeviceDetail from './pages/DeviceDetail'
import Devices from './pages/Devices'
import Events from './pages/Events'
import NotFound from './pages/NotFound'
import Overview from './pages/Overview'
import Review from './pages/Review'
import Stories from './pages/Stories'
import StoryDetail from './pages/StoryDetail'

const NAV = [
  ['/', 'Overview'],
  ['/stories', 'Stories'],
  ['/events', 'Change events'],
  ['/changelog', 'Changelog'],
  ['/devices', 'Devices'],
  ['/review', 'Review queue'],
  ['/ask', 'Ask the Copilot'],
] as const

export default function App({ role, onRoleChange }: { role: Role; onRoleChange: (r: Role) => void }) {
  const location = useLocation()
  const meta = useQuery({ queryKey: ['meta', role], queryFn: () => api<Meta>('/meta') })
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/health'), refetchInterval: 60_000 })

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="logo">D2D</span>
          <div>
            <strong>
              Change Control <span className="beta">Beta</span>
            </strong>
            <small>Demo · {meta.data?.home.id ?? 'simulated home'}</small>
          </div>
        </div>
        <nav>
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => (isActive ? 'active' : '')}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="role-picker">
          <label htmlFor="role">Viewing as</label>
          <select id="role" value={role} onChange={(e) => onRoleChange(e.target.value as Role)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {humanize(r)}
              </option>
            ))}
          </select>
          <small>{meta.data?.roles.find((r) => r.name === role)?.description}</small>
        </div>
        <div className="status-line" data-testid="health">
          <span className={`dot ${health.data?.ok ? 'ok' : 'bad'}`} />
          API {health.data ? `v${health.data.version} · schema ${health.data.schema}` : health.isError ? 'offline' : '…'}
          <br />
          <span className={`dot ${health.data?.llm_available ? 'ok' : 'warn'}`} />
          {health.data?.llm ? `${health.data.llm}${health.data.llm_available ? '' : ' (offline)'}` : 'LLM off'}
        </div>
        <p className="toy-note">Toy data from the D2D simulator. No real devices or company systems.</p>
      </aside>
      <main className="content">
        <ErrorBoundary key={location.pathname}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/stories" element={<Stories />} />
          <Route path="/stories/:storyId" element={<StoryDetail />} />
          <Route path="/events" element={<Events />} />
          <Route path="/changelog" element={<Changelog />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/devices/:deviceId" element={<DeviceDetail />} />
          <Route path="/review" element={<Review />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
        </ErrorBoundary>
      </main>
      <FeedbackWidget />
    </div>
  )
}
