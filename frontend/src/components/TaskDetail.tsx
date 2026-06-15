import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchTask,
  deleteTask,
  fetchScript,
  updateScript,
  regenerateTask,
  renderTask,
  videoUrl,
  audioUrl,
  scriptUrl,
} from '../api'
import LogPanel from './LogPanel'

const STAGES = ['extracting', 'digesting', 'tts', 'awaiting_review', 'composing', 'complete'] as const
const STAGE_LABELS: Record<string, string> = {
  extracting: 'Extract',
  digesting: 'Digest',
  tts: 'TTS',
  awaiting_review: 'Review',
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
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && !['complete', 'failed', 'awaiting_review'].includes(status) ? 2000 : false
    },
  })

  const hasScript = !!task?.script_path
  const { data: scriptText } = useQuery({
    queryKey: ['script', id],
    queryFn: () => fetchScript(id!),
    enabled: !!id && hasScript,
  })

  const [draftOverride, setDraftOverride] = useState<string | null>(null)
  const draft = draftOverride ?? scriptText ?? ''
  const dirty = draftOverride !== null && draftOverride !== scriptText

  const deleteMutation = useMutation({
    mutationFn: () => deleteTask(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      navigate('/')
    },
  })

  const saveMutation = useMutation({
    mutationFn: () => updateScript(id!, draft),
    onSuccess: () => {
      setDraftOverride(null)
      queryClient.invalidateQueries({ queryKey: ['script', id] })
    },
  })

  const regenMutation = useMutation({
    mutationFn: () => regenerateTask(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['task', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })

  const renderMutation = useMutation({
    mutationFn: () => renderTask(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['task', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })

  if (isLoading || !task) return <div className="empty-state">Loading...</div>

  const isRunning = !['complete', 'failed', 'queued', 'awaiting_review'].includes(task.status)
  const awaitingReview = task.status === 'awaiting_review'

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
            {task.status === 'tts'
              ? 'Generating Audio'
              : task.status === 'awaiting_review'
                ? 'Awaiting Review'
                : task.status}
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

        {task.audio_path && task.status !== 'complete' && (
          <div style={{ marginTop: 16 }}>
            <h3 style={{ margin: '0 0 8px', fontSize: 15 }}>Audio Preview</h3>
            <audio controls src={audioUrl(task.id)} style={{ width: '100%' }} />
            {awaitingReview && (
              <>
                <p style={{ fontSize: 13, color: 'var(--text-dim)', margin: '8px 0' }}>
                  Listen to the generated audio. Render the video to continue, or edit the
                  script below and re-generate the audio.
                </p>
                <div className="actions">
                  <button
                    className="btn-primary"
                    disabled={renderMutation.isPending}
                    onClick={() => renderMutation.mutate()}
                  >
                    {renderMutation.isPending ? 'Starting...' : 'Render Video'}
                  </button>
                </div>
                {renderMutation.isError && (
                  <div className="error-box" style={{ marginTop: 8 }}>
                    {(renderMutation.error as Error).message}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        <div style={{ marginTop: 16, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
            <strong>Duration:</strong> {task.config.target_duration_minutes} min &middot;
            <strong> Voices:</strong> {task.config.voice_1} + {task.config.voice_2}
            {task.config.include_character && <> &middot; <strong>Character:</strong> On</>}
          </div>
        </div>

        {hasScript && (
          <div style={{ marginTop: 24 }}>
            <h3 style={{ margin: '0 0 8px', fontSize: 15 }}>Script</h3>
            <textarea
              value={draft}
              onChange={(e) => setDraftOverride(e.target.value)}
              spellCheck={false}
              style={{
                width: '100%',
                minHeight: 280,
                fontFamily: 'var(--font-mono, monospace)',
                fontSize: 13,
                lineHeight: 1.5,
                padding: 12,
                resize: 'vertical',
              }}
            />
            <div className="actions" style={{ marginTop: 8 }}>
              <button
                className="btn-ghost"
                disabled={!dirty || saveMutation.isPending}
                onClick={() => saveMutation.mutate()}
              >
                {saveMutation.isPending ? 'Saving...' : 'Save Script'}
              </button>
              <button
                className="btn-primary"
                disabled={isRunning || dirty || regenMutation.isPending}
                title={dirty ? 'Save your changes first' : isRunning ? 'Task is processing' : ''}
                onClick={() => regenMutation.mutate()}
              >
                {regenMutation.isPending ? 'Starting...' : 'Re-generate Audio'}
              </button>
            </div>
            {regenMutation.isError && (
              <div className="error-box" style={{ marginTop: 8 }}>
                {(regenMutation.error as Error).message}
              </div>
            )}
          </div>
        )}

        <LogPanel taskId={task.id} taskStatus={task.status} />

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
