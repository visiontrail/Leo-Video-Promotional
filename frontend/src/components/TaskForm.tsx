import { useState, useRef } from 'react'
import type { DragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createTask, fetchProviders } from '../api'
import type { TaskConfig } from '../api'

type SourceType = 'youtube' | 'epub' | 'pdf'

const VOICES = [
  { name: 'Carter', gender: 'Male' },
  { name: 'Frank', gender: 'Male' },
  { name: 'Alice', gender: 'Female' },
  { name: 'Maya', gender: 'Female' },
  { name: 'Mary', gender: 'Female' },
  { name: 'Samuel', gender: 'Male' },
]

export default function TaskForm() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [sourceType, setSourceType] = useState<SourceType>('youtube')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [duration, setDuration] = useState(10)
  const [voice1, setVoice1] = useState('Carter')
  const [voice2, setVoice2] = useState('Alice')
  const [character, setCharacter] = useState(false)
  const [providerId, setProviderId] = useState<number | null>(null)
  const [dragover, setDragover] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: fetchProviders })

  const mutation = useMutation({
    mutationFn: () => {
      const config: TaskConfig = {
        target_duration_minutes: duration,
        speaker_count: 2,
        voice_1: voice1,
        voice_2: voice2,
        include_character: character,
        provider_id: providerId,
      }
      return createTask(
        sourceType,
        sourceType === 'youtube' ? url : null,
        config,
        file || undefined,
      )
    },
    onSuccess: (task) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      navigate(`/tasks/${task.id}`)
    },
  })

  const canSubmit =
    (sourceType === 'youtube' && url.trim()) ||
    ((sourceType === 'epub' || sourceType === 'pdf') && file)

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragover(false)
    const f = e.dataTransfer.files[0]
    if (f) setFile(f)
  }

  return (
    <div className="card" style={{ maxWidth: 700, margin: '0 auto' }}>
      <h2 style={{ marginBottom: 20, fontSize: 18, fontWeight: 600 }}>New Podcast Task</h2>

      <div className="source-tabs">
        {(['youtube', 'epub', 'pdf'] as SourceType[]).map((t) => (
          <button
            key={t}
            className={`source-tab ${sourceType === t ? 'active' : ''}`}
            onClick={() => { setSourceType(t); setFile(null); setUrl(''); }}
          >
            {t === 'youtube' ? 'YouTube' : t.toUpperCase()}
          </button>
        ))}
      </div>

      {sourceType === 'youtube' && (
        <div className="form-group">
          <label>YouTube URL</label>
          <input
            type="text"
            placeholder="https://www.youtube.com/watch?v=..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
      )}

      {(sourceType === 'epub' || sourceType === 'pdf') && (
        <div className="form-group">
          <label>{sourceType.toUpperCase()} File</label>
          <div
            className={`file-drop ${dragover ? 'dragover' : ''}`}
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragover(true); }}
            onDragLeave={() => setDragover(false)}
            onDrop={handleDrop}
          >
            {file ? file.name : `Drop your ${sourceType.toUpperCase()} file here or click to browse`}
            <input
              ref={fileRef}
              type="file"
              accept={sourceType === 'epub' ? '.epub' : '.pdf'}
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </div>
        </div>
      )}

      <div className="grid-2">
        <div className="form-group">
          <label>Target Duration (minutes)</label>
          <select value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
            <option value={5}>5 min</option>
            <option value={10}>10 min</option>
            <option value={15}>15 min</option>
            <option value={20}>20 min</option>
          </select>
        </div>
        <div className="form-group">
          <label style={{ visibility: 'hidden' }}>Character</label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14, color: 'var(--text)' }}>
            <input type="checkbox" checked={character} onChange={(e) => setCharacter(e.target.checked)} style={{ width: 'auto' }} />
            Include animated character
          </label>
        </div>
      </div>

      <div className="grid-2">
        <div className="form-group">
          <label>Host Voice (Speaker 1)</label>
          <select value={voice1} onChange={(e) => setVoice1(e.target.value)}>
            {VOICES.map((v) => (
              <option key={v.name} value={v.name}>{v.name} ({v.gender})</option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label>Co-host Voice (Speaker 2)</label>
          <select value={voice2} onChange={(e) => setVoice2(e.target.value)}>
            {VOICES.map((v) => (
              <option key={v.name} value={v.name}>{v.name} ({v.gender})</option>
            ))}
          </select>
        </div>
      </div>

      {providers.length > 0 && (
        <div className="form-group">
          <label>AI Provider</label>
          <select
            value={providerId ?? ''}
            onChange={(e) => setProviderId(e.target.value === '' ? null : Number(e.target.value))}
          >
            <option value="">Default {providers.find((p) => p.is_default) ? `(${providers.find((p) => p.is_default)!.name})` : ''}</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>{p.name} — {p.model}</option>
            ))}
          </select>
        </div>
      )}

      <div style={{ marginTop: 8 }}>
        <button
          className="btn-primary"
          disabled={!canSubmit || mutation.isPending}
          onClick={() => mutation.mutate()}
          style={{ width: '100%', padding: '12px 0', fontSize: 15 }}
        >
          {mutation.isPending ? 'Creating...' : 'Generate Podcast Video'}
        </button>
        {mutation.isError && (
          <div className="error-box" style={{ marginTop: 12 }}>
            {(mutation.error as Error).message}
          </div>
        )}
      </div>
    </div>
  )
}
