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
  const [participantForm, setParticipantForm] = useState({ email: '', name: '', team_id: '' })
  const [participantTeams, setParticipantTeams] = useState<Record<string, string>>({})
  const [participantBusy, setParticipantBusy] = useState(false)
  const [resetTarget, setResetTarget] = useState<string | null>(null)
  const [resetPassword, setResetPassword] = useState('')
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
    if (!teams.length) {
      setParticipantForm((old) => ({ ...old, team_id: '' }))
      return
    }
    if (!teams.some((team) => team.id === participantForm.team_id)) {
      setParticipantForm((old) => ({ ...old, team_id: teams[0].id }))
    }
    setParticipantTeams((old) => {
      const next = { ...old }
      for (const participant of participants) {
        if (!next[participant.id]) next[participant.id] = participant.team_id
      }
      return next
    })
  }, [teams, participants, participantForm.team_id])

  useEffect(() => {
    setConfigDirty(false)
    setConfigReason('')
    setFreezeConfig(false)
    setParticipantForm({ email: '', name: '', team_id: '' })
    setParticipantTeams({})
    setResetTarget(null)
    setResetPassword('')
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

  async function registerParticipant(e: React.FormEvent) {
    e.preventDefault()
    if (!eventId || !participantForm.team_id || !scoreElevated || user?.role !== 'admin') return
    setParticipantBusy(true)
    clearMessages()
    try {
      const result = await api<{ participant_id: string; password_setup_required: boolean }>(
        `/v1/admin/events/${eventId}/participants`,
        {
          method: 'POST',
          body: {
            email: participantForm.email.trim().toLowerCase(),
            name: participantForm.name.trim(),
            team_id: participantForm.team_id,
          },
        },
      )
      setParticipantForm({ email: '', name: '', team_id: '' })
      setNotice(
        result.password_setup_required
          ? 'Participant registered without a password. They set their initial password on first sign-in.'
          : 'Existing participant account assigned to this event team.',
      )
      await queryClient.invalidateQueries({ queryKey: ['admin-participants', eventId] })
      await queryClient.invalidateQueries({ queryKey: ['admin-teams', eventId] })
    } catch (err) {
      showError(err)
    } finally {
      setParticipantBusy(false)
    }
  }

  async function reassignParticipant(participantId: string) {
    const team_id = participantTeams[participantId]
    if (!eventId || !team_id || !scoreElevated || user?.role !== 'admin') return
    setParticipantBusy(true)
    clearMessages()
    try {
      await api(`/v1/admin/events/${eventId}/participants/${participantId}/team`, {
        method: 'PATCH',
        body: { team_id },
      })
      setParticipantTeams((old) => ({ ...old, [participantId]: team_id }))
      setNotice('Participant team assignment updated.')
      await queryClient.invalidateQueries({ queryKey: ['admin-participants', eventId] })
      await queryClient.invalidateQueries({ queryKey: ['admin-teams', eventId] })
    } catch (err) {
      showError(err)
    } finally {
      setParticipantBusy(false)
    }
  }

  async function removeParticipant(participant: AdminParticipant) {
    if (!eventId || !scoreElevated || user?.role !== 'admin') return
    if (!window.confirm(`Remove ${participant.email} from this event?`)) return
    setParticipantBusy(true)
    clearMessages()
    try {
      await api(`/v1/admin/events/${eventId}/participants/${participant.id}`, { method: 'DELETE' })
      setNotice('Participant removed from this event. Their sessions are revoked if they had no other event team membership.')
      await queryClient.invalidateQueries({ queryKey: ['admin-participants', eventId] })
      await queryClient.invalidateQueries({ queryKey: ['admin-teams', eventId] })
      setResetTarget(null)
      setResetPassword('')
    } catch (err) {
      showError(err)
    } finally {
      setParticipantBusy(false)
    }
  }

  async function resetParticipantPassword(participant: AdminParticipant) {
    if (!scoreElevated || user?.role !== 'admin' || resetPassword.length < 12) return
    setParticipantBusy(true)
    clearMessages()
    try {
      await api(`/v1/admin/users/${participant.id}/password-reset`, {
        method: 'POST',
        body: { password: resetPassword },
      })
      setNotice(`Password reset for ${participant.email}. Share the new password privately; existing sessions were revoked.`)
      setResetTarget(null)
      setResetPassword('')
      await queryClient.invalidateQueries({ queryKey: ['admin-participants', eventId] })
    } catch (err) {
      showError(err)
    } finally {
      setParticipantBusy(false)
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
          {user?.role === 'admin' && (
            <StepUpPanel
              stepUpUntil={stepUpUntil}
              onStepUp={setStepUpUntil}
              note="Participant registration, team changes, removals, password resets and manual scoring require fresh password confirmation."
            />
          )}
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

            <Panel exhibit="03" title="Register participant">
              {user?.role === 'admin' ? (
                <>
                  <form onSubmit={registerParticipant} className="stack" style={{ gap: 12 }}>
                    <Field label="Participant email" htmlFor="participant-email" required>
                      <input id="participant-email" className="input" type="email" autoComplete="email" required value={participantForm.email} onChange={(e) => setParticipantForm({ ...participantForm, email: e.target.value })} />
                    </Field>
                    <Field label="Participant name" htmlFor="participant-name" required>
                      <input id="participant-name" className="input" minLength={2} maxLength={160} required value={participantForm.name} onChange={(e) => setParticipantForm({ ...participantForm, name: e.target.value })} />
                    </Field>
                    <Field label="Assign team" htmlFor="participant-team" required>
                      <select id="participant-team" className="select" value={participantForm.team_id} onChange={(e) => setParticipantForm({ ...participantForm, team_id: e.target.value })} required>
                        <option value="">Select team</option>
                        {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
                      </select>
                    </Field>
                    <Button type="submit" variant="primary" disabled={participantBusy || !scoreElevated || !participantForm.email || participantForm.name.trim().length < 2 || !participantForm.team_id}>
                      {participantBusy ? 'Registering' : 'Register participant'}
                    </Button>
                  </form>
                  <p className="muted small" style={{ marginTop: 12 }}>
                    Registration is admin-only. No invitation or activation token is issued. New accounts start without a password; existing registered participant accounts keep their current password. A participant without a password chooses it on first sign-in.
                  </p>
                </>
              ) : (
                <p className="muted small">Only an administrator can register, reassign, remove, or reset passwords for participants.</p>
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
            {participantsQ.data && filteredParticipants.length === 0 && <EmptyState title="No matching participants" note="Register participants above. Each account belongs to one team in this event." />}
            {filteredParticipants.length > 0 && (
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Email</th><th>Name</th><th>Team</th><th>Role</th><th>Account</th><th>Password</th>
                      {user?.role === 'admin' && <th>Admin controls</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredParticipants.map((participant) => (
                      <tr key={participant.id}>
                        <td className="mono small">{participant.email}</td>
                        <td>{participant.name}</td>
                        <td>{participant.team_name}</td>
                        <td>{participant.role}</td>
                        <td><StatusChip value={participant.status} /></td>
                        <td><Chip tone={participant.password_set ? 'ok' : 'warn'}>{participant.password_set ? 'set' : 'first sign-in pending'}</Chip></td>
                        {user?.role === 'admin' && (
                          <td style={{ minWidth: 260 }}>
                            <div className="stack" style={{ gap: 8 }}>
                              <div style={{ display: 'flex', gap: 6 }}>
                                <select
                                  className="select"
                                  aria-label={`Team for ${participant.email}`}
                                  value={participantTeams[participant.id] ?? participant.team_id}
                                  onChange={(e) => setParticipantTeams((old) => ({ ...old, [participant.id]: e.target.value }))}
                                >
                                  {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
                                </select>
                                <Button size="sm" disabled={participantBusy || !scoreElevated || (participantTeams[participant.id] ?? participant.team_id) === participant.team_id} onClick={() => void reassignParticipant(participant.id)}>Move</Button>
                              </div>
                              {resetTarget === participant.id ? (
                                <div className="stack" style={{ gap: 6 }}>
                                  <input
                                    className="input mono-input"
                                    type="password"
                                    autoComplete="new-password"
                                    minLength={12}
                                    maxLength={200}
                                    aria-label={`New password for ${participant.email}`}
                                    placeholder="New password (12+ chars)"
                                    value={resetPassword}
                                    onChange={(e) => setResetPassword(e.target.value)}
                                  />
                                  <div style={{ display: 'flex', gap: 6 }}>
                                    <Button size="sm" variant="primary" disabled={participantBusy || !scoreElevated || resetPassword.length < 12} onClick={() => void resetParticipantPassword(participant)}>Reset password</Button>
                                    <Button size="sm" variant="ghost" onClick={() => { setResetTarget(null); setResetPassword('') }}>Cancel</Button>
                                  </div>
                                </div>
                              ) : (
                                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                  <Button size="sm" variant="ghost" disabled={participantBusy || !scoreElevated} onClick={() => { setResetTarget(participant.id); setResetPassword('') }}>Reset password</Button>
                                  <Button size="sm" variant="danger" disabled={participantBusy || !scoreElevated} onClick={() => void removeParticipant(participant)}>Remove</Button>
                                </div>
                              )}
                            </div>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="muted small" style={{ marginTop: 10 }}>
              Participant registration, team changes, removal and password resets are server-enforced admin-only actions. Password reset revokes the participant's existing sessions.
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
                      <div className="stack" style={{ alignItems: 'flex-end', gap: 6 }}>
                        <div className="muted small">{fmtDateTime(revision.created_at)}</div>
                        <Button
                          size="sm"
                          onClick={() => {
                            setConfigText(JSON.stringify(revision.config, null, 2))
                            setConfigReason(`Create a new revision based on v${revision.version}`)
                            setFreezeConfig(false)
                            setConfigDirty(true)
                          }}
                        >
                          Load into editor
                        </Button>
                      </div>
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
                    {participants.map((participant) => <option key={participant.id} value={participant.id}>{participant.email}</option>)}
                  </select>
                </Field>
              </div>
              <Button type="submit" variant="primary" disabled={busy || !stageId || Number(timeForm.delta) === 0}>Record time adjustment</Button>
            </form>
          </Panel>

          {user?.role === 'admin' && (
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
          )}
        </>
      )}
    </div>
  )
}
