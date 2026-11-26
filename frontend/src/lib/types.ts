// API payload shapes, verified against backend/app/routers/*.py.

export type Role = 'participant' | 'reviewer' | 'organizer' | 'admin'

export interface User {
  id: string
  email: string
  name: string
  role: Role
  status: string
}

export interface Team {
  id: string
  name: string
  join_code: string
  event_id: string
  slug: string
  event_status: string
}

export interface Me extends User {
  team: Team | null
  rules_accepted: boolean
  rules_version: number | null
}

export interface LoginResponse {
  access_token: string
  expires_at: string
  server_time: string
  user: User
}

export interface SessionInfo {
  id: string
  created_at: string
  last_seen_at: string
  device: string
}

export interface PublicEvent {
  slug: string
  name: string
  status: string
  starts_at: string | null
  ends_at: string | null
  timezone: string
  duration_minutes: number
  max_participants: number
  max_teams: number
  rules_version: number
  format: Record<string, unknown>
}

export interface PublicRules {
  version: number
  body_markdown: string
  frozen_at: string | null
  event_name: string
}

export interface TimeResponse {
  server_time_ms: number
  server_time: string
}

export type StageStatus =
  | 'draft'
  | 'sealed'
  | 'armed'
  | 'active'
  | 'paused'
  | 'ended'
  | 'evaluating'
  | 'released'
  | 'final'

export interface RoundState {
  id: string
  name: string
  kind: string
  status: StageStatus
  event_status: string
  starts_at: string | null
  ends_at_effective: string | null
  grace_seconds: number
  paused: boolean
}

export interface StateResponse {
  server_time: string
  round: RoundState | null
  rules_version?: number
  announcements_version: number
  leaderboard_version?: number
  leaderboard_visibility: string
}

export interface ConfigResponse {
  stage_id: string
  grace_seconds: number
  runner_enabled: boolean
  ai_assessment_enabled: boolean
  scoring_weights: Record<string, number>
  ctf_bonus_cap_points?: number | null
  limits: Record<string, unknown>
  schedule: { phase: string; duration_minutes: number }[]
}

export interface ChallengeFile {
  path: string
  lang: string
  size_bytes: number
  line_count: number
}

export interface Assignment {
  challenge_id: string
  slug: string
  title: string
  version: number
  instructions_markdown: string
  repo_url: string | null
  codespace_url: string | null
  stage_id: string
  stage_status: StageStatus
  files: ChallengeFile[]
}

export interface FileContent {
  path: string
  lang: string
  content: string
  line_count: number
  sha256: string
}

export interface SearchHit {
  path: string
  line_number: number
  text: string
}

export type SubmissionKind = 'bug_report' | 'ctf_flag'
export type Severity = 'low' | 'medium' | 'high' | 'critical'

export interface Receipt {
  submission_id: string
  receipt_code: string
  received_at: string
  status: string
  replay: boolean
  team_id: string | null
}

export interface SubmissionRow {
  id: string
  kind: SubmissionKind
  title: string
  file_path: string | null
  line_start: number | null
  line_end: number | null
  category: string | null
  receipt_code: string
  received_at: string
  flag_code: string | null
  status: string
  matched_bug_code?: string | null
  points_visible?: number | null
}

export interface SubmissionDetail extends SubmissionRow {
  payload: Record<string, unknown>
  released: boolean
  matched_bug_code: string | null
  points: number | null
}

export interface DraftState {
  payload: Record<string, unknown>
  version: number
  updated_at: string | null
}

export interface Announcement {
  id: string
  severity: 'info' | 'warning' | 'critical'
  audience: string
  body: string
  author: string
  created_at: string
}

export interface LeaderboardRow {
  rank: number
  team_id: string
  team_name: string
  score: number
  invalid_reports: number
  last_accepted_at: string | null
}

export interface LeaderboardSnapshot {
  stage_id: string
  event_id: string
  rows: LeaderboardRow[]
  generated_at: string
}

export interface LeaderboardResponse {
  visible: boolean
  message?: string
  version?: number
  snapshot?: LeaderboardSnapshot | null
  generated_at?: string
}

