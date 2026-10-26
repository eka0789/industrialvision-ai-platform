import { useEffect, useRef, useState } from 'react'
import type { DragEvent, FormEvent } from 'react'
import { ArrowLeft, ArrowRight, Check, FileImage, ImagePlus, RotateCcw, ScanSearch, Trash2, UploadCloud } from 'lucide-react'
import { Link } from 'react-router-dom'
import { cameraApi, inspectionApi, modelApi } from '../api'
import { InspectionDetail } from '../components/InspectionDetail'
import { Button, ErrorState, LoadingBlock, PageHeader } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { Inspection } from '../types'
import { cameraCode } from '../utils/format'
import { errorMessage } from '../utils/errors'

const MAX_FILE_BYTES = 100 * 1024 * 1024

export default function NewInspectionPage() {
  const { user } = useAuth()
  const toast = useToast()
  const fileInput = useRef<HTMLInputElement>(null)
  const resources = useApi(() => Promise.all([
    cameraApi.list().then((items) => Promise.all(items.map((camera) => cameraApi.get(camera.id)))),
    modelApi.list({ active: true }),
  ]), [])
  const cameras = resources.data?.[0].filter((camera) => camera.is_active) ?? []
  const models = resources.data?.[1].items.filter((model) => model.is_active) ?? []
  const [cameraId, setCameraId] = useState('')
  const [modelId, setModelId] = useState('')
  const [productId, setProductId] = useState('')
  const [batchId, setBatchId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string>()
  const [dragging, setDragging] = useState(false)
  const [formError, setFormError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<Inspection | null>(null)

  useEffect(() => {
    if (!previewUrl) return
    return () => URL.revokeObjectURL(previewUrl)
  }, [previewUrl])

  const selectedCameraId = cameras.some((camera) => String(camera.id) === cameraId) ? cameraId : String(cameras[0]?.id ?? '')
  const selectedModelId = models.some((model) => String(model.id) === modelId) ? modelId : String(models[0]?.id ?? '')

  const selectFile = (selected?: File) => {
    if (!selected) return
    setFormError('')
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(selected.type)) {
      setFormError('Gunakan gambar berformat JPEG, PNG, atau WebP.')
      return
    }
    if (selected.size > MAX_FILE_BYTES) {
      setFormError('Ukuran gambar melebihi batas unggah 100 MB.')
      return
    }
    setFile(selected)
    setPreviewUrl(URL.createObjectURL(selected))
    setResult(null)
  }

  const drop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    selectFile(event.dataTransfer.files[0])
  }

  const reset = () => {
    setFile(null)
    setPreviewUrl(undefined)
    setProductId('')
    setBatchId('')
    setResult(null)
    setFormError('')
    if (fileInput.current) fileInput.current.value = ''
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!file || !selectedCameraId || !selectedModelId) {
      setFormError('Pilih stasiun, model aktif, dan gambar terlebih dahulu.')
      return
    }
    setSubmitting(true)
    setFormError('')
    try {
      const inspection = await inspectionApi.inspect(file, Number(selectedCameraId), Number(selectedModelId), productId, batchId)
      setResult(inspection)
      if (inspection.decision === 'ERROR') toast('Inspeksi tersimpan, tetapi inferensi tidak berhasil.', 'warning')
      else toast(`Hasil ${inspection.decision} berhasil dicatat.`)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (reason) { setFormError(errorMessage(reason, 'Inspeksi tidak dapat diproses.')) }
    finally { setSubmitting(false) }
  }

  if (result) {
    return (
      <div className="page inspection-result-page">
        <Link to="/inspections/new" className="back-link" onClick={(event) => { event.preventDefault(); reset() }}><ArrowLeft size={16} />Inspeksi berikutnya</Link>
        <PageHeader eyebrow="INSPECTION / COMPLETE" title={result.decision === 'ERROR' ? 'Perlu pemeriksaan sistem.' : 'Hasil sudah tercatat.'}
          description="Keputusan, bukti visual, dan metadata tersimpan dalam satu jejak yang dapat ditelusuri."
          actions={<><Button variant="secondary" onClick={reset}><RotateCcw size={16} />Inspeksi baru</Button><Link to={`/inspections/${result.id}`} className="button button--primary">Buka catatan<ArrowRight size={16} /></Link></>} />
        <InspectionDetail inspection={result} cameraName={cameras.find((camera) => camera.id === result.camera_id)?.name} previewUrl={previewUrl} />
      </div>
    )
  }

  return (
    <div className="page new-inspection-page">
      <PageHeader eyebrow="INSPECTION / NEW RECORD" title={<>Dari gambar menjadi<br /><span className="text-muted">keputusan.</span></>}
        description="Uji satu sampel dengan model aktif. Hasil inferensi dan bukti visual akan tersimpan otomatis." />
      {resources.isLoading && <LoadingBlock label="Menyiapkan stasiun dan model aktif…" />}
      {resources.error && <ErrorState message={resources.error} retry={resources.reload} />}
      {!resources.isLoading && !resources.error && (
        <>
          <ol className="process-rail" aria-label="Tahapan inspeksi"><li className="process-step process-step--active"><span>01</span><div><strong>Konfigurasi</strong><small>Stasiun & model</small></div></li><li className={file ? 'process-step process-step--active' : 'process-step'}><span>02</span><div><strong>Bukti visual</strong><small>Unggah satu gambar</small></div></li><li className="process-step"><span>03</span><div><strong>Keputusan</strong><small>Inferensi & pencatatan</small></div></li></ol>
          {!cameras.length || !models.length ? (
            <div className="inline-notice inline-notice--warning setup-warning"><span><strong>Konfigurasi belum lengkap.</strong> {!cameras.length ? 'Belum ada stasiun aktif. ' : ''}{!models.length ? 'Belum ada model aktif.' : ''}</span>{user?.role === 'admin' ? <Link to={!cameras.length ? '/cameras' : '/models'}>Lengkapi konfigurasi<ArrowRight size={15} /></Link> : <span>Hubungi administrator atau AI engineer.</span>}</div>
          ) : null}
          <form className="inspection-workbench" onSubmit={submit}>
            <section className="inspection-config">
              <div className="section-heading"><span className="section-number">01</span><div><h2>Konfigurasi inspeksi</h2><p>Pilih sumber kerja dan model yang akan digunakan.</p></div></div>
              <div className="form-grid form-grid--two">
                <label className="field"><span>Stasiun aktif <b>*</b></span><select value={selectedCameraId} onChange={(event) => setCameraId(event.target.value)} required><option value="">Pilih stasiun</option>{cameras.map((camera) => <option key={camera.id} value={camera.id}>{cameraCode(camera.id)} — {camera.name}</option>)}</select></label>
                <label className="field"><span>Model aktif <b>*</b></span><select value={selectedModelId} onChange={(event) => setModelId(event.target.value)} required><option value="">Pilih model</option>{models.map((model) => <option key={model.id} value={model.id}>{model.name} / {model.version ?? model.framework_version ?? model.framework}</option>)}</select></label>
                <label className="field"><span>ID produk <small>opsional</small></span><input value={productId} onChange={(event) => setProductId(event.target.value)} maxLength={200} placeholder="cth. BRACKET-A104" /></label>
                <label className="field"><span>ID batch <small>opsional</small></span><input value={batchId} onChange={(event) => setBatchId(event.target.value)} maxLength={200} placeholder="cth. BATCH-2026-001" /></label>
              </div>
              <div className="config-note"><Check size={17} /><p>Hanya stasiun dan model berstatus aktif yang ditampilkan. Keputusan tetap perlu mengikuti validasi model Anda.</p></div>
            </section>
            <section className="upload-section">
              <div className="section-heading"><span className="section-number">02</span><div><h2>Bukti visual</h2><p>Gunakan gambar yang tajam dan pencahayaan konsisten.</p></div></div>
              {!file ? (
                <label className={`dropzone ${dragging ? 'dropzone--dragging' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={drop}>
                  <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => selectFile(event.target.files?.[0])} />
                  <span className="dropzone-icon"><UploadCloud size={30} strokeWidth={1.4} /></span>
                  <strong>Tarik gambar ke sini</strong><p>atau <span>pilih dari perangkat</span></p><small>JPEG, PNG, WebP · maksimum 100 MB</small>
                  <span className="dropzone-corner dropzone-corner--tl" /><span className="dropzone-corner dropzone-corner--br" />
                </label>
              ) : (
                <div className="upload-preview">
                  {previewUrl && <img src={previewUrl} alt="Pratinjau gambar yang akan diinspeksi" />}
                  <div className="upload-file-info"><FileImage size={21} /><div><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(2)} MB · siap diperiksa</span></div><Button type="button" variant="ghost" onClick={reset} aria-label="Hapus gambar"><Trash2 size={18} /></Button></div>
                </div>
              )}
            </section>
            <div className="inspection-submit-bar"><div><ImagePlus size={19} /><p>{file ? 'Gambar siap. Jalankan inferensi untuk membuat catatan.' : 'Lengkapi gambar untuk melanjutkan inspeksi.'}</p></div><Button type="submit" variant="lime" loading={submitting} disabled={!file || !selectedCameraId || !selectedModelId}>{submitting ? 'Menjalankan inferensi…' : 'Jalankan inspeksi'}<ScanSearch size={18} /></Button></div>
            {formError && <div className="form-error inspection-form-error" role="alert"><span>!</span>{formError}</div>}
          </form>
        </>
      )}
    </div>
  )
}
