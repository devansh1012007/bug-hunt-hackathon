import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiRequestError } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import { fmtDateTime } from '../../lib/format'
import type {
  AdminEvent,
  AdminParticipant,
  AdminTeam,
  ConfigRevision,
  LiveOps,
} from '../../lib/types'
import {
  Button,
  Chip,
  EmptyState,
  ErrorBanner,
  Field,
  LoadingShell,
  Panel,
  StatusChip,
} from '../../ui/components'
import { adminErrorText, StepUpGateHint, StepUpPanel } from '../../components/StepUp'

const TIME_REASONS = ['ACCOMMODATION', 'OUTAGE', 'DEVICE_FAILURE', 'OTHER'] as const

type InviteResult = { invite_token: string; activation_url: string }

export default function AdminManagement() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const eventsQ = useQuery({
    queryKey: ['admin-events'],
    queryFn: () => api<{ events: AdminEvent[] }>('/v1/admin/events'),
  })
  const [eventId, setEventId] = useState('')
  const [stageId, setStageId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const liveQ = useQuery({
    queryKey: ['admin-live', eventId],
    queryFn: () => api<LiveOps>(`/v1/admin/events/${eventId}/live`),
    enabled: !!eventId,
  })
  const teamsQ = useQuery({
    queryKey: ['admin-teams', eventId],
    queryFn: () => api<{ teams: AdminTeam[] }>(`/v1/admin/events/${eventId}/teams`),
    enabled: !!eventId,
  })
  const participantsQ = useQuery({
    queryKey: ['admin-participants', eventId],
    queryFn: () => api<{ participants: AdminParticipant[] }>(`/v1/admin/events/${eventId}/participants`),
    enabled: !!eventId,
  })
  const configQ = useQuery({
    queryKey: ['admin-config-revisions', eventId],
    queryFn: () => api<{
      config_version: number
      current_config: Record<string, unknown>
      revisions: ConfigRevision[]
    }>(`/v1/admin/events/${eventId}/config/revisions`),
    enabled: !!eventId,
  })

  useEffect(() => {
    if (!eventId && eventsQ.data?.events.length) setEventId(eventsQ.data.events[0].id)
  }, [eventsQ.data, eventId])

  useEffect(() => {
    const stages = liveQ.data?.stages ?? []
    if (stages.length && !stages.some((stage) => stage.id === stageId)) setStageId(stages[0].id)
    if (!stages.length) setStageId('')
  }, [liveQ.data, stageId])

  const selectedEvent = useMemo(
    () => eventsQ.data?.events.find((event) => event.id === eventId) ?? null,
    [eventsQ.data, eventId],
  )
  const stages = liveQ.data?.stages ?? []
  const teams = teamsQ.data?.teams ?? []
  const participants = participantsQ.data?.participants ?? []

  const [stageForm, setStageForm] = useState({ name: '', kind: 'main', grace: '3' })
  const [teamName, setTeamName] = useState('')
  const [teamCreated, setTeamCreated] = useState<{ name: string; code: string } | null>(null)
  const [inviteForm, setInviteForm] = useState({ email: '', team_id: '' })
  const [inviteResult, setInviteResult] = useState<{ email: string; url: string } | null>(null)
  const [rosterFilter, setRosterFilter] = useState('')
  const [configText, setConfigText] = useState('')
  const [configDirty, setConfigDirty] = useState(false)
  const [configReason, setConfigReason] = useState('')
  const [freezeConfig, setFreezeConfig] = useState(false)
  const [timeForm, setTimeForm] = useState({ delta: '300', reason: 'OUTAGE', user_id: '' })
  const [scoreForm, setScoreForm] = useState({ team_id: '', points: '', reason: '', confirmed: false })
  const [scoreBusy, setScoreBusy] = useState(false)
  const [stepUpUntil, setStepUpUntil] = useState<number | null>(null)
  const scoreElevated = stepUpUntil !== null && stepUpUntil > Date.now()

  useEffect(() => {
    if (!configDirty && configQ.data) {
      setConfigText(JSON.stringify(configQ.data.current_config ?? {}, null, 2))
    }
  }, [configQ.data, configDirty])

  useEffect(() => {
    setConfigDirty(false)
    setConfigReason('')
    setFreezeConfig(false)
    setInviteResult(null)
    setInviteForm({ email: '', team_id: '' })
    setScoreForm({ team_id: '', points: '', reason: '', confirmed: false })
    setTeamCreated(null)
    setNotice(null)
    setError(null)
  }, [eventId])

  function clearMessages() {
    setError(null)
    setNotice(null)
  }

  function showError(err: unknown) {
    setError(err instanceof ApiRequestError ? adminErrorText(err) : 'The operation failed. Please retry.')
  }

  async function createStage(e: React.FormEvent) {
    e.preventDefault()
    if (!eventId) return
    setBusy(true)
    clearMessages()
    try {
      await api(`/v1/admin/events/${eventId}/stages`, {
        method: 'POST',
        body: {
          name: stageForm.name.trim(),
          kind: stageForm.kind,
          grace_seconds: Number(stageForm.grace),
          config: {},
        },
      })
      setStageForm({ name: '', kind: 'main', grace: '3' })
      setNotice('Stage created. It is available in live operations and challenge publishing.')
      await queryClient.invalidateQueries({ queryKey: ['admin-live', eventId] })
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  async function createTeam(e: React.FormEvent) {
    e.preventDefault()
    if (!eventId) return
    setBusy(true)
    clearMessages()
    try {
      const result = await api<{ team_id: string; join_code: string }>(`/v1/admin/events/${eventId}/teams`, {
        method: 'POST',
        body: { name: teamName.trim() },
      })
      setTeamCreated({ name: teamName.trim(), code: result.join_code })
      setTeamName('')
      setNotice('Team created. Keep the join code with the organizer team; it is displayed here only as an operational aid.')
      await queryClient.invalidateQueries({ queryKey: ['admin-teams', eventId] })
      await queryClient.invalidateQueries({ queryKey: ['admin-live', eventId] })
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  async function createInvite(e: React.FormEvent) {
    e.preventDefault()
    if (!eventId) return
    setBusy(true)
    clearMessages()
    try {
      const result = await api<InviteResult>(`/v1/admin/events/${eventId}/invites`, {
        method: 'POST',
        body: { email: inviteForm.email.trim().toLowerCase(), team_id: inviteForm.team_id || null, role: 'participant' },
      })
      const url = new URL(result.activation_url, window.location.origin).toString()
      setInviteResult({ email: inviteForm.email.trim(), url })
      setInviteForm({ email: '', team_id: '' })
      setNotice('Invitation created. Share the one-time activation link through your approved private channel.')
      await queryClient.invalidateQueries({ queryKey: ['admin-participants', eventId] })
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  async function saveConfig(e: React.FormEvent) {
    e.preventDefault()
    if (!eventId) return
    setBusy(true)
    clearMessages()
    try {
      const parsed: unknown = JSON.parse(configText)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Configuration must be a JSON object.')
      }
      await api(`/v1/admin/events/${eventId}/config`, {
        method: 'POST',
        body: { config: parsed, reason: configReason.trim(), freeze: freezeConfig },
      })
      setNotice('Configuration revision saved.')
      setConfigDirty(false)
      await queryClient.invalidateQueries({ queryKey: ['admin-config-revisions', eventId] })
      await queryClient.invalidateQueries({ queryKey: ['admin-events'] })
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  async function adjustTime(e: React.FormEvent) {
    e.preventDefault()
    if (!eventId || !stageId) return
    setBusy(true)
    clearMessages()
    try {
      await api(`/v1/admin/events/${eventId}/time-adjustment`, {
        method: 'POST',
        body: {
          stage_id: stageId,
          delta_seconds: Number(timeForm.delta),
          reason_code: timeForm.reason,
          user_id: timeForm.user_id || null,
        },
      })
      setNotice('Time adjustment recorded. An empty participant selection applies the adjustment to the whole stage.')
      setTimeForm((old) => ({ ...old, user_id: '' }))
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  async function adjustScore(e: React.FormEvent) {
    e.preventDefault()
    if (!stageId || !scoreForm.team_id || !scoreForm.reason.trim() || !scoreForm.confirmed) return
    setScoreBusy(true)
    clearMessages()
    try {
      await api('/v1/admin/adjustments', {
        method: 'POST',
        body: {
          stage_id: stageId,
          team_id: scoreForm.team_id,
          points: Number(scoreForm.points),
          reason: scoreForm.reason.trim(),
        },
      })
      setNotice('Manual score adjustment appended to the score ledger.')
      setScoreForm({ team_id: '', points: '', reason: '', confirmed: false })
    } catch (err) {
      showError(err)
    } finally {
      setScoreBusy(false)
    }
  }

  const filteredParticipants = participants.filter((participant) => {
    const needle = rosterFilter.trim().toLowerCase()
    return !needle || [participant.email, participant.name ?? '', participant.team_name ?? '', participant.status]
      .some((value) => value.toLowerCase().includes(needle))
  })

  return (
    <div className="stack-lg">
      <header className="stack">
        <span className="exhibit-num">ORGANIZER SETUP</span>
        <h1 className="display" style={{ margin: 0, fontSize: 'clamp(1.6rem, 4vw, 2.4rem)' }}>Setup &amp; scoring</h1>
        <p className="muted small" style={{ maxWidth: '78ch' }}>
          Event configuration, stage setup, team onboarding, participant roster and audited scoring operations.
          Privileged scoring changes require an administrator account.
        </p>
        <div className="field" style={{ width: 420, maxWidth: '100%' }}>
          <label className="field-label" htmlFor="management-event">Event</label>
          <div className="select-wrap">
            <select id="management-event" className="select" value={eventId} onChange={(e) => setEventId(e.target.value)}>
              <option value="">Select event</option>
              {(eventsQ.data?.events ?? []).map((event) => (
                <option key={event.id} value={event.id}>{event.name} ({event.status})</option>
              ))}
            </select>
          </div>
        </div>
        {selectedEvent && (
          <div className="small muted mono">
            {selectedEvent.slug} · config v{selectedEvent.config_version} · {selectedEvent.timezone}
          </div>
        )}
      </header>

      {eventsQ.isLoading && <LoadingShell label="Loading events" />}
      {eventsQ.isError && <ErrorBanner error={eventsQ.error} onRetry={() => void eventsQ.refetch()} />}
      {error && <ErrorBanner error={new ApiRequestError({ code: 'OPERATION', serverMessage: error })} />}
      {notice && <div className="banner banner-tone-ok" role="status">{notice}</div>}

      {!eventId && eventsQ.data && <EmptyState title="Select an event" note="Create an event in Operations before setting up stages, teams and participants." />}

      {eventId && (
        <>
          <Panel exhibit="01" title="Stage creation">
            <form onSubmit={createStage} className="stack" style={{ gap: 12 }}>
              <div className="grid-2">
                <Field label="Stage name" htmlFor="stage-name" required>
                  <input id="stage-name" className="input" minLength={2} maxLength={120} required value={stageForm.name} onChange={(e) => setStageForm({ ...stageForm, name: e.target.value })} />
                </Field>
                <Field label="Stage kind" htmlFor="stage-kind" required>
                  <select id="stage-kind" className="select" value={stageForm.kind} onChange={(e) => setStageForm({ ...stageForm, kind: e.target.value })}>
                    <option value="main">main</option>
                    <option value="practice">practice</option>
                    <option value="bonus">bonus</option>
                  </select>
                </Field>
                <Field label="Grace period (seconds)" htmlFor="stage-grace" hint="Applied by the server to the stage timer.">
                  <input id="stage-grace" className="input mono-input" type="number" min={0} max={3600} value={stageForm.grace} onChange={(e) => setStageForm({ ...stageForm, grace: e.target.value })} />
                </Field>
              </div>
              <Button type="submit" variant="primary" disabled={busy || stageForm.name.trim().length < 2}>Create stage</Button>
            </form>
            <div className="ruled" style={{ marginTop: 16 }}>
              {liveQ.isLoading && <LoadingShell label="Loading stages" />}
              {liveQ.isError && <ErrorBanner error={liveQ.error} onRetry={() => void liveQ.refetch()} />}
              {stages.map((stage) => (
                <div className="kv-row" key={stage.id}>
                  <span>{stage.name} <span className="muted small mono">{stage.id.slice(0, 8)}</span></span>
                  <StatusChip value={stage.status} />
                </div>
              ))}
              {liveQ.data && stages.length === 0 && <p className="muted small">No stages exist for this event yet.</p>}
            </div>
          </Panel>

          <div className="grid-2">
            <Panel exhibit="02" title="Create team">
              <form onSubmit={createTeam} className="stack" style={{ gap: 12 }}>
                <Field label="Team name" htmlFor="team-name" required>
                  <input id="team-name" className="input" minLength={2} maxLength={120} required value={teamName} onChange={(e) => setTeamName(e.target.value)} />
                </Field>
                <Button type="submit" variant="primary" disabled={busy || teamName.trim().length < 2}>Create team</Button>
              </form>
              {teamCreated && (
                <div className="banner banner-tone-ok" style={{ marginTop: 12 }}>
                  <div className="banner-title">{teamCreated.name} created</div>
                  <div className="small">Join code: <strong className="mono">{teamCreated.code}</strong></div>
                </div>
              )}
              <div className="ruled" style={{ marginTop: 14 }}>
                {teamsQ.isLoading && <LoadingShell label="Loading teams" />}
                {teamsQ.isError && <ErrorBanner error={teamsQ.error} onRetry={() => void teamsQ.refetch()} />}
                {teams.map((team) => (
                  <div className="kv-row" key={team.id}>
                    <span>{team.name}<span className="muted small"> · {team.member_count} members</span></span>
                    <Chip>{team.status}</Chip>
                  </div>
                ))}
                {teamsQ.data && teams.length === 0 && <p className="muted small">No teams created yet.</p>}
              </div>
            </Panel>

            <Panel exhibit="03" title="Invite participant">
              <form onSubmit={createInvite} className="stack" style={{ gap: 12 }}>
                <Field label="Participant email" htmlFor="invite-email" required>
                  <input id="invite-email" className="input" type="email" autoComplete="email" required value={inviteForm.email} onChange={(e) => setInviteForm({ ...inviteForm, email: e.target.value })} />
                </Field>
                <Field label="Assign team" htmlFor="invite-team" hint="Optional. You can create an invitation before assigning a team.">
                  <select id="invite-team" className="select" value={inviteForm.team_id} onChange={(e) => setInviteForm({ ...inviteForm, team_id: e.target.value })}>
                    <option value="">No team yet</option>
                    {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
                  </select>
                </Field>
                <Button type="submit" variant="primary" disabled={busy || !inviteForm.email}>Create invitation</Button>
              </form>
              {inviteResult && (
                <div className="banner banner-tone-info" style={{ marginTop: 12 }}>
                  <div className="banner-title">One-time invitation for {inviteResult.email}</div>
                  <p className="small">The token is shown once here. Copy the link and send it only to the invited participant.</p>
                  <input className="input mono-input" readOnly value={inviteResult.url} aria-label="Invitation activation URL" />
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                    <Button size="sm" onClick={() => void navigator.clipboard?.writeText(inviteResult.url)}>Copy activation link</Button>
                    <a className="btn btn-ghost btn-sm" href={inviteResult.url} target="_blank" rel="noreferrer">Open link</a>
                  </div>
                </div>
              )}
            </Panel>
          </div>

          <Panel exhibit="04" title="Participant roster">
            <div className="field" style={{ maxWidth: 420, marginBottom: 12 }}>
              <label className="field-label" htmlFor="roster-filter">Filter roster</label>
              <input id="roster-filter" className="input" value={rosterFilter} onChange={(e) => setRosterFilter(e.target.value)} placeholder="Email, name, team or status" />
            </div>
            {participantsQ.isLoading && <LoadingShell label="Loading participant roster" />}
            {participantsQ.isError && <ErrorBanner error={participantsQ.error} onRetry={() => void participantsQ.refetch()} />}
            {participantsQ.data && filteredParticipants.length === 0 && <EmptyState title="No matching roster entries" note="Create participant invitations above. Pending invitations appear until activated." />}
            {filteredParticipants.length > 0 && (
              <div className="table-scroll">
                <table className="table">
                  <thead><tr><th>Email</th><th>Name</th><th>Team</th><th>Role</th><th>Account</th><th>Invitation</th></tr></thead>
                  <tbody>
                    {filteredParticipants.map((participant, index) => (
                      <tr key={`${participant.id ?? participant.email}-${index}`}>
                        <td className="mono small">{participant.email}</td>
                        <td>{participant.name ?? 'Not activated'}</td>
                        <td>{participant.team_name ?? 'Unassigned'}</td>
                        <td>{participant.role}</td>
                        <td><StatusChip value={participant.status} /></td>
                        <td><Chip tone={participant.activated ? 'ok' : 'warn'}>{participant.activated ? 'activated' : 'pending'}</Chip><div className="muted small">{fmtDateTime(participant.expires_at)}</div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="muted small" style={{ marginTop: 10 }}>
              This API exposes invitation-backed roster rows. Revoking invitations or moving existing users between teams requires dedicated backend mutation endpoints and is not implied by this read-only roster.
            </p>
          </Panel>

          <Panel exhibit="05" title="Configuration revisions">
            {configQ.isLoading && <LoadingShell label="Loading configuration history" />}
            {configQ.isError && <ErrorBanner error={configQ.error} onRetry={() => void configQ.refetch()} />}
            {configQ.data && (
              <>
                <div className="small muted">Current event config version: <strong className="mono">{configQ.data.config_version}</strong></div>
                <form onSubmit={saveConfig} className="stack" style={{ gap: 12, marginTop: 12 }}>
                  <Field label="Configuration JSON" htmlFor="config-json" required hint="Edit the complete JSON object. Unknown keys are preserved only when included in this document.">
                    <textarea id="config-json" className="textarea mono-input" rows={12} spellCheck={false} value={configText} onChange={(e) => { setConfigText(e.target.value); setConfigDirty(true) }} required />
                  </Field>
                  <Field label="Revision reason" htmlFor="config-reason" required>
                    <input id="config-reason" className="input" minLength={5} maxLength={500} value={configReason} onChange={(e) => setConfigReason(e.target.value)} required />
                  </Field>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}><input type="checkbox" checked={freezeConfig} onChange={(e) => setFreezeConfig(e.target.checked)} /> Freeze this snapshot into the event's active config</label>
                  <div className="small muted">Saving a revision records a new immutable version. Freeze also copies the snapshot to the event's active config.</div>
                  <Button type="submit" variant="primary" disabled={busy || !configDirty || configReason.trim().length < 5}>Save revision</Button>
                </form>
                <div className="ruled" style={{ marginTop: 18 }}>
                  <div className="overline" style={{ padding: '10px 0' }}>Revision history</div>
                  {configQ.data.revisions.length === 0 && <p className="muted small">No revisions recorded yet. The event's initial config is shown above.</p>}
                  {configQ.data.revisions.map((revision) => (
                    <div className="kv-row" key={revision.version} style={{ alignItems: 'flex-start' }}>
                      <div>
                        <strong>v{revision.version}</strong> {revision.frozen_at && <Chip tone="ok">frozen</Chip>}
                        <div className="muted small">{revision.reason}</div>
                        <div className="mono small muted">{revision.rule_set_hash}</div>
                      </div>
                      <div className="muted small">{fmtDateTime(revision.created_at)}</div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Panel>

          <Panel exhibit="06" title="Time adjustment">
            <form onSubmit={adjustTime} className="stack" style={{ gap: 12 }}>
              <div className="grid-2">
                <Field label="Stage" htmlFor="time-stage" required>
                  <select id="time-stage" className="select" value={stageId} onChange={(e) => setStageId(e.target.value)} required>
                    <option value="">Select stage</option>
                    {stages.map((stage) => <option key={stage.id} value={stage.id}>{stage.name} ({stage.status})</option>)}
                  </select>
                </Field>
                <Field label="Time delta (seconds)" htmlFor="time-delta" required hint="Positive extends time; negative removes time.">
                  <input id="time-delta" className="input mono-input" type="number" min={-86400} max={86400} step={1} value={timeForm.delta} onChange={(e) => setTimeForm({ ...timeForm, delta: e.target.value })} required />
                </Field>
                <Field label="Reason code" htmlFor="time-reason" required>
                  <select id="time-reason" className="select" value={timeForm.reason} onChange={(e) => setTimeForm({ ...timeForm, reason: e.target.value })}>
                    {TIME_REASONS.map((reason) => <option key={reason}>{reason}</option>)}
                  </select>
                </Field>
                <Field label="Participant (optional)" htmlFor="time-user" hint="Leave empty to record a stage-wide adjustment.">
                  <select id="time-user" className="select" value={timeForm.user_id} onChange={(e) => setTimeForm({ ...timeForm, user_id: e.target.value })}>
                    <option value="">Whole stage</option>
                    {participants.filter((participant) => participant.id).map((participant, index) => <option key={`${participant.id}-${index}`} value={participant.id ?? ''}>{participant.email}</option>)}
                  </select>
                </Field>
              </div>
              <Button type="submit" variant="primary" disabled={busy || !stageId || Number(timeForm.delta) === 0}>Record time adjustment</Button>
            </form>
          </Panel>

          {user?.role === 'admin' && (
            <>
            <StepUpPanel stepUpUntil={stepUpUntil} onStepUp={setStepUpUntil} note="Manual score changes affect the append-only score ledger. Reconfirm your password before enabling this operation." />
            <Panel exhibit="07" title="Manual score adjustment" actions={!scoreElevated ? <StepUpGateHint /> : undefined}>
              <form onSubmit={adjustScore} className="stack" style={{ gap: 12 }}>
                <div className="grid-2">
                  <Field label="Stage" htmlFor="score-stage" required>
                    <select id="score-stage" className="select" value={stageId} onChange={(e) => setStageId(e.target.value)} required>
                      <option value="">Select stage</option>
                      {stages.map((stage) => <option key={stage.id} value={stage.id}>{stage.name}</option>)}
                    </select>
                  </Field>
                  <Field label="Team" htmlFor="score-team" required>
                    <select id="score-team" className="select" value={scoreForm.team_id} onChange={(e) => setScoreForm({ ...scoreForm, team_id: e.target.value })} required>
                      <option value="">Select team</option>
                      {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
                    </select>
                  </Field>
                  <Field label="Points (+/-)" htmlFor="score-points" required hint="Use a negative value to deduct points.">
                    <input id="score-points" className="input mono-input" type="number" min={-10000} max={10000} step="0.01" value={scoreForm.points} onChange={(e) => setScoreForm({ ...scoreForm, points: e.target.value })} required />
                  </Field>
                  <Field label="Reason (audit record)" htmlFor="score-reason" required>
                    <input id="score-reason" className="input" minLength={5} maxLength={1000} value={scoreForm.reason} onChange={(e) => setScoreForm({ ...scoreForm, reason: e.target.value })} required />
                  </Field>
                </div>
                <label className="small" style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <input type="checkbox" checked={scoreForm.confirmed} onChange={(e) => setScoreForm({ ...scoreForm, confirmed: e.target.checked })} />
                  I understand this writes an append-only ledger entry and must be corrected by a reversal, not edited or deleted.
                </label>
                <Button type="submit" variant="danger" disabled={scoreBusy || !scoreElevated || !scoreForm.confirmed || !scoreForm.team_id || !stageId || !scoreForm.reason.trim() || !Number(scoreForm.points)}>
                  {scoreBusy ? 'Recording' : 'Append score adjustment'}
                </Button>
                <p className="muted small">This control is restricted to administrators and requires a fresh password confirmation. The server enforces both checks.</p>
              </form>
            </Panel>
            </>
          )}
        </>
      )}
    </div>
  )
}
