import { useEffect, useEffectEvent, useReducer, useState } from 'react'
import { errorMessage } from '../utils/errors'

interface ApiState<T> { data: T | null; error: string; isLoading: boolean }
type ApiAction<T> = { type: 'pending' } | { type: 'success'; data: T | null } | { type: 'error'; error: string }

function reducer<T>(_state: ApiState<T>, action: ApiAction<T>): ApiState<T> {
  if (action.type === 'pending') return { data: null, error: '', isLoading: true }
  if (action.type === 'success') return { data: action.data, error: '', isLoading: false }
  return { data: null, error: action.error, isLoading: false }
}

export function useApi<T>(fetcher: () => Promise<T>, dependencies: readonly unknown[] = []) {
  const [state, dispatch] = useReducer(reducer<T>, { data: null, error: '', isLoading: true })
  const [version, setVersion] = useState(0)
  const signature = JSON.stringify(dependencies)
  const load = useEffectEvent(async () => {
    dispatch({ type: 'pending' })
    try {
      const result = await fetcher()
      dispatch({ type: 'success', data: result })
    } catch (reason) {
      dispatch({ type: 'error', error: errorMessage(reason) })
    }
  })

  useEffect(() => {
    let active = true
    const request = async () => { if (active) await load() }
    void request()
    return () => { active = false }
  }, [signature, version])

  return {
    ...state,
    reload: () => setVersion((value) => value + 1),
    setData: (data: T | null) => dispatch({ type: 'success', data }),
  }
}
