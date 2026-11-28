import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiRequestError } from '../../lib/api'
import type {
  AdminEvent,
  AuditEntry,
  AuditVerifyResult,
  Dispute,
  ExportFile,
  LiveOps,
  ReadinessItem,
  SystemHealth,
} from '../../lib/types'
import { fmtDateTime } from '../../lib/format'
import {
  Button,
  Chip,
  ConfirmButton,
  EmptyState,
  ErrorBanner,
  Field,
  LoadingShell,
  Panel,
  StatusChip,
  KVRow,
} from '../../ui/components'
import { adminErrorText, downloadCsv, needsStepUp, StepUpPanel, useStepUpWindow } from '../../components/StepUp'

/* event lifecycle transitions as implemented by the backend */
const EVENT_TRANSITIONS: Record<string, string[]> = {
  draft: ['configured'],
  configured: ['ready', 'draft'],
  ready: ['live'],
  live: ['closed'],
  closed: ['finalized'],
  finalized: ['archived'],
}

/* stage control actions per current status (backend mapping) */
const STAGE_ACTIONS: Record<string, string[]> = {
  draft: ['seal'],
  sealed: ['arm'],
  armed: ['start'],
  active: ['pause', 'end'],
  paused: ['resume', 'end'],
  ended: [],
  evaluating: [],
  released: [],
  final: [],
}
const ACTION_LABELS: Record<string, string> = {
  seal: 'Seal',
  arm: 'Arm',
  start: 'Start',
  pause: 'Pause',
  resume: 'Resume',
  end: 'End',
  extend: 'Extend',
}

/* ---------------- events + lifecycle ---------------- */

