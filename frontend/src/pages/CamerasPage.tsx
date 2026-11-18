import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, Cctv, History, Layers, Pencil, Plus, Power, Search, Unplug, Zap } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { cameraApi, otApi, recipeApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, Modal, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useLiveFeed } from '../hooks/useLiveFeed'
import { useToast } from '../hooks/useToast'
import type { Camera as CameraType, CameraInput, OTTriggerResult } from '../types'
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
  const { status: liveStatus, onInspection } = useLiveFeed()
  const resourceRef = useRef(resource)
  useEffect(() => { resourceRef.current = resource })

  // Status operasional stasiun menyegarkan dirinya: refresh berkala plus
  // pembaruan langsung setiap kali event inspeksi masuk lewat feed WS.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') resourceRef.current.reload()
    }, 15_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => onInspection((inspection) => {
    const current = resourceRef.current.data
    if (!current) return
    resourceRef.current.setData(current.map((camera) => camera.id === inspection.camera_id
      ? { ...camera, status: 'connected', last_inspection_at: inspection.timestamp }
      : camera))
  }), [onInspection])

  const cameras = resource.data?.filter((camera) => `${camera.name} ${camera.camera_identity ?? ''} ${cameraCode(camera.id)}`.toLowerCase().includes(search.toLowerCase())) ?? []
  const canAdmin = user?.role === 'admin'

  const closeDetail = () => { setSelectedId(null); setSearchParams({}, { replace: true }) }

  return (
    <div className="page cameras-page">
      <PageHeader eyebrow="FLEET / INSPECTION STATIONS" title={<>Setiap stasiun.<br /><span className="text-muted">Satu standar.</span></>}
        description="Kelola titik inspeksi, sumber gambar, dan status operasional dari satu tempat."
        actions={canAdmin ? <Button onClick={() => setFormCamera(null)}><Plus size={17} />Tambah stasiun</Button> : undefined} />
      <div className="toolbar"><label className="search-box"><Search size={18} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Cari nama atau kode stasiun…" aria-label="Cari stasiun" />{search && <kbd>{cameras.length}</kbd>}</label><span className="toolbar-meta">{liveStatus === 'live' && <span className="live-chip"><i aria-hidden="true" />LIVE</span>}{resource.data?.length ?? 0} STASIUN TERDAFTAR</span></div>
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
      <p className="page-footnote"><span>*</span> Status stasiun diperbarui otomatis setiap ada inspeksi baru masuk. Pratinjau streaming video langsung belum tersedia di antarmuka ini.</p>
      {selectedId && <CameraDetailModal id={selectedId} onClose={closeDetail} onChanged={resource.reload} onEdit={(camera) => { closeDetail(); setFormCamera(camera) }} canAdmin={canAdmin} canConfigureOt={user?.role === 'owner' || user?.role === 'admin' || user?.role === 'ai_engineer'} canOperate={user?.role !== 'ai_engineer'} />}
      {formCamera !== undefined && <CameraFormModal camera={formCamera} onClose={() => setFormCamera(undefined)} onSaved={() => { setFormCamera(undefined); resource.reload(); toast(formCamera ? 'Perubahan stasiun disimpan.' : 'Stasiun baru berhasil ditambahkan.') }} />}
    </div>
  )
}

