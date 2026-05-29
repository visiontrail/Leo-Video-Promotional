import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchTask, deleteTask, videoUrl, audioUrl, scriptUrl } from '../api'

const STAGES = ['extracting', 'digesting', 'tts', 'composing', 'complete'] as const
const STAGE_LABELS: Record<string, string> = {
  extracting: 'Extract',
  digesting: 'Digest',
  tts: 'TTS',
  composing: 'Compose',
  complete: 'Done',
}

function stageState(current: string, stage: string): 'done' | 'active' | '' {
  const ci = STAGES.indexOf(current as typeof STAGES[number])
  const si = STAGES.indexOf(stage as typeof STAGES[number])
  if (ci < 0) return ''
  if (si < ci) return 'done'
  if (si === ci) return current === 'complete' ? 'done' : 'active'
  return ''
}

export default function TaskDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: task, isLoading } = useQuery({
    queryKey: ['task', id],
    queryFn: () => fetchTask(id!),
    enabled: !!id,
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteTask(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      navigate('/')
    },
  })

  if (isLoading || !task) return <div className="empty-state">Loading...</div>

  const isRunning = !['complete', 'failed', 'queued'].includes(task.status)

  return (
    <div>
      <button className="btn-ghost" onClick={() => navigate('/')} style={{ marginBottom: 16 }}>
        &larr; Back
      </button>

      <div className="card">
        <div className="detail-header">
          <div>
            <h2>{task.source_title || task.id}</h2>
            <div style={{ color: 'var(--text-dim)', fontSize: 13 }}>
              {task.source_type.toUpperCase()}
              {task.source_url && <> &middot; <span style={{ wordBreak: 'break-all' }}>{task.source_url}</span></>}
            </div>
          </div>
          <span className={`badge ${task.status}`}>
            {task.status === 'tts' ? 'Generating Audio' : task.status}
            {isRunning && ' ...'}
          </span>
        </div>

        <div className="pipeline-stages">
          {STAGES.map((s) => (
            <div key={s} className={`stage ${stageState(task.status, s)}`}>
              {STAGE_LABELS[s]}
            </div>
          ))}
        </div>

        {task.status === 'failed' && task.error_message && (
          <div className="error-box">{task.error_message}</div>
        )}

        {task.status === 'complete' && task.video_path && (
          <div style={{ marginTop: 16 }}>
            <video controls src={videoUrl(task.id)} />
          </div>
        )}

        <div style={{ marginTop: 16, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
            <strong>Duration:</strong> {task.config.target_duration_minutes} min &middot;
            <strong> Voices:</strong> {task.config.voice_1} + {task.config.voice_2}
            {task.config.include_character && <> &middot; <strong>Character:</strong> On</>}
          </div>
        </div>

        <div className="actions">
          {task.script_path && (
            <a href={scriptUrl(task.id)} target="_blank" rel="noopener">
              <button className="btn-ghost">Download Script</button>
            </a>
          )}
          {task.audio_path && (
            <a href={audioUrl(task.id)} target="_blank" rel="noopener">
              <button className="btn-ghost">Download Audio</button>
            </a>
          )}
          {task.video_path && (
            <a href={videoUrl(task.id)} download>
              <button className="btn-primary">Download Video</button>
            </a>
          )}
          <button
            className="btn-danger"
            onClick={() => { if (confirm('Delete this task?')) deleteMutation.mutate() }}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}
