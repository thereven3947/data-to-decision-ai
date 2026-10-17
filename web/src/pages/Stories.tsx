import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, getRole, type Meta, type Story } from '../api'
import { Empty, ErrorBox, Loading, StoryCard } from '../components/ui'

export default function Stories() {
  const [params, setParams] = useSearchParams()
  const status = params.get('status') ?? ''
  const category = params.get('category') ?? ''
  const meta = useQuery({ queryKey: ['meta', getRole()], queryFn: () => api<Meta>('/meta') })
  const qs = new URLSearchParams()
  if (status) qs.set('status', status)
  if (category) qs.set('category', category)
  const stories = useQuery({
    queryKey: ['stories', status, category],
    queryFn: () => api<Story[]>(`/stories?${qs}`),
  })
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next)
  }

  return (
    <section>
      <header className="page-head">
        <h1>Change stories</h1>
        <p className="muted">Linked change events (stage 4) with their label and a plain-language summary.</p>
      </header>
      <div className="filters">
        <select value={status} onChange={(e) => set('status', e.target.value)} aria-label="Status">
          <option value="">All statuses</option>
          <option value="published">Published</option>
          <option value="needs_review">Needs review</option>
          <option value="new">New</option>
          <option value="closed">Closed</option>
        </select>
        <select value={category} onChange={(e) => set('category', e.target.value)} aria-label="Category">
          <option value="">All categories</option>
          {meta.data?.categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <span className="muted">{stories.data?.length ?? 0} stories</span>
      </div>
      {stories.isLoading && <Loading />}
      {stories.isError && <ErrorBox error={stories.error} />}
      {stories.data?.length === 0 && <Empty>No stories match these filters.</Empty>}
      <div className="stack">
        {stories.data?.map((s) => <StoryCard key={s.story_id} story={s} />)}
      </div>
    </section>
  )
}
