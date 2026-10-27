import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { getAccessToken } from '../api/client'
import { useAuth } from './useAuth'
import type { Inspection } from '../types'

export type LiveStatus = 'idle' | 'connecting' | 'live' | 'offline'

type InspectionHandler = (inspection: Inspection) => void

interface LiveFeedContextValue {
  status: LiveStatus
  lastEventAt: number | null
  onInspection: (handler: InspectionHandler) => () => void
}

const LiveFeedContext = createContext<LiveFeedContextValue | null>(null)

interface InspectionCreatedMessage {
  type?: string
  inspection?: Inspection
}

function resolveSocketUrl(): string | null {
  const token = getAccessToken()
  if (!token) return null
  const override = import.meta.env.VITE_WS_URL as string | undefined
  const base = override
    ? override.replace(/\/+$/, '')
    : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
  return `${base}/ws/inspections/live?token=${encodeURIComponent(token)}`
}

export function LiveFeedProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
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
      if (!url) {
        setStatus('idle')
        return
      }
      setStatus('connecting')
      let socket: WebSocket
      try {
        socket = new WebSocket(url)
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

    if (user) connect()
    else setStatus('idle')

    return () => {
      disposed = true
      window.clearTimeout(timerRef.current)
      const socket = socketRef.current
      socketRef.current = null
      if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
        socket.close()
      }
      setStatus('idle')
    }
  }, [user?.id])

  const value = useMemo<LiveFeedContextValue>(
    () => ({ status, lastEventAt, onInspection }),
    [status, lastEventAt, onInspection],
  )

  return <LiveFeedContext.Provider value={value}>{children}</LiveFeedContext.Provider>
}

export function useLiveFeed() {
  const context = useContext(LiveFeedContext)
  if (!context) throw new Error('useLiveFeed must be used within LiveFeedProvider')
  return context
}
