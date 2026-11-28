// Typed boundary for the four-level event flow endpoints (PR #20 backend).
//
// Every participant action goes through here so endpoint paths, request
// bodies and response shapes live in exactly one place. Shapes mirror
// backend/app/routers/levels.py and backend/app/flow.py; see
// docs/implementation/PARTICIPANT_FRONTEND_EVENT_FLOW_PLAN.md section 2.

import { api, newIdempotencyKey } from './api'
import type { ChallengeFile, Severity } from './types'

/* ---------- enums and primitives ---------- */

export type LevelNode = 'explain' | 'exec' | 'done'

export type LevelStatus =
  | 'locked'
  | 'available'
  | 'in_progress'
  | 'submitted'
  | 'auto_submitted'
  | 'missed'

export interface NodePointer {
  level_no: number
  node: LevelNode
}

export interface GateWaitingOn {
  name: string
  status: string
}

export interface GateInfo {
  allowed: boolean
  reason: string
  waiting_on: GateWaitingOn[]
  stage_status: string | null
}

export const DONE_STATUSES: LevelStatus[] = ['submitted', 'auto_submitted']

export function isDoneStatus(status: LevelStatus | string | null | undefined): boolean {
  return status === 'submitted' || status === 'auto_submitted'
}

/* ---------- responses ---------- */

export interface LevelSummary {
  level_no: number
  name: string
  stage_id: string
  status: string
  starts_at: string | null
  ends_at: string | null
  grace_seconds: number
  max_points: number | null
  gate_allowed: boolean
  gate_reason: string
  participant_status: LevelStatus
}

export interface LevelsResponse {
  levels: LevelSummary[]
  pointer: NodePointer | null
}

export interface RepoOptionCard {
  option_key: string
  label: string
  description: string
}

export interface RepoOptionRef {
  option_key: string
  title: string
}

export interface LevelContentResponse {
  level_no: number
  stage_id: string
  stage_status: string
  gate: GateInfo
  explain_markdown: string
  instructions_markdown: string | null
  title: string
  starts_at: string | null
  ends_at_effective: string | null
  grace_seconds: number
  repo_option: RepoOptionRef | null
  pointer: NodePointer | null
  is_lead: boolean
  repo_options: RepoOptionCard[]
}

export interface WorkspaceLimits {
  max_span: number
  max_flags: number
  max_locations: number
}

export interface WorkspaceResponse {
  level_no: number
  stage_id: string
  editable: boolean
  repo_option: RepoOptionRef
  challenge_id: string
  files: ChallengeFile[]
  draft: { payload: Record<string, unknown>; version: number; updated_at: string | null }
  ends_at_effective: string | null
  grace_seconds: number
  limits: WorkspaceLimits
}

export interface DraftResponse {
  payload: Record<string, unknown>
  version: number
  updated_at: string | null
}

export interface DraftPutBody {
  payload: Record<string, unknown>
  version?: number | null
}

export interface FlagLocation {
  file_path: string
  line_start: number
  line_end: number
}

/** One structured bug report; L1 has one location, L2 has 1..5 ordered locations. */
export interface FlagDraft {
  title: string
  category: string
  description: string
  evidence: string | null
  claimed_severity: Severity | null
  locations: FlagLocation[]
}

export interface LevelSubmitBody {
  flags?: FlagDraft[]
  confirm_empty?: boolean
  artifact_id?: string | null
  compute_diff?: boolean
}

export interface LevelSubmitReceipt {
  submission_id: string
  receipt_code: string
  received_at: string
  status: string
  replay: boolean
  level_no: number
}

export interface ProgressLevel {
  level_no: number
  status: LevelStatus
  gate: GateInfo
}

export interface TeammateProgress {
  name: string
  levels: Record<string, string>
}

export interface ProgressResponse {
  pointer: NodePointer | null
  levels: ProgressLevel[]
  teammates: TeammateProgress[]
  repo_option: RepoOptionRef | null
}

export interface TeamRepoOption {
  option_key: string
  challenge_version_id: string
  locked_at: string
  lock_source: string
  idempotency_key: string
  locked_by_name: string
  title: string
  slug: string
}

export interface TeamMember {
  id: string
  name: string
  is_lead: boolean
  levels: Record<string, string>
  pointer: NodePointer | null
}

export interface TeamResponse {
  team_id: string
  team_name: string
  event_id: string
  is_lead: boolean
  repo_option: TeamRepoOption | null
  members: TeamMember[]
}

