import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Check, CircleAlert, Info, X } from 'lucide-react'
import { ToastContext } from '../contexts/toastContext'
import type { ToastTone } from '../contexts/toastContext'

interface Toast { id: number; message: string; tone: ToastTone }

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const dismiss = useCallback((id: number) => setToasts((items) => items.filter((item) => item.id !== id)), [])
  const notify = useCallback((message: string, tone: ToastTone = 'success') => {
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
            {toast.tone === 'success' ? <Check size={18} aria-hidden="true" /> : toast.tone === 'warning' ? <CircleAlert size={18} aria-hidden="true" /> : <Info size={18} aria-hidden="true" />}
            <span>{toast.message}</span>
            <button type="button" className="icon-button" aria-label="Tutup notifikasi" onClick={() => dismiss(toast.id)}><X size={16} aria-hidden="true" /></button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
