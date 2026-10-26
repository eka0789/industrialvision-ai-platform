import { useEffect, useState } from 'react'
import { ImageOff, LoaderCircle } from 'lucide-react'
import { inspectionApi } from '../api'
import type { Inspection } from '../types'
import { formatPercent } from '../utils/format'

export function EvidenceImage({ inspection, previewUrl }: { inspection: Inspection; previewUrl?: string }) {
  const [imageUrl, setImageUrl] = useState(previewUrl ?? '')
  const [error, setError] = useState(() => !previewUrl && !inspection.evidence_path ? 'Bukti visual tidak tersimpan untuk inspeksi ini.' : '')
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null)

  useEffect(() => {
    if (previewUrl) return
    if (!inspection.evidence_path) return
    let active = true
    let objectUrl = ''
    inspectionApi.evidence(inspection.id)
      .then((blob) => {
        if (!active) return
        objectUrl = URL.createObjectURL(blob)
        setImageUrl(objectUrl)
      })
      .catch(() => { if (active) setError('Bukti visual tidak dapat dimuat.') })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [inspection.id, inspection.evidence_path, previewUrl])

  return (
    <div className="evidence-image">
      <div className="evidence-ruler" aria-hidden="true"><span>00</span><span>25</span><span>50</span><span>75</span><span>100</span></div>
      <div className="evidence-canvas">
        {!imageUrl && !error && <div className="evidence-placeholder" role="status"><LoaderCircle className="spin" size={23} /><span>Memuat bukti visual</span></div>}
        {error && <div className="evidence-placeholder"><ImageOff size={28} /><span>{error}</span></div>}
        {imageUrl && (
          <div className="evidence-frame">
            <img src={imageUrl} alt={`Bukti visual inspeksi ${inspection.id}`}
              onLoad={(event) => setDimensions({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} />
            {dimensions && (
              <svg className="detection-overlay" viewBox={`0 0 ${dimensions.width} ${dimensions.height}`} aria-label={`${inspection.detections.length} deteksi pada gambar`}>
                {inspection.detections.map((detection, index) => detection.bbox && (
                  <g key={`${detection.label}-${index}`}>
                    <rect x={detection.bbox.x1} y={detection.bbox.y1}
                      width={Math.max(0, detection.bbox.x2 - detection.bbox.x1)}
                      height={Math.max(0, detection.bbox.y2 - detection.bbox.y1)}
                      vectorEffect="non-scaling-stroke" />
                  </g>
                ))}
              </svg>
            )}
            <span className="viewfinder viewfinder--tl" aria-hidden="true" /><span className="viewfinder viewfinder--tr" aria-hidden="true" />
            <span className="viewfinder viewfinder--bl" aria-hidden="true" /><span className="viewfinder viewfinder--br" aria-hidden="true" />
            <span className="evidence-coordinate">XY / {dimensions?.width ?? '—'} × {dimensions?.height ?? '—'}</span>
          </div>
        )}
      </div>
      <div className="evidence-caption">
        <span>BUKTI VISUAL / {String(inspection.id).padStart(4, '0')}</span>
        <span>{inspection.detections.length ? `${inspection.detections.length} area terdeteksi` : 'Tanpa area terdeteksi'}</span>
      </div>
      {inspection.detections.length > 0 && (
        <ul className="detection-list" aria-label="Daftar deteksi">
          {inspection.detections.map((detection, index) => (
            <li key={`${detection.label}-${index}`}><span className="detection-index">{String(index + 1).padStart(2, '0')}</span><strong>{detection.label}</strong><span>{formatPercent(detection.confidence)} confidence</span></li>
          ))}
        </ul>
      )}
    </div>
  )
}
