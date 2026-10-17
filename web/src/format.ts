const formatters = new Map<string, Intl.DateTimeFormat>()

/** Timestamp in the home's time zone, e.g. "Thu, Oct 1, 03:00". */
export function formatTs(iso: string | null | undefined, timeZone = 'America/Los_Angeles'): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  let f = formatters.get(timeZone)
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone,
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
    formatters.set(timeZone, f)
  }
  return f.format(d)
}

export function humanize(id: string | null | undefined): string {
  if (!id) return '—'
  const s = id.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function pct(x: number | null | undefined): string {
  return x == null ? '—' : `${Math.round(x * 100)}%`
}

export type Segment = { type: 'text'; text: string } | { type: 'story'; id: string }

/** Split an agent answer into text and [story:<id>] citation segments. */
export function splitCitations(text: string): Segment[] {
  const out: Segment[] = []
  const re = /\[story:([0-9a-f]{16})\]/g
  let last = 0
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push({ type: 'text', text: text.slice(last, m.index) })
    out.push({ type: 'story', id: m[1] })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ type: 'text', text: text.slice(last) })
  return out
}

/** Replace [story:<id>] citations with Markdown links to the story page. */
export function linkCitations(text: string, titles: Record<string, string> = {}): string {
  return splitCitations(text)
    .map((s) =>
      s.type === 'text' ? s.text : `[${titles[s.id] ? `↗ ${titles[s.id]}` : `story ${s.id.slice(0, 6)}`}](/stories/${s.id})`,
    )
    .join('')
}
