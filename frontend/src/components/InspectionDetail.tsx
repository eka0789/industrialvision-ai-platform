import { CircleAlert, ScanLine } from 'lucide-react'
import type { Inspection } from '../types'
import { cameraCode, decisionLabels, formatDate, formatLatency, inspectionCode } from '../utils/format'
import { EvidenceImage } from './EvidenceImage'
import { StatusBadge } from './Ui'

export function InspectionDetail({ inspection, cameraName, previewUrl }: {
  inspection: Inspection; cameraName?: string; previewUrl?: string
}) {
  const isError = inspection.decision === 'ERROR'
  return (
    <div className="result-layout">
      <section className="evidence-panel" aria-label="Bukti dan deteksi">
        <EvidenceImage inspection={inspection} previewUrl={previewUrl} />
      </section>
      <section className={`result-summary result-summary--${inspection.decision.toLowerCase()}`} aria-label="Ringkasan hasil">
        <div className="result-summary-top"><span className="eyebrow">KEPUTUSAN SISTEM</span><ScanLine size={24} /></div>
        <div className="result-verdict">
          <span className="result-verdict-code">{isError ? 'ERR' : inspection.decision}</span>
          <h2>{isError ? 'Inferensi tidak selesai.' : `${decisionLabels[inspection.decision]}.`}</h2>
          <StatusBadge value={inspection.decision} />
        </div>
        <p className="result-reason">{inspection.decision_reason ?? (isError ? 'Hasil tidak dapat digunakan sebagai keputusan kualitas.' : 'Keputusan dihitung dari hasil inferensi dan aturan model.')}</p>
        {isError && inspection.error_message && <div className="inline-notice inline-notice--error"><CircleAlert size={17} /><span>{inspection.error_message}</span></div>}
        <dl className="data-list">
          <div><dt>ID inspeksi</dt><dd>{inspectionCode(inspection.id)}</dd></div>
          <div><dt>Waktu</dt><dd>{formatDate(inspection.timestamp)}</dd></div>
          <div><dt>Stasiun</dt><dd>{cameraName ?? cameraCode(inspection.camera_id)}</dd></div>
          <div><dt>Produk</dt><dd>{inspection.product_id ?? 'Tidak dicatat'}</dd></div>
          <div><dt>Batch</dt><dd>{inspection.batch_id ?? 'Tidak dicatat'}</dd></div>
          <div><dt>Model</dt><dd>{inspection.model_name ?? '—'} <small>{inspection.model_version}</small></dd></div>
          <div><dt>Latensi inferensi</dt><dd>{formatLatency(inspection.latency_ms)}</dd></div>
          <div><dt>Temuan</dt><dd>{inspection.detections.length} objek</dd></div>
        </dl>
        {inspection.anomaly?.score !== null && inspection.anomaly?.score !== undefined && (
          <div className="anomaly-note"><span>Skor anomali</span><strong>{inspection.anomaly.score.toFixed(4)}</strong><small>Skor mentah, bukan probabilitas.</small></div>
        )}
      </section>
    </div>
  )
}
