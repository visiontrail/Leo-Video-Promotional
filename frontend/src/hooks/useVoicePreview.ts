import { useEffect, useRef, useState } from 'react'
import { voicePreviewUrl } from '../api'

/** One player per form; a changed model or voice cancels any pending play. */
export function useVoicePreview(model: string) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const requestRef = useRef(0)
  const [preview, setPreview] = useState<{ model: string; voice: string; state: 'loading' | 'playing' } | null>(null)
  const [error, setError] = useState<{ model: string; message: string } | null>(null)
  const active = preview?.model === model ? preview : null

  useEffect(() => {
    const audio = audioRef.current
    return () => {
      requestRef.current += 1
      if (audio) {
        audio.pause()
        audio.removeAttribute('src')
        audio.load()
      }
    }
  }, [model])

  const stopPreview = () => {
    requestRef.current += 1
    const audio = audioRef.current
    if (audio) {
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
    setPreview(null)
    setError(null)
  }

  const togglePreview = (voice: string) => {
    if (active?.voice === voice) {
      stopPreview()
      return
    }
    stopPreview()
    const audio = audioRef.current
    if (!audio) return
    const request = requestRef.current
    setPreview({ model, voice, state: 'loading' })
    audio.src = voicePreviewUrl(voice, model)
    void audio.play().then(() => {
      if (request === requestRef.current) setPreview({ model, voice, state: 'playing' })
    }).catch(() => {
      if (request !== requestRef.current) return
      setPreview(null)
      setError({ model, message: `Could not generate or play the ${voice} preview. Try again or check the TTS service settings.` })
    })
  }

  return {
    audioRef,
    playingVoice: active?.voice ?? null,
    loading: active?.state === 'loading',
    previewError: error?.model === model ? error.message : null,
    stopPreview,
    togglePreview,
  }
}
