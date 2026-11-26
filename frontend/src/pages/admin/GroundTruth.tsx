import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import { api, ApiRequestError } from '../../lib/api'
import type { AdminEvent, ChallengeVersion } from '../../lib/types'
import { CATEGORIES, SEVERITIES } from '../../lib/types'
import { Button, Chip, EmptyState, ErrorBanner, Field, LoadingShell, Panel, StatusChip } from '../../ui/components'
import { adminErrorText, StepUpGateHint, StepUpPanel } from '../../components/StepUp'

const emptyBug = {
  code: '',
  title: '',
  severity: 'medium',
  category: 'logic',
  points: '50',
  root_cause_group: '',
  explanation: '',
  fix_guidance: '',
  file_path: '',
  line_start: '',
  line_end: '',
}

export default function AdminGroundTruth() {
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  const eventsQ = useQuery({
    queryKey: ['admin-events'],
    queryFn: () => api<{ events: AdminEvent[] }>('/v1/admin/events'),
  })
  const [eventId, setEventId] = useState('')
  const [challengeId, setChallengeId] = useState(params.get('challenge') ?? '')
  const challengesQ = useQuery({
    queryKey: ['admin-challenges', eventId],
    queryFn: () => api<{ challenges: ChallengeVersion[] }>(`/v1/admin/events/${eventId}/challenges`),
    enabled: !!eventId,
  })
  const [stepUpUntil, setStepUpUntil] = useState<number | null>(null)
  const elevated = stepUpUntil !== null && stepUpUntil > Date.now()
  const [bug, setBug] = useState(emptyBug)
  const [flag, setFlag] = useState({ flag_key: '', code: '', points: '25', max_awards: '99' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    if (!eventId && eventsQ.data?.events.length) setEventId(eventsQ.data.events[0].id)
  }, [eventsQ.data, eventId])

  useEffect(() => {
    if (!eventId || challengesQ.isLoading) return
    const rows = challengesQ.data?.challenges ?? []
    if (challengeId && rows.some((row) => row.id === challengeId)) return
    if (rows.length) setChallengeId(rows[0].id)
    else setChallengeId('')
  }, [challengesQ.data, challengeId])

  function resetMessages() {
    setError(null)
    setNotice(null)
  }

  function fail(err: unknown) {
    setError(err instanceof ApiRequestError ? adminErrorText(err) : err instanceof Error ? err.message : 'Operation failed.')
  }

  async function createBug(e: React.FormEvent) {
    e.preventDefault()
    if (!challengeId) return
    setBusy(true)
    resetMessages()
    try {
      const hasPath = bug.file_path.trim().length > 0
      if (hasPath && (!bug.line_start || !bug.line_end || Number(bug.line_start) < 1 || Number(bug.line_end) < Number(bug.line_start))) {
        throw new Error('Provide a valid file path and line range, or leave all three location fields empty.')
      }
      await api(`/v1/admin/challenges/${challengeId}/ground-truth/bugs`, {
        method: 'POST',
        body: {
          code: bug.code.trim(),
          title: bug.title.trim(),
          severity: bug.severity,
          category: bug.category,
          points: Number(bug.points),
          root_cause_group: bug.root_cause_group.trim(),
          explanation: bug.explanation.trim(),
          fix_guidance: bug.fix_guidance.trim() || null,
          locations: hasPath ? [{
            file_path: bug.file_path.trim(),
            line_start: Number(bug.line_start),
            line_end: Number(bug.line_end),
            is_primary: true,
          }] : [],
        },
      })
      setBug(emptyBug)
      setNotice('Ground-truth bug added. The record is stored in the isolated ground-truth database.')
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  async function createFlag(e: React.FormEvent) {
    e.preventDefault()
    if (!challengeId) return
    setBusy(true)
    resetMessages()
    try {
      await api(`/v1/admin/challenges/${challengeId}/ground-truth/flags`, {
        method: 'POST',
        body: {
          flag_key: flag.flag_key.trim(),
          code: flag.code.trim(),
          points: Number(flag.points),
          max_awards: Number(flag.max_awards),
        },
      })
      setFlag({ flag_key: '', code: '', points: '25', max_awards: '99' })
      setNotice('Ground-truth flag added. The raw flag value is not returned by the API.')
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  const challenges = challengesQ.data?.challenges ?? []

  return (
    <div className="stack-lg">
      <header className="stack">
        <span className="exhibit-num">CHALLENGE EVALUATION</span>
        <h1 className="display" style={{ margin: 0, fontSize: 'clamp(1.6rem, 4vw, 2.4rem)' }}>Ground-truth authoring</h1>
        <p className="muted small" style={{ maxWidth: '78ch' }}>
          Add expected bug matches and hashed CTF flags to a draft or reviewed challenge version. The server enforces
          step-up confirmation and writes these records through the isolated ground-truth database role.
        </p>
      </header>

      {eventsQ.isLoading && <LoadingShell label="Loading events" />}
      {eventsQ.isError && <ErrorBanner error={eventsQ.error} onRetry={() => void eventsQ.refetch()} />}
      <div className="grid-2">
        <Field label="Event" htmlFor="gt-event" required>
          <select id="gt-event" className="select" value={eventId} onChange={(e) => { setEventId(e.target.value); setChallengeId('') }}>
            <option value="">Select event</option>
            {(eventsQ.data?.events ?? []).map((event) => <option key={event.id} value={event.id}>{event.name}</option>)}
          </select>
        </Field>
        <Field label="Challenge version" htmlFor="gt-challenge" required hint="Only safe challenge metadata is listed here. Ground-truth details are never fetched into the browser.">
          <select id="gt-challenge" className="select" value={challengeId} onChange={(e) => setChallengeId(e.target.value)} disabled={!eventId || challenges.length === 0}>
            <option value="">Select challenge version</option>
            {challenges.map((challenge) => (
              <option key={challenge.id} value={challenge.id}>
                {challenge.title} · v{challenge.version} · {challenge.status}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {challengesQ.isLoading && <LoadingShell label="Loading challenge versions" />}
      {challengesQ.isError && <ErrorBanner error={challengesQ.error} onRetry={() => void challengesQ.refetch()} />}
      {challengesQ.data && challenges.length === 0 && <EmptyState title="No challenge versions" note="Create a challenge version in Challenge Tools before authoring ground truth." />}
      {challengeId && (
        <div className="small muted mono">
          selected challenge id: {challengeId}
          {challenges.find((challenge) => challenge.id === challengeId) && (
            <> · <StatusChip value={challenges.find((challenge) => challenge.id === challengeId)?.status} /></>
          )}
        </div>
      )}

      <StepUpPanel stepUpUntil={stepUpUntil} onStepUp={setStepUpUntil} note="Adding ground-truth bugs and flags changes scoring data. Reconfirm your password to unlock these controls for five minutes." />
      {error && <ErrorBanner error={new ApiRequestError({ code: 'GROUND_TRUTH', serverMessage: error })} title="Ground-truth operation failed" />}
      {notice && <div className="banner banner-tone-ok" role="status">{notice}</div>}

      <div className="grid-2">
        <Panel exhibit="01" title="Add ground-truth bug" actions={!elevated ? <StepUpGateHint /> : undefined}>
          <form onSubmit={createBug} className="stack" style={{ gap: 12 }}>
            <div className="grid-2">
              <Field label="Bug code" htmlFor="bug-code" required>
                <input id="bug-code" className="input mono-input" maxLength={40} required value={bug.code} onChange={(e) => setBug({ ...bug, code: e.target.value })} disabled={!elevated} />
              </Field>
              <Field label="Points" htmlFor="bug-points" required>
                <input id="bug-points" className="input mono-input" type="number" min={0.01} max={1000} step="0.01" required value={bug.points} onChange={(e) => setBug({ ...bug, points: e.target.value })} disabled={!elevated} />
              </Field>
            </div>
            <Field label="Title" htmlFor="bug-title" required>
              <input id="bug-title" className="input" required value={bug.title} onChange={(e) => setBug({ ...bug, title: e.target.value })} disabled={!elevated} />
            </Field>
            <div className="grid-2">
              <Field label="Severity" htmlFor="bug-severity" required>
                <select id="bug-severity" className="select" value={bug.severity} onChange={(e) => setBug({ ...bug, severity: e.target.value })} disabled={!elevated}>
                  {SEVERITIES.map((severity) => <option key={severity}>{severity}</option>)}
                </select>
              </Field>
              <Field label="Category" htmlFor="bug-category" required>
                <select id="bug-category" className="select" value={bug.category} onChange={(e) => setBug({ ...bug, category: e.target.value })} disabled={!elevated}>
                  {CATEGORIES.map((category) => <option key={category}>{category}</option>)}
                </select>
              </Field>
            </div>
            <Field label="Root-cause group" htmlFor="bug-root" required>
              <input id="bug-root" className="input" required value={bug.root_cause_group} onChange={(e) => setBug({ ...bug, root_cause_group: e.target.value })} disabled={!elevated} />
            </Field>
            <Field label="Expected root cause / explanation" htmlFor="bug-explanation" required>
              <textarea id="bug-explanation" className="textarea" rows={4} required value={bug.explanation} onChange={(e) => setBug({ ...bug, explanation: e.target.value })} disabled={!elevated} />
            </Field>
            <Field label="Fix guidance" htmlFor="bug-guidance">
              <textarea id="bug-guidance" className="textarea" rows={3} value={bug.fix_guidance} onChange={(e) => setBug({ ...bug, fix_guidance: e.target.value })} disabled={!elevated} />
            </Field>
            <div className="overline">Primary source location (optional)</div>
            <Field label="File path" htmlFor="bug-file">
              <input id="bug-file" className="input mono-input" value={bug.file_path} onChange={(e) => setBug({ ...bug, file_path: e.target.value })} disabled={!elevated} placeholder="backend/app/service.py" />
            </Field>
            <div className="grid-2">
              <Field label="Line start" htmlFor="bug-line-start">
                <input id="bug-line-start" className="input mono-input" type="number" min={1} value={bug.line_start} onChange={(e) => setBug({ ...bug, line_start: e.target.value })} disabled={!elevated} />
              </Field>
              <Field label="Line end" htmlFor="bug-line-end">
                <input id="bug-line-end" className="input mono-input" type="number" min={1} value={bug.line_end} onChange={(e) => setBug({ ...bug, line_end: e.target.value })} disabled={!elevated} />
              </Field>
            </div>
            <Button type="submit" variant="primary" disabled={busy || !elevated || !challengeId}>Add bug</Button>
          </form>
        </Panel>

        <Panel exhibit="02" title="Add CTF flag" actions={!elevated ? <StepUpGateHint /> : undefined}>
          <form onSubmit={createFlag} className="stack" style={{ gap: 12 }}>
            <Field label="Flag key" htmlFor="flag-key" required hint="Stable award-unit identifier, unique within the challenge.">
              <input id="flag-key" className="input mono-input" maxLength={40} required value={flag.flag_key} onChange={(e) => setFlag({ ...flag, flag_key: e.target.value })} disabled={!elevated} />
            </Field>
            <Field label="Flag code" htmlFor="flag-code" required hint="The raw code is sent once to the API and stored as a hash.">
              <input id="flag-code" className="input mono-input" minLength={4} maxLength={200} required value={flag.code} onChange={(e) => setFlag({ ...flag, code: e.target.value })} disabled={!elevated} autoComplete="off" />
            </Field>
            <div className="grid-2">
              <Field label="Points" htmlFor="flag-points" required>
                <input id="flag-points" className="input mono-input" type="number" min={0.01} max={500} step="0.01" required value={flag.points} onChange={(e) => setFlag({ ...flag, points: e.target.value })} disabled={!elevated} />
              </Field>
              <Field label="Maximum awards" htmlFor="flag-awards" required>
                <input id="flag-awards" className="input mono-input" type="number" min={1} max={10000} required value={flag.max_awards} onChange={(e) => setFlag({ ...flag, max_awards: e.target.value })} disabled={!elevated} />
              </Field>
            </div>
            <Button type="submit" variant="primary" disabled={busy || !elevated || !challengeId}>Add flag</Button>
          </form>
          <div className="banner banner-tone-warn" style={{ marginTop: 14 }}>
            <div className="banner-title">Treat flag codes as secrets</div>
            <div className="small">The API stores a hash, but the code will still pass through this browser during creation. Do not paste it into notes, screenshots or shared logs.</div>
          </div>
        </Panel>
      </div>
    </div>
  )
}
