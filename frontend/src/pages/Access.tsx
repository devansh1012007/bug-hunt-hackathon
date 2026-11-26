import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api, ApiRequestError } from '../lib/api'
import { Button, ErrorBanner, Field, Panel } from '../ui/components'

function failure(error: unknown): ApiRequestError {
  return error instanceof ApiRequestError
    ? error
    : new ApiRequestError({ code: 'UNKNOWN', serverMessage: 'The request could not be completed.' })
}

export function ActivateInvite() {
  const location = useLocation()
  const navigate = useNavigate()
  const inviteToken = new URLSearchParams(location.search).get('invite') ?? ''
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [accepted, setAccepted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiRequestError | null>(null)
  const [done, setDone] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (password !== confirmPassword) {
      setError(new ApiRequestError({ code: 'PASSWORD_MISMATCH', serverMessage: 'The passwords do not match.' }))
      return
    }
    setBusy(true)
    try {
      await api('/v1/auth/activate', {
        method: 'POST',
        body: { invite_token: inviteToken, password, accept_rules: accepted },
      })
      setDone(true)
      setPassword('')
      setConfirmPassword('')
    } catch (err) {
      setError(failure(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="wrap page" style={{ display: 'grid', justifyContent: 'center', alignContent: 'start' }}>
      <div style={{ width: 'min(100%, 520px)', display: 'grid', gap: 16, paddingTop: '7vh' }}>
        <header className="stack">
          <span className="exhibit-num">EX-INVITE</span>
          <h1 className="display" style={{ margin: 0 }}>Activate invitation</h1>
          <p className="muted small">Set a password for the account attached to this one-time invitation, then sign in.</p>
        </header>
        {error && <ErrorBanner error={error} title="Activation failed" />}
        {done ? (
          <Panel title="Invitation activated" exhibit="EX-OK">
            <p>Your account is active and the event rules have been accepted.</p>
            <Button variant="primary" onClick={() => navigate('/login', { replace: true })}>Continue to sign in</Button>
          </Panel>
        ) : !inviteToken ? (
          <Panel title="Invitation token missing" exhibit="EX-404">
            <p className="muted">Open the complete activation link sent by the organizers. The invitation token is part of that link.</p>
            <Link className="btn btn-primary" to="/login">Back to sign in</Link>
          </Panel>
        ) : (
          <Panel ariaLabel="Invitation activation form">
            <form onSubmit={submit} className="stack" style={{ gap: 14 }}>
              <Field label="New password" htmlFor="activate-password" required hint="At least 12 characters.">
                <input id="activate-password" className="input mono" type="password" autoComplete="new-password" minLength={12} maxLength={200} required value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
              <Field label="Confirm password" htmlFor="activate-confirm" required>
                <input id="activate-confirm" className="input mono" type="password" autoComplete="new-password" minLength={12} maxLength={200} required value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} />
              </Field>
              <label className="small" style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} required />
                <span>I have read and accept the <Link to="/rules">competition rules</Link>.</span>
              </label>
              <Button type="submit" variant="primary" disabled={busy || !accepted || password.length < 12 || password !== confirmPassword}>
                {busy ? 'Activating' : 'Activate account'}
              </Button>
            </form>
          </Panel>
        )}
        <p className="muted small"><Link to="/login">Return to sign in</Link></p>
      </div>
    </div>
  )
}

export function PasswordReset() {
  const location = useLocation()
  const navigate = useNavigate()
  const tokenFromUrl = new URLSearchParams(location.search).get('token') ?? ''
  const [email, setEmail] = useState('')
  const [token, setToken] = useState(tokenFromUrl)
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiRequestError | null>(null)
  const [requestSent, setRequestSent] = useState(false)
  const [resetDone, setResetDone] = useState(false)

  async function requestReset(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setRequestSent(false)
    try {
      await api('/v1/auth/password/reset-request', { method: 'POST', body: { email: email.trim() } })
      setRequestSent(true)
    } catch (err) {
      setError(failure(err))
    } finally {
      setBusy(false)
    }
  }

  async function completeReset(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (password !== confirmPassword) {
      setError(new ApiRequestError({ code: 'PASSWORD_MISMATCH', serverMessage: 'The passwords do not match.' }))
      return
    }
    setBusy(true)
    try {
      await api('/v1/auth/password/reset', { method: 'POST', body: { token: token.trim(), password } })
      setResetDone(true)
      setPassword('')
      setConfirmPassword('')
    } catch (err) {
      setError(failure(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="wrap page" style={{ display: 'grid', justifyContent: 'center', alignContent: 'start' }}>
      <div style={{ width: 'min(100%, 520px)', display: 'grid', gap: 16, paddingTop: '7vh' }}>
        <header className="stack">
          <span className="exhibit-num">EX-RECOVERY</span>
          <h1 className="display" style={{ margin: 0 }}>Reset password</h1>
          <p className="muted small">Request a reset, then use the single-use token from the reset link to set a new password.</p>
        </header>
        {error && <ErrorBanner error={error} title="Password reset failed" />}
        {resetDone ? (
          <Panel title="Password updated" exhibit="EX-OK">
            <p>Your password has been changed and existing sessions have been revoked.</p>
            <Button variant="primary" onClick={() => navigate('/login', { replace: true })}>Return to sign in</Button>
          </Panel>
        ) : (
          <>
            <Panel exhibit="01" title="Request a reset link">
              <form onSubmit={requestReset} className="stack" style={{ gap: 12 }}>
                <Field label="Account email" htmlFor="reset-email" required>
                  <input id="reset-email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
                </Field>
                <Button type="submit" variant="primary" disabled={busy || !email.trim()}>{busy ? 'Submitting' : 'Send reset request'}</Button>
              </form>
              {requestSent && (
                <div className="banner banner-tone-info" role="status" style={{ marginTop: 12 }}>
                  If the address is registered, a reset token has been created. This deployment does not have an email provider configured yet, so the token is not delivered by email. Contact the event organizers through their approved support channel to obtain the reset link.
                </div>
              )}
            </Panel>
            <Panel exhibit="02" title="Set a new password">
              <form onSubmit={completeReset} className="stack" style={{ gap: 12 }}>
                <Field label="Reset token" htmlFor="reset-token" required hint="It may be prefilled when opening a reset link.">
                  <input id="reset-token" className="input mono-input" autoComplete="off" required value={token} onChange={(e) => setToken(e.target.value)} />
                </Field>
                <Field label="New password" htmlFor="reset-password" required hint="At least 12 characters. All active sessions will be revoked.">
                  <input id="reset-password" className="input mono" type="password" autoComplete="new-password" minLength={12} maxLength={200} required value={password} onChange={(e) => setPassword(e.target.value)} />
                </Field>
                <Field label="Confirm password" htmlFor="reset-confirm" required>
                  <input id="reset-confirm" className="input mono" type="password" autoComplete="new-password" minLength={12} maxLength={200} required value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} />
                </Field>
                <Button type="submit" variant="primary" disabled={busy || !token.trim() || password.length < 12 || password !== confirmPassword}>
                  {busy ? 'Updating password' : 'Reset password'}
                </Button>
              </form>
            </Panel>
          </>
        )}
        <p className="muted small"><Link to="/login">Return to sign in</Link></p>
      </div>
    </div>
  )
}
