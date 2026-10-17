// Static demo (GitHub Pages): answers API calls from JSON files exported by
// scripts/export_demo.py. Read-only, no server, never contacts the private beta.
import { ApiError, type ChatAnswer, type Role } from './api'

type Pages = Record<string, unknown>

const base = import.meta.env.BASE_URL
const roleData = new Map<Role, Promise<Pages>>()
let copilot: Promise<Record<string, Omit<ChatAnswer, 'message_id' | 'session_id'>>> | null = null

function load<T>(file: string): Promise<T> {
  return fetch(`${base}demo/${file}`).then((r) => {
    if (!r.ok) throw new ApiError(r.status, `demo data ${file} missing`)
    return r.json() as Promise<T>
  })
}

const READ_ONLY =
  'This is the read-only static demo on GitHub Pages. Labels, cases and feedback work in the private beta.'

function filter(path: string, query: URLSearchParams, data: unknown): unknown {
  if (!Array.isArray(data)) return data
  const rows = data as Record<string, unknown>[]
  const match = (key: string, field = key) => {
    const v = query.get(key)
    return (r: Record<string, unknown>) =>
      !v || (Array.isArray(r[field]) ? (r[field] as unknown[]).includes(v) : r[field] === v)
  }
  if (path === '/stories') return rows.filter(match('status')).filter(match('category')).filter(match('device_id', 'device_ids'))
  if (path === '/events') return rows.filter(match('change_kind')).filter(match('device_id'))
  if (path === '/changelog') return rows.filter(match('device_id'))
  return rows
}

function normalize(q: string): string {
  return q.toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim()
}

export async function demoApi<T>(path: string, role: Role, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET'
  const [p, qs = ''] = path.split('?')
  if (p === '/auth/status') return { auth_enabled: false, users_configured: true, user: null } as T
  if (p === '/health') {
    return { ok: true, version: 'demo', schema: 'static', llm: 'recorded Copilot answers', llm_available: true } as T
  }
  if (method === 'POST' && p === '/chat') {
    copilot ??= load('copilot.json')
    const recorded = await copilot.catch(() => ({}) as Awaited<NonNullable<typeof copilot>>)
    const question = String(JSON.parse(String(init?.body ?? '{}')).question ?? '')
    const hit = Object.entries(recorded).find(([q]) => normalize(q) === normalize(question))
    await new Promise((resolve) => setTimeout(resolve, 600))
    if (hit) return { ...hit[1], message_id: 0, session_id: 'demo' } as T
    return {
      message_id: 0,
      session_id: 'demo',
      answer:
        'This static demo replays real, recorded Copilot answers for the suggested questions. ' +
        'Free-form questions need the live Copilot, which runs on a local LLM in the private beta.',
      skill: 'demo',
      skill_sha: '',
      llm_model_tag: 'static demo',
      tool_calls: [],
      cited_ids: [],
      checker: { ok: true, errors: [], attempts: 0 },
      latency_ms: 0,
      tokens_in: 0,
      tokens_out: 0,
      citations: {},
    } as T
  }
  if (method !== 'GET') throw new ApiError(403, READ_ONLY)
  if (p.startsWith('/chat/')) return [] as T
  if (!roleData.has(role)) roleData.set(role, load<Pages>(`${role}.json`))
  const pages = await roleData.get(role)!
  if (!(p in pages)) throw new ApiError(404, 'not found in this demo snapshot')
  return filter(p, new URLSearchParams(qs), pages[p]) as T
}
