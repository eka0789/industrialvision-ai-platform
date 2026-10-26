import type { Decision } from '../types'

export const decisionLabels: Record<Decision, string> = {
  PASS: 'Lulus',
  FAIL: 'Gagal',
  REVIEW: 'Tinjau',
  ERROR: 'Error',
}

export const sourceLabels: Record<string, string> = {
  upload: 'Unggah gambar',
  rtsp: 'RTSP',
  webcam: 'Webcam',
  local_file: 'File lokal',
  simulated: 'Simulasi',
}

export const taskLabels: Record<string, string> = {
  object_detection: 'Deteksi objek',
  anomaly_image: 'Anomali gambar',
  anomaly_segmentation: 'Segmentasi anomali',
}

export function formatDate(value: string | null | undefined, includeDate = true) {
  if (!value) return 'Belum ada'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('id-ID', {
    ...(includeDate ? { day: '2-digit', month: 'short', year: 'numeric' } : {}),
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function inspectionCode(id: number) {
  return `INS-${String(id).padStart(6, '0')}`
}

export function cameraCode(id: number) {
  return `ST-${String(id).padStart(2, '0')}`
}

export function formatLatency(value: number | null) {
  if (value === null || !Number.isFinite(value)) return '—'
  return value < 1000 ? `${value.toFixed(1)} ms` : `${(value / 1000).toFixed(2)} s`
}

export function formatPercent(value: number) {
  return `${(value * 100).toFixed(value > 0 && value < 0.01 ? 1 : 0)}%`
}

export function initials(value: string) {
  return value.split(/[\s_-]+/).filter(Boolean).slice(0, 2).map((word) => word[0]).join('').toUpperCase() || 'MV'
}

export function downloadJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
