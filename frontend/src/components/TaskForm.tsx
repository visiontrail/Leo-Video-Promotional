import { useState, useRef } from 'react'
import type { CSSProperties, DragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createTask, fetchProviders, fetchVoices, voicePreviewUrl } from '../api'
import type { TaskConfig, VoiceOption } from '../api'
import { countdown, formatStart, localInputToIso, toLocalInputValue } from '../schedule'
import { IconPlay, IconStop } from './Icons'

type SourceType = 'youtube' | 'epub' | 'pdf'
type VideoTemplate = 'podcast' | 'kinetic' | 'swiss' | 'minimal' | 'shanshui'
type ScriptFormat = 'monologue' | 'dialogue'
type FootageProvider = 'wikimedia' | 'hybrid' | 'opencli_web'

const SCRIPT_FORMATS: Array<{ key: ScriptFormat; name: string; description: string }> = [
  { key: 'monologue', name: 'Solo Talk-Show', description: 'One host talking straight to the audience' },
  { key: 'dialogue', name: 'Two-Host Dialogue', description: 'Back-and-forth between a host and a guest (needs 1.5B)' },
]

/* Fallback list, used until /api/voices answers (or if it fails). The backend
   is the source of truth for which voices exist and which can be previewed. */
const VOICES: VoiceOption[] = [
  { name: 'Carter', gender: 'male', lang: 'en' },
  { name: 'Frank', gender: 'male', lang: 'en' },
  { name: 'Alice', gender: 'female', lang: 'en' },
  { name: 'Maya', gender: 'female', lang: 'en' },
  { name: 'Mary', gender: 'female', lang: 'en' },
  { name: 'Samuel', gender: 'male', lang: 'in' },
].map((v) => ({ ...v, resolved_name: v.name, preview_available: false }))

const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

type VoiceFieldProps = {
  label: string
  value: string
  onChange: (voice: string) => void
  voices: VoiceOption[]
  playing: boolean
  onPreview: (voice: string) => void
}

/* A voice select with an inline preview button. The preview plays VibeVoice's
   own reference sample for the preset — the clip the model clones — so it is
   what the finished audio will sound like, without running synthesis. */
function VoiceField({ label, value, onChange, voices, playing, onPreview }: VoiceFieldProps) {
  const selected = voices.find((v) => v.name === value)
  const canPreview = selected?.preview_available ?? false
  const substituted = selected && selected.resolved_name !== selected.name

  return (
    <div>
      <label>{label}</label>
      <div className="voice-row">
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          {voices.map((v) => (
            <option key={v.name} value={v.name}>
              {v.name} ({titleCase(v.gender)})
            </option>
          ))}
        </select>
        <button
          type="button"
          className="icon-btn voice-preview"
          onClick={() => onPreview(value)}
          disabled={!canPreview}
          aria-label={playing ? `Stop ${value} preview` : `Preview ${value}`}
          title={canPreview ? `Preview ${value}` : `No preview sample installed for ${value}`}
        >
          {playing ? <IconStop /> : <IconPlay />}
        </button>
      </div>
      {substituted && (
        <small className="wb-hint">
          This model substitutes {selected!.name} → {selected!.resolved_name}
          {canPreview ? '; the preview plays the substitute.' : ', which ships no preview sample.'}
        </small>
      )}
    </div>
  )
}

/* The template picks the visual system used by deterministic scenes and the
   authoring agents. Most vary the surface treatment; Shan Shui also introduces
   its own paper, terrain, contour, and route-mark layers.

   `swatch` mirrors the real theme in backend/pipeline/scene_kit.py — page, ink,
   scene accent and wash alpha — so the picker previews the room the video is
   actually shot in instead of a decorative colour. Keep the two in sync. */
type TemplateSwatch = { bg: string; ink: string; accent: string; wash: number }

