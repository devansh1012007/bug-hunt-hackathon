import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth, homePathFor } from '../lib/auth'
import { ApiRequestError } from '../lib/api'
import { Button, ErrorBanner, Field, Panel } from '../ui/components'

export default function Login() {
  const { login, user, status } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiRequestError | null>(null)

  // Already signed in (or signed in during this render): go to the role home.
  useEffect(() => {
    if (status === 'authed' && user) {
      const from = (location.state as { from?: string } | null)?.from
      navigate(from ?? homePathFor(user.role), { replace: true })
    }
  }, [status, user, navigate, location.state])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await login(email.trim(), password)
      const from = (location.state as { from?: string } | null)?.from
      navigate(from ?? homePathFor(res.user.role), { replace: true })
    } catch (err) {
      setError(err instanceof ApiRequestError ? err : new ApiRequestError({ code: 'UNKNOWN', serverMessage: 'Sign in failed. Try again.' }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="wrap page" style={{ display: 'grid', justifyContent: 'center', alignContent: 'start' }}>
      <div style={{ width: 'min(100%, 440px)', display: 'grid', gap: 16, paddingTop: '7vh' }}>
        <header style={{ display: 'grid', gap: 6 }}>
          <span className="exhibit-num">EX-AUTH</span>
          <h1 className="display" style={{ fontSize: 'clamp(1.6rem, 4vw, 2.2rem)', margin: 0 }}>Sign in</h1>
          <p className="muted small">
            Sign in with your email id. Accounts are registered by an administrator through the admin panel; unregistered
            emails are rejected with a generic error. If you are registered but have no password yet, the password you
            type becomes your password; if you already have one, it is checked. No separate activation step is needed
            for this event. The console opens by role: participants land in the lobby, reviewers in the queue,
            organizers in operations.
          </p>
        </header>

        {error && <ErrorBanner error={error} title="Sign in failed" />}

        <Panel as="section" ariaLabel="Sign in form">
          <form onSubmit={onSubmit} className="stack" style={{ gap: 14 }}>
            <Field label="Email" htmlFor="login-email" required>
              <input
                id="login-email"
                className="input mono"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@vitbhopal.ac.in"
              />
            </Field>
            <Field label="Password" htmlFor="login-password" required>
              <input
                id="login-password"
                className="input mono"
                type="password"
                autoComplete="current-password"
                minLength={12}
                maxLength={200}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Your account password"
              />
            </Field>
            <Button type="submit" variant="primary" disabled={busy || !email || !password}>
              {busy ? 'Signing in' : 'Sign in'}
            </Button>
          </form>
        </Panel>

        <p className="muted small">
          Not registered? Contact an administrator. Accounts are created only by admins. On the first sign-in for
          a registered account with no password, the password entered here becomes that account's password.
          Later sign-ins verify it normally. This event deliberately accepts the risk that the first person who
          knows a registered email can claim its initial password. Password resets are performed by an admin.
        </p>
      </div>
    </div>
  )
}
