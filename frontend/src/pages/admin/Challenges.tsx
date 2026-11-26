import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiRequestError } from '../../lib/api'
import type { AdminEvent, Assignment, ChallengeVersion, LiveOps } from '../../lib/types'
import { fmtBytes, fmtDateTime } from '../../lib/format'
import {
  Button,
  Chip,
  EmptyState,
  ErrorBanner,
  Field,
  LoadingShell,
  Panel,
  KVRow,
} from '../../ui/components'
import { FileIcon } from '../../ui/icons'
import { StepUpGateHint, StepUpPanel } from '../../components/StepUp'

const KNOWN_KEY = 'bhctf.admin.challenge_ids'

function loadKnown(): string[] {
  try {
    const raw = localStorage.getItem(KNOWN_KEY)
    return raw ? (JSON.parse(raw) as string[]) : []
  } catch {
    return []
  }
}

function saveKnown(ids: string[]): void {
  try {
    localStorage.setItem(KNOWN_KEY, JSON.stringify(ids.slice(-20)))
  } catch {
    // storage unavailable: the form still works with a pasted id
  }
}

export default function AdminChallenges() {
  const queryClient = useQueryClient()
  const eventsQ = useQuery({
    queryKey: ['admin-events'],
    queryFn: () => api<{ events: AdminEvent[] }>('/v1/admin/events'),
  })
  const [eventId, setEventId] = useState<string | null>(null)
  useEffect(() => {
    if (!eventId && eventsQ.data && eventsQ.data.events.length > 0) setEventId(eventsQ.data.events[0].id)
  }, [eventsQ.data, eventId])

  const liveQ = useQuery({
    queryKey: ['admin-live', eventId],
    queryFn: () => api<LiveOps>(`/v1/admin/events/${eventId}/live`),
    enabled: !!eventId,
  })
  const assignedQ = useQuery({
    queryKey: ['admin-assignments'],
    queryFn: () => api<{ assignments: Assignment[] }>('/v1/assignments'),
    staleTime: 15_000,
  })
  const versionsQ = useQuery({
    queryKey: ['admin-challenges', eventId],
    queryFn: () => api<{ challenges: ChallengeVersion[] }>(`/v1/admin/events/${eventId}/challenges`),
    enabled: !!eventId,
  })

  const [stepUpUntil, setStepUpUntil] = useState<number | null>(null)
  const elevated = stepUpUntil !== null && stepUpUntil > Date.now()

  const [known, setKnown] = useState<string[]>(loadKnown)
  const [challengeId, setChallengeId] = useState('')
  const [form, setForm] = useState({ slug: '', title: '', instructions: '' })
  const [uploadState, setUploadState] = useState<string | null>(null)
  const [error, setError] = useState<ApiRequestError | null>(null)
  const [busy, setBusy] = useState(false)
  const [lastCreated, setLastCreated] = useState<string | null>(null)
  const [publishStage, setPublishStage] = useState('')
  const [testForm, setTestForm] = useState({ submissionId: '', passed: '', total: '' })
  const [testDone, setTestDone] = useState<string | null>(null)

  async function guard<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true)
    setError(null)
    try {
      return await fn()
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError(err)
      } else {
        setError(new ApiRequestError({ code: 'UNKNOWN', serverMessage: 'Operation failed.' }))
      }
      return undefined
    } finally {
      setBusy(false)
    }
  }

  async function createChallenge(e: React.FormEvent) {
    e.preventDefault()
    const res = await guard(() =>
      api<{ challenge_id: string }>(`/v1/admin/events/${eventId}/challenges`, {
        method: 'POST',
        body: {
          slug: form.slug.trim(),
          title: form.title.trim(),
          instructions_markdown: form.instructions.trim() || undefined,
        },
      }),
    )
    if (res) {
      const ids = [...known, res.challenge_id]
      setKnown(ids)
      saveKnown(ids)
      setChallengeId(res.challenge_id)
      setLastCreated(res.challenge_id)
      setForm({ slug: '', title: '', instructions: '' })
      void queryClient.invalidateQueries({ queryKey: ['admin-assignments'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-challenges', eventId] })
    }
  }

  async function uploadFile(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const formEl = e.currentTarget
    const fileInput = formEl.elements.namedItem('challenge-file') as HTMLInputElement | null
    if (!fileInput?.files?.[0]) return
    const fd = new FormData()
    fd.append('file', fileInput.files[0])
    const res = await guard(() =>
      api<{ ok: boolean; path: string; line_count: number }>(`/v1/admin/challenges/${challengeId}/files`, {
        method: 'POST',
        body: fd,
      }),
    )
    if (res) {
      setUploadState(`Stored ${res.path} (${res.line_count} lines, ${fmtBytes(fileInput.files[0].size)})`)
      formEl.reset()
      void queryClient.invalidateQueries({ queryKey: ['admin-assignments'] })
    }
  }

  async function freezeChallenge() {
    const res = await guard<{ ok: boolean; content_hash: string }>(() =>
      api<{ ok: boolean; content_hash: string }>(`/v1/admin/challenges/${challengeId}/freeze`, { method: 'POST' }),
    )
    if (res) setUploadState(`Frozen. Content hash ${res.content_hash.slice(0, 24)}...`)
  }

  async function publishChallenge(e: React.FormEvent) {
    e.preventDefault()
    await guard(() =>
      api<{ ok: boolean }>(`/v1/admin/challenges/${challengeId}/publish`, {
        method: 'POST',
        body: { stage_id: publishStage },
      }),
    )
    if (!error) void queryClient.invalidateQueries({ queryKey: ['admin-assignments'] })
  }

  async function recordTestResults(e: React.FormEvent) {
    e.preventDefault()
    const res = await guard<{ ok: boolean }>(() =>
      api<{ ok: boolean }>(`/v1/admin/challenges/${challengeId}/ground-truth/test-results`, {
        method: 'POST',
        body: {
          submission_id: testForm.submissionId.trim(),
          tests_passed: Number(testForm.passed),
          tests_total: Number(testForm.total),
        },
      }),
    )
    if (res) {
      setTestDone(`Recorded ${testForm.passed}/${testForm.total} for submission ${testForm.submissionId.slice(0, 8)}. The ledger application job is queued.`)
      setTestForm({ submissionId: '', passed: '', total: '' })
    }
  }

  const assignment = assignedQ.data?.assignments[0] ?? null
  const stages = liveQ.data?.stages ?? []

  return (
    <div className="stack-lg">
      <header style={{ display: 'grid', gap: 8 }}>
        <span className="exhibit-num">CHALLENGE TOOLS</span>
        <h1 className="display" style={{ margin: 0, fontSize: 'clamp(1.6rem, 4vw, 2.4rem)' }}>Challenge content</h1>
        <p className="muted small" style={{ maxWidth: '78ch' }}>
          Challenge versions are now listed from the event API instead of relying only on browser-local ids.
          Creating, uploading and freezing content requires a fresh identity confirmation (step-up); publishing
          requires a stage to attach the sealed version to.
        </p>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <div className="field" style={{ width: 320, maxWidth: '100%' }}>
            <label className="field-label" htmlFor="ch-event-picker">Event</label>
            <div className="select-wrap">
              <select id="ch-event-picker" className="select" value={eventId ?? ''} onChange={(e) => setEventId(e.target.value)}>
                {(eventsQ.data?.events ?? []).map((e) => (
                  <option key={e.id} value={e.id}>{e.name} ({e.status})</option>
                ))}
              </select>
            </div>
          </div>
          {assignedQ.data && (
            <Chip tone={assignment ? 'ok' : 'muted'} size="lg">
              {assignment ? `assigned: ${assignment.title} (${assignment.files.length} files)` : 'no published assignment yet'}
            </Chip>
          )}
        </div>
      </header>

      <StepUpPanel stepUpUntil={stepUpUntil} onStepUp={(t) => setStepUpUntil(t)} />

      {error && <ErrorBanner error={error} title="Challenge operation failed" />}
      {eventsQ.isError && <ErrorBanner error={eventsQ.error} onRetry={() => void eventsQ.refetch()} />}
      {eventsQ.isLoading && <LoadingShell label="Loading events" />}

      {eventId && (
        <Panel exhibit="EX-00" title="Server-side challenge registry">
          {versionsQ.isLoading && <LoadingShell label="Loading challenge versions" />}
          {versionsQ.isError && <ErrorBanner error={versionsQ.error} onRetry={() => void versionsQ.refetch()} />}
          {versionsQ.data?.challenges.length === 0 && <p className="muted small">No challenge versions exist for this event yet.</p>}
          <div className="ruled">
            {(versionsQ.data?.challenges ?? []).map((version) => (
              <div key={version.id} className="kv-row" style={{ alignItems: 'flex-start', gap: 10 }}>
                <div style={{ minWidth: 0 }}>
                  <strong>{version.title}</strong> <span className="muted small">v{version.version} · {version.slug}</span>
                  <div className="mono small muted" style={{ overflowWrap: 'anywhere' }}>{version.id}</div>
                  {version.content_hash && <div className="mono small muted">{version.content_hash}</div>}
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  <StatusChip value={version.status} />
                  <Button size="sm" onClick={() => setChallengeId(version.id)}>Select</Button>
                  <Link className="btn btn-ghost btn-sm" to={`/admin/ground-truth?challenge=${encodeURIComponent(version.id)}`}>Ground truth</Link>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {eventId && (
        <div className="grid-2">
          <div className="stack">
            <Panel exhibit="EX-01" title="Create challenge version" actions={!elevated ? <StepUpGateHint /> : undefined}>
              <form onSubmit={createChallenge} className="stack" style={{ gap: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
                  <Field label="Slug" htmlFor="ch-slug" required hint="lowercase, dashes">
                    <input id="ch-slug" className="input mono-input" required pattern="[a-z0-9-]+" minLength={3} value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} disabled={!elevated} />
                  </Field>
                  <Field label="Title" htmlFor="ch-title" required>
                    <input id="ch-title" className="input" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} disabled={!elevated} />
                  </Field>
                </div>
                <Field label="Instructions (markdown, stored as text)" htmlFor="ch-inst">
                  <textarea id="ch-inst" className="textarea" rows={4} value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} disabled={!elevated} />
                </Field>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <Button type="submit" variant="primary" disabled={busy || !elevated}>Create version</Button>
                  {lastCreated && <span className="mono small muted">created {lastCreated.slice(0, 13)}</span>}
                </div>
                {!elevated && <span className="muted small">Confirm your identity above to enable challenge creation.</span>}
              </form>
            </Panel>

            <Panel exhibit="EX-02" title="Upload source file (inert storage)" actions={!elevated ? <StepUpGateHint /> : undefined}>
              <form onSubmit={uploadFile} className="stack" style={{ gap: 12 }}>
                <Field label="Challenge version id" htmlFor="ch-id" required hint="Prefilled after creation; known ids kept in this browser.">
                  <input
                    id="ch-id"
                    className="input mono-input"
                    list="known-ch-ids"
                    value={challengeId}
                    onChange={(e) => setChallengeId(e.target.value)}
                    required
                    disabled={!elevated}
                    placeholder="uuid of the challenge version"
                  />
                  <datalist id="known-ch-ids">
                    {known.map((id) => (
                      <option key={id} value={id} />
                    ))}
                  </datalist>
                </Field>
                <Field label="Source file" htmlFor="ch-file" required hint="Max 512 KiB; path is taken from the filename. Content is stored as text, never executed.">
                  <input id="ch-file" type="file" name="challenge-file" required disabled={!elevated} />
                </Field>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                  <Button type="submit" variant="primary" disabled={busy || !elevated || !challengeId}>Upload file</Button>
                  <Button disabled={busy || !elevated || !challengeId} onClick={() => void freezeChallenge()} title="Seal the version: requires at least one file">
                    Freeze version
                  </Button>
                  {uploadState && <span className="small mono" style={{ color: 'var(--ok)' }} role="status">{uploadState}</span>}
                </div>
              </form>
            </Panel>

            <Panel exhibit="EX-03" title="Publish to a stage">
              <form onSubmit={publishChallenge} className="stack" style={{ gap: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <Field label="Challenge version id" htmlFor="pub-ch">
                    <input id="pub-ch" className="input mono-input" value={challengeId} onChange={(e) => setChallengeId(e.target.value)} required />
                  </Field>
                  <Field label="Stage" htmlFor="pub-stage" required hint="Stage ids come from live ops on the operations page.">
                    <div className="select-wrap">
                      <select id="pub-stage" className="select" value={publishStage} onChange={(e) => setPublishStage(e.target.value)} required>
                        <option value="">Select stage</option>
                        {stages.map((s) => (
                          <option key={s.id} value={s.id}>{s.name} ({s.status})</option>
                        ))}
                      </select>
                    </div>
                  </Field>
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Button type="submit" disabled={busy || !publishStage || !challengeId}>Publish and assign</Button>
                  <span className="muted small">Requires the version to be sealed (frozen). No step-up needed; this is an organizer action.</span>
                </div>
              </form>
              {stages.length === 0 && <p className="muted small" style={{ marginTop: 8 }}>No stages exist for this event yet; create one first (stages are created via the stage API).</p>}
            </Panel>
          </div>

          <div className="stack">
            <Panel exhibit="EX-04" title="Ground-truth test results" actions={!elevated ? <StepUpGateHint /> : undefined}>
              <form onSubmit={recordTestResults} className="stack" style={{ gap: 12 }}>
                <Field label="Submission id" htmlFor="gt-sub" required>
                  <input id="gt-sub" className="input mono-input" value={testForm.submissionId} onChange={(e) => setTestForm({ ...testForm, submissionId: e.target.value })} required disabled={!elevated} />
                </Field>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <Field label="Tests passed" htmlFor="gt-passed" required>
                    <input id="gt-passed" className="input mono-input" type="number" min={0} value={testForm.passed} onChange={(e) => setTestForm({ ...testForm, passed: e.target.value })} required disabled={!elevated} />
                  </Field>
                  <Field label="Tests total" htmlFor="gt-total" required>
                    <input id="gt-total" className="input mono-input" type="number" min={1} value={testForm.total} onChange={(e) => setTestForm({ ...testForm, total: e.target.value })} required disabled={!elevated} />
                  </Field>
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Button type="submit" variant="primary" disabled={busy || !elevated}>Record results</Button>
                  <span className="muted small">Enqueues the ledger application for that submission (ADR-019).</span>
                </div>
                {testDone && <div className="small" style={{ color: 'var(--ok)' }} role="status">{testDone}</div>}
              </form>
            </Panel>

            <Panel exhibit="EX-05" title="Currently assigned challenge (from /assignments)">
              {assignedQ.isLoading && <LoadingShell label="Loading assignment" />}
              {assignedQ.isError && <ErrorBanner error={assignedQ.error} title="Assignment lookup failed" />}
              {assignedQ.data && !assignment && (
                <EmptyState
                  title="No published assignment visible"
                  note="Assignments become visible once a stage is released and a challenge is published to it. Sealed or draft challenges do not appear here."
                />
              )}
              {assignment && (
                <div className="ruled">
                  <KVRow k="Challenge id" v={assignment.challenge_id} />
                  <KVRow k="Title" v={`${assignment.title} (v${assignment.version})`} />
                  <KVRow k="Stage" v={`${assignment.stage_status}`} />
                  {assignment.repo_url && <KVRow k="Repo" v={assignment.repo_url} />}
                  <div style={{ padding: '10px 0' }}>
                    <Button
                      size="sm"
                      onClick={() => {
                        setChallengeId(assignment.challenge_id)
                        const ids = known.includes(assignment.challenge_id) ? known : [...known, assignment.challenge_id]
                        setKnown(ids)
                        saveKnown(ids)
                      }}
                    >
                      Use this id below
                    </Button>
                  </div>
                  <div className="ruled" style={{ borderTop: 0 }}>
                    {assignment.files.map((f) => (
                      <div key={f.path} className="kv-row" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <FileIcon size={13} />
                        <span className="mono small" style={{ flex: 1, overflowWrap: 'anywhere' }}>{f.path}</span>
                        <span className="muted small mono">{f.line_count} lines {'·'} {fmtBytes(f.size_bytes)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Panel>

            <Panel exhibit="EX-06" title="Known challenge ids (this browser)">
              {known.length === 0 && (
                <EmptyState title="No ids recorded" note="Create a version above or paste an id into the upload form. This list lives in localStorage, not in the API." />
              )}
              {known.length > 0 && (
                <div className="ruled">
                  {known.slice().reverse().map((id) => (
                    <div key={id} className="kv-row" style={{ alignItems: 'center' }}>
                      <span className="mono small" style={{ overflowWrap: 'anywhere' }}>{id}</span>
                      <span style={{ display: 'flex', gap: 6 }}>
                        <Button size="sm" onClick={() => setChallengeId(id)}>Use</Button>
                        <ConfirmRemove id={id} onRemoved={(rest) => { setKnown(rest); saveKnown(rest) }} />
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {lastCreated && <p className="muted small mono" style={{ marginTop: 8 }}>created {fmtDateTime(new Date().toISOString())}: {lastCreated}</p>}
            </Panel>
          </div>
        </div>
      )}
    </div>
  )
}

function ConfirmRemove({ id, onRemoved }: { id: string; onRemoved: (rest: string[]) => void }) {
  const [armed, setArmed] = useState(false)
  if (!armed) {
    return <Button size="sm" onClick={() => setArmed(true)} title="Remove from this browser list">Forget</Button>
  }
  return (
    <span style={{ display: 'inline-flex', gap: 6 }}>
      <Button size="sm" variant="danger" onClick={() => { setArmed(false); onRemoved(loadKnown().filter((x) => x !== id)) }}>
        Confirm
      </Button>
      <Button size="sm" onClick={() => setArmed(false)}>Cancel</Button>
    </span>
  )
}