export interface RepoLockResult {
  option: string
  title: string
  locked_at: string
  locked_by: string
  replay: boolean
}

export type LevelEvaluationStatus = 'not_released' | 'not_submitted' | 'evaluating' | 'complete'

export interface TeamPointsLevel {
  level_no: number
  name: string
  components: Record<string, number>
  /** Legacy alias for points. */
  total: number
  points: number
  score: number
  max_points: number | null
  evaluation_status: LevelEvaluationStatus
}

export interface TeamPointsResponse {
  team_id: string
  levels: TeamPointsLevel[]
  /** Legacy alias for points. */
  total: number
  points: number
  released: boolean
  released_total: number | null
}

export interface RepoTreeResponse {
  level_no: number
  repo_option: string
  files: ChallengeFile[]
}

export interface RepoFileResponse {
  path: string
  lang: string
  content: string
  line_count: number
  sha256: string
  level_no: number
  editable: boolean
}

export interface UploadResult {
  artifact_id: string
  kind: 'zip' | 'diff' | 'patch'
  filename: string
  sha256: string
  size_bytes: number
  entry_count: number | null
  level_no: number
  status: string
}

/* ---------- endpoints ---------- */

export function fetchLevels(): Promise<LevelsResponse> {
  return api<LevelsResponse>('/v1/levels')
}

export function fetchGate(level: number): Promise<GateInfo> {
  return api<GateInfo>(`/v1/levels/${level}/gate`)
}

export function fetchLevelContent(level: number): Promise<LevelContentResponse> {
  return api<LevelContentResponse>(`/v1/levels/${level}/content`)
}

export function fetchWorkspace(level: number): Promise<WorkspaceResponse> {
  return api<WorkspaceResponse>(`/v1/levels/${level}/workspace`)
}

export function fetchLevelDraft(level: number): Promise<DraftResponse> {
  return api<DraftResponse>(`/v1/levels/${level}/draft`)
}

export function putLevelDraft(level: number, body: DraftPutBody): Promise<{ version: number }> {
  return api<{ version: number }>(`/v1/levels/${level}/draft`, { method: 'PUT', body })
}

export function fetchProgress(): Promise<ProgressResponse> {
  return api<ProgressResponse>('/v1/progress')
}

export function fetchTeam(): Promise<TeamResponse> {
  return api<TeamResponse>('/v1/team')
}

export function fetchTeamPoints(): Promise<TeamPointsResponse> {
  return api<TeamPointsResponse>('/v1/team/score')
}

export function lockRepo(option: string): Promise<RepoLockResult> {
  return lockRepoWithKey(option, newIdempotencyKey())
}

/**
 * Repo lock with a caller-held idempotency key so a lost response can be
 * retried with the SAME key (the server replays the existing choice instead
 * of surfacing a generic failure).
 */
export function lockRepoWithKey(option: string, key: string): Promise<RepoLockResult> {
  return api<RepoLockResult>('/v1/team/repo-lock', {
    method: 'POST',
    body: { option, confirm: true },
    idempotencyKey: key,
  })
}

export function fetchRepoTree(level?: number): Promise<RepoTreeResponse> {
  const suffix = level ? `?level=${level}` : ''
  return api<RepoTreeResponse>(`/v1/repo/tree${suffix}`)
}

export function fetchRepoFile(path: string, level?: number): Promise<RepoFileResponse> {
  const suffix = level ? `?level=${level}` : ''
  return api<RepoFileResponse>(`/v1/repo/files/${encodeFilePath(path)}${suffix}`)
}

export function uploadArtifact(level: number, file: File): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  return api<UploadResult>(`/v1/uploads?level=${level}`, { method: 'POST', body: form })
}

export function submitLevel(level: number, body: LevelSubmitBody): Promise<LevelSubmitReceipt> {
  return api<LevelSubmitReceipt>(`/v1/levels/${level}/submit`, {
    method: 'POST',
    body,
    idempotencyKey: newIdempotencyKey(),
  })
}

/** Retry a submit with a caller-held idempotency key (same payload = replay). */
export function submitLevelWithKey(level: number, body: LevelSubmitBody, key: string): Promise<LevelSubmitReceipt> {
  return api<LevelSubmitReceipt>(`/v1/levels/${level}/submit`, {
    method: 'POST',
    body,
    idempotencyKey: key,
  })
}

/** Repo file paths may contain safe special characters; segments are encoded. */
function encodeFilePath(path: string): string {
  return path
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')
}