function EventsSection({
  selectedId,
  onCreated,
}: {
  selectedId: string | null
  onCreated: () => void
}) {
  const queryClient = useQueryClient()
  const q = useQuery({
    queryKey: ['admin-events'],
    queryFn: () => api<{ events: AdminEvent[] }>('/v1/admin/events'),
  })
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ name: '', slug: '', duration: '180', maxP: '50', maxT: '12' })

  async function transition(ev: AdminEvent, target: string) {
    setBusyId(ev.id)
    setError(null)
    try {
      await api<{ ok: boolean }>(`/v1/admin/events/${ev.id}/status`, { method: 'POST', body: { status: target } })
      await queryClient.invalidateQueries({ queryKey: ['admin-events'] })
    } catch (err) {
      setError(adminErrorText(err))
    } finally {
      setBusyId(null)
    }
  }

  async function createEvent(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await api<{ event_id: string }>('/v1/admin/events', {
        method: 'POST',
        body: {
          name: form.name.trim(),
          slug: form.slug.trim(),
          duration_minutes: Number(form.duration) || 180,
          max_participants: Number(form.maxP) || 50,
          max_teams: Number(form.maxT) || 12,
        },
      })
      setForm({ name: '', slug: '', duration: '180', maxP: '50', maxT: '12' })
      setShowCreate(false)
      onCreated()
      await queryClient.invalidateQueries({ queryKey: ['admin-events'] })
    } catch (err) {
      setError(adminErrorText(err))
    }
  }

  return (
    <Panel
      exhibit="EX-01"
      title="Events and lifecycle"
      actions={
        <Button size="sm" onClick={() => setShowCreate((s) => !s)}>
          {showCreate ? 'Close form' : 'New event'}
        </Button>
      }
    >
      {error && <ErrorBanner error={new ApiRequestError({ code: 'OPERATION', serverMessage: error })} />}
      {q.isLoading && <LoadingShell label="Loading events" />}
      {q.isError && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.events.length === 0 && (
        <EmptyState title="No events yet" note="Create the event record first; stages and teams attach to it." />
      )}
      {q.data && q.data.events.length > 0 && (
        <div className="table-scroll">
          <table className="table">
            <caption className="sr-only">Events with lifecycle controls</caption>
            <thead>
              <tr>
                <th scope="col">Event</th>
                <th scope="col">Status</th>
                <th scope="col">Start</th>
                <th scope="col">Caps</th>
                <th scope="col">Versions</th>
                <th scope="col">Transitions</th>
              </tr>
            </thead>
            <tbody>
              {q.data.events.map((ev) => (
                <tr key={ev.id} style={ev.id === selectedId ? { background: 'var(--panel-2)' } : undefined}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{ev.name}</div>
                    <div className="muted small mono">{ev.slug}</div>
                  </td>
                  <td><StatusChip value={ev.status} /></td>
                  <td className="mono small">{ev.starts_at ? fmtDateTime(ev.starts_at) : 'not set'}</td>
                  <td className="mono small">{ev.max_participants}p / {ev.max_teams}t</td>
                  <td className="mono small">rules v{ev.rules_version} {'·'} config v{ev.config_version}</td>
                  <td>
                    <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                      {(EVENT_TRANSITIONS[ev.status] ?? []).map((t) => (
                        <ConfirmButton
                          key={t}
                          label={`To ${t}`}
                          confirmLabel={`Set ${t}`}
                          disabled={busyId === ev.id}
                          onConfirm={() => void transition(ev, t)}
                        />
                      ))}
                      {(EVENT_TRANSITIONS[ev.status] ?? []).length === 0 && <span className="muted small">terminal</span>}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && (
        <form onSubmit={createEvent} className="stack panel-2" style={{ gap: 12, padding: 14, marginTop: 14 }} aria-label="Create event">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
            <Field label="Name" htmlFor="ev-name" required>
              <input id="ev-name" className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required minLength={3} />
            </Field>
            <Field label="Slug (lowercase, dashes)" htmlFor="ev-slug" required>
              <input id="ev-slug" className="input mono-input" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} required pattern="[a-z0-9-]+" />
            </Field>
            <Field label="Duration (min)" htmlFor="ev-dur">
              <input id="ev-dur" className="input mono-input" type="number" min={30} max={480} value={form.duration} onChange={(e) => setForm({ ...form, duration: e.target.value })} />
            </Field>
            <Field label="Max participants" htmlFor="ev-mp">
              <input id="ev-mp" className="input mono-input" type="number" min={1} value={form.maxP} onChange={(e) => setForm({ ...form, maxP: e.target.value })} />
            </Field>
            <Field label="Max teams" htmlFor="ev-mt">
              <input id="ev-mt" className="input mono-input" type="number" min={1} value={form.maxT} onChange={(e) => setForm({ ...form, maxT: e.target.value })} />
            </Field>
          </div>
          <div>
            <Button type="submit" variant="primary">Create event</Button>
            <span className="muted small" style={{ marginLeft: 10 }}>
              Timezone defaults to Asia/Kolkata. Stages are created under the live ops panel after the event exists.
            </span>
          </div>
        </form>
      )}
    </Panel>
  )
}

/* ---------------- live ops + stages ---------------- */

function LiveOpsSection({ eventId }: { eventId: string }) {
  const queryClient = useQueryClient()
  const q = useQuery({
    queryKey: ['admin-live', eventId],
    queryFn: () => api<LiveOps>(`/v1/admin/events/${eventId}/live`),
    refetchInterval: 5_000,
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const [extendSeconds, setExtendSeconds] = useState<Record<string, string>>({})
  const [announce, setAnnounce] = useState({ severity: 'info', body: '' })
  const [announceDone, setAnnounceDone] = useState<string | null>(null)
  const [stepUpUntil, setStepUpUntil] = useState<number | null>(null)
  const { nowMs, elevated } = useStepUpWindow(stepUpUntil)

  async function stageAction(stageId: string, action: string) {
    setBusy(true)
    setError(null)
    try {
      await api<{ ok: boolean }>(`/v1/admin/stages/${stageId}/control`, {
        method: 'POST',
        body: { action, reason: reasons[stageId] ?? '' },
      })
      await queryClient.invalidateQueries({ queryKey: ['admin-live', eventId] })
    } catch (err) {
      setError(adminErrorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function extendStage(stageId: string) {
    setBusy(true)
    setError(null)
    try {
      await api<{ ok: boolean }>(`/v1/admin/stages/${stageId}/control`, {
        method: 'POST',
        body: { action: 'extend', seconds: Number(extendSeconds[stageId] ?? 0), reason: reasons[stageId] ?? '' },
      })
      await queryClient.invalidateQueries({ queryKey: ['admin-live', eventId] })
    } catch (err) {
      setError(adminErrorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function release(stageId: string, action: 'evaluate' | 'release') {
    if (!elevated) {
      setError('Confirm your password in the step-up panel before evaluating or publishing results.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api<{ ok: boolean }>(`/v1/admin/stages/${stageId}/release`, {
        method: 'POST',
        body: { action, reason: reasons[stageId] || 'results release' },
      })
      await queryClient.invalidateQueries({ queryKey: ['admin-live', eventId] })
    } catch (err) {
      if (needsStepUp(err)) setStepUpUntil(null)
      setError(adminErrorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function postAnnouncement(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setAnnounceDone(null)
    try {
      await api<{ ok: boolean }>(`/v1/admin/events/${eventId}/announcements`, {
        method: 'POST',
        body: { severity: announce.severity, body: announce.body.trim() },
      })
      setAnnounce({ severity: 'info', body: '' })
      setAnnounceDone('Announcement published to every signed-in console.')
    } catch (err) {
      setError(adminErrorText(err))
    } finally {
      setBusy(false)
    }
  }

  if (q.isLoading) return <LoadingShell label="Loading live ops" />
  if (q.isError) return <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return null

  const live = q.data
  const subCounts = Object.entries(live.submission_counts)

  return (
    <div className="stack">
      {error && <ErrorBanner error={new ApiRequestError({ code: 'OPERATION', serverMessage: error })} />}
      <StepUpPanel
        stepUpUntil={stepUpUntil}
        onStepUp={setStepUpUntil}
        nowMs={nowMs}
        note="Stage evaluation and results publication require a recent password confirmation."
      />

      <Panel
        exhibit="EX-02"
        title="Live operations"
        actions={<Chip tone="signal" size="lg">auto-refresh 5s</Chip>}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 14 }}>
          <div className="panel-2" style={{ padding: 12 }}>
            <span className="overline" style={{ display: 'block' }}>Teams</span>
            <span className="mono" style={{ fontSize: 22 }}>{live.teams}</span>
          </div>
          <div className="panel-2" style={{ padding: 12 }}>
            <span className="overline" style={{ display: 'block' }}>Participants</span>
            <span className="mono" style={{ fontSize: 22 }}>{live.participants_online_estimate}</span>
            <div className="muted small">accounts in teams</div>
          </div>
          <div className="panel-2" style={{ padding: 12 }}>
            <span className="overline" style={{ display: 'block' }}>Review backlog</span>
            <span className="mono" style={{ fontSize: 22, color: live.review_backlog > 10 ? 'var(--warn)' : 'var(--paper)' }}>{live.review_backlog}</span>
            <div className="muted small">open items</div>
          </div>
          <div className="panel-2" style={{ padding: 12 }}>
            <span className="overline" style={{ display: 'block' }}>Oldest queued job</span>
            <span className="mono" style={{ fontSize: 22 }}>{live.job_queue.oldest_queued_age_s}s</span>
            <div className="muted small">dead letters: {live.job_queue.dead_letters}</div>
          </div>
          <div className="panel-2" style={{ padding: 12 }}>
            <span className="overline" style={{ display: 'block' }}>Databases</span>
            <span style={{ display: 'flex', gap: 6, marginTop: 4 }}>
              <StatusChip value={live.health.database_app} />
              <StatusChip value={live.health.database_gt} />
            </span>
          </div>
        </div>
        <div className="ruled">
          {subCounts.length === 0 && <p className="muted small" style={{ padding: '8px 0' }}>No submissions recorded for this event yet.</p>}
          {subCounts.map(([status, count]) => (
            <KVRow key={status} k={`submissions: ${status.replaceAll('_', ' ')}`} v={String(count)} />
          ))}
        </div>
        <div style={{ marginTop: 10 }}>
          <span className="overline" style={{ display: 'block', marginBottom: 6 }}>Kill switches (deployment settings)</span>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {Object.entries(live.kill_switches).map(([k, v]) => (
              <Chip key={k} tone={v ? 'ok' : 'muted'}>{k.replaceAll('_', ' ')}: {v ? 'on' : 'off'}</Chip>
            ))}
          </div>
        </div>
      </Panel>

      <Panel exhibit="EX-03" title="Stage control">
        {live.stages.length === 0 && (
          <EmptyState
            title="No stages configured"
            note="Create a stage for this event via POST /admin/events/{id}/stages before the round can be sealed, armed and started."
          />
        )}
        <div className="stack" style={{ gap: 14 }}>
          {live.stages.map((st) => {
            const actions = STAGE_ACTIONS[st.status] ?? []
            const reasonOk = (reasons[st.id] ?? '').trim().length > 0
            return (
              <div key={st.id} className="panel-2" style={{ padding: 14, display: 'grid', gap: 10 }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 600 }}>{st.name}</span>
                  <StatusChip value={st.status} size="lg" />
                  <span className="muted small mono">
                    {st.starts_at ? `start ${fmtDateTime(st.starts_at)}` : 'not started'} {'·'} {st.ends_at ? `end ${fmtDateTime(st.ends_at)}` : 'no end'}
                  </span>
                  <span className="stage-flow" style={{ marginLeft: 'auto' }}>id {st.id.slice(0, 8)}</span>
                </div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                  <div className="field" style={{ flex: '1 1 240px' }}>
                    <label className="field-label" htmlFor={`reason-${st.id}`}>Reason code (required)</label>
                    <input
                      id={`reason-${st.id}`}
                      className="input mono-input"
                      value={reasons[st.id] ?? ''}
                      onChange={(e) => setReasons((r) => ({ ...r, [st.id]: e.target.value }))}
                      placeholder="e.g. TEC-104 projector outage"
                    />
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    {actions.map((a) =>
                      a === 'end' ? (
                        <ConfirmButton key={a} label={ACTION_LABELS[a]} confirmLabel="End round" variant="danger" disabled={busy || !reasonOk} onConfirm={() => void stageAction(st.id, a)} />
                      ) : (
                        <Button key={a} size="sm" disabled={busy || !reasonOk} onClick={() => void stageAction(st.id, a)}>
                          {ACTION_LABELS[a]}
                        </Button>
                      ),
                    )}
                    {(st.status === 'active' || st.status === 'paused') && (
                      <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                        <input
                          className="input mono-input"
                          style={{ width: 90 }}
                          type="number"
                          min={1}
                          max={3600}
                          aria-label={`Extend stage ${st.name} by seconds`}
                          placeholder="+sec"
                          value={extendSeconds[st.id] ?? ''}
                          onChange={(e) => setExtendSeconds((s) => ({ ...s, [st.id]: e.target.value }))}
                        />
                        <Button size="sm" disabled={busy || !reasonOk || !extendSeconds[st.id]} onClick={() => void extendStage(st.id)}>
                          Extend
                        </Button>
                      </span>
                    )}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span className="overline">Release</span>
                  <Button size="sm" disabled={busy || !elevated || st.status !== 'ended'} onClick={() => void release(st.id, 'evaluate')} title={!elevated ? 'Requires recent password confirmation' : st.status !== 'ended' ? 'Requires stage status ended' : undefined}>
                    Evaluate
                  </Button>
                  <Button size="sm" variant="primary" disabled={busy || !elevated || !(st.status === 'ended' || st.status === 'evaluating')} onClick={() => void release(st.id, 'release')} title={!elevated ? 'Requires recent password confirmation' : 'Requires stage ended or evaluating'}>
                    Release results
                  </Button>
                  <span className="muted small">ended {'>'} evaluating {'>'} released {'>'} final</span>
                </div>
              </div>
            )
          })}
        </div>
      </Panel>

      <Panel exhibit="EX-04" title="Announcement composer">
        <form onSubmit={postAnnouncement} className="stack" style={{ gap: 12 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 12 }}>
            <Field label="Severity" htmlFor="ann-sev">
              <div className="select-wrap">
                <select id="ann-sev" className="select" value={announce.severity} onChange={(e) => setAnnounce({ ...announce, severity: e.target.value })}>
                  <option value="info">info</option>
                  <option value="warning">warning</option>
                  <option value="critical">critical</option>
                </select>
              </div>
            </Field>
            <Field label="Body" htmlFor="ann-body" required hint={`${announce.body.trim().length}/1000`}>
              <textarea id="ann-body" className="textarea" maxLength={1000} value={announce.body} onChange={(e) => setAnnounce({ ...announce, body: e.target.value })} required minLength={3} />
            </Field>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <Button type="submit" variant="primary" disabled={busy || announce.body.trim().length < 3}>Publish announcement</Button>
            {announceDone && <span className="small" style={{ color: 'var(--ok)' }} role="status">{announceDone}</span>}
          </div>
        </form>
      </Panel>
    </div>
  )
}

/* ---------------- readiness ---------------- */

function ReadinessSection({ eventId }: { eventId: string }) {
  const queryClient = useQueryClient()
  const q = useQuery({
    queryKey: ['admin-readiness', eventId],
    queryFn: () => api<{ items: ReadinessItem[] }>(`/v1/admin/events/${eventId}/readiness`),
  })
  const [error, setError] = useState<string | null>(null)

  async function setState(key: string, state: string) {
    setError(null)
    try {
      await api<{ ok: boolean }>(`/v1/admin/events/${eventId}/readiness/${key}`, { method: 'POST', body: { state } })
      await queryClient.invalidateQueries({ queryKey: ['admin-readiness', eventId] })
    } catch (err) {
      setError(adminErrorText(err))
    }
  }

  if (q.isLoading) return <LoadingShell label="Loading readiness" />
  if (q.isError) return <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />

  const items = q.data?.items ?? []
  const unchecked = items.filter((i) => i.state === 'unchecked').length

  return (
    <Panel
      exhibit="EX-05"
      title="Readiness gate"
      actions={unchecked > 0 ? <Chip tone="warn" size="lg">{unchecked} unchecked: going live is blocked</Chip> : <Chip tone="ok" size="lg">all checked</Chip>}
    >
      {error && <ErrorBanner error={new ApiRequestError({ code: 'OPERATION', serverMessage: error })} />}
      {items.length === 0 && (
        <EmptyState title="No readiness items" note="The seed script creates the checklist for the event. Every item must be checked before the event can go live." />
      )}
      {items.map((item) => (
        <div key={item.key} className="kv-row" style={{ alignItems: 'center' }}>
          <span style={{ display: 'flex', gap: 10, alignItems: 'center', minWidth: 0 }}>
            <StatusChip value={item.state} />
            <span style={{ minWidth: 0 }}>
              {item.label}
              <span className="muted small mono" style={{ display: 'block' }}>
                {item.key}
                {item.checked_at ? ` · checked ${fmtDateTime(item.checked_at)}` : ''}
              </span>
            </span>
          </span>
          <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Button size="sm" disabled={item.state === 'checked'} onClick={() => void setState(item.key, 'checked')}>Check</Button>
            <Button size="sm" disabled={item.state === 'unchecked'} onClick={() => void setState(item.key, 'unchecked')}>Uncheck</Button>
            <Button size="sm" disabled={item.state === 'na'} onClick={() => void setState(item.key, 'na')}>N/A</Button>
          </span>
        </div>
      ))}
    </Panel>
  )
}

/* ---------------- exports + audit ---------------- */

function ExportsSection({ eventId }: { eventId: string }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function doExport(kind: 'results' | 'submissions' | 'ledger') {
    setBusy(kind)
    setError(null)
    try {
      const res = await api<ExportFile>(`/v1/admin/events/${eventId}/export/${kind}`)
      downloadCsv(res.filename, res.content)
    } catch (err) {
      setError(adminErrorText(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel exhibit="EX-06" title="Exports">
      <p className="muted small">CSV downloads generated server-side from the live database.</p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {(['results', 'submissions', 'ledger'] as const).map((kind) => (
          <Button key={kind} size="sm" disabled={busy !== null} onClick={() => void doExport(kind)}>
            {busy === kind ? 'Preparing' : `Download ${kind}`}
          </Button>
        ))}
      </div>
      {error && <div className="field-error" role="alert" style={{ marginTop: 8 }}>{error}</div>}
    </Panel>
  )
}

function AuditSection() {
  const [actionFilter, setActionFilter] = useState('')
  const [limit, setLimit] = useState(100)
  const q = useQuery({
    queryKey: ['admin-audit', actionFilter, limit],
    queryFn: () => api<{ entries: AuditEntry[] }>(`/v1/admin/audit?limit=${limit}${actionFilter ? `&action=${encodeURIComponent(actionFilter)}` : ''}`),
    refetchInterval: 15_000,
  })
  const [verifyResult, setVerifyResult] = useState<AuditVerifyResult | null>(null)
  const [verifyError, setVerifyError] = useState<string | null>(null)

  async function runVerify() {
    setVerifyError(null)
    try {
      const res = await api<AuditVerifyResult>('/v1/admin/audit/verify')
      setVerifyResult(res)
    } catch (err) {
      setVerifyError(adminErrorText(err))
    }
  }

  return (
    <Panel
      exhibit="EX-07"
      title="Audit log and chain verification"
      actions={
        <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            className="input mono-input"
            style={{ width: 160 }}
            placeholder="filter by action"
            aria-label="Filter audit log by action"
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
          />
          <Button size="sm" onClick={() => void runVerify()}>Verify chain</Button>
        </span>
      }
    >
      {verifyResult && (
        <div className={`banner ${verifyResult.intact ? 'banner-tone-ok' : 'banner-tone-danger'}`} role="status" style={{ marginBottom: 12 }}>
          <span className="banner-title">{verifyResult.intact ? 'Hash chain intact' : 'Chain break detected'}</span>
          <div className="small mono">
            scope {verifyResult.scope} {'·'} entries {verifyResult.entries} {'·'} verified {verifyResult.verified}
            {verifyResult.broken_at ? ` · first break at ${verifyResult.broken_at}` : ''}
          </div>
        </div>
      )}
      {verifyError && <div className="field-error" role="alert" style={{ marginBottom: 8 }}>{verifyError}</div>}

      {q.isLoading && <LoadingShell label="Loading audit log" />}
      {q.isError && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.entries.length === 0 && <EmptyState title="No audit entries" note="Privileged actions append entries here in newest-first order." />}
      {q.data && q.data.entries.length > 0 && (
        <>
          <div className="table-scroll" style={{ maxHeight: 420, overflowY: 'auto' }}>
            <table className="table" style={{ minWidth: 640 }}>
              <caption className="sr-only">Audit entries, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Seq</th>
                  <th scope="col">Time</th>
                  <th scope="col">Actor</th>
                  <th scope="col">Action</th>
                  <th scope="col">Entity</th>
                  <th scope="col">Request</th>
                </tr>
              </thead>
              <tbody>
                {q.data.entries.map((e) => (
                  <tr key={e.seq}>
                    <td className="mono small">{e.seq}</td>
                    <td className="mono small">{fmtDateTime(e.ts)}</td>
                    <td className="mono small">{e.actor_role}</td>
                    <td className="mono small" style={{ color: 'var(--signal)' }}>{e.action}</td>
                    <td className="mono small" style={{ maxWidth: 220, wordBreak: 'break-all' }}>{e.entity_type}:{e.entity_id.slice(0, 13)}</td>
                    <td className="mono small">{e.request_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10 }}>
            <span className="muted small">showing {q.data.entries.length}</span>
            <Button size="sm" onClick={() => setLimit((l) => Math.min(500, l + 100))}>Load more</Button>
          </div>
        </>
      )}
    </Panel>
  )
}

/* ---------------- disputes ---------------- */

function DisputesSection() {
  const queryClient = useQueryClient()
  const q = useQuery({
    queryKey: ['admin-disputes'],
    queryFn: () => api<{ disputes: Dispute[] }>('/v1/admin/disputes'),
    refetchInterval: 20_000,
  })
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [forms, setForms] = useState<Record<string, { outcome: string; resolution: string; adjust: string }>>({})

  async function resolve(id: string) {
    const f = forms[id]
    if (!f?.outcome) return
    setBusy(id)
    setError(null)
    try {
      await api<{ ok: boolean }>(`/v1/admin/disputes/${id}/resolve`, {
        method: 'POST',
        body: {
          outcome: f.outcome,
          resolution: f.resolution.trim(),
          adjust_points: f.adjust !== '' ? Number(f.adjust) : undefined,
        },
      })
      await queryClient.invalidateQueries({ queryKey: ['admin-disputes'] })
    } catch (err) {
      setError(adminErrorText(err))
    } finally {
      setBusy(null)
    }
  }

  if (q.isLoading) return <LoadingShell label="Loading disputes" />
  if (q.isError) return <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />

  const disputes = q.data?.disputes ?? []

  return (
    <Panel exhibit="EX-08" title="Disputes">
      {error && <ErrorBanner error={new ApiRequestError({ code: 'OPERATION', serverMessage: error })} />}
      {disputes.length === 0 && <EmptyState title="No disputes filed" note="Teams can dispute released results within the announced window; filings appear here." />}
      {disputes.map((d) => {
        const open = !['upheld', 'rejected'].includes(d.status)
        const f = forms[d.id] ?? { outcome: '', resolution: '', adjust: '' }
        return (
          <div key={d.id} className="panel-2" style={{ padding: 14, marginBottom: 12, display: 'grid', gap: 10 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <StatusChip value={d.status} />
              <span className="mono small" style={{ color: 'var(--signal)' }}>{d.receipt_code}</span>
              <span className="small">{d.title}</span>
              <span className="muted small mono" style={{ marginLeft: 'auto' }}>{fmtDateTime(d.created_at)}</span>
            </div>
            <p className="small" style={{ margin: 0 }}>{d.statement}</p>
            {open ? (
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <div className="field" style={{ width: 160 }}>
                  <label className="field-label" htmlFor={`out-${d.id}`}>Outcome</label>
                  <div className="select-wrap">
                    <select id={`out-${d.id}`} className="select" value={f.outcome} onChange={(e) => setForms((m) => ({ ...m, [d.id]: { ...f, outcome: e.target.value } }))}>
                      <option value="">Select</option>
                      <option value="upheld">upheld</option>
                      <option value="rejected">rejected</option>
                    </select>
                  </div>
                </div>
                <div className="field" style={{ flex: '1 1 220px' }}>
                  <label className="field-label" htmlFor={`res-${d.id}`}>Resolution note</label>
                  <input id={`res-${d.id}`} className="input" value={f.resolution} onChange={(e) => setForms((m) => ({ ...m, [d.id]: { ...f, resolution: e.target.value } }))} />
                </div>
                <div className="field" style={{ width: 140 }}>
                  <label className="field-label" htmlFor={`adj-${d.id}`}>Adjust points</label>
                  <input id={`adj-${d.id}`} className="input mono-input" type="number" step="0.5" placeholder="0" value={f.adjust} onChange={(e) => setForms((m) => ({ ...m, [d.id]: { ...f, adjust: e.target.value } }))} />
                </div>
                <ConfirmButton label="Resolve" confirmLabel="Confirm resolve" variant="primary" disabled={busy === d.id || !f.outcome} onConfirm={() => void resolve(d.id)} />
              </div>
            ) : (
              <Chip tone="muted">resolved</Chip>
            )}
          </div>
        )
      })}
    </Panel>
  )
}

/* ---------------- system health ---------------- */

function SystemHealthSection() {
  const q = useQuery({
    queryKey: ['admin-health'],
    queryFn: () => api<SystemHealth>('/v1/admin/system/health'),
    refetchInterval: 10_000,
  })
  if (q.isLoading) return <LoadingShell label="Loading system health" />
  if (q.isError) return <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return null
  const h = q.data
  return (
    <Panel exhibit="EX-09" title="System health">
      <div className="ruled">
        <KVRow k="database app" v={<StatusChip value={h.databases.app} />} mono={false} />
        <KVRow k="database gt (ground truth)" v={<StatusChip value={h.databases.gt} />} mono={false} />
        {Object.entries(h.jobs).map(([status, count]) => (
          <KVRow key={status} k={`jobs: ${status.replaceAll('_', ' ')}`} v={String(count)} />
        ))}
        <KVRow k="oldest queued job" v={`${h.oldest_queued_job_age_s}s`} />
        <KVRow k="profile" v={h.profile} />
        <KVRow k="environment" v={h.environment} />
        <KVRow k="runner" v={h.runner_enabled ? 'enabled' : 'disabled'} />
        <KVRow k="ai assessment" v={h.ai_assessment_enabled ? 'enabled' : 'disabled'} />
      </div>
      <p className="muted small" style={{ marginTop: 10 }}>
        Code execution is disabled in this deployment (ADR-010); reports are scored by reviewers and organizer-run
        tests. The runner switch reflects the deployment configuration, not a live toggle.
      </p>
    </Panel>
  )
}

/* ---------------- page ---------------- */

export default function AdminDashboard() {
  const eventsQ = useQuery({
    queryKey: ['admin-events'],
    queryFn: () => api<{ events: AdminEvent[] }>('/v1/admin/events'),
  })
  const [eventId, setEventId] = useState<string | null>(null)

  useEffect(() => {
    if (!eventId && eventsQ.data && eventsQ.data.events.length > 0) {
      setEventId(eventsQ.data.events[0].id)
    }
  }, [eventsQ.data, eventId])

  const selected = useMemo(
    () => eventsQ.data?.events.find((e) => e.id === eventId) ?? null,
    [eventsQ.data, eventId],
  )

  return (
    <div className="stack-lg">
      <header style={{ display: 'grid', gap: 8 }}>
        <span className="exhibit-num">OPERATIONS</span>
        <h1 className="display" style={{ margin: 0, fontSize: 'clamp(1.6rem, 4vw, 2.4rem)' }}>Organizer console</h1>
        {eventsQ.data && eventsQ.data.events.length > 0 && (
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <div className="field" style={{ width: 320, maxWidth: '100%' }}>
              <label className="field-label" htmlFor="event-picker">Active event</label>
              <div className="select-wrap">
                <select id="event-picker" className="select" value={eventId ?? ''} onChange={(e) => setEventId(e.target.value)}>
                  {eventsQ.data.events.map((e) => (
                    <option key={e.id} value={e.id}>{e.name} ({e.status})</option>
                  ))}
                </select>
              </div>
            </div>
            {selected && (
              <span className="muted small mono" style={{ alignSelf: 'flex-end' }}>
                {selected.id.slice(0, 8)} {'·'} <Link to="/admin/challenges">challenge tools</Link>
              </span>
            )}
          </div>
        )}
        {eventsQ.data && eventsQ.data.events.length === 0 && (
          <p className="muted small">No events exist yet. Create one below to unlock the rest of the console.</p>
        )}
      </header>

      <EventsSection selectedId={eventId} onCreated={() => undefined} />

      {eventId ? (
        <>
          <LiveOpsSection eventId={eventId} />
          <ReadinessSection eventId={eventId} />
          <ExportsSection eventId={eventId} />
        </>
      ) : eventsQ.data ? (
        <EmptyState title="Select or create an event" note="Live ops, readiness and exports operate on one event at a time." />
      ) : null}

      <AuditSection />
      <DisputesSection />
      <SystemHealthSection />
    </div>
  )
}
