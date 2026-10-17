export type Role = 'owner' | 'family' | 'energy_advisor' | 'guest'
export const ROLES: Role[] = ['owner', 'family', 'energy_advisor', 'guest']

export interface DeviceRef {
  device_id: string
  name: string
  kind: string
  room?: string | null
}

export interface ChangeEvent {
  event_id: string
  device_id: string
  property: string
  old_value: string | null
  new_value: string | null
  change_kind: string
  semantic_tag: string
  event_ts: string
  snapshot_run_id: string | null
  story_id?: string | null
}

export interface ChangelogRow {
  event_id: string
  device_id: string
  property: string
  old_value: string | null
  new_value: string | null
  change_kind: string
  semantic_tag: string
  snapshot_ts_before: string | null
  snapshot_ts_after: string
  snapshot_run_id: string | null
}

export interface Story {
  story_id: string
  window_start: string
  window_end: string
  device_ids: string[]
  event_count: number
  link_reasons: string[]
  linking_config_sha: string
  status: 'new' | 'needs_review' | 'published' | 'closed'
  category: string | null
  category_name: string | null
  confidence: number | null
  label_source: 'human' | 'model' | 'sim_reviewer' | null
  model_version: string | null
  title: string
  summary: string
  devices: DeviceRef[]
}

export interface Playbook {
  category: string
  name: string
  definition: string
  plain: string
  grid_analogue: string
  checks: string[]
  actions: string[]
}

export interface LabelRow {
  label_id: number
  category: string
  source: string
  confidence: number | null
  reviewer: string | null
  note: string | null
  model_version: string | null
  created_at: string
}

export interface ReviewInfo {
  reason: 'abstain' | 'audit' | 'feedback'
  suggested_category: string | null
  suggested_confidence: number | null
  queued_at: string
  resolved_at: string | null
  resolved_by: string | null
}

export interface StoryDetail extends Story {
  events: ChangeEvent[]
  enrichment: { title: string; summary: string; facts: Record<string, unknown>; enrichment_template_sha: string }
  playbook: Playbook | null
  labels: LabelRow[]
  cases: { case_id: number; title: string; status: string }[]
  review: ReviewInfo | null
}

export interface Device extends DeviceRef {
  circuit_id: string
  network_id: string
  rated_w: number
  active: boolean
  tags: string[]
  event_count: number
  story_count: number
  latest: {
    snapshot_ts: string
    state: string | null
    power_w: number | null
    energy_kwh_total: number | null
    firmware_version: string | null
    mode: string | null
    setpoint: number | null
    rssi_dbm: number | null
    error_code: string | null
  } | null
}

export interface Kpis {
  stories: number
  events: number
  by_status: Record<string, number>
  by_label_source: Record<string, number>
  by_category: Record<string, number>
  auto_label_coverage: number | null
  open_reviews: number
  abstained_open: number
  audit_agreement: number | null
  audited: number
}

export interface Meta {
  home: { id: string; timezone: string }
  role: Role
  can_label: boolean
  roles: { name: Role; description: string }[]
  categories: { id: string; name: string; plain: string; energy: boolean }[]
  feedback_reasons: string[]
  skills: Record<string, string>
}

export interface Health {
  ok: boolean
  version: string
  schema: string
  llm: string | null
  llm_available: boolean
}

export interface ChatAnswer {
  message_id: number
  session_id: string
  answer: string
  skill: string
  skill_sha: string
  llm_model_tag: string
  tool_calls: { name: string; arguments: Record<string, unknown>; ok: boolean; error: string | null; prefetch: boolean }[]
  cited_ids: string[]
  checker: { ok: boolean; errors: string[]; attempts: number }
  latency_ms: number
  tokens_in: number
  tokens_out: number
  citations: Record<string, string>
}

const ROLE_KEY = 'd2d.role'

export function getRole(): Role {
  const r = localStorage.getItem(ROLE_KEY)
  return ROLES.includes(r as Role) ? (r as Role) : 'owner'
}

export function setRole(role: Role) {
  localStorage.setItem(ROLE_KEY, role)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', 'X-D2D-Role': getRole(), ...init?.headers },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export const post = <T>(path: string, body: unknown) =>
  api<T>(path, { method: 'POST', body: JSON.stringify(body) })
