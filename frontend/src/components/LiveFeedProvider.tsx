import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { getAccessToken } from '../api/client'
import { LiveFeedContext } from '../contexts/liveFeedContext'
import type { InspectionHandler, LiveFeedContextValue, LiveStatus } from '../contexts/liveFeedContext'
import { useAuth } from '../hooks/useAuth'
import type { Inspection } from '../types'

interface InspectionCreatedMessage {
  type?: string
  inspection?: Inspection
}

function resolveSocketUrl(): string | null {
  const override = import.meta.env.VITE_WS_URL as string | undefined
  const base = override
    ? override.replace(/\/+$/, '')
    : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
  return `${base}/ws/inspections/live`
}

// The JWT travels in the Sec-WebSocket-Protocol header rather than the URL, so
// reverse-proxy and browser-history logs never contain a bearer credential.
function socketProtocols(): string[] | null {
  const token = getAccessToken()
  return token ? [`bearer.${token}`] : null
}

export function LiveFeedProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const userId = user?.id ?? null
  const [status, setStatus] = useState<LiveStatus>('idle')
  const [lastEventAt, setLastEventAt] = useState<number | null>(null)
  const handlersRef = useRef<Set<InspectionHandler>>(new Set())
  const socketRef = useRef<WebSocket | null>(null)
  const attemptRef = useRef(0)
  const timerRef = useRef<number | undefined>(undefined)

  const onInspection = useCallback((handler: InspectionHandler) => {
    handlersRef.current.add(handler)
    return () => { handlersRef.current.delete(handler) }
  }, [])

  useEffect(() => {
    if (userId === null) return

    let disposed = false

    const scheduleReconnect = (connect: () => void) => {
      if (disposed) return
      const delay = Math.min(30_000, 1_500 * 2 ** Math.min(attemptRef.current, 4))
      attemptRef.current += 1
      window.clearTimeout(timerRef.current)
      timerRef.current = window.setTimeout(connect, delay)
    }

    const connect = () => {
      if (disposed) return
      const url = resolveSocketUrl()
      const protocols = socketProtocols()
      if (!url || !protocols) {
        setStatus('idle')
        return
      }
      setStatus('connecting')
      let socket: WebSocket
      try {
        socket = new WebSocket(url, protocols)
      } catch {
        setStatus('offline')
        scheduleReconnect(connect)
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        attemptRef.current = 0
        setStatus('live')
      }
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as InspectionCreatedMessage
          if (payload.type === 'inspection.created' && payload.inspection) {
            setLastEventAt(Date.now())
            handlersRef.current.forEach((handler) => handler(payload.inspection as Inspection))
          }
        } catch {
          // Malformed frames are ignored; the heartbeat keeps the socket warm.
        }
      }
      socket.onerror = () => {
        try { socket.close() } catch { /* already closing */ }
      }
      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null
        if (disposed) return
        setStatus('offline')
        scheduleReconnect(connect)
      }
    }

    timerRef.current = window.setTimeout(connect, 0)

    return () => {
      disposed = true
      window.clearTimeout(timerRef.current)
      const socket = socketRef.current
      socketRef.current = null
      if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
        socket.close()
      }
    }
  }, [userId])

  const effectiveStatus: LiveStatus = userId === null ? 'idle' : status

  const value = useMemo<LiveFeedContextValue>(
    () => ({ status: effectiveStatus, lastEventAt, onInspection }),
    [effectiveStatus, lastEventAt, onInspection],
  )

  return <LiveFeedContext.Provider value={value}>{children}</LiveFeedContext.Provider>
}