const VIDEO_TEMPLATES: Array<{
  key: VideoTemplate
  name: string
  description: string
  swatch: TemplateSwatch
}> = [
  {
    key: 'podcast',
    name: 'Documentary',
    description: 'Deep navy with warm ink — the default look',
    swatch: { bg: '#0B0D17', ink: '#F5F2EA', accent: '#F0A63C', wash: 0.16 },
  },
  {
    key: 'kinetic',
    name: 'Kinetic',
    description: 'Near-black and high contrast for punchy statement cuts',
    swatch: { bg: '#08070C', ink: '#FFFFFF', accent: '#F0A63C', wash: 0.24 },
  },
  {
    key: 'swiss',
    name: 'Swiss Grid',
    description: 'Paper-white editorial with dark type',
    swatch: { bg: '#F4F1EA', ink: '#14161F', accent: '#F0A63C', wash: 0.12 },
  },
  {
    key: 'minimal',
    name: 'Minimal',
    description: 'Restrained washes, type does the work',
    swatch: { bg: '#0E0E10', ink: '#EDEDED', accent: '#F0A63C', wash: 0.09 },
  },
  {
    key: 'shanshui',
    name: 'Shan Shui',
    description: 'Rice paper, ink-green contours and ochre route lines',
    swatch: { bg: '#F7F0E4', ink: '#263A30', accent: '#B46F35', wash: 0.13 },
  },
]

const SOURCE_LABEL: Record<SourceType, string> = { youtube: 'YouTube', epub: 'EPUB', pdf: 'PDF' }

/* Quick picks for parking a run in an idle window — TTS and the LLM both want
   the machine to themselves, so "tonight" is the common case. */
