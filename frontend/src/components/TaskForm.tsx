import { useState, useRef } from 'react'
import type { DragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createTask, fetchProviders } from '../api'
import type { TaskConfig } from '../api'

type SourceType = 'youtube' | 'epub' | 'pdf'
type VideoTemplate = 'podcast' | 'kinetic' | 'swiss' | 'minimal'
type ScriptFormat = 'monologue' | 'dialogue'

const SCRIPT_FORMATS: Array<{ key: ScriptFormat; name: string; description: string }> = [
  { key: 'monologue', name: 'Solo Talk-Show', description: 'One host talking straight to the audience' },
  { key: 'dialogue', name: 'Two-Host Dialogue', description: 'Back-and-forth between a host and a guest (needs 1.5B)' },
]

const VOICES = [
  { name: 'Carter', gender: 'Male' },
  { name: 'Frank', gender: 'Male' },
  { name: 'Alice', gender: 'Female' },
  { name: 'Maya', gender: 'Female' },
  { name: 'Mary', gender: 'Female' },
  { name: 'Samuel', gender: 'Male' },
]

const VIDEO_TEMPLATES: Array<{ key: VideoTemplate; name: string; description: string }> = [
  { key: 'podcast', name: 'Podcast Studio', description: 'Dark studio with waveform and host focus' },
  { key: 'kinetic', name: 'Kinetic Text', description: 'Large animated type with punchy speaker turns' },
  { key: 'swiss', name: 'Swiss Grid', description: 'Precise editorial grid with clean typography' },
  { key: 'minimal', name: 'Minimal', description: 'Calm centered text with soft motion' },
]

export default function TaskForm() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [sourceType, setSourceType] = useState<SourceType>('youtube')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [duration, setDuration] = useState(10)
  const [scriptFormat, setScriptFormat] = useState<ScriptFormat>('monologue')
  const [voice1, setVoice1] = useState('Carter')
  const [voice2, setVoice2] = useState('Alice')
  const [ttsModel, setTtsModel] = useState('vibevoice-1.5b')
  const [videoTemplate, setVideoTemplate] = useState<VideoTemplate>('podcast')
  const [processingMode, setProcessingMode] = useState<'full_text' | 'curated_highlights'>('full_text')
  const [character, setCharacter] = useState(false)
  const [providerId, setProviderId] = useState<number | null>(null)
  const [dragover, setDragover] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: fetchProviders })

  // Solo talk-show (monologue) is the primary style; dialogue is secondary.
  // The 0.5B realtime model is single-speaker, so it can only do monologue —
  // keep the style and model selections in sync.
  const isMonologue = scriptFormat === 'monologue'
  const is05b = ttsModel === 'vibevoice-0.5b'

  const selectFormat = (f: ScriptFormat) => {
    if (f === 'dialogue' && ttsModel === 'vibevoice-0.5b') setTtsModel('vibevoice-1.5b')
    setScriptFormat(f)
  }
  const selectModel = (m: string) => {
    if (m === 'vibevoice-0.5b' && scriptFormat === 'dialogue') setScriptFormat('monologue')
    setTtsModel(m)
  }

  const mutation = useMutation({
    mutationFn: () => {
      const config: TaskConfig = {
        target_duration_minutes: duration,
        script_format: scriptFormat,
        speaker_count: isMonologue ? 1 : 2,
        voice_1: voice1,
        voice_2: voice2,
        tts_model: ttsModel,
        video_template: videoTemplate,
        processing_mode: sourceType === 'epub' ? processingMode : 'full_text',
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

      {sourceType === 'epub' && (
        <div className="form-group">
          <label>Processing Mode</label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <label className={`choice-tile ${processingMode === 'full_text' ? 'active' : ''}`}>
              <input
                type="radio"
                name="processing_mode"
                value="full_text"
                checked={processingMode === 'full_text'}
                onChange={() => setProcessingMode('full_text')}
              />
              <span>
                <strong>Full Text</strong>
                <small>Use the standard EPUB extractor</small>
              </span>
            </label>
            <label className={`choice-tile ${processingMode === 'curated_highlights' ? 'active' : ''}`}>
              <input
                type="radio"
                name="processing_mode"
                value="curated_highlights"
                checked={processingMode === 'curated_highlights'}
                onChange={() => setProcessingMode('curated_highlights')}
              />
              <span>
                <strong>Curated Highlights</strong>
                <small>Use Isla-Reader selected quotes</small>
              </span>
            </label>
          </div>
        </div>
      )}

      <div className="form-group">
        <label>Format</label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          {SCRIPT_FORMATS.map((f) => (
            <label key={f.key} className={`choice-tile ${scriptFormat === f.key ? 'active' : ''}`}>
              <input
                type="radio"
                name="script_format"
                value={f.key}
                checked={scriptFormat === f.key}
                onChange={() => selectFormat(f.key)}
              />
              <span>
                <strong>{f.name}</strong>
                <small>{f.description}</small>
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className={isMonologue ? '' : 'grid-2'}>
        <div className="form-group">
          <label>{isMonologue ? 'Host Voice' : 'Host Voice (Speaker 1)'}</label>
          <select value={voice1} onChange={(e) => setVoice1(e.target.value)}>
            {VOICES.map((v) => (
              <option key={v.name} value={v.name}>{v.name} ({v.gender})</option>
            ))}
          </select>
        </div>
        {!isMonologue && (
          <div className="form-group">
            <label>Co-host Voice (Speaker 2)</label>
            <select value={voice2} onChange={(e) => setVoice2(e.target.value)}>
              {VOICES.map((v) => (
                <option key={v.name} value={v.name}>{v.name} ({v.gender})</option>
              ))}
            </select>
          </div>
        )}
      </div>
      <small style={{ display: 'block', marginTop: 6, marginBottom: 16, color: 'var(--text-dim)' }}>
        {isMonologue
          ? 'Solo talk-show uses a single voice.'
          : 'Two-host dialogue uses two voices — a host and a co-host.'}
      </small>

      <div className="form-group">
        <label>TTS Model</label>
        <select value={ttsModel} onChange={(e) => selectModel(e.target.value)}>
          <option value="vibevoice-1.5b">1.5B (high quality)</option>
          <option value="vibevoice-0.5b" disabled={scriptFormat === 'dialogue'}>
            0.5B (fast draft, solo only)
          </option>
        </select>
        {is05b && (
          <small style={{ display: 'block', marginTop: 6, color: 'var(--text-dim)' }}>
            The 0.5B model is single-speaker — solo talk-show only.
          </small>
        )}
        {scriptFormat === 'dialogue' && (
          <small style={{ display: 'block', marginTop: 6, color: 'var(--text-dim)' }}>
            Two-host dialogue requires the 1.5B model.
          </small>
        )}
      </div>

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

      <div className="form-group">
        <label>Video Template</label>
        <div className="template-grid">
          {VIDEO_TEMPLATES.map((template) => (
            <label
              key={template.key}
              className={`choice-tile ${videoTemplate === template.key ? 'active' : ''}`}
            >
              <input
                type="radio"
                name="video_template"
                value={template.key}
                checked={videoTemplate === template.key}
                onChange={() => setVideoTemplate(template.key)}
              />
              <span>
                <strong>{template.name}</strong>
                <small>{template.description}</small>
              </span>
            </label>
          ))}
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
