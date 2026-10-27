import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { authApi } from '../api'
import { clearTokens, getAccessToken, saveTokens } from '../api/client'
import type { User } from '../types'

interface AuthContextValue {
  user: User | null
  isLoading: boolean
  sessionExpired: boolean
  login: (username: string, password: string, remember: boolean) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(Boolean(getAccessToken()))
  const [sessionExpired, setSessionExpired] = useState(false)

  useEffect(() => {
    let active = true
    if (!getAccessToken()) return
    authApi.me()
      .then((currentUser) => { if (active) setUser(currentUser) })
      .catch(() => { if (active) clearTokens() })
      .finally(() => { if (active) setIsLoading(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    const expire = () => {
      setUser(null)
      setIsLoading(false)
      setSessionExpired(true)
    }
    window.addEventListener('industrialvision:session-expired', expire)
    return () => window.removeEventListener('industrialvision:session-expired', expire)
  }, [])

  const value = useMemo<AuthContextValue>(() => ({
    user,
    isLoading,
    sessionExpired,
    login: async (username, password, remember) => {
      const response = await authApi.login(username, password)
      saveTokens(response.token.access_token, response.refresh_token, remember)
      setUser(response.user)
      setSessionExpired(false)
    },
    logout: async () => {
      try { await authApi.logout() } finally {
        clearTokens()
        setUser(null)
        setSessionExpired(false)
      }
    },
  }), [user, isLoading, sessionExpired])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}