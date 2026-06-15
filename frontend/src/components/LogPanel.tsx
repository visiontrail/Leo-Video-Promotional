import { useEffect, useRef, useState } from 'react'
import type { Task } from '../api'
import { logsStreamUrl } from '../api'

type TaskStatus = Task['status']

interface LogPanelProps {
  taskId: string
  taskStatus: TaskStatus
}

const FINAL_STATUSES: TaskStatus[] = ['complete', 'failed', 'awaiting_review']

export default function LogPanel({ taskId, taskStatus }: LogPanelProps) {
  const [expanded, setExpanded] = useState(true)
  const [lines, setLines] = useState<string[]>([])
  const [streamStatus, setStreamStatus] = useState<TaskStatus | null>(null)
  const [connectionState, setConnectionState] = useState<'connecting' | 'live' | 'closed' | 'error'>('connecting')
  const scrollRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const source = new EventSource(logsStreamUrl(taskId))

    source.addEventListener('open', () => {
      setLines([])
      setStreamStatus(null)
      setConnectionState('live')
    })

    source.addEventListener('log', (event) => {
      setLines((current) => [...current, event.data])
    })

    source.addEventListener('status', (event) => {
      const status = event.data as TaskStatus
      setStreamStatus(status)
      setConnectionState('closed')
      source.close()
    })

    source.addEventListener('ping', () => {
      setConnectionState('live')
    })

    source.addEventListener('error', () => {
      if (FINAL_STATUSES.includes(taskStatus)) {
        setConnectionState('closed')
        source.close()
        return
      }
      setConnectionState('error')
    })

    return () => {
      source.close()
    }
  }, [taskId, taskStatus])

  useEffect(() => {
    if (!expanded) return
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [expanded, lines])

  const effectiveStatus = streamStatus ?? taskStatus
  const stateLabel =
    connectionState === 'live' && !FINAL_STATUSES.includes(effectiveStatus)
      ? 'Live'
      : connectionState === 'connecting'
        ? 'Connecting'
        : connectionState === 'error'
          ? 'Reconnecting'
          : effectiveStatus

  return (
    <section className="log-panel">
      <button
        type="button"
        className="log-panel-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span>{expanded ? '-' : '+'}</span>
        <strong>Pipeline Logs</strong>
        <em>{stateLabel}</em>
      </button>

      {expanded && (
        <div className="log-panel-body" ref={scrollRef}>
          {lines.length ? (
            lines.map((line, index) => (
              <div className="log-line" key={`${index}-${line}`}>
                {line}
              </div>
            ))
          ) : (
            <div className="log-empty">No logs yet.</div>
          )}
        </div>
      )}
    </section>
  )
}
