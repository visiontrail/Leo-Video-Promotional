import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchTasks } from '../api'
import type { Task } from '../api'

const STATUS_LABELS: Record<string, string> = {
  queued: 'Queued',
  extracting: 'Extracting',
  digesting: 'Digesting',
  tts: 'Generating Audio',
  composing: 'Composing Video',
  complete: 'Complete',
  failed: 'Failed',
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function TaskList() {
  const navigate = useNavigate()
  const { data: tasks, isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: fetchTasks,
  })

  if (isLoading) return <div className="empty-state">Loading...</div>

  if (!tasks?.length) {
    return (
      <div className="empty-state">
        <h2>No tasks yet</h2>
        <p>Create a new task to generate a podcast video from YouTube, EPUB, or PDF.</p>
      </div>
    )
  }

  return (
    <div>
      {tasks.map((t: Task) => (
        <div
          key={t.id}
          className="card task-card"
          onClick={() => navigate(`/tasks/${t.id}`)}
        >
          <div className="left">
            <div className="title">{t.source_title || t.source_url || t.id}</div>
            <div className="meta">
              {t.source_type.toUpperCase()} &middot; {relativeTime(t.created_at)}
              {t.config.target_duration_minutes && ` · ${t.config.target_duration_minutes} min`}
            </div>
          </div>
          <span className={`badge ${t.status}`}>
            {STATUS_LABELS[t.status] || t.status}
          </span>
        </div>
      ))}
    </div>
  )
}
