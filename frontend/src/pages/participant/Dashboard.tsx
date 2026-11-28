// Participant dashboard: the primary authenticated landing experience.
//
// Replaces the v1 Lobby for the four-level flow. Hierarchy:
//   1. The single server-derived Continue/Resume action.
//   2. Level status list, team, points, leaderboard, announcements.
// All state comes from the server (GET /v1/progress, /v1/levels, /v1/team,
// /v1/team/score, /v1/leaderboard/{level_stage_id}); the browser never
// derives progression, scores or rank locally.

import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { isDoneStatus } from '../../lib/api-levels'
import {
  deriveResume,
  levelStatusLabel,
  useAnnouncementsQuery,
  useLevelsQuery,
  useProgressQuery,
  useServerNow,
  useTeamQuery,
  useTeamPointsQuery,
} from '../../lib/event-session'
import { EventProgressBar } from '../../components/event/ProgressBar'
import { EventWarnings } from '../../components/event/Warnings'
import { api, ApiRequestError } from '../../lib/api'
import type { LeaderboardResponse } from '../../lib/types'
import { useAuth } from '../../lib/auth'
import { fmtDateTime, fmtPoints } from '../../lib/format'
import { Button, Chip, EmptyState, ErrorBanner, LoadingShell, Panel, SectionHeading, StatusChip } from '../../ui/components'

export default function Dashboard() {
  const { user, acceptRules } = useAuth()
  const navigate = useNavigate()
  const nowMs = useServerNow()
  const levelsQ = useLevelsQuery()
  const progressQ = useProgressQuery()
  const teamQ = useTeamQuery()
  const pointsQ = useTeamPointsQuery()

  const resume = deriveResume(progressQ.data, levelsQ.data)

  // Deliberate lifecycle states for the progress surface.
  const levelsError = levelsQ.error instanceof ApiRequestError ? levelsQ.error : null
  const lifecycleNote = useMemo(() => {
    if (!levelsError) return null
    if (levelsError.code === 'LEVEL_NOT_OPEN') return 'The event is not running yet. The dashboard fills in once the schedule is live.'
    if (levelsError.code === 'RULES_NOT_ACCEPTED') return 'Accept the competition rules to join the event.'
    if (levelsError.code === 'AUTH_FORBIDDEN') return 'Your account is not on a team yet. Ask the organizers to add you.'
    return null
  }, [levelsError])

  return (
    <div>
      <EventProgressBar levels={levelsQ.data} progress={progressQ.data} nowMs={nowMs} />
      <div className="wrap-wide page">
        <EventWarnings levels={levelsQ.data} progress={progressQ.data} nowMs={nowMs} deadlineModal={false} />

        <header style={{ display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'baseline', marginBottom: 18 }}>
          <span className="exhibit-num">EX-01</span>
          <h1 className="display" style={{ margin: 0 }}>Dashboard</h1>
          {teamQ.data && <Chip tone="info" size="lg">{teamQ.data.team_name}</Chip>}
          {user && <span className="muted small">Signed in as {user.name}</span>}
        </header>

        {teamQ.data?.repo_option && (
          <p className="small muted" style={{ marginTop: -8 }}>
            Repository locked: <span className="mono">{teamQ.data.repo_option.title}</span> (Option {teamQ.data.repo_option.option_key}, by{' '}
            {teamQ.data.repo_option.locked_by_name})
          </p>
        )}

        {lifecycleNote && (
          <div className="banner banner-tone-info" role="status">
            <span className="banner-title">{lifecycleNote}</span>
            {levelsError?.code === 'RULES_NOT_ACCEPTED' && (
              <div>
                <Button size="sm" onClick={() => void acceptRules()}>
                  Accept rules
                </Button>
              </div>
            )}
          </div>
        )}
        {levelsError && !lifecycleNote && <ErrorBanner error={levelsError} title="Could not load event progress" onRetry={() => void levelsQ.refetch()} />}

        {/* Primary action: exactly one. */}
        <Panel exhibit="EX-02" title="Next step" className="resume-panel">
          <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <Button
              variant="primary"
              disabled={resume.target === null}
              onClick={() => resume.target && navigate(resume.target)}
              ariaLabel={resume.label}
            >
              {resume.label}
            </Button>
            <div style={{ minWidth: 220, flex: 1 }}>
              {resume.note && <div className="small muted">{resume.note}</div>}
              {resume.target === null && resume.kind === 'unknown' && <span className="loading-inline">Reading server progress</span>}
              {resume.target !== null && resume.node === 'exec' && (
                <div className="small muted">The workspace restores your saved draft automatically.</div>
              )}
            </div>
          </div>
        </Panel>

        <div className="grid-2" style={{ marginTop: 18 }}>
          <div className="stack">
            <LevelListCard levelsQ={levelsQ} progressQ={progressQ} />
            <TeamCard />
          </div>
          <div className="stack">
            <PointsCard levelsQ={levelsQ} pointsQ={pointsQ} />
            <LeaderboardCard levelsQ={levelsQ} />
            <AnnouncementsCard />
          </div>
        </div>

        <SectionHeading num="EX-07" title="Records" note="v1 compatibility surfaces" />
        <p className="small muted">
          Submission history, per-stage results and account sessions live on their case file pages:{' '}
          <Link to="/app/submissions">Submissions</Link>, <Link to="/app/results">Results</Link>,{' '}
          <Link to="/app/leaderboard">Leaderboard</Link>, <Link to="/app/account">Account</Link>. Event rules and details:{' '}
          <Link to="/event">Event details</Link>, <Link to="/rules">Rules</Link>.
        </p>
      </div>
    </div>
  )
}

