import { Route, Routes } from 'react-router-dom'
import { PublicLayout, RequireRole, ConsoleShell } from './components/chrome'
import Landing from './pages/Landing'
import Rules from './pages/Rules'
import Privacy from './pages/Privacy'
import Terms from './pages/Terms'
import Login from './pages/Login'
import { ActivateInvite, PasswordReset } from './pages/Access'
import NotFound from './pages/NotFound'
import Lobby from './pages/participant/Lobby'
import Workspace from './pages/participant/Workspace'
import Submissions from './pages/participant/Submissions'
import SubmissionDetail from './pages/participant/SubmissionDetail'
import Results from './pages/participant/Results'
import Leaderboard from './pages/participant/Leaderboard'
import Account from './pages/participant/Account'
import ReviewQueue from './pages/review/Queue'
import ReviewItem from './pages/review/ItemDetail'
import AdminDashboard from './pages/admin/Dashboard'
import AdminChallenges from './pages/admin/Challenges'
import AdminManagement from './pages/admin/Management'
import AdminGroundTruth from './pages/admin/GroundTruth'

export default function App() {
  return (
    <Routes>
      {/* Public case file */}
      <Route element={<PublicLayout />}>
        <Route path="/" element={<Landing />} />
        <Route path="/rules" element={<Rules />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/terms" element={<Terms />} />
      </Route>

      <Route path="/login" element={<Login />} />
      <Route path="/activate" element={<ActivateInvite />} />
      <Route path="/password-reset" element={<PasswordReset />} />

      {/* Participant console */}
      <Route
        path="/app"
        element={
          <RequireRole roles={['participant']}>
            <ConsoleShell area="/app">
              <Routes>
                <Route index element={<Lobby />} />
                <Route path="workspace" element={<Workspace />} />
                <Route path="submissions" element={<Submissions />} />
                <Route path="submissions/:submissionId" element={<SubmissionDetail />} />
                <Route path="results" element={<Results />} />
                <Route path="leaderboard" element={<Leaderboard />} />
                <Route path="account" element={<Account />} />
              </Routes>
            </ConsoleShell>
          </RequireRole>
        }
      />

      {/* Reviewer console */}
      <Route
        path="/review"
        element={
          <RequireRole roles={['reviewer']}>
            <ConsoleShell area="/review">
              <Routes>
                <Route index element={<ReviewQueue />} />
                <Route path="items/:itemId" element={<ReviewItem />} />
              </Routes>
            </ConsoleShell>
          </RequireRole>
        }
      />

      {/* Organizer / admin console */}
      <Route
        path="/admin"
        element={
          <RequireRole roles={['organizer', 'admin']}>
            <ConsoleShell area="/admin">
              <Routes>
                <Route index element={<AdminDashboard />} />
                <Route path="challenges" element={<AdminChallenges />} />
                <Route path="setup" element={<AdminManagement />} />
                <Route path="ground-truth" element={<AdminGroundTruth />} />
              </Routes>
            </ConsoleShell>
          </RequireRole>
        }
      />

      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}
