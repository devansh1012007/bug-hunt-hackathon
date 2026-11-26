import { NavLink, Link, Outlet, Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth, homePathFor } from '../lib/auth'
import { Button, LoadingShell, Panel, Chip } from '../ui/components'
import { ShieldIcon } from '../ui/icons'

export function Wordmark({ to = '/' }: { to?: string }) {
  return (
    <Link to={to} className="wordmark">
      BUG HUNT CTF
      <span className="wm-sub">Fall Back</span>
    </Link>
  )
}

function roleLabel(role: string): string {
  if (role === 'participant') return 'Participant'
  if (role === 'reviewer') return 'Reviewer'
  if (role === 'organizer') return 'Organizer'
  if (role === 'admin') return 'Admin'
  return role
}

export function PublicLayout() {
  const { user } = useAuth()
  return (
    <div>
      <header className="topbar">
        <div className="wrap topbar-inner">
          <Wordmark />
          <nav className="topnav" aria-label="Site">
            <NavLink to="/rules">Rules</NavLink>
            {user ? (
              <NavLink to={homePathFor(user.role)}>Open console</NavLink>
            ) : (
              <NavLink to="/login">Sign in</NavLink>
            )}
          </nav>
        </div>
      </header>
      <main id="main">
        <Outlet />
      </main>
      <SiteFooter />
    </div>
  )
}

export function SiteFooter() {
  return (
    <footer className="footer">
      <div className="wrap footer-grid">
        <div>
          <div className="wordmark" style={{ marginBottom: 8 }}>
            BUG HUNT CTF <span className="wm-sub">Fall Back</span>
          </div>
          <p className="small">
            A competition by the Software Development Club, VIT Bhopal University. Proposed date: 6 December 2026,
            on campus, offline. Platform questions go to the organizers; good-faith reports of platform
            vulnerabilities are welcome under the safe-harbor notice in the rules.
          </p>
        </div>
        <div>
          <div className="overline" style={{ marginBottom: 8 }}>Coordinators</div>
          <p className="small" style={{ margin: 0 }}>
            Kalpesh Parashar
            <br />
            Aashish Prasad
            <br />
            <span className="muted">Faculty coordinator: Dr. Praveen Lalwani</span>
          </p>
        </div>
        <div>
          <div className="overline" style={{ marginBottom: 8 }}>Documents</div>
          <p className="small" style={{ margin: 0, display: 'grid', gap: 6 }}>
            <Link to="/rules">Competition rules</Link>
            <Link to="/privacy">Privacy notice</Link>
            <Link to="/terms">Terms of use</Link>
          </p>
        </div>
      </div>
    </footer>
  )
}

const NAV: Record<string, { to: string; label: string; roles: string[] }[]> = {
  '/app': [
    { to: '/app', label: 'Lobby', roles: ['participant'] },
    { to: '/app/workspace', label: 'Workspace', roles: ['participant'] },
    { to: '/app/submissions', label: 'Submissions', roles: ['participant'] },
    { to: '/app/results', label: 'Results', roles: ['participant'] },
    { to: '/app/leaderboard', label: 'Leaderboard', roles: ['participant'] },
    { to: '/app/account', label: 'Account', roles: ['participant'] },
  ],
  '/review': [{ to: '/review', label: 'Review queue', roles: ['reviewer'] }],
  '/admin': [
    { to: '/admin', label: 'Operations', roles: ['organizer', 'admin'] },
    { to: '/admin/challenges', label: 'Challenges', roles: ['organizer', 'admin'] },
    { to: '/admin/setup', label: 'Setup & scoring', roles: ['organizer', 'admin'] },
    { to: '/admin/ground-truth', label: 'Ground truth', roles: ['organizer', 'admin'] },
  ],
}

export function ConsoleShell({ area, children, aside }: { area: '/app' | '/review' | '/admin'; children: ReactNode; aside?: ReactNode }) {
  const { user, logout } = useAuth()
  const items = NAV[area] ?? []
  return (
    <div>
      <header className="topbar">
        <div className="wrap-wide topbar-inner">
          <Wordmark />
          <nav className="topnav" aria-label="Console">
            {items.map((i) => (
              <NavLink key={i.to} to={i.to} end={i.to === area}>
                {i.label}
              </NavLink>
            ))}
            {user && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginLeft: 8 }}>
                <Chip tone="muted" title={`Signed in as ${user.email}`}>
                  {roleLabel(user.role)}
                </Chip>
                <Button size="sm" onClick={() => void logout()}>
                  Sign out
                </Button>
              </span>
            )}
          </nav>
        </div>
      </header>
      <main id="main" className="wrap-wide page">
        {aside}
        {children}
      </main>
    </div>
  )
}

/** Route guard: waits for session restore, then enforces the role. */
export function RequireRole({ roles, children }: { roles: string[]; children: ReactNode }) {
  const { user, status } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return (
      <div>
        <div className="topbar">
          <div className="wrap topbar-inner">
            <Wordmark />
            <span className="loading-inline">Checking session</span>
          </div>
        </div>
        <LoadingShell label="Restoring session" />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  if (!roles.includes(user.role)) {
    return (
      <div>
        <div className="topbar">
          <div className="wrap topbar-inner">
            <Wordmark />
            <nav className="topnav" aria-label="Site">
              <Link to={homePathFor(user.role)}>Open console</Link>
            </nav>
          </div>
        </div>
        <div className="wrap page">
          <Panel title="Access denied" exhibit="EX-403">
            <p>
              This console area is for {roles.join(' and ')} accounts. You are signed in as {user.email} with the{' '}
              {user.role} role.
            </p>
            <p className="muted small">
              Use the Open console link above to reach the area that matches your role, or sign out and sign in with
              the right account.
            </p>
          </Panel>
        </div>
      </div>
    )
  }

  return <>{children}</>
}

export function OfflineBanner({ onRetry, show }: { onRetry?: () => void; show: boolean }) {
  if (!show) return null
  return (
    <div className="banner banner-tone-warn" role="alert" style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <ShieldIcon size={15} />
        <span className="banner-title">Platform API unreachable</span>
      </div>
      <div className="small">Showing the last loaded data. Retry to refresh from the server.</div>
      {onRetry && (
        <div>
          <Button size="sm" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}
    </div>
  )
}
