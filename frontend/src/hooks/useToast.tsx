import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Check, CircleAlert, Info, X } from 'lucide-react'

interface Toast { id: number; message: string; tone: 'success' | 'warning' | 'info' }
const ToastContext = createContext<((message: string, tone?: Toast['tone']) => void) | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const dismiss = useCallback((id: number) => setToasts((items) => items.filter((item) => item.id !== id)), [])
  const notify = useCallback((message: string, tone: Toast['tone'] = 'success') => {
    const id = Date.now() + Math.random()
    setToasts((items) => [...items.slice(-2), { id, message, tone }])
    window.setTimeout(() => dismiss(id), 6000)
  }, [dismiss])
  const context = useMemo(() => notify, [notify])

  return (
    <ToastContext.Provider value={context}>
      {children}
      <div className="toast-region" aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => (
          <div className={`toast toast--${toast.tone}`} key={toast.id}>
            {toast.tone === 'success' ? <Check size={18} /> : toast.tone === 'warning' ? <CircleAlert size={18} /> : <Info size={18} />}
            <span>{toast.message}</span>
            <button className="icon-button" aria-label="Tutup notifikasi" onClick={() => dismiss(toast.id)}><X size={16} /></button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used within ToastProvider')
  return context
}
