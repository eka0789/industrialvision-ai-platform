import { createContext } from 'react'

export type ToastTone = 'success' | 'warning' | 'info'

export const ToastContext = createContext<((message: string, tone?: ToastTone) => void) | null>(null)
