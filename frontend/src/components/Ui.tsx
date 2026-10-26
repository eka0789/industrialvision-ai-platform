import { useEffect, useId, useRef } from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { ArrowUpRight, CircleAlert, Inbox, LoaderCircle, RotateCcw, X } from 'lucide-react'
import type { Decision } from '../types'
import { decisionLabels } from '../utils/format'

export function Button({ children, variant = 'primary', loading = false, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'lime'
  loading?: boolean
}) {
  return (
    <button className={`button button--${variant} ${className}`} {...props} disabled={loading || props.disabled}>
      {loading && <LoaderCircle className="spin" size={16} aria-hidden="true" />}
      {children}
    </button>
  )
}

export function StatusBadge({ value }: { value: Decision | string }) {
  const key = value.toLowerCase()
  const labels: Record<string, string> = {
    connected: 'Terhubung', disconnected: 'Terputus', degraded: 'Terganggu',
    active: 'Aktif', inactive: 'Nonaktif', loaded: 'Termuat', unloaded: 'Belum dimuat',
  }
  return <span className={`status-badge status-badge--${key}`}><i aria-hidden="true" />{decisionLabels[value as Decision] ?? labels[key] ?? value}</span>
}

export function PageHeader({ eyebrow, title, description, actions }: {
  eyebrow: string; title: ReactNode; description?: string; actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow"><span aria-hidden="true" />{eyebrow}</p>
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  )
}

export function LoadingBlock({ label = 'Mengambil data dari server…' }: { label?: string }) {
  return <div className="state-panel" role="status"><LoaderCircle className="spin" size={24} /><p>{label}</p><span className="loading-line" /></div>
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="state-panel state-panel--error" role="alert">
      <CircleAlert size={26} />
      <h2>Data belum dapat ditampilkan</h2>
      <p>{message}</p>
      {retry && <Button variant="secondary" onClick={retry}><RotateCcw size={16} />Coba lagi</Button>}
    </div>
  )
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="state-panel">
      <Inbox size={28} strokeWidth={1.5} />
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  )
}

export function Modal({ title, eyebrow, onClose, children, wide = false }: {
  title: string; eyebrow?: string; onClose: () => void; children: ReactNode; wide?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    const overflow = document.body.style.overflow
    dialog.showModal()
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = overflow }
  }, [])

  return (
    <dialog ref={ref} className={`modal ${wide ? 'modal--wide' : ''}`} aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); onClose() }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="modal-inner">
        <header className="modal-header">
          <div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2 id={titleId}>{title}</h2></div>
          <button type="button" className="icon-button" aria-label="Tutup dialog" onClick={onClose}><X size={21} /></button>
        </header>
        {children}
      </div>
    </dialog>
  )
}

export function TextLink({ children }: { children: ReactNode }) {
  return <span className="text-link">{children}<ArrowUpRight size={15} /></span>
}
