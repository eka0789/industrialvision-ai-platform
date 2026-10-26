import { ArrowLeft, Download, RotateCcw } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { cameraApi, inspectionApi } from '../api'
import { InspectionDetail } from '../components/InspectionDetail'
import { Button, ErrorState, LoadingBlock, PageHeader } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { downloadJson, inspectionCode } from '../utils/format'

export default function InspectionDetailPage() {
  const id = Number(useParams().id)
  const resource = useApi(() => {
    if (!Number.isInteger(id) || id <= 0) return Promise.reject(new Error('Invalid inspection id'))
    return Promise.all([inspectionApi.get(id), cameraApi.list()])
  }, [id])
  const inspection = resource.data?.[0]
  const cameras = resource.data?.[1] ?? []

  return (
    <div className="page inspection-detail-page">
      <Link to="/inspections" className="back-link"><ArrowLeft size={16} />Kembali ke riwayat</Link>
      {resource.isLoading && <LoadingBlock label="Membuka bukti inspeksi…" />}
      {resource.error && <ErrorState message={resource.error} retry={resource.reload} />}
      {!resource.isLoading && !resource.error && !inspection && <ErrorState message="ID inspeksi tidak valid." />}
      {inspection && (
        <>
          <PageHeader eyebrow={`TRACEABILITY / ${inspectionCode(inspection.id)}`} title="Detail inspeksi."
            description="Satu catatan utuh: gambar asli, hasil model, dan keputusan akhir."
            actions={<><Button variant="secondary" onClick={resource.reload}><RotateCcw size={16} />Muat ulang</Button><Button variant="primary" onClick={() => downloadJson(inspection, `${inspectionCode(inspection.id).toLowerCase()}.json`)}><Download size={16} />Unduh data</Button></>} />
          <InspectionDetail inspection={inspection} cameraName={cameras.find((camera) => camera.id === inspection.camera_id)?.name} />
        </>
      )}
    </div>
  )
}
