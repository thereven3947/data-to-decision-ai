import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { getRole, setRole, type Role } from './api'
import App from './App'

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

export default function Root() {
  const [role, setRoleState] = useState<Role>(getRole())
  const changeRole = (r: Role) => {
    setRole(r)
    clientFor(r).clear()
    setRoleState(r)
  }
  return (
    <QueryClientProvider client={clientFor(role)} key={role}>
      <BrowserRouter>
        <App role={role} onRoleChange={changeRole} />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