function CameraDetailModal({ id, onClose, onChanged, onEdit, canAdmin, canConfigureOt, canOperate }: {
  id: number; onClose: () => void; onChanged: () => void; onEdit: (camera: CameraType) => void; canAdmin: boolean; canConfigureOt: boolean; canOperate: boolean
}) {
  const resource = useApi(() => cameraApi.get(id), [id])
  const recipes = useApi(() => recipeApi.list(), [id])
  const toast = useToast()
  const [changing, setChanging] = useState(false)
  const [triggering, setTriggering] = useState(false)
  const [otResult, setOtResult] = useState<OTTriggerResult | null>(null)
  const [newSku, setNewSku] = useState('')
  const [newSkuName, setNewSkuName] = useState('')
  const camera = resource.data
  const activeRecipe = (recipes.data ?? []).find((r) => r.is_active && (r.camera_id === id || !r.camera_id))

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

  const switchRecipe = async (skuCode: string) => {
    try {
      await recipeApi.changeover(skuCode, id)
      recipes.reload()
      toast(`Changeover SKU berhasil ke ${skuCode}.`)
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    }
  }

  const createQuickRecipe = async (e: FormEvent) => {
    e.preventDefault()
    if (!newSku.trim() || !newSkuName.trim()) return
    try {
      await recipeApi.create({
        sku_code: newSku.trim().toUpperCase(),
        name: newSkuName.trim(),
        camera_id: id,
        is_active: true,
        pass_sample_rate: 500,
      })
      setNewSku('')
      setNewSkuName('')
      recipes.reload()
      toast('SKU Recipe Multi-Zone ROI ditambahkan & diaktifkan.')
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    }
  }

  const runOtTrigger = async (
    defect: 'seal_leak' | 'missing_exp_date' | 'foreign_matter' | 'burnt' | null = null,
    blur = false,
  ) => {
    setTriggering(true)
    try {
      const res = await otApi.trigger(id, {
        protocol: 'modbus_tcp',
        actuator_type: 'pneumatic_air_blast',
        simulate_defect: defect,
        simulate_blur: blur,
      })
      setOtResult(res)
      resource.reload()
      onChanged()
      toast(
        res.preprocessing.is_blurred
          ? 'BLUR_ALERT: Lensa berembun terdeteksi — stasiun DEGRADED!'
          : res.rejector.actuated
            ? `FAIL (${res.defect_categories.join(', ')}) — Sinyal PLC Air-Blast Rejector dikirim (${res.inference_latency_ms} ms)`
            : `PASS — Semua 3 Zona ROI lulus (${res.inference_latency_ms} ms)`,
        res.decision === 'PASS' ? 'info' : 'warning',
      )
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setTriggering(false)
    }
  }

  return (
    <Modal title={camera?.name ?? 'Detail stasiun'} eyebrow={`STATION / ${cameraCode(id)}`} onClose={onClose}>
      {resource.isLoading && <LoadingBlock />}
      {resource.error && <ErrorState message={resource.error} retry={resource.reload} />}
      {camera && <div className="detail-modal-content">
        <div className="detail-status-row"><StatusBadge value={camera.status} /><StatusBadge value={camera.is_active ? 'active' : 'inactive'} /></div>
        {camera.last_error && <div className="inline-notice inline-notice--error">{camera.last_error}</div>}
        <dl className="data-list data-list--stacked"><div><dt>Kode stasiun</dt><dd>{cameraCode(camera.id)}</dd></div><div><dt>Identitas</dt><dd>{camera.camera_identity ?? '—'}</dd></div><div><dt>Jenis sumber</dt><dd>{sourceLabels[camera.source_type] ?? camera.source_type}</dd></div><div><dt>Lokasi sumber</dt><dd className="break-text">{camera.source_url ?? 'Unggah melalui browser'}</dd></div><div><dt>Resolusi / FPS</dt><dd>{camera.resolution ?? '—'} / {camera.fps_input ?? '—'}</dd></div><div><dt>Inspeksi terakhir</dt><dd>{formatDate(camera.last_inspection_at)}</dd></div></dl>

        <section className="panel" style={{ marginTop: '1rem', padding: '0.85rem' }}>
          <p className="eyebrow"><Layers size={14} /> FOOD DIVISION / SKU RECIPE & MULTI-ZONE ROI</p>
          <p style={{ fontSize: '0.85rem', margin: '0.35rem 0' }}>
            SKU Aktif: <strong>{activeRecipe ? `${activeRecipe.sku_code} — ${activeRecipe.name}` : 'Standar Multi-Zone (Default)'}</strong>
            {' · '}Preprocessing: <code>CLAHE + Gamma {activeRecipe?.preprocessing_json.gamma ?? 1.2} + Blur Check (Laplacian &ge; {activeRecipe?.preprocessing_json.blur_threshold ?? 80})</code>
          </p>
          <ul style={{ fontSize: '0.8rem', margin: '0.4rem 0', paddingLeft: '1.1rem' }}>
            {(activeRecipe?.zones_json ?? []).map((zone) => (
              <li key={zone.zone_id}>
                <strong>{zone.name}</strong> ({zone.zone_type}) — BBox: <code>[{zone.bbox.join(', ')}]</code>
              </li>
            ))}
          </ul>
          {!!(recipes.data ?? []).length && (
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
              {(recipes.data ?? [])
                .filter((r): r is typeof r & { sku_code: string } => Boolean(r.sku_code))
                .map((r) => (
                <Button
                  key={r.id}
                  variant={r.id === activeRecipe?.id ? 'primary' : 'secondary'}
                  onClick={() => switchRecipe(r.sku_code)}
                >
                  Changeover: {r.sku_code}
                </Button>
              ))}
            </div>
          )}
          <form onSubmit={createQuickRecipe} style={{ display: 'flex', gap: '0.5rem', marginTop: '0.6rem', flexWrap: 'wrap' }}>
            <input
              value={newSku}
              onChange={(e) => setNewSku(e.target.value)}
              placeholder="Kode SKU baru (mis. BIS-STR-100G)"
              aria-label="Kode SKU baru"
              style={{ flex: '1 1 140px' }}
            />
            <input
              value={newSkuName}
              onChange={(e) => setNewSkuName(e.target.value)}
              placeholder="Nama Varian Produk"
              aria-label="Nama Varian Produk"
              style={{ flex: '1 1 160px' }}
            />
            <Button type="submit" variant="secondary"><Plus size={14} />Tambah SKU</Button>
          </form>
        </section>

        <section className="panel" style={{ marginTop: '0.85rem', padding: '0.85rem' }}>
          <p className="eyebrow"><Zap size={14} /> OT BRIDGE / PHOTOELECTRIC SENSOR & PLC REJECTOR</p>
          <p style={{ fontSize: '0.8rem', margin: '0.35rem 0' }}>
            Simulasikan sinyal sensor fotolistrik konveyor (&le; 30 ms ONNX) dan aktuator penendang produk cacat (Modbus TCP / MQTT).
          </p>
          <div style={{ display: 'flex', gap: '0.45rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
            <Button variant="secondary" loading={triggering} onClick={() => runOtTrigger(null, false)}>
              Trigger PASS
            </Button>
            <Button variant="secondary" loading={triggering} onClick={() => runOtTrigger('seal_leak', false)}>
              Trigger FAIL (Seal Leak)
            </Button>
            <Button variant="secondary" loading={triggering} onClick={() => runOtTrigger('missing_exp_date', false)}>
              Trigger FAIL (No Exp Date)
            </Button>
            <Button variant="secondary" loading={triggering} onClick={() => runOtTrigger(null, true)}>
              Simulasi Uap/Washdown (BLUR_ALERT)
            </Button>
          </div>
          {otResult && (
            <div style={{ marginTop: '0.6rem', fontSize: '0.8rem' }}>
              <strong>Hasil Siklus OT #{otResult.trigger_event_id}:</strong> Keputusan <code>{otResult.decision}</code> ({otResult.inference_latency_ms} ms) ·
              Quality Guard: <code>{otResult.preprocessing.quality_status ?? otResult.preprocessing.status}</code> ·
              Laplacian Sharpness: <code>{otResult.preprocessing.sharpness_score ?? otResult.preprocessing.laplacian_variance}</code> (Ambang &ge; {otResult.preprocessing.blur_threshold}) ·
              Luminance (Mean/Std): <code>{otResult.preprocessing.mean_luminance ?? '—'} / {otResult.preprocessing.std_luminance ?? '—'}</code> ({otResult.preprocessing.lighting_ok !== false ? 'LAMPU NORMAL' : 'LIGHTING FAILURE'}) ·
              PLC Rejector ({otResult.rejector.actuator_type}): <strong>{otResult.rejector.actuated ? `AKTIF (${otResult.rejector.plc_target})` : 'IDLE'}</strong>
            </div>
          )}
        </section>

        <section className="panel" aria-label="Image Conditioning and Lens Health" style={{ marginTop: '0.85rem', padding: '0.85rem' }}>
          <p className="eyebrow">IMAGE CONDITIONING & LENS HEALTH GUARD</p>
          <dl className="data-list data-list--stacked" style={{ marginTop: '0.35rem' }}>
            <div>
              <dt>Ketajaman Lensa (Laplacian)</dt>
              <dd>{otResult ? `${otResult.preprocessing.sharpness_score ?? otResult.preprocessing.laplacian_variance} (min ${otResult.preprocessing.blur_threshold})` : `Ambang aktif: ≥ ${activeRecipe?.preprocessing_config?.blur_threshold ?? 80.0}`}</dd>
            </div>
            <div>
              <dt>Status Pencahayaan & Kabut</dt>
              <dd>{otResult ? `${otResult.preprocessing.quality_status ?? otResult.preprocessing.status} (Mean Y: ${otResult.preprocessing.mean_luminance ?? '—'})` : camera.status === 'degraded' ? 'DEGRADED — LENS_BLUR_OR_FOG' : 'NOMINAL (OK)'}</dd>
            </div>
            <div>
              <dt>Conditioning Pipeline</dt>
              <dd>CLAHE LAB + Gray-World White Balance + Gamma {activeRecipe?.preprocessing_config?.gamma ?? 1.2}</dd>
            </div>
          </dl>
        </section>

        <CameraOTConfigSection
          key={`${camera.id}:${camera.ot_protocol ?? ''}:${camera.ot_host ?? ''}:${camera.ot_port ?? ''}:${camera.trigger_register ?? ''}:${camera.reject_coil ?? ''}:${camera.reject_delay_ms ?? ''}:${camera.reject_pulse_ms ?? ''}`}
          camera={camera}
          canConfigureOt={canConfigureOt}
          onReload={resource.reload}
        />

        <div className="modal-actions"><Link to="/inspections" className="button button--secondary"><History size={16} />Riwayat inspeksi</Link>{canOperate && camera.source_type !== 'upload' && <Button variant="secondary" loading={changing} onClick={lifecycle}>{camera.status === 'connected' ? <Unplug size={16} /> : <Power size={16} />}{camera.status === 'connected' ? 'Tandai terputus' : 'Tandai terhubung'}</Button>}{canAdmin && <Button onClick={() => onEdit(camera)}><Pencil size={16} />Edit stasiun</Button>}</div>
      </div>}
    </Modal>
  )
}

function CameraOTConfigSection({ camera, canConfigureOt, onReload }: {
  camera: CameraType
  canConfigureOt: boolean
  onReload: () => void
}) {
  const toast = useToast()
  const [otProto, setOtProto] = useState(camera.ot_protocol ?? 'simulated')
  const [otHost, setOtHost] = useState(camera.ot_host ?? '127.0.0.1')
  const [otPort, setOtPort] = useState(camera.ot_port ?? 502)
  const [trigReg, setTrigReg] = useState(camera.trigger_register ?? 1)
  const [rejCoil, setRejCoil] = useState(camera.reject_coil ?? 100)
  const [rejDelay, setRejDelay] = useState(camera.reject_delay_ms ?? 120)
  const [rejPulse, setRejPulse] = useState(camera.reject_pulse_ms ?? 45)
  const [savingOt, setSavingOt] = useState(false)
  const [testingPulse, setTestingPulse] = useState(false)

  return (
    <section className="panel" aria-label="PLC and Actuator IO" style={{ marginTop: '0.85rem', padding: '0.85rem' }}>
      <p className="eyebrow"><Zap size={14} /> PLC & ACTUATOR I/O (MODBUS TCP / MQTT / REJECTOR)</p>
      <div className="form-grid form-grid--two" style={{ marginTop: '0.5rem' }}>
        <label className="field">
          <span>Protokol OT</span>
          <select value={otProto} disabled={!canConfigureOt} onChange={(e) => setOtProto(e.target.value)}>
            <option value="simulated">SimulatedOTAdapter (Lokal / CI)</option>
            <option value="modbus_tcp">ModbusTCPAdapter (Siemens S7 / Omron / Allen-Bradley)</option>
            <option value="mqtt">MQTTIndustrialAdapter (factory/org/line/reject)</option>
          </select>
        </label>
        <label className="field">
          <span>Host PLC / MQTT Broker</span>
          <input
            value={otHost}
            disabled={!canConfigureOt}
            onChange={(e) => setOtHost(e.target.value)}
            placeholder="192.168.10.50 atau mqtt://broker:1883"
          />
        </label>
        <label className="field">
          <span>Port & Trigger Register</span>
          <div style={{ display: 'flex', gap: '0.4rem' }}>
            <input
              type="number"
              disabled={!canConfigureOt}
              value={otPort}
              onChange={(e) => setOtPort(Number(e.target.value))}
              aria-label="Port OT"
            />
            <input
              type="number"
              disabled={!canConfigureOt}
              value={trigReg}
              onChange={(e) => setTrigReg(Number(e.target.value))}
              aria-label="Trigger Register"
            />
          </div>
        </label>
        <label className="field">
          <span>Reject Coil, Delay (ms) & Pulse (ms)</span>
          <div style={{ display: 'flex', gap: '0.4rem' }}>
            <input
              type="number"
              disabled={!canConfigureOt}
              value={rejCoil}
              onChange={(e) => setRejCoil(Number(e.target.value))}
              aria-label="Reject Coil"
              title="Reject Coil Address"
            />
            <input
              type="number"
              disabled={!canConfigureOt}
              value={rejDelay}
              onChange={(e) => setRejDelay(Number(e.target.value))}
              aria-label="Reject Delay ms"
              title="Reject Delay ms (Travel time camera to nozzle)"
            />
            <input
              type="number"
              disabled={!canConfigureOt}
              value={rejPulse}
              onChange={(e) => setRejPulse(Number(e.target.value))}
              aria-label="Reject Pulse ms"
              title="Reject Pulse ms"
            />
          </div>
        </label>
      </div>
      {canConfigureOt && (
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.65rem' }}>
          <Button
            variant="secondary"
            loading={savingOt}
            onClick={async () => {
              setSavingOt(true)
              try {
                await otApi.updateConfig(camera.id, {
                  ot_protocol: otProto,
                  ot_host: otHost,
                  ot_port: Number(otPort),
                  trigger_register: Number(trigReg),
                  reject_coil: Number(rejCoil),
                  reject_delay_ms: Number(rejDelay),
                  reject_pulse_ms: Number(rejPulse),
                })
                onReload()
                toast('Konfigurasi PLC & Actuator I/O berhasil disimpan.')
              } catch (reason) {
                toast(errorMessage(reason), 'warning')
              } finally {
                setSavingOt(false)
              }
            }}
          >
            Simpan Konfigurasi PLC
          </Button>
          <Button
            variant="primary"
            loading={testingPulse}
            onClick={async () => {
              setTestingPulse(true)
              try {
                const res = await otApi.testPulse(camera.id, {
                  actuator_type: 'pneumatic_air_blast',
                  reject_coil: Number(rejCoil),
                  pulse_ms: Number(rejPulse),
                })
                toast(`Test Rejector Pulse berhasil dikirim ke ${res.rejector.plc_target} (${res.pulse_ms} ms) — tercatat di AuditLog.`)
              } catch (reason) {
                toast(errorMessage(reason), 'warning')
              } finally {
                setTestingPulse(false)
              }
            }}
          >
            <Zap size={15} />Test Rejector Pulse
          </Button>
        </div>
      )}
    </section>
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
