import axios from 'axios'

const ACCESS_TOKEN_KEY = 'industrialvision.access_token'
const REFRESH_TOKEN_KEY = 'industrialvision.refresh_token'

export function getAccessToken() {
  return sessionStorage.getItem(ACCESS_TOKEN_KEY)
}

export function getRefreshToken() {
  return sessionStorage.getItem(REFRESH_TOKEN_KEY)
}

export function saveTokens(accessToken: string, refreshToken: string, remember: boolean) {
  clearTokens()
  const storage = remember ? localStorage : sessionStorage
  storage.setItem(ACCESS_TOKEN_KEY, accessToken)
  storage.setItem(REFRESH_TOKEN_KEY, refreshToken)
}

export function clearTokens() {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY)
  sessionStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
}

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30_000,
  headers: { Accept: 'application/json' },
})

let isRefreshing = false
let failedQueue: Array<{
  resolve: (token: string) => void
  reject: (error: unknown) => void
}> = []

function processQueue(error: unknown, token: string | null = null) {
  failedQueue.forEach(({ resolve, reject }) => {
    if (error) reject(error)
    else resolve(token!)
  })
  failedQueue = []
}

api.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401 && !error.config?.url?.includes('/auth/')) {
      const originalRequest = error.config
      if (!originalRequest) return Promise.reject(error)

      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject })
        }).then((token) => {
          originalRequest.headers.Authorization = `Bearer ${token}`
          return api(originalRequest)
        }).catch((err) => Promise.reject(err))
      }

      isRefreshing = true
      const refreshToken = getRefreshToken()
      if (refreshToken) {
        try {
          const res = await api.post<{
            access_token: string
            refresh_token: string
            token_type: string
            role: string
            user_id: number
          }>('/auth/refresh', { refresh_token: refreshToken })
          const { access_token, refresh_token } = res.data
          saveTokens(access_token, refresh_token, false)
          processQueue(null, access_token)
          originalRequest.headers.Authorization = `Bearer ${access_token}`
          return api(originalRequest)
        } catch (err) {
          processQueue(err, null)
          clearTokens()
          window.dispatchEvent(new Event('industrialvision:session-expired'))
          return Promise.reject(err)
        } finally {
          isRefreshing = false
        }
      } else {
        clearTokens()
        window.dispatchEvent(new Event('industrialvision:session-expired'))
        return Promise.reject(error)
      }
    }
    return Promise.reject(error)
  },
)