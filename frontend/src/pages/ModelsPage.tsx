import { useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, Box, Check, CirclePause, Code2, Plus, Search } from 'lucide-react'
import { modelApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, Modal, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { ModelInput, ModelVersion } from '../types'
import { formatDate, initials, taskLabels } from '../utils/format'
import { errorMessage } from '../utils/errors'

export default function ModelsPage() {
  const { user } = useAuth()
  const toast = useToast()
  const [search, setSearch] = useState('')
  const [task, setTask] = useState('')
  const [selected, setSelected] = useState<ModelVersion | null>(null)
  const [creating, setCreating] = useState(false)
  const [changingId, setChangingId] = useState<number | null>(null)
  const resource = useApi(() => modelApi.list({ task_type: task || undefined }), [task])
  const canManage = user?.role === 'admin' || user?.role === 'ai_engineer'
  const models = resource.data?.items.filter((model) => model.name.toLowerCase().includes(search.toLowerCase())) ?? []
  const toggle = async (model: ModelVersion) => {
    setChangingId(model.id)
    try {
      const updated = model.is_active ? await modelApi.deactivate(model.id) : await modelApi.activate(model.id)
      resource.setData(resource.data ? { ...resource.data, items: resource.data.items.map((item) => item.id === updated.id ? updated : item.task_type === updated.task_type ? { ...item, is_active: false } : item) } : null)
      setSelected((current) => current?.id === updated.id ? updated : current)
      toast(updated.is_active ? `${model.name} aktif untuk inferensi.` : `${model.name} dinonaktifkan.`)
    } catch (reason) { toast(errorMessage(reason), 'warning') }
    finally { setChangingId(null) }
  }

  return (
    <div className="page models-page">
      <PageHeader eyebrow="AI / MODEL REGISTRY" title={<>Model yang tepat.<br /><span className="text-muted">Asal yang jelas.</span></>}
        description="Kelola versi, artefak, kelas deteksi, dan status penggunaan model secara transparan."
        actions={canManage ? <Button onClick={() => setCreating(true)}><Plus size={17} />Registrasi model</Button> : undefined} />
      <div className="toolbar"><label className="search-box"><Search size={18} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Cari nama model…" aria-label="Cari model" /></label><label className="compact-filter"><span>Tugas</span><select value={task} onChange={(event) => setTask(event.target.value)}><option value="">Semua tugas</option><option value="object_detection">Deteksi objek</option><option value="anomaly_image">Anomali gambar</option><option value="anomaly_segmentation">Segmentasi anomali</option></select></label></div>
      {resource.isLoading && <LoadingBlock label="Membaca registry model…" />}
      {resource.error && <ErrorState message={resource.error} retry={resource.reload} />}
      {!resource.isLoading && !resource.error && !models.length && <EmptyState title={search || task ? 'Model tidak ditemukan' : 'Registry masih kosong'} description={search || task ? 'Ubah pencarian atau filter tugas.' : canManage ? 'Registrasikan artefak yang sudah tersedia di MODEL_ROOT server.' : 'AI engineer atau administrator perlu mendaftarkan model terlebih dahulu.'} action={canManage && !search && !task ? <Button onClick={() => setCreating(true)}><Plus size={17} />Registrasi model</Button> : undefined} />}
      {!resource.isLoading && !resource.error && models.length > 0 && <div className="model-grid">{models.map((model) => (
        <article className={`model-card ${model.is_active ? 'model-card--active' : ''}`} key={model.id}>
          <div className="model-card-top"><span className="model-monogram" aria-hidden="true">{initials(model.name)}</span><StatusBadge value={model.is_active ? 'active' : 'inactive'} /></div>
          <p className="eyebrow">{model.framework.toUpperCase()} / {model.device.toUpperCase()}</p><h2>{model.name}</h2><p className="model-version">VERSI {model.version ?? model.framework_version ?? '—'}</p>
          <p className="model-description">{model.description ?? 'Belum ada deskripsi untuk versi model ini.'}</p>
          <div className="model-tags"><span>{taskLabels[model.task_type ?? ''] ?? model.task_type ?? 'Tugas belum diatur'}</span><span>{model.classes?.length ?? 0} kelas</span></div>
          <footer className="model-card-actions"><button className="text-link" onClick={() => setSelected(model)}>Detail & evaluasi<ArrowRight size={16} /></button>{canManage && <Button variant={model.is_active ? 'secondary' : 'primary'} loading={changingId === model.id} onClick={() => toggle(model)}>{model.is_active ? <CirclePause size={15} /> : <Check size={15} />}{model.is_active ? 'Nonaktifkan' : 'Aktifkan'}</Button>}</footer>
        </article>
      ))}</div>}
      <p className="page-footnote"><span>*</span> Status aktif menunjukkan ketersediaan untuk inferensi, bukan validasi performa produksi.</p>
      {selected && <ModelDetailModal model={selected} onClose={() => setSelected(null)} onToggle={() => toggle(selected)} canManage={canManage} changing={changingId === selected.id} />}
      {creating && <ModelFormModal onClose={() => setCreating(false)} onSaved={() => { setCreating(false); resource.reload(); toast('Model berhasil diregistrasi.') }} />}
    </div>
  )
}

function ModelDetailModal({ model, onClose, onToggle, canManage, changing }: { model: ModelVersion; onClose: () => void; onToggle: () => void; canManage: boolean; changing: boolean }) {
  const evaluations = useApi(() => modelApi.evaluations(model.id), [model.id])
  return (
    <Modal title={model.name} eyebrow={`MODEL / MV-${String(model.id).padStart(3, '0')}`} onClose={onClose} wide>
      <div className="model-detail">
        <div className="model-detail-intro"><div className="model-monogram model-monogram--large">{initials(model.name)}</div><div><StatusBadge value={model.is_active ? 'active' : 'inactive'} /><p>{model.description ?? 'Tidak ada deskripsi.'}</p></div></div>
        <dl className="data-list data-list--columns"><div><dt>Versi</dt><dd>{model.version ?? model.framework_version ?? '—'}</dd></div><div><dt>Framework / perangkat</dt><dd>{model.framework} / {model.device}</dd></div><div><dt>Tugas</dt><dd>{taskLabels[model.task_type ?? ''] ?? model.task_type}</dd></div><div><dt>Lisensi</dt><dd>{model.license ?? '—'}</dd></div><div><dt>Artefak (relatif)</dt><dd className="break-text">{model.source_path ?? '—'}</dd></div><div><dt>Checksum</dt><dd className="break-text mono checksum">{model.checksum ?? '—'}</dd></div></dl>
        <section className="detail-section"><h3><Box size={17} />Kelas & ambang</h3><div className="class-chips">{model.classes?.map((name) => <span key={name}>{name}</span>) ?? <span>Belum ada kelas terdaftar</span>}</div><pre className="code-block">{JSON.stringify({ thresholds: model.thresholds, config: model.config_json }, null, 2)}</pre></section>
        <section className="detail-section"><h3><Code2 size={17} />Evaluasi tercatat <span className="count-chip">{evaluations.data?.total ?? '—'}</span></h3>{evaluations.isLoading && <LoadingBlock label="Memuat evaluasi…" />}{evaluations.error && <p className="muted-text">{evaluations.error}</p>}{!evaluations.isLoading && !evaluations.data?.items.length && <p className="muted-text">Belum ada evaluasi. Aktivasi model bukan bukti kesiapan produksi.</p>}{evaluations.data?.items.map((evaluation) => <div className="evaluation-card" key={evaluation.id}><span>{formatDate(evaluation.measured_at)}</span><strong>{evaluation.task_type}</strong><pre>{JSON.stringify(evaluation.metrics_json, null, 2)}</pre></div>)}</section>
        <div className="modal-actions"><Button variant="ghost" onClick={onClose}>Tutup</Button>{canManage && <Button variant={model.is_active ? 'secondary' : 'primary'} onClick={onToggle} loading={changing}>{model.is_active ? 'Nonaktifkan model' : 'Aktifkan model'}</Button>}</div>
      </div>
    </Modal>
  )
}

function ModelFormModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [version, setVersion] = useState('1.0.0')
  const [description, setDescription] = useState('')
  const [framework, setFramework] = useState('onnx')
  const [taskType, setTaskType] = useState('object_detection')
  const [sourcePath, setSourcePath] = useState('')
  const [license, setLicense] = useState('Proprietary / internal')
  const [classes, setClasses] = useState('')
  const [device, setDevice] = useState('cpu')
  const [config, setConfig] = useState('')
  const [active, setActive] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError('')
    let parsed: Record<string, unknown> | undefined
    try {
      if (config.trim()) {
        const value: unknown = JSON.parse(config)
        if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Invalid config')
        parsed = value as Record<string, unknown>
      }
    } catch { setError('Konfigurasi inferensi harus berupa objek JSON yang valid.'); return }
    const classList = classes.split(',').map((value) => value.trim()).filter(Boolean)
    if (parsed && !parsed.class_names) parsed.class_names = classList
    const payload: ModelInput = { name: name.trim(), description: description.trim(), framework, framework_version: version.trim(), task_type: taskType, source_path: sourcePath.trim(), version: version.trim(), license: license.trim(), classes: classList, device, config_json: parsed, is_active: active }
    setSaving(true)
    try { await modelApi.create(payload); onSaved() }
    catch (reason) { setError(errorMessage(reason, 'Model tidak dapat diregistrasi.')); setSaving(false) }
  }
  return (
    <Modal title="Registrasi model" eyebrow="AI / NEW MODEL VERSION" onClose={onClose} wide>
      <form className="modal-form" onSubmit={submit}>
        <div className="inline-notice"><Box size={18} /><p>Artefak harus <strong>sudah tersedia di MODEL_ROOT server</strong>. Form ini mendaftarkan metadata, bukan mengunggah file model.</p></div>
        <div className="form-grid form-grid--two"><label className="field"><span>Nama model <b>*</b></span><input required autoFocus maxLength={200} value={name} onChange={(event) => setName(event.target.value)} placeholder="cth. Surface defect detector" /></label><label className="field"><span>Versi model <b>*</b></span><input required maxLength={50} value={version} onChange={(event) => setVersion(event.target.value)} /></label><label className="field field--full"><span>Deskripsi</span><textarea rows={2} maxLength={1000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Tujuan, cakupan, dan batas penggunaan model…" /></label><label className="field"><span>Framework</span><select value={framework} onChange={(event) => setFramework(event.target.value)}><option value="onnx">ONNX</option><option value="onnxruntime">ONNX Runtime</option><option value="yolo">YOLO</option><option value="ultralytics">Ultralytics</option></select></label><label className="field"><span>Jenis tugas</span><select value={taskType} onChange={(event) => setTaskType(event.target.value)}><option value="object_detection">Deteksi objek</option><option value="anomaly_image">Anomali gambar</option><option value="anomaly_segmentation">Segmentasi anomali</option></select></label><label className="field field--full"><span>Referensi artefak <b>*</b></span><input required maxLength={500} value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="detector-v1.onnx" /><small>Relatif terhadap MODEL_ROOT. Server akan memvalidasi dan menghitung checksum.</small></label><label className="field"><span>Lisensi <b>*</b></span><input required maxLength={200} value={license} onChange={(event) => setLicense(event.target.value)} /></label><label className="field"><span>Perangkat</span><select value={device} onChange={(event) => setDevice(event.target.value)}><option value="cpu">CPU</option><option value="cuda">CUDA</option></select></label><label className="field field--full"><span>Nama kelas <small>pisahkan dengan koma</small></span><input value={classes} onChange={(event) => setClasses(event.target.value)} placeholder="scratch, dent, discoloration" /></label><label className="field field--full"><span>Konfigurasi inferensi <small>JSON, opsional</small></span><textarea className="code-input" rows={5} value={config} onChange={(event) => setConfig(event.target.value)} placeholder={'{"output_format":"xyxy_conf_class_nx6", "confidence_threshold":0.25}'} /><small>Harus sesuai artefak dan kebijakan keputusan backend. Validasi akhir dilakukan saat inferensi.</small></label><label className="checkbox-field field--full"><input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} /><span>Aktifkan setelah registrasi <small>model aktif lain untuk tugas yang sama akan dinonaktifkan</small></span></label></div>
        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="modal-actions"><Button type="button" variant="ghost" onClick={onClose}>Batal</Button><Button type="submit" loading={saving}>Registrasi model<ArrowRight size={16} /></Button></div>
      </form>
    </Modal>
  )
}
