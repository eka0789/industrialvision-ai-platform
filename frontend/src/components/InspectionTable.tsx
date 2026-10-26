import { ArrowUpRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { Camera, Inspection } from '../types'
import { cameraCode, formatDate, formatLatency, inspectionCode } from '../utils/format'
import { StatusBadge } from './Ui'

export function InspectionTable({ inspections, cameras = [], compact = false }: {
  inspections: Inspection[]; cameras?: Camera[]; compact?: boolean
}) {
  const cameraName = (id: number) => cameras.find((camera) => camera.id === id)?.name ?? cameraCode(id)
  return (
    <div className={`table-wrap ${compact ? 'table-wrap--compact' : ''}`}>
      <table className="data-table inspection-table">
        <thead><tr><th>Inspeksi</th><th>Waktu</th><th>Stasiun / produk</th>{!compact && <th>Model</th>}<th>Latensi</th><th>Keputusan</th><th><span className="sr-only">Buka</span></th></tr></thead>
        <tbody>
          {inspections.map((inspection) => (
            <tr key={inspection.id}>
              <td data-label="Inspeksi"><Link className="record-id" to={`/inspections/${inspection.id}`}>{inspectionCode(inspection.id)}</Link>{inspection.batch_id && <small>Batch {inspection.batch_id}</small>}</td>
              <td data-label="Waktu">{formatDate(inspection.timestamp, !compact)}</td>
              <td data-label="Stasiun / produk"><strong>{cameraName(inspection.camera_id)}</strong><small>{inspection.product_id ?? 'Produk tidak dicatat'}</small></td>
              {!compact && <td data-label="Model"><span>{inspection.model_name ?? '—'}</span><small>{inspection.model_version}</small></td>}
              <td data-label="Latensi" className="mono">{formatLatency(inspection.latency_ms)}</td>
              <td data-label="Keputusan"><StatusBadge value={inspection.decision} /></td>
              <td><Link className="icon-button row-action" to={`/inspections/${inspection.id}`} aria-label={`Buka ${inspectionCode(inspection.id)}`}><ArrowUpRight size={17} /></Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
