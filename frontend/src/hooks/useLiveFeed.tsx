import { useContext } from 'react'
import { LiveFeedContext } from '../contexts/liveFeedContext'

export type { LiveStatus } from '../contexts/liveFeedContext'

export function useLiveFeed() {
  const context = useContext(LiveFeedContext)
  if (!context) throw new Error('useLiveFeed must be used within LiveFeedProvider')
  return context
}
