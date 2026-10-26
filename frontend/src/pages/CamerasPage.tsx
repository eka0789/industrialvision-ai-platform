import { useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, Cctv, History, Pencil, Plus, Power, Search, Unplug } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { cameraApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, Modal, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { Camera as CameraType, CameraInput } from '../types'
import { cameraCode, formatDate, sourceLabels } from '../utils/format'
import { errorMessage } from '../utils/errors'

export default function CamerasPage() {
  const { user } = useAuth()
  const toast = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialStation = Number(searchParams.get('station'))
  const [selectedId, setSelectedId] = useState<number | null>(Number.isInteger(initialStation) && initialStation > 0 ? initialStation : null)
  const [formCamera, setFormCamera] = useState<CameraType | null | undefined>(undefined)
  const [search, setSearch] = useState('')
  const resource = useApi(cameraApi.list)
  const cameras = resource.data?.filter((camera) => `${camera.name} ${camera.camera_identity ?? ''} ${cameraCode(camera.id)}`.toLowerCase().includes(search.toLowerCase())) ?? []
  const canAdmin = user?.role === 'admin'

  const closeDetail = () => { setSelectedId(null); setSearchParams({}, { replace: true }) }

  return (
    <div className="page cameras-page">
      <PageHeader eyebrow="FLEET / INSPECTION STATIONS" title={<>Setiap stasiun.<br /><span className="text-muted">Satu standar.</span></>}
        description="Kelola titik inspeksi, sumber gambar, dan status operasional dari satu tempat."
        actions={canAdmin ? <Button onClick={() => setFormCamera(null)}><Plus size={17} />Tambah stasiun</Button> : undefined} />
      <div className="toolbar"><label className="search-box"><Search size={18} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Cari nama atau kode stasiun…" aria-label="Cari stasiun" />{search && <kbd>{cameras.length}</kbd>}</label><span className="toolbar-meta">{resource.data?.length ?? 0} STASIUN TERDAFTAR</span></div>
      {resource.isLoading && <LoadingBlock label="Mengambil data stasiun…" />}
      {resource.error && <ErrorState message={resource.error} retry={resource.reload} />}
      {!resource.isLoading && !resource.error && !cameras.length && <EmptyState title={search ? 'Stasiun tidak ditemukan' : 'Belum ada stasiun kerja'} description={search ? 'Coba kata kunci lain atau hapus pencarian.' : canAdmin ? 'Buat stasiun dengan sumber unggah gambar untuk memulai inspeksi pertama.' : 'Administrator perlu menambahkan stasiun sebelum inspeksi dapat dijalankan.'} action={search ? <Button variant="secondary" onClick={() => setSearch('')}>Hapus pencarian</Button> : canAdmin ? <Button onClick={() => setFormCamera(null)}><Plus size={17} />Tambah stasiun</Button> : undefined} />}
      {!resource.isLoading && !resource.error && cameras.length > 0 && (
        <div className="camera-grid">
          {cameras.map((camera) => (
            <article className="camera-card" key={camera.id}>
              <div className="camera-card-top"><span className="camera-number">{String(camera.id).padStart(2, '0')}</span><StatusBadge value={camera.status} /></div>
              <div className="camera-icon"><Cctv size={28} strokeWidth={1.4} /><span>{cameraCode(camera.id)}</span></div>
              <h2>{camera.name}</h2><p className="camera-identity">{camera.camera_identity ?? 'Identitas belum dilengkapi'}</p>
              <dl className="camera-specs"><div><dt>Sumber</dt><dd>{sourceLabels[camera.source_type] ?? camera.source_type}</dd></div><div><dt>Resolusi</dt><dd>{camera.resolution ?? 'Tidak diatur'}</dd></div><div><dt>FPS input</dt><dd>{camera.fps_input ?? '—'}</dd></div><div><dt>Inspeksi terakhir</dt><dd>{formatDate(camera.last_inspection_at)}</dd></div></dl>
              <button className="camera-card-link" onClick={() => setSelectedId(camera.id)}>Lihat detail stasiun<ArrowRight size={17} /></button>
            </article>
          ))}
        </div>
      )}
      <p className="page-footnote"><span>*</span> Status koneksi pada API bersifat administratif. Pratinjau streaming langsung belum tersedia di antarmuka ini.</p>
      {selectedId && <CameraDetailModal id={selectedId} onClose={closeDetail} onChanged={resource.reload} onEdit={(camera) => { closeDetail(); setFormCamera(camera) }} canAdmin={canAdmin} canOperate={user?.role !== 'ai_engineer'} />}
      {formCamera !== undefined && <CameraFormModal camera={formCamera} onClose={() => setFormCamera(undefined)} onSaved={() => { setFormCamera(undefined); resource.reload(); toast(formCamera ? 'Perubahan stasiun disimpan.' : 'Stasiun baru berhasil ditambahkan.') }} />}
    </div>
  )
}

function CameraDetailModal({ id, onClose, onChanged, onEdit, canAdmin, canOperate }: {
  id: number; onClose: () => void; onChanged: () => void; onEdit: (camera: CameraType) => void; canAdmin: boolean; canOperate: boolean
}) {
  const resource = useApi(() => cameraApi.get(id), [id])
  const toast = useToast()
  const [changing, setChanging] = useState(false)
  const camera = resource.data
  const lifecycle = async () => {
    if (!camera) return
    setChanging(true)
    try {
      const updated = camera.status === 'connected' ? await cameraApi.stop(id) : await cameraApi.start(id)
      resource.setData(updated)
      onChanged()
      toast(updated.status === 'connected' ? 'Stasiun ditandai terhubung.' : 'Stasiun ditandai terputus.')
    } catch (reason) { toast(errorMessage(reason), 'warning') }
    finally { setChanging(false) }
  }
  return (
    <Modal title={camera?.name ?? 'Detail stasiun'} eyebrow={`STATION / ${cameraCode(id)}`} onClose={onClose}>
      {resource.isLoading && <LoadingBlock />}
      {resource.error && <ErrorState message={resource.error} retry={resource.reload} />}
      {camera && <div className="detail-modal-content">
        <div className="detail-status-row"><StatusBadge value={camera.status} /><StatusBadge value={camera.is_active ? 'active' : 'inactive'} /></div>
        {camera.last_error && <div className="inline-notice inline-notice--error">{camera.last_error}</div>}
        <dl className="data-list data-list--stacked"><div><dt>Kode stasiun</dt><dd>{cameraCode(camera.id)}</dd></div><div><dt>Identitas</dt><dd>{camera.camera_identity ?? '—'}</dd></div><div><dt>Jenis sumber</dt><dd>{sourceLabels[camera.source_type] ?? camera.source_type}</dd></div><div><dt>Lokasi sumber</dt><dd className="break-text">{camera.source_url ?? 'Unggah melalui browser'}</dd></div><div><dt>Resolusi / FPS</dt><dd>{camera.resolution ?? '—'} / {camera.fps_input ?? '—'}</dd></div><div><dt>Inspeksi terakhir</dt><dd>{formatDate(camera.last_inspection_at)}</dd></div></dl>
        <div className="modal-actions"><Link to="/inspections" className="button button--secondary"><History size={16} />Riwayat inspeksi</Link>{canOperate && camera.source_type !== 'upload' && <Button variant="secondary" loading={changing} onClick={lifecycle}>{camera.status === 'connected' ? <Unplug size={16} /> : <Power size={16} />}{camera.status === 'connected' ? 'Tandai terputus' : 'Tandai terhubung'}</Button>}{canAdmin && <Button onClick={() => onEdit(camera)}><Pencil size={16} />Edit stasiun</Button>}</div>
      </div>}
    </Modal>
  )
}

function CameraFormModal({ camera, onClose, onSaved }: { camera: CameraType | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<CameraInput>({ name: camera?.name ?? '', camera_identity: camera?.camera_identity ?? '', source_type: camera?.source_type ?? 'upload', source_url: camera?.source_url ?? '', resolution: camera?.resolution ?? '1920 × 1080', fps_input: camera?.fps_input ?? 25, is_active: camera?.is_active ?? true })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const update = (key: keyof CameraInput, value: string | number | boolean | null) => setForm((current) => ({ ...current, [key]: value }))
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true); setError('')
    const payload = { ...form, source_url: form.source_type === 'upload' || form.source_type === 'simulated' ? null : String(form.source_url ?? '').trim() || null, camera_identity: String(form.camera_identity ?? '').trim() || null, resolution: String(form.resolution ?? '').trim() || null }
    try { if (camera) await cameraApi.update(camera.id, payload); else await cameraApi.create(payload); onSaved() }
    catch (reason) { setError(errorMessage(reason, 'Stasiun tidak dapat disimpan.')); setSaving(false) }
  }
  return (
    <Modal title={camera ? 'Edit stasiun' : 'Stasiun baru'} eyebrow={camera ? `EDIT / ${cameraCode(camera.id)}` : 'FLEET / NEW STATION'} onClose={onClose}>
      <form onSubmit={submit} className="modal-form">
        <p className="form-intro">Untuk alur inspeksi dari browser, pilih sumber <strong>Unggah gambar</strong>.</p>
        <div className="form-grid form-grid--two"><label className="field field--full"><span>Nama stasiun <b>*</b></span><input required autoFocus maxLength={120} value={form.name} onChange={(event) => update('name', event.target.value)} placeholder="cth. Inspeksi bracket — Line 01" /></label>{!camera && <label className="field field--full"><span>Identitas kamera <small>opsional</small></span><input maxLength={255} value={form.camera_identity ?? ''} onChange={(event) => update('camera_identity', event.target.value)} placeholder="cth. CAM-L01-04" /></label>}<label className="field"><span>Jenis sumber</span><select value={form.source_type} onChange={(event) => update('source_type', event.target.value)}><option value="upload">Unggah gambar</option><option value="webcam">Webcam</option><option value="rtsp">RTSP</option><option value="local_file">File lokal di server</option><option value="simulated">Simulasi (webcam fallback)</option></select></label><label className="field"><span>FPS input</span><input type="number" min="0.1" max="240" step="0.1" value={form.fps_input ?? ''} onChange={(event) => update('fps_input', event.target.value ? Number(event.target.value) : null)} /></label>{!['upload', 'simulated'].includes(form.source_type) && <label className="field field--full"><span>{form.source_type === 'webcam' ? 'Indeks perangkat' : 'URL / lokasi sumber'}</span><input value={form.source_url ?? ''} onChange={(event) => update('source_url', event.target.value)} placeholder={form.source_type === 'rtsp' ? 'rtsp://host:554/stream' : form.source_type === 'webcam' ? '0' : '/data/samples/line-01.mp4'} /><small>Jangan sertakan kredensial pada URL RTSP.</small></label>}<label className="field"><span>Resolusi</span><input maxLength={40} value={form.resolution ?? ''} onChange={(event) => update('resolution', event.target.value)} /></label><label className="checkbox-field form-active"><input type="checkbox" checked={form.is_active ?? true} onChange={(event) => update('is_active', event.target.checked)} /><span>Aktif untuk inspeksi</span></label></div>
        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="modal-actions"><Button type="button" variant="ghost" onClick={onClose}>Batal</Button><Button type="submit" loading={saving}>{camera ? 'Simpan perubahan' : 'Tambahkan stasiun'}<ArrowRight size={16} /></Button></div>
      </form>
    </Modal>
  )
}