export interface ResultEntry {
  component: string
  kind: string
  points: number
  reason_code: string
  title: string | null
  receipt_code: string | null
  matched_bug_code: string | null
  received_at: string | null
}

export interface ResultsResponse {
  released: boolean
  message?: string
  total?: number
  by_component?: Record<string, number>
  penalties?: number
  entries?: ResultEntry[]
}

export interface ReviewQueueItem {
  review_item_id: string
  submission_id: string
  kind: SubmissionKind
  title: string
  file_path: string | null
  line_range: [number | null, number | null]
  category: string | null
  received_at: string
  priority: number
  status: 'open' | 'claimed' | 'decided'
  claimed_by_me: boolean
}

export interface ReviewDetail {
  review_item_id: string
  status: 'open' | 'claimed' | 'decided'
  submission: {
    id: string
    kind: SubmissionKind
    title: string
    payload: Record<string, unknown>
    file_path: string | null
    line_start: number | null
    line_end: number | null
    category: string | null
    received_at: string
    receipt_code: string
    flag_code: string | null
    status: string
  }
  file: { content: string; lang: string; line_count: number } | null
  team_total_reports: number
}

export interface AdminEvent {
  id: string
  slug: string
  name: string
  status: string
  starts_at: string | null
  ends_at: string | null
  timezone: string
  max_participants: number
  max_teams: number
  rules_version: number
  config_version: number
}

export interface LiveStage {
  id: string
  name: string
  status: StageStatus
  starts_at: string | null
  ends_at: string | null
  config: Record<string, unknown>
}

export interface LiveOps {
  teams: number
  participants_online_estimate: number
  submission_counts: Record<string, number>
  job_queue: { oldest_queued_age_s: number; dead_letters: number }
  review_backlog: number
  stages: LiveStage[]
  health: { database_app: string; database_gt: string; clock: string | null }
  kill_switches: Record<string, boolean>
}

export interface ReadinessItem {
  key: string
  label: string
  state: 'checked' | 'unchecked' | 'na'
  owner: string | null
  checked_at: string | null
}

export interface AuditEntry {
  seq: number
  ts: string
  actor_role: string
  action: string
  entity_type: string
  entity_id: string
  request_id: string
  details: Record<string, unknown> | string | null
}

export interface AuditVerifyResult {
  scope: string
  entries: number
  verified: number
  broken_at: string | null
  intact: boolean
}

export interface Dispute {
  id: string
  status: string
  statement: string
  created_at: string
  title: string
  receipt_code: string
  submission_id: string
}

export interface SystemHealth {
  databases: { app: string; gt: string }
  jobs: Record<string, number>
  oldest_queued_job_age_s: number
  profile: string
  environment: string
  runner_enabled: boolean
  ai_assessment_enabled: boolean
}

export interface ExportFile {
  filename: string
  content: string
}

export const CATEGORIES = [
  'logic',
  'input_validation',
  'authentication_authorization',
  'injection',
  'cryptography',
  'resource_handling',
  'concurrency',
  'error_handling',
  'configuration',
  'integration_api_contract',
  'data_handling',
  'other',
] as const

export const SEVERITIES: Severity[] = ['low', 'medium', 'high', 'critical']

export const DECISIONS = [
  'accept_full',
  'accept_partial',
  'duplicate',
  'reject_invalid',
  'unplanned_valid',
  'needs_second_review',
] as const

export const RUBRIC_KEYS = ['functionality', 'root_cause', 'code_quality', 'testing', 'evidence'] as const


export interface AdminTeam {
  id: string
  name: string
  status: string
  member_count: number
}

export interface AdminParticipant {
  id: string | null
  email: string
  name: string | null
  role: Role
  status: string
  team_id: string | null
  team_name: string | null
  activated: boolean
  created_at: string
  expires_at: string
}

export interface ConfigRevision {
  version: number
  config: Record<string, unknown>
  rule_set_hash: string
  reason: string
  created_at: string
  frozen_at: string | null
}

export interface ChallengeVersion {
  id: string
  slug: string
  title: string
  version: number
  status: string
  content_hash: string | null
  frozen_at: string | null
  published_at: string | null
  created_at: string
}
