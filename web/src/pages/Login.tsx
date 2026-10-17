import { useState, type FormEvent } from 'react'
import { ApiError, post } from '../api'

export default function Login({ usersConfigured, onLoggedIn }: { usersConfigured: boolean; onLoggedIn: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await post('/auth/login', { username: username.trim().toLowerCase(), password })
      setPassword('')
      onLoggedIn()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="center-page">
      <form className="card login" onSubmit={submit}>
        <div className="brand dark">
          <span className="logo">D2D</span>
          <div>
            <strong>Change Control <span className="beta">Beta</span></strong>
            <small>Demo on a simulated smart home</small>
          </div>
        </div>
        <h1>Sign in</h1>
        {!usersConfigured && (
          <p className="notice small">
            No accounts yet. On the Mac mini run <code>d2d user add &lt;name&gt; --role owner --env production</code>
            {' '}(see docs/deployment.md).
          </p>
        )}
        <label>
          Username
          <input autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="error small" role="alert">{error}</p>}
        <button type="submit" disabled={busy || !username || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <p className="muted small">Private beta: owner access only. Toy data; no real devices or company systems.</p>
      </form>
    </div>
  )
}
