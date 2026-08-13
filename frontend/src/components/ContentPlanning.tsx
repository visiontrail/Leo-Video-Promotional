import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  approveContentPlanItem,
  createContentPlanItem,
  createContentSeries,
  deleteContentPlanItem,
  fetchContentPlanItems,
  fetchContentPlanningStatus,
  fetchContentSeries,
  recordContentPlanPublication,
  updateContentPlanItem,
  updateContentSeries,
} from '../api'
import type { ContentPlanInput, ContentPlanItem } from '../api'
import { localInputToIso, toLocalInputValue } from '../schedule'

const PLAN_LABELS: Record<string, string> = {
  draft: 'Draft',
  scheduled: 'Scheduled',
  generating: 'Generating',
  review: 'Needs review',
  ready: 'Approved',
  published: 'Published',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

function nextDate(hours: number): string {
  const date = new Date(Date.now() + hours * 60 * 60 * 1000)
  date.setMinutes(0, 0, 0)
  return toLocalInputValue(date)
}

function formatDate(value: string | null): string {
  if (!value) return 'Not scheduled'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function shortDate(value: string | null): { day: string; month: string } {
  if (!value) return { day: '—', month: 'Draft' }
  const date = new Date(value)
  return {
    day: new Intl.DateTimeFormat(undefined, { day: '2-digit' }).format(date),
    month: new Intl.DateTimeFormat(undefined, { month: 'short' }).format(date).toUpperCase(),
  }
}

type PlanDraft = {
  seriesId: string
  title: string
  brief: string
  episode: string
  generationAt: string
  publishAt: string
  platform: string
}

const EMPTY_PLAN = (): PlanDraft => ({
  seriesId: '',
  title: '',
  brief: '',
  episode: '',
  generationAt: nextDate(24),
  publishAt: nextDate(48),
  platform: 'YouTube',
})

export default function ContentPlanning() {
  const queryClient = useQueryClient()
  const [seriesFormOpen, setSeriesFormOpen] = useState(false)
  const [planFormOpen, setPlanFormOpen] = useState(false)
  const [activeSeries, setActiveSeries] = useState<string>('all')
  const [seriesDraft, setSeriesDraft] = useState({ name: '', theme: '', description: '' })
  const [planDraft, setPlanDraft] = useState<PlanDraft>(EMPTY_PLAN)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [publicationUrls, setPublicationUrls] = useState<Record<string, string>>({})

  const { data: planningStatus } = useQuery({
    queryKey: ['content-planning-status'],
    queryFn: fetchContentPlanningStatus,
  })
  const { data: series = [], isLoading: seriesLoading } = useQuery({
    queryKey: ['content-series'],
    queryFn: fetchContentSeries,
  })
  const { data: items = [], isLoading: itemsLoading } = useQuery({
    queryKey: ['content-plan-items'],
    queryFn: fetchContentPlanItems,
    refetchInterval: 5000,
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['content-series'] })
    queryClient.invalidateQueries({ queryKey: ['content-plan-items'] })
    queryClient.invalidateQueries({ queryKey: ['tasks'] })
  }

  const seriesMutation = useMutation({
    mutationFn: createContentSeries,
    onSuccess: (created) => {
      setSeriesDraft({ name: '', theme: '', description: '' })
      setSeriesFormOpen(false)
      setActiveSeries(created.id)
      setPlanDraft((draft) => ({ ...draft, seriesId: created.id }))
      refresh()
    },
  })

  const planMutation = useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: ContentPlanInput }) => (
      id ? updateContentPlanItem(id, input) : createContentPlanItem(input)
    ),
    onSuccess: () => {
      setPlanDraft(EMPTY_PLAN())
      setEditingId(null)
      setPlanFormOpen(false)
      refresh()
    },
  })

  const deleteMutation = useMutation({ mutationFn: deleteContentPlanItem, onSuccess: refresh })
  const approveMutation = useMutation({ mutationFn: approveContentPlanItem, onSuccess: refresh })
  const publishMutation = useMutation({
    mutationFn: ({ id, url }: { id: string; url: string | null }) => recordContentPlanPublication(id, url),
    onSuccess: (_, variables) => {
      setPublicationUrls((current) => ({ ...current, [variables.id]: '' }))
      refresh()
    },
  })
  const archiveSeriesMutation = useMutation({
    mutationFn: (id: string) => updateContentSeries(id, { archived: true }),
    onSuccess: () => {
      setActiveSeries('all')
      refresh()
    },
  })

  const visibleItems = useMemo(
    () => activeSeries === 'all' ? items : items.filter((item) => item.series_id === activeSeries),
    [activeSeries, items],
  )

  const counts = useMemo(() => ({
    planned: items.filter((item) => ['draft', 'scheduled'].includes(item.status)).length,
    production: items.filter((item) => item.status === 'generating').length,
    review: items.filter((item) => item.status === 'review').length,
    published: items.filter((item) => item.status === 'published').length,
  }), [items])

  const submitSeries = (event: FormEvent) => {
    event.preventDefault()
    seriesMutation.mutate(seriesDraft)
  }

  const submitPlan = (event: FormEvent) => {
    event.preventDefault()
    const input: ContentPlanInput = {
      series_id: planDraft.seriesId || null,
      title: planDraft.title,
      brief: planDraft.brief,
      episode_number: planDraft.episode ? Number(planDraft.episode) : null,
      generation_at: localInputToIso(planDraft.generationAt),
      publish_at: localInputToIso(planDraft.publishAt),
      platform: planDraft.platform || 'manual',
      auto_publish_requested: false,
    }
    planMutation.mutate({ id: editingId, input })
  }

  const editPlan = (item: ContentPlanItem) => {
    setEditingId(item.id)
    setPlanDraft({
      seriesId: item.series_id || '',
      title: item.title,
      brief: item.brief,
      episode: item.episode_number ? String(item.episode_number) : '',
      generationAt: item.generation_at ? toLocalInputValue(new Date(item.generation_at)) : '',
      publishAt: item.publish_at ? toLocalInputValue(new Date(item.publish_at)) : '',
      platform: item.platform,
    })
    setPlanFormOpen(true)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <section className="planning-workspace">
      <header className="planning-command">
        <div className="planning-title-block">
          <span className="eyebrow">Editorial runway · {new Date().getFullYear()}</span>
          <h1>Content Plan</h1>
          <p>Shape the series. Set the clock. Keep publication human.</p>
        </div>
        <div className="planning-actions">
          <button className="btn-ghost" type="button" onClick={() => setSeriesFormOpen((open) => !open)}>
            {seriesFormOpen ? 'Close series form' : 'New series'}
          </button>
          <button className="btn-primary" type="button" onClick={() => {
            setEditingId(null)
            setPlanDraft({ ...EMPTY_PLAN(), seriesId: activeSeries === 'all' ? '' : activeSeries })
            setPlanFormOpen((open) => !open)
          }}>
            {planFormOpen && !editingId ? 'Close plan form' : 'Plan a video'}
          </button>
        </div>
      </header>

      <div className="planning-ledger" aria-label="Plan summary">
        <div><span>Planned</span><strong>{counts.planned}</strong><small>ideas on the runway</small></div>
        <div><span>In production</span><strong>{counts.production}</strong><small>tasks currently running</small></div>
        <div><span>Needs review</span><strong>{counts.review}</strong><small>finished, never auto-posted</small></div>
        <div><span>Published</span><strong>{counts.published}</strong><small>manually confirmed</small></div>
      </div>

      {(seriesFormOpen || planFormOpen) && (
        <div className="planning-form-deck">
          {seriesFormOpen && (
            <form className="planning-form series-form" onSubmit={submitSeries}>
              <div className="planning-form-heading">
                <span className="form-index">01</span>
                <div><h2>Define a series</h2><p>A durable editorial lens, not a one-off title.</p></div>
              </div>
              <div className="planning-field-grid">
                <div className="form-group">
                  <label htmlFor="series-name">Series name</label>
                  <input id="series-name" required maxLength={120} placeholder="Borderlands" value={seriesDraft.name} onChange={(e) => setSeriesDraft({ ...seriesDraft, name: e.target.value })} />
                </div>
                <div className="form-group">
                  <label htmlFor="series-theme">Theme / territory</label>
                  <input id="series-theme" maxLength={120} placeholder="Countries, borders, identity" value={seriesDraft.theme} onChange={(e) => setSeriesDraft({ ...seriesDraft, theme: e.target.value })} />
                </div>
                <div className="form-group planning-span-2">
                  <label htmlFor="series-description">Editorial promise</label>
                  <textarea id="series-description" rows={3} placeholder="What question will every episode pursue?" value={seriesDraft.description} onChange={(e) => setSeriesDraft({ ...seriesDraft, description: e.target.value })} />
                </div>
              </div>
              <div className="planning-form-footer">
                {seriesMutation.isError && <span className="planning-form-error">{(seriesMutation.error as Error).message}</span>}
                <button className="btn-primary" disabled={seriesMutation.isPending}>{seriesMutation.isPending ? 'Creating…' : 'Create series'}</button>
              </div>
            </form>
          )}

          {planFormOpen && (
            <form className="planning-form plan-form" onSubmit={submitPlan}>
              <div className="planning-form-heading">
                <span className="form-index">{editingId ? 'REV' : '02'}</span>
                <div><h2>{editingId ? 'Revise the plan' : 'Schedule a video'}</h2><p>The generation time creates and controls a real pipeline task.</p></div>
              </div>
              <div className="planning-field-grid planning-field-grid--three">
                <div className="form-group">
                  <label htmlFor="plan-series">Series</label>
                  <select id="plan-series" value={planDraft.seriesId} onChange={(e) => setPlanDraft({ ...planDraft, seriesId: e.target.value })}>
                    <option value="">Standalone video</option>
                    {series.filter((entry) => !entry.archived).map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}
                  </select>
                </div>
                <div className="form-group planning-span-2">
                  <label htmlFor="plan-title">Working title</label>
                  <input id="plan-title" required maxLength={180} placeholder="The country that moved its capital overnight" value={planDraft.title} onChange={(e) => setPlanDraft({ ...planDraft, title: e.target.value })} />
                </div>
                <div className="form-group planning-span-3">
                  <label htmlFor="plan-brief">Editorial brief</label>
                  <textarea id="plan-brief" required minLength={10} rows={4} placeholder="State the angle, essential facts, audience promise, and questions the script must answer." value={planDraft.brief} onChange={(e) => setPlanDraft({ ...planDraft, brief: e.target.value })} />
                </div>
                <div className="form-group">
                  <label htmlFor="plan-episode">Episode number</label>
                  <input id="plan-episode" type="number" min="1" placeholder="1" value={planDraft.episode} onChange={(e) => setPlanDraft({ ...planDraft, episode: e.target.value })} />
                </div>
                <div className="form-group">
                  <label htmlFor="plan-generation">Begin generation</label>
                  <input id="plan-generation" type="datetime-local" value={planDraft.generationAt} onInput={(e) => setPlanDraft({ ...planDraft, generationAt: e.currentTarget.value })} />
                  <small>Creates a linked task and releases it at this exact time.</small>
                </div>
                <div className="form-group">
                  <label htmlFor="plan-publish">Target publication</label>
                  <input id="plan-publish" type="datetime-local" value={planDraft.publishAt} min={planDraft.generationAt || undefined} onInput={(e) => setPlanDraft({ ...planDraft, publishAt: e.currentTarget.value })} />
                  <small>A deadline only; it does not post automatically.</small>
                </div>
                <div className="form-group planning-span-3">
                  <label htmlFor="plan-platform">Publication destination</label>
                  <input id="plan-platform" maxLength={80} placeholder="YouTube" value={planDraft.platform} onChange={(e) => setPlanDraft({ ...planDraft, platform: e.target.value })} />
                </div>
              </div>
              <div className="planning-safety-note">
                <span className="safety-lock" aria-hidden="true">×</span>
                <div><strong>Automatic publication is locked</strong><p>Every finished video must be reviewed, approved, and manually published. The global pipeline switch is {planningStatus?.auto_publish_enabled ? 'enabled, but this plan remains opted out' : 'off'}.</p></div>
              </div>
              <div className="planning-form-footer">
                {planMutation.isError && <span className="planning-form-error">{(planMutation.error as Error).message}</span>}
                {editingId && <button className="btn-ghost" type="button" onClick={() => { setEditingId(null); setPlanFormOpen(false) }}>Cancel</button>}
                <button className="btn-primary" disabled={planMutation.isPending}>{planMutation.isPending ? 'Saving…' : editingId ? 'Save changes' : 'Commit to calendar'}</button>
              </div>
            </form>
          )}
        </div>
      )}

      <div className="planning-layout">
        <aside className="series-rail">
          <div className="series-rail-head"><span>Series index</span><small>{series.length} collections</small></div>
          <button type="button" className={`series-card series-card--all${activeSeries === 'all' ? ' is-active' : ''}`} onClick={() => setActiveSeries('all')}>
            <span className="series-monogram">ALL</span><span><strong>Master calendar</strong><small>{items.length} total videos</small></span>
          </button>
          {seriesLoading ? <div className="series-loading">Loading series…</div> : series.filter((entry) => !entry.archived).map((entry, index) => (
            <button key={entry.id} type="button" className={`series-card${activeSeries === entry.id ? ' is-active' : ''}`} onClick={() => setActiveSeries(entry.id)}>
              <span className="series-monogram">{String(index + 1).padStart(2, '0')}</span>
              <span className="series-card-copy"><strong>{entry.name}</strong><small>{entry.theme || 'Open theme'} · {entry.item_count} videos</small></span>
            </button>
          ))}
          {activeSeries !== 'all' && (
            <button className="series-archive" type="button" onClick={() => {
              if (confirm('Archive this series? Its video plans will remain available.')) archiveSeriesMutation.mutate(activeSeries)
            }}>Archive selected series</button>
          )}
        </aside>

        <main className="rundown-board">
          <div className="rundown-head">
            <div><span className="eyebrow">Production rundown</span><h2>{activeSeries === 'all' ? 'All planned videos' : series.find((entry) => entry.id === activeSeries)?.name}</h2></div>
            <span className={`publication-mode ${planningStatus?.auto_publish_enabled ? 'is-enabled' : ''}`}><i /> Human review</span>
          </div>

          {itemsLoading ? (
            <div className="rundown-empty"><span className="loading-mark" />Loading editorial calendar…</div>
          ) : visibleItems.length === 0 ? (
            <div className="rundown-empty"><strong>No videos on this runway</strong><p>Plan the first topic and its generation task will appear here and in Tasks.</p><button className="btn-primary" onClick={() => setPlanFormOpen(true)}>Plan a video</button></div>
          ) : (
            <div className="rundown-list">
              {visibleItems.map((item) => {
                const date = shortDate(item.generation_at)
                const canEdit = ['draft', 'scheduled'].includes(item.status)
                const canDelete = ['draft', 'scheduled'].includes(item.status)
                return (
                  <article className={`rundown-item rundown-item--${item.status}`} key={item.id}>
                    <div className="rundown-date"><strong>{date.day}</strong><span>{date.month}</span></div>
                    <div className="rundown-content">
                      <div className="rundown-meta">
                        <span>{item.series_name || 'Standalone'}</span>
                        {item.episode_number && <span>EP {String(item.episode_number).padStart(2, '0')}</span>}
                        <span>{item.platform}</span>
                      </div>
                      <h3>{item.title}</h3>
                      <p>{item.brief}</p>
                      <div className="rundown-timeline">
                        <div className="timeline-stop timeline-stop--generation"><i /><span><small>Generation</small><strong>{formatDate(item.generation_at)}</strong></span></div>
                        <div className="timeline-line" />
                        <div className="timeline-stop timeline-stop--publish"><i /><span><small>Target release</small><strong>{formatDate(item.publish_at)}</strong></span></div>
                      </div>
                      {item.error_message && <div className="planning-inline-error">{item.error_message}</div>}
                      {item.status === 'published' && (
                        <div className="publication-proof">
                          <span>Published {formatDate(item.published_at)}</span>
                          {item.publication_url && <a href={item.publication_url} target="_blank" rel="noopener">Open published video ↗</a>}
                        </div>
                      )}
                      {item.status === 'ready' && (
                        <div className="publication-recorder">
                          <input aria-label={`Publication URL for ${item.title}`} placeholder="Paste the live publication URL (optional)" value={publicationUrls[item.id] || ''} onChange={(e) => setPublicationUrls((current) => ({ ...current, [item.id]: e.target.value }))} />
                          <button className="btn-primary" disabled={publishMutation.isPending} onClick={() => publishMutation.mutate({ id: item.id, url: publicationUrls[item.id] || null })}>Mark published</button>
                        </div>
                      )}
                    </div>
                    <div className="rundown-controls">
                      <span className={`plan-status plan-status--${item.status}`}><i />{PLAN_LABELS[item.status]}</span>
                      <div className="rundown-buttons">
                        {item.task_id && <Link className="rundown-link" to={`/tasks/${item.task_id}`}>Open task ↗</Link>}
                        {item.status === 'review' && <button className="btn-primary" disabled={approveMutation.isPending} onClick={() => approveMutation.mutate(item.id)}>Approve for manual publish</button>}
                        {canEdit && <button className="btn-ghost" onClick={() => editPlan(item)}>Edit plan</button>}
                        {canDelete && <button className="rundown-delete" onClick={() => { if (confirm('Delete this plan and its queued task?')) deleteMutation.mutate(item.id) }}>Delete</button>}
                      </div>
                    </div>
                  </article>
                )
              })}
            </div>
          )}
        </main>
      </div>
    </section>
  )
}