const START_PRESETS: Array<{ label: string; at: () => Date }> = [
  { label: 'In 1 hour', at: () => new Date(Date.now() + 60 * 60 * 1000) },
  { label: 'In 3 hours', at: () => new Date(Date.now() + 3 * 60 * 60 * 1000) },
  {
    label: 'Tonight 01:00',
    at: () => {
      const d = new Date()
      d.setHours(1, 0, 0, 0)
      if (d.getTime() <= Date.now()) d.setDate(d.getDate() + 1)
      return d
    },
  },
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
  const [ttsModel, setTtsModel] = useState('vibevoice-0.5b')
  const [videoTemplate, setVideoTemplate] = useState<VideoTemplate>('podcast')
  const [processingMode, setProcessingMode] = useState<'full_text' | 'curated_highlights'>('full_text')
  const [character, setCharacter] = useState(false)
  const [captionsEnabled, setCaptionsEnabled] = useState(false)
  const [thumbnailEnabled, setThumbnailEnabled] = useState(true)
  const [autoRender, setAutoRender] = useState(true)
  const [footageEnabled, setFootageEnabled] = useState(true)
  const [footageProvider, setFootageProvider] = useState<FootageProvider>('hybrid')
  const [footageClipCount, setFootageClipCount] = useState(8)
  const [footageOrientation, setFootageOrientation] = useState<'landscape' | 'portrait'>('landscape')
  const [providerId, setProviderId] = useState<number | null>(null)
  const [startMode, setStartMode] = useState<'now' | 'later'>('now')
  const [startAt, setStartAt] = useState('')
  const [dragover, setDragover] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // One audio element serves both voice fields — starting a preview replaces
  // whatever was playing, so two clips can never overlap.
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playingVoice, setPlayingVoice] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: fetchProviders })
  // Preview availability and voice substitution are model-dependent, so refetch
  // when the engine changes.
  const { data: voices = VOICES } = useQuery({
    queryKey: ['voices', ttsModel],
    queryFn: () => fetchVoices(ttsModel),
    placeholderData: VOICES,
  })

  const stopPreview = () => {
    const audio = audioRef.current
    if (audio) {
      audio.pause()
      audio.currentTime = 0
    }
    setPlayingVoice(null)
  }

  const togglePreview = (voice: string) => {
    if (playingVoice === voice) {
      stopPreview()
      return
    }
    const audio = audioRef.current
    if (!audio) return
    setPreviewError(null)
    audio.src = voicePreviewUrl(voice, ttsModel)
    audio.play().then(
      () => setPlayingVoice(voice),
      () => {
        setPlayingVoice(null)
        setPreviewError(`Could not play the ${voice} preview.`)
      },
    )
  }

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
    // The sample a voice maps to can change with the model — drop stale audio.
    stopPreview()
    setTtsModel(m)
  }

  // The picker holds local wall-clock; the API takes UTC.
  const scheduled = startMode === 'later' ? localInputToIso(startAt) : null
  const scheduleIsPast = !!scheduled && new Date(scheduled).getTime() <= Date.now()
  const startLabel = scheduled ? formatStart(scheduled) : 'Immediately'

  const pickPreset = (at: Date) => {
    setStartMode('later')
    setStartAt(toLocalInputValue(at))
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
        captions_enabled: captionsEnabled,
        provider_id: providerId,
        footage_enabled: footageEnabled,
        footage_provider: footageProvider,
        footage_license_policy: footageProvider === 'wikimedia' ? 'open_only' : 'review_required',
        footage_clip_count: footageClipCount,
        footage_orientation: footageOrientation,
        footage_multimodal_analyzer: 'gemini_web',
        thumbnail_enabled: thumbnailEnabled,
        auto_render: autoRender,
      }
      return createTask(
        sourceType,
        sourceType === 'youtube' ? url : null,
        config,
        file || undefined,
        scheduled,
      )
    },
    onSuccess: (task) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      navigate(`/tasks/${task.id}`)
    },
  })

  const sourceReady =
    (sourceType === 'youtube' && !!url.trim()) ||
    ((sourceType === 'epub' || sourceType === 'pdf') && !!file)
  const startReady = startMode === 'now' || !!scheduled
  const canSubmit = sourceReady && startReady

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragover(false)
    const f = e.dataTransfer.files[0]
    if (f) setFile(f)
  }

  const activeTemplate = VIDEO_TEMPLATES.find((t) => t.key === videoTemplate)!

  // Preview of the pipeline this configuration will actually run.
  const pipeline: Array<{ label: string; note: string; on: boolean }> = [
    ...(scheduled && !scheduleIsPast
      ? [{ label: 'Hold', note: `Wait in the queue until ${startLabel}`, on: true }]
      : []),
    { label: 'Extract', note: `Pull text from the ${SOURCE_LABEL[sourceType]} source`, on: true },
    { label: 'Digest', note: isMonologue ? 'Write a solo talk-show script' : 'Write a two-host dialogue script', on: true },
    { label: 'Title', note: 'Generate a publication title in an independent Agent session', on: true },
    { label: 'Thumbnail', note: thumbnailEnabled ? 'Generate cover art through ChatGPT Web' : 'Skipped — cover generation is off', on: thumbnailEnabled },
    { label: 'Footage', note: footageEnabled ? `Scout ${footageClipCount} clips via ${footageProvider === 'wikimedia' ? 'Commons' : footageProvider === 'hybrid' ? 'Commons + web' : 'web platforms'}` : 'Skipped — media scout is off', on: footageEnabled },
    { label: 'Voice', note: `Synthesise with VibeVoice ${is05b ? '0.5B' : '1.5B'}`, on: true },
    {
      label: 'Captions',
      note: captionsEnabled ? 'Show concise, single-line captions' : 'Skipped — captions are off',
      on: captionsEnabled,
    },
    {
      label: 'Review',
      note: autoRender ? 'Skipped — render starts without approval' : 'Pause for your audio approval',
      on: !autoRender,
    },
    { label: 'Compose', note: `Render the ${activeTemplate.name} template`, on: true },
  ]

  // Live read-out pinned above the launch button — the run at a glance.
  const summary: Array<[string, string]> = [
    ['Source', SOURCE_LABEL[sourceType]],
    ['Start', scheduled && !scheduleIsPast ? startLabel : 'Now'],
    ['Length', `${duration} min`],
    ['Style', isMonologue ? 'Solo' : 'Two-host'],
    ['Voice', isMonologue ? voice1 : `${voice1} · ${voice2}`],
    ['Engine', is05b ? '0.5B' : '1.5B'],
    ['Template', activeTemplate.name],
    ['Captions', captionsEnabled ? 'On · single line' : 'Off'],
    ['Title', 'Independent Agent'],
    ['Thumbnail', thumbnailEnabled ? 'ChatGPT Web' : 'Off'],
    ['B-roll', footageEnabled ? `${footageClipCount} · ${footageProvider === 'hybrid' ? 'Hybrid' : footageProvider === 'wikimedia' ? 'Commons' : 'Web'}` : 'Off'],
    ['Review', autoRender ? 'Auto-render' : 'Manual'],
  ]

  return (
    <div className="task-workbench">
      {/* ── Left: what goes in, and the launch control ─────────────── */}
      <section className="wb-source" aria-label="Source">
        <div className="wb-pane-scroll">
          <header className="wb-head">
            <span className="wb-index">01</span>
            <div>
              <span className="eyebrow">Ingest</span>
              <h2>Source material</h2>
            </div>
          </header>

          <div className="source-tabs">
            {(['youtube', 'epub', 'pdf'] as SourceType[]).map((t) => (
              <button
                key={t}
                className={`source-tab ${sourceType === t ? 'active' : ''}`}
                onClick={() => { setSourceType(t); setFile(null); setUrl(''); }}
              >
                {SOURCE_LABEL[t]}
              </button>
            ))}
          </div>

          {sourceType === 'youtube' && (
            <div className="form-group">
              <label>YouTube URL</label>
              <input
                className="wb-url"
                type="text"
                placeholder="https://www.youtube.com/watch?v=..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              <small className="wb-hint">The transcript is pulled straight from the video.</small>
            </div>
          )}

          {(sourceType === 'epub' || sourceType === 'pdf') && (
            <div className="form-group">
              <label>{SOURCE_LABEL[sourceType]} File</label>
              <div
                className={`file-drop ${dragover ? 'dragover' : ''}`}
                onClick={() => fileRef.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDragover(true); }}
                onDragLeave={() => setDragover(false)}
                onDrop={handleDrop}
              >
                {file ? file.name : `Drop your ${SOURCE_LABEL[sourceType]} file here or click to browse`}
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
              <div className="tile-row">
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

          <div className="wb-flow">
            <span className="eyebrow">What will run</span>
            <ol>
              {pipeline.map((step) => (
                <li key={step.label} className={step.on ? '' : 'is-off'}>
                  <strong>{step.label}</strong>
                  <small>{step.note}</small>
                </li>
              ))}
            </ol>
          </div>
        </div>

        <footer className="launch-dock">
          <dl className="run-summary">
            {summary.map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
          <button
            className="btn-primary launch-btn"
            disabled={!canSubmit || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending
              ? 'Creating…'
              : scheduled && !scheduleIsPast
                ? `Schedule for ${startLabel}`
                : 'Generate Podcast Video'}
          </button>
          {!canSubmit && (
            <small className="wb-hint">
              {!sourceReady
                ? sourceType === 'youtube'
                  ? 'Paste a YouTube URL to continue.'
                  : `Add a ${SOURCE_LABEL[sourceType]} file to continue.`
                : 'Pick a start date and time to continue.'}
            </small>
          )}
          {canSubmit && scheduled && scheduleIsPast && (
            <small className="wb-hint">That time has already passed — the run starts right away.</small>
          )}
          {mutation.isError && (
            <div className="error-box">{(mutation.error as Error).message}</div>
          )}
        </footer>
      </section>

      {/* ── Right: everything that shapes the render ───────────────── */}
      <section className="wb-config" aria-label="Production setup">
        <div className="wb-pane-scroll">
          <header className="wb-head">
            <span className="wb-index">02</span>
            <div>
              <span className="eyebrow">Direction</span>
              <h2>Production setup</h2>
            </div>
          </header>

          <div className="wb-grid">
            <article className="wb-panel wb-wide">
              <h3>Hosts &amp; format</h3>
              <div className="tile-row">
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

              <div className="grid-2 wb-voices">
                <VoiceField
                  label={isMonologue ? 'Host Voice' : 'Host Voice (Speaker 1)'}
                  value={voice1}
                  onChange={(v) => {
                    stopPreview()
                    setVoice1(v)
                  }}
                  voices={voices}
                  playing={playingVoice === voice1}
                  onPreview={togglePreview}
                />
                {!isMonologue && (
                  <VoiceField
                    label="Co-host Voice (Speaker 2)"
                    value={voice2}
                    onChange={(v) => {
                      stopPreview()
                      setVoice2(v)
                    }}
                    voices={voices}
                    playing={playingVoice === voice2}
                    onPreview={togglePreview}
                  />
                )}
              </div>
              <audio ref={audioRef} onEnded={() => setPlayingVoice(null)} hidden />
              <small className="wb-hint">
                {previewError
                  ? previewError
                  : isMonologue
                    ? 'Solo talk-show uses a single voice. Hit ▶ to hear a sample.'
                    : 'Two-host dialogue uses two voices — a host and a co-host. Hit ▶ to hear a sample.'}
              </small>
            </article>

            <article className="wb-panel">
              <h3>Engines &amp; length</h3>
              {providers.length > 0 && (
                <div className="form-group">
                  <label>AI provider</label>
                  <select
                    value={providerId ?? ''}
                    onChange={(e) => setProviderId(e.target.value === '' ? null : Number(e.target.value))}
                  >
                    <option value="">Default {providers.find((p) => p.is_default) ? `(${providers.find((p) => p.is_default)!.name})` : ''}</option>
                    {providers.map((p) => (
                      <option key={p.id} value={p.id}>{p.name} — {p.model}</option>
                    ))}
                  </select>
                  <small className="wb-hint">Drives script digestion, thumbnail direction, and the media scout.</small>
                </div>
              )}
              <div className="form-group">
                <label>TTS Model</label>
                <select value={ttsModel} onChange={(e) => selectModel(e.target.value)}>
                  <option value="vibevoice-1.5b">1.5B (high quality)</option>
                  <option value="vibevoice-0.5b" disabled={scriptFormat === 'dialogue'}>
                    0.5B (fast draft, solo only)
                  </option>
                </select>
                {is05b && (
                  <small className="wb-hint">The 0.5B model is single-speaker — solo talk-show only.</small>
                )}
                {scriptFormat === 'dialogue' && (
                  <small className="wb-hint">Two-host dialogue requires the 1.5B model.</small>
                )}
              </div>
              <div className="form-group">
                <label>Target Duration</label>
                <select value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
                  <option value={5}>5 min</option>
                  <option value={10}>10 min</option>
                  <option value={15}>15 min</option>
                  <option value={20}>20 min</option>
                </select>
              </div>
            </article>

            {/* Five stacked description tiles made this the tallest card on the
                deck; the swatch carries the look and the copy follows the pick. */}
            <article className="wb-panel">
              <h3>Video template</h3>
              <div className="template-picker">
                {VIDEO_TEMPLATES.map((template) => (
                  <label
                    key={template.key}
                    className={`template-option ${videoTemplate === template.key ? 'active' : ''}`}
                    title={template.description}
                  >
                    <input
                      type="radio"
                      name="video_template"
                      value={template.key}
                      checked={videoTemplate === template.key}
                      onChange={() => setVideoTemplate(template.key)}
                    />
                    <span
                      className="template-swatch"
                      aria-hidden="true"
                      style={{
                        '--tpl-bg': template.swatch.bg,
                        '--tpl-ink': template.swatch.ink,
                        '--tpl-accent': template.swatch.accent,
                        '--tpl-wash': template.swatch.wash,
                      } as CSSProperties}
                    />
                    <b>{template.name}</b>
                  </label>
                ))}
              </div>
              <small className="wb-hint template-note">{activeTemplate.description}</small>
            </article>

            {/* The run-level switches share one panel: on their own they were
                four near-empty cards that pushed the rest of the deck apart. */}
            <article className="wb-panel">
              <h3>Output &amp; review</h3>
              <div className="switch-stack">
                <div className="switch-row">
                  <span className="switch-copy">
                    <strong>Captions</strong>
                    <small>{captionsEnabled ? 'One concise line at a time' : 'No captions on the render'}</small>
                  </span>
                  <label className="footage-toggle">
                    <input
                      type="checkbox"
                      checked={captionsEnabled}
                      onChange={(event) => setCaptionsEnabled(event.target.checked)}
                      aria-label="Include captions"
                    />
                    <span aria-hidden="true" />
                    <b>{captionsEnabled ? 'On' : 'Off'}</b>
                  </label>
                </div>

                <div className="switch-row">
                  <span className="switch-copy">
                    <strong>Animated character</strong>
                    <small>{character ? 'A Lottie host shares the frame' : 'No character overlay'}</small>
                  </span>
                  <label className="footage-toggle">
                    <input
                      type="checkbox"
                      checked={character}
                      onChange={(event) => setCharacter(event.target.checked)}
                      aria-label="Include animated character"
                    />
                    <span aria-hidden="true" />
                    <b>{character ? 'On' : 'Off'}</b>
                  </label>
                </div>

                <div className="switch-row">
                  <span className="switch-copy">
                    <strong>Viral thumbnail</strong>
                    <small>{thumbnailEnabled ? '16:9 cover via ChatGPT Web' : 'No automatic cover art'}</small>
                  </span>
                  <label className="footage-toggle">
                    <input
                      type="checkbox"
                      checked={thumbnailEnabled}
                      onChange={(event) => setThumbnailEnabled(event.target.checked)}
                      aria-label="Generate viral thumbnail"
                    />
                    <span aria-hidden="true" />
                    <b>{thumbnailEnabled ? 'On' : 'Off'}</b>
                  </label>
                </div>

                {/* The row reads as a gate you switch on, so checked is the
                    pause and `autoRender` stays its inverse. */}
                <div className="switch-row">
                  <span className="switch-copy">
                    <strong>Audio review</strong>
                    <small>{autoRender ? 'Renders without approval' : 'Pause after TTS to approve'}</small>
                  </span>
                  <label className="footage-toggle">
                    <input
                      type="checkbox"
                      checked={!autoRender}
                      onChange={(event) => setAutoRender(!event.target.checked)}
                      aria-label="Pause for audio review"
                    />
                    <span aria-hidden="true" />
                    <b>{autoRender ? 'Off' : 'On'}</b>
                  </label>
                </div>
              </div>
            </article>

            <article className="wb-panel">
              <h3>Start time</h3>
              <div className="tile-row schedule-modes">
                <label className={`choice-tile ${startMode === 'now' ? 'active' : ''}`}>
                  <input
                    type="radio"
                    name="start_mode"
                    value="now"
                    checked={startMode === 'now'}
                    onChange={() => setStartMode('now')}
                  />
                  <span>
                    <strong>Start now</strong>
                    <small>Run as soon as the worker is free</small>
                  </span>
                </label>
                <label className={`choice-tile ${startMode === 'later' ? 'active' : ''}`}>
                  <input
                    type="radio"
                    name="start_mode"
                    value="later"
                    checked={startMode === 'later'}
                    onChange={() => setStartMode('later')}
                  />
                  <span>
                    <strong>Schedule</strong>
                    <small>Hold the run for an idle window</small>
                  </span>
                </label>
              </div>

              {startMode === 'later' && (
                <div className="schedule-picker">
                  <div className="schedule-presets">
                    {START_PRESETS.map((preset) => (
                      <button
                        key={preset.label}
                        type="button"
                        className="schedule-chip"
                        onClick={() => pickPreset(preset.at())}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                  <div className="form-group">
                    <label htmlFor="start-at">Start at (your local time)</label>
                    <input
                      id="start-at"
                      type="datetime-local"
                      value={startAt}
                      min={toLocalInputValue(new Date())}
                      onChange={(e) => setStartAt(e.target.value)}
                    />
                  </div>
                  <small className="wb-hint">
                    {scheduled
                      ? scheduleIsPast
                        ? 'That time has passed — the task will be picked up immediately.'
                        : `Queued now, starts ${startLabel} · ${countdown(scheduled)}`
                      : 'The task sits in the queue and the pipeline starts at this time.'}
                  </small>
                </div>
              )}
            </article>

            <section className={`footage-config wb-wide ${footageEnabled ? 'is-enabled' : ''}`}>
              <div className="footage-config-head">
                <div>
                  <span className="eyebrow">Agent media scout</span>
                  <h3>Public Footage</h3>
                  <p>Plan visual searches, download eligible B-roll, and keep a license audit trail.</p>
                </div>
                <label className="footage-toggle">
                  <input
                    type="checkbox"
                    checked={footageEnabled}
                    onChange={(event) => setFootageEnabled(event.target.checked)}
                  />
                  <span aria-hidden="true" />
                  <b>{footageEnabled ? 'On' : 'Off'}</b>
                </label>
              </div>

              {footageEnabled && (
                <>
                  <div className="form-group">
                    <label>Source strategy</label>
                    <select
                      value={footageProvider}
                      onChange={(event) => setFootageProvider(event.target.value as FootageProvider)}
                    >
                      <option value="hybrid">Hybrid — Commons + YouTube</option>
                      <option value="wikimedia">Wikimedia Commons only</option>
                      <option value="opencli_web">YouTube only</option>
                    </select>
                  </div>

                  <div className="source-readiness">
                    <span className="source-monogram">OC</span>
                    <span>
                      <strong>{footageProvider === 'wikimedia' ? 'Wikimedia Commons' : 'YouTube web scout'}</strong>
                      <small>{footageProvider === 'wikimedia' ? 'Explicit open-license metadata' : 'Gemini trim analysis · FFmpeg edits'}</small>
                    </span>
                    <em>Project local</em>
                  </div>

                  <div className="grid-2 footage-options">
                    <div>
                      <label>Target clips</label>
                      <select
                        value={footageClipCount}
                        onChange={(event) => setFootageClipCount(Number(event.target.value))}
                      >
                        <option value={2}>2 clips</option>
                        <option value={3}>3 clips</option>
                        <option value={4}>4 clips</option>
                        <option value={5}>5 clips</option>
                        <option value={6}>6 clips</option>
                        <option value={7}>7 clips</option>
                        <option value={8}>8 clips</option>
                        <option value={9}>9 clips</option>
                        <option value={10}>10 clips</option>
                        <option value={11}>11 clips</option>
                        <option value={12}>12 clips</option>
                        <option value={13}>13 clips</option>
                        <option value={14}>14 clips</option>
                        <option value={15}>15 clips</option>
                        <option value={16}>16 clips</option>
                        <option value={17}>17 clips</option>
                        <option value={18}>18 clips</option>
                        <option value={19}>19 clips</option>
                        <option value={20}>20 clips</option>
                        <option value={21}>21 clips</option>
                        <option value={22}>22 clips</option>
                        <option value={23}>23 clips</option>
                        <option value={24}>24 clips</option>
                        <option value={25}>25 clips</option>
                        <option value={26}>26 clips</option>
                        <option value={27}>27 clips</option>
                        <option value={28}>28 clips</option>
                        <option value={29}>29 clips</option>
                        <option value={30}>30 clips</option>
                      </select>
                    </div>
                    <div>
                      <label>Frame orientation</label>
                      <select
                        value={footageOrientation}
                        onChange={(event) => setFootageOrientation(event.target.value as 'landscape' | 'portrait')}
                      >
                        <option value="landscape">Landscape</option>
                        <option value="portrait">Portrait</option>
                      </select>
                    </div>
                  </div>
                  <div className={`license-gate ${footageProvider === 'wikimedia' ? '' : 'review-required'}`}>
                    <span className="license-gate-icon">{footageProvider === 'wikimedia' ? '✓' : '!'}</span>
                    <span>
                      <strong>{footageProvider === 'wikimedia' ? 'Open-license gate' : 'Rights review gate'}</strong>
                      <small>{footageProvider === 'wikimedia' ? 'Public Domain · CC0 · CC BY · CC BY-SA' : 'Platform downloadability is not reuse permission · review before publishing'}</small>
                    </span>
                  </div>
                </>
              )}
            </section>
          </div>
        </div>
      </section>
    </div>
  )
}