/* ---------- level status list ---------- */

function LevelListCard({
  levelsQ,
  progressQ,
}: {
  levelsQ: ReturnType<typeof useLevelsQuery>
  progressQ: ReturnType<typeof useProgressQuery>
}) {
  const navigate = useNavigate()
  const pointer = progressQ.data?.pointer ?? null
  return (
    <Panel exhibit="EX-03" title="Level status">
      {!levelsQ.data && !levelsQ.isError && <LoadingShell label="Loading levels" />}
      {levelsQ.isError && <ErrorBanner error={levelsQ.error} />}
      {levelsQ.data && (
        <div className="stack">
          {levelsQ.data.levels.map((l) => {
            const isCurrent = pointer !== null && pointer.level_no === l.level_no && pointer.node !== 'done'
            const done = isDoneStatus(l.participant_status)
            return (
              <div key={l.level_no} className="kv-row" style={{ alignItems: 'center' }}>
                <span style={{ display: 'flex', gap: 10, alignItems: 'center', minWidth: 0 }}>
                  <span className="exhibit-num">L{l.level_no}</span>
                  <span style={{ display: 'grid' }}>
                    <span>{l.name}</span>
                    <span className="small muted">{l.gate_reason}</span>
                  </span>
                </span>
                <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <StatusChip value={l.participant_status} />
                  {isCurrent && pointer?.node === 'exec' && (
                    <Button size="sm" onClick={() => navigate(`/level/${l.level_no}/run`)}>
                      Open workspace
                    </Button>
                  )}
                  {isCurrent && pointer?.node === 'explain' && (
                    <Button size="sm" onClick={() => navigate(`/level/${l.level_no}/explain`)}>
                      Open
                    </Button>
                  )}
                  {done && <Chip tone="ok">done</Chip>}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}

/* ---------- team ---------- */

function TeamCard() {
  const teamQ = useTeamQuery()
  return (
    <Panel exhibit="EX-04" title="Team">
      {!teamQ.data && !teamQ.isError && <LoadingShell label="Loading team" />}
      {teamQ.isError && <ErrorBanner error={teamQ.error} />}
      {teamQ.data && (
        <div className="table-scroll">
          <table className="table">
            <caption className="sr-only">Team members and per-level status</caption>
            <thead>
              <tr>
                <th scope="col">Member</th>
                <th scope="col">L1</th>
                <th scope="col">L2</th>
                <th scope="col">L3</th>
                <th scope="col">L4</th>
              </tr>
            </thead>
            <tbody>
              {teamQ.data.members.map((m) => (
                <tr key={m.id}>
                  <td>
                    {m.name}
                    {m.is_lead && (
                      <span className="chip chip-tone-signal" style={{ marginLeft: 8 }}>
                        lead
                      </span>
                    )}
                  </td>
                  {[1, 2, 3, 4].map((n) => (
                    <td key={n}>
                      <span className={m.levels[String(n)] === 'missed' ? 'mono' : 'mono small'}>{levelStatusLabel(m.levels[String(n)])}</span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

/* ---------- points ---------- */

function PointsCard({
  levelsQ,
  pointsQ,
}: {
  levelsQ: ReturnType<typeof useLevelsQuery>
  pointsQ: ReturnType<typeof useTeamPointsQuery>
}) {
  const data = pointsQ.data
  const completedLevels = (levelsQ.data?.levels ?? []).filter((level) => isDoneStatus(level.participant_status))
  const pointsByLevel = new Map(data?.levels.map((level) => [level.level_no, level]) ?? [])
  return (
    <Panel exhibit="EX-05" title="Points">
      {!data && !pointsQ.isError && <LoadingShell label="Loading points" />}
      {pointsQ.isError && <ErrorBanner error={pointsQ.error} />}
      {data && (
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
            <span className="countdown" style={{ fontSize: '2.2rem' }}>{fmtPoints(data.points)}</span>
            <span className="overline">Total points</span>
          </div>
          {completedLevels.map((level) => {
            const result = pointsByLevel.get(level.level_no)
            const maxPoints = result?.max_points ?? level.max_points
            const value =
              result?.evaluation_status === 'not_released'
                ? 'Not released'
                : result?.evaluation_status !== 'complete'
                  ? 'Evaluating'
                  : maxPoints === null
                    ? fmtPoints(result.score)
                    : `${fmtPoints(result.score)} / ${fmtPoints(maxPoints)}`
            return (
              <div key={level.level_no} className="kv-row">
                <span className="kv-key">L{level.level_no}</span>
                <span className="kv-val">{value}</span>
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}

/* ---------- leaderboard ---------- */

function LeaderboardCard({ levelsQ }: { levelsQ: ReturnType<typeof useLevelsQuery> }) {
  const [stageChoice, setStageChoice] = useState<string>('')
  const stages = levelsQ.data?.levels ?? []
  const selectedStage = stageChoice || stages.find((s) => s.gate_allowed)?.stage_id || stages[0]?.stage_id || ''

  const boardQ = useQuery({
    queryKey: ['leaderboard', selectedStage],
    queryFn: (): Promise<LeaderboardResponse> => api<LeaderboardResponse>(`/v1/leaderboard/${selectedStage}`),
    enabled: selectedStage !== '',
    refetchInterval: 30000,
    staleTime: 4000,
  })

  const teamQ = useTeamQuery()
  const myTeamId = teamQ.data?.team_id

  return (
    <Panel
      exhibit="EX-06"
      title="Leaderboard"
      actions={
        stages.length > 0 ? (
          <label className="overline" htmlFor="board-stage" style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
            Level
            <select id="board-stage" className="select" value={selectedStage} onChange={(e) => setStageChoice(e.target.value)}>
              {stages.map((s) => (
                <option key={s.stage_id} value={s.stage_id}>
                  L{s.level_no}
                </option>
              ))}
            </select>
          </label>
        ) : undefined
      }
    >
      {!boardQ.isEnabled && <EmptyState title="No leaderboard stage available yet" />}
      {boardQ.isEnabled && !boardQ.data && !boardQ.isError && <LoadingShell label="Loading leaderboard" />}
      {boardQ.isError && <ErrorBanner error={boardQ.error} />}
      {boardQ.data && !boardQ.data.visible && (
        <div className="banner banner-tone-info" role="status">
          <span className="banner-title">Leaderboard hidden</span>
          <div className="small">{boardQ.data.message ?? 'The leaderboard is hidden for this level.'}</div>
        </div>
      )}
      {boardQ.data?.visible && boardQ.data.snapshot === null && (
        <EmptyState title="No results published yet" note="The server publishes snapshots as evaluations finish." />
      )}
      {boardQ.data?.visible && boardQ.data.snapshot && (
        <div className="table-scroll">
          <table className="table">
            <caption className="sr-only">Leaderboard for the selected level</caption>
            <thead>
              <tr>
                <th scope="col">Rank</th>
                <th scope="col">Team</th>
                <th scope="col">Points</th>
              </tr>
            </thead>
            <tbody>
              {boardQ.data.snapshot.rows.map((r) => (
                <tr key={r.team_id} className={r.team_id === myTeamId ? 'highlight-row' : ''}>
                  <td className="num">{r.rank}</td>
                  <td>
                    {r.team_name}
                    {r.team_id === myTeamId && <span className="chip chip-tone-signal" style={{ marginLeft: 8 }}>your team</span>}
                  </td>
                  <td className="num">{fmtPoints(r.score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {boardQ.data?.visible && boardQ.data.snapshot && (
        <p className="small muted">Snapshot generated {fmtDateTime(boardQ.data.snapshot.generated_at)}</p>
      )}
    </Panel>
  )
}

/* ---------- announcements ---------- */

function AnnouncementsCard() {
  const announcementsQ = useAnnouncementsQuery()
  const items = announcementsQ.data?.announcements ?? []
  return (
    <Panel exhibit="EX-08" title="Announcements">
      {items.length === 0 && <EmptyState title="No announcements" note="Organizer messages appear here during the event." />}
      {items.map((a) => (
        <div key={a.id} className="stack" style={{ borderBottom: '1px solid var(--line)', paddingBottom: 10 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Chip tone={a.severity === 'critical' ? 'danger' : a.severity === 'warning' ? 'warn' : 'info'}>{a.severity}</Chip>
            <span className="small muted">{fmtDateTime(a.created_at)}</span>
          </div>
          <p style={{ margin: 0 }}>{a.body}</p>
        </div>
      ))}
    </Panel>
  )
}
