import { createContext } from 'react'
import type { Inspection } from '../types'

export type LiveStatus = 'idle' | 'connecting' | 'live' | 'offline'

export type InspectionHandler = (inspection: Inspection) => void

export interface LiveFeedContextValue {
  status: LiveStatus
  lastEventAt: number | null
  onInspection: (handler: InspectionHandler) => () => void
}

export const LiveFeedContext = createContext<LiveFeedContextValue | null>(null)
