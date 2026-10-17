import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { api, getRole, post, setPreviewRole, setRole, UNAUTHORIZED_EVENT, type AuthStatus, type Role } from './api'
import App from './App'
import Login from './pages/Login'

// One cache per role: a response fetched for one role can never be shown to another,
// even if the request was still in flight when the role changed.
const clients = new Map<Role, QueryClient>()
function clientFor(role: Role): QueryClient {
  let c = clients.get(role)
  if (!c) {
    c = new QueryClient({
      defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 30_000 } },
    })
    clients.set(role, c)
  }
  return c
}

function forgetAll() {
  for (const c of clients.values()) c.clear()
  clients.clear()
}

export default function Root() {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [chosen, setChosen] = useState<Role>(getRole())

  const refresh = useCallback(() => {
    api<AuthStatus>('/auth/status')
      .then((s) => {
        setStatus(s)
        setError(null)
      })
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    refresh()
    const onUnauthorized = () => {
      forgetAll()
      refresh()
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [refresh])

  if (!status) {
    return <div className="center-page">{error ? <p className="error">API unreachable: {error}</p> : <p className="muted">Loading…</p>}</div>
  }
  if (status.auth_enabled && !status.user) {
    return <Login usersConfigured={status.users_configured} onLoggedIn={() => { forgetAll(); refresh() }} />
  }

  const user = status.user
  const canSwitch = !status.auth_enabled || user?.role === 'owner'
  const role: Role = canSwitch ? chosen : user!.role
  setPreviewRole(canSwitch ? role : null)

  const changeRole = (r: Role) => {
    setRole(r)
    clientFor(r).clear()
    setChosen(r)
  }
  const logout = async () => {
    await post('/auth/logout', {}).catch(() => undefined)
    forgetAll()
    refresh()
  }

  return (
    <QueryClientProvider client={clientFor(role)} key={`${user?.username ?? '-'}:${role}`}>
      <BrowserRouter>
        <App role={role} onRoleChange={changeRole} canSwitch={canSwitch} user={user} onLogout={logout} />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
