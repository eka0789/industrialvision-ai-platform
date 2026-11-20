import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, ArrowUpRight, Camera, Check, CircleAlert, PackageCheck, Pause, Play, Plus, ScanSearch, Settings2, Square } from 'lucide-react'
import { Link } from 'react-router-dom'
import { batchApi, cameraApi, inspectionApi } from '../api'
import { InspectionTable } from '../components/InspectionTable'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useLiveFeed } from '../hooks/useLiveFeed'
import { useToast } from '../hooks/useToast'
import type { Camera as CameraType, Decision, Inspection } from '../types'
import { cameraCode, formatDate, formatPercent } from '../utils/format'
import { errorMessage } from '../utils/errors'

export default function OverviewPage() {
  const { user } = useAuth()
  const { status: liveStatus, onInspection } = useLiveFeed()
  const { data, error, isLoading, reload } = useApi(() => Promise.all([
    cameraApi.list(), inspectionApi.list({ page_size: 100 }),
  ]), [])
  const [liveItems, setLiveItems] = useState<Inspection[]>([])
  const [cameraPatches, setCameraPatches] = useState<Record<number, Partial<CameraType>>>({})

  useEffect(() => onInspection((inspection) => {
    setLiveItems((items) => [inspection, ...items].slice(0, 100))
    setCameraPatches((patches) => ({
      ...patches,
      [inspection.camera_id]: { last_inspection_at: inspection.timestamp, status: 'connected' },
    }))
  }), [onInspection])

  const refreshAll = () => {
    setLiveItems([])
    setCameraPatches({})
    reload()
  }

  const baseCameras = useMemo(
    () => (data?.[0] ?? []).map((camera) => {
      const patch = cameraPatches[camera.id]
      return patch ? { ...camera, ...patch } : camera
    }),
    [data, cameraPatches],
  )
  const history = data?.[1]
  const baseInspections = useMemo(() => history?.items ?? [], [history])
  const inspections = useMemo(() => {
    const seen = new Set<number>()
    const merged: Inspection[] = []
    for (const item of [...liveItems, ...baseInspections]) {
      if (seen.has(item.id)) continue
      seen.add(item.id)
      merged.push(item)
    }
    return merged
  }, [liveItems, baseInspections])
  const liveCount = inspections.length - baseInspections.length
  const totalInspections = (history?.total ?? 0) + Math.max(0, liveCount)
  const cameras = baseCameras
  const sampleCounts = inspections.reduce<Record<Decision, number>>((counts, inspection) => {
    counts[inspection.decision] += 1
    return counts
  }, { PASS: 0, FAIL: 0, REVIEW: 0, ERROR: 0 })
  const passRatio = inspections.length ? sampleCounts.PASS / inspections.length : 0
  const connected = cameras.filter((camera) => camera.status === 'connected').length
  const circumference = 2 * Math.PI * 70

  return (
    <div className="page overview-page">
      <PageHeader
        eyebrow="OPERASI / RINGKASAN"
        title={<>Ruang kendali<span className="title-dot">.</span></>}
        description={`Selamat datang, ${user?.username}. Berikut aktivitas inspeksi kualitas Anda.`}
        actions={
          <>
            <Link to="/recipes" className="button button--secondary">Ganti SKU</Link>
            <Link to="/inspections/new" className="button button--primary">
              <ScanSearch size={17} aria-hidden="true" />Mulai inspeksi
            </Link>
          </>
        }
      />
      <ActiveBatchBanner canOperate={user?.role !== 'viewer'} />
      {isLoading && <LoadingBlock label="Menyusun data operasional…" />}
      {error && <ErrorState message={error} retry={refreshAll} />}
      {!isLoading && !error && history && (
        <>
          <section className="overview-hero" aria-label="Ringkasan kualitas">
            <div className="overview-hero-copy">
              <p className="eyebrow eyebrow--light">
                <span />KUALITAS / SEKILAS {liveStatus === 'live' && <span className="live-chip"><i aria-hidden="true" />LIVE</span>}
              </p>
              <h2>{inspections.length ? <>Mutu terlihat.<br /><em>Keputusan terukur.</em></> : <>Setiap keputusan<br /><em>dimulai di sini.</em></>}</h2>
              <p className="hero-description">{inspections.length ? 'Pantau hasil, telusuri bukti, dan ambil tindakan dari satu ruang kerja.' : 'Hubungkan stasiun, pilih model, lalu jalankan inspeksi gambar pertama Anda.'}</p>
              <Link className="hero-link" to={inspections.length ? '/inspections' : '/inspections/new'}>
                {inspections.length ? 'Telusuri seluruh inspeksi' : 'Jalankan inspeksi pertama'}
                <ArrowRight size={17} aria-hidden="true" />
              </Link>
              <div className="decision-tape-wrap">
                <div className="decision-tape-label"><span>JEJAK KEPUTUSAN</span><span>{Math.min(32, inspections.length)} SAMPEL TERBARU</span></div>
                {inspections.length ? (
                  <div className="decision-tape" aria-label="Urutan keputusan terbaru">
                    {inspections.slice(0, 32).reverse().map((inspection) => (
                      <span
                        key={inspection.id}
                        className={`decision-tick decision-tick--${inspection.decision.toLowerCase()}`}
                        title={`${inspectionCodeForTitle(inspection.id)}: ${inspection.decision}`}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="decision-tape decision-tape--empty" aria-hidden="true">
                    {Array.from({ length: 24 }, (_, index) => <span key={index} />)}
                  </div>
                )}
              </div>
            </div>
            <div className="quality-index">
              <span className="quality-index-label">INDEKS KUALITAS / SAMPEL</span>
              <div className="quality-ring-wrap">
                <svg className="quality-ring" viewBox="0 0 180 180" role="img" aria-label={inspections.length ? `${formatPercent(passRatio)} lulus dari ${inspections.length} inspeksi terbaru` : 'Belum ada data rasio kelulusan'}>
                  <circle className="quality-ring-track" cx="90" cy="90" r="70" />
                  {inspections.length > 0 && <circle className="quality-ring-value" cx="90" cy="90" r="70" strokeDasharray={`${passRatio * circumference} ${circumference}`} />}
                </svg>
                <div className="quality-ring-text"><strong>{inspections.length ? formatPercent(passRatio) : '-'}</strong><span>{inspections.length ? 'LULUS' : 'MENUNGGU DATA'}</span></div>
                <span className="quality-cross quality-cross--one" aria-hidden="true">+</span><span className="quality-cross quality-cross--two" aria-hidden="true">+</span>
              </div>
              <p>{inspections.length ? `Dari ${inspections.length} inspeksi terbaru${totalInspections > inspections.length ? ` / ${totalInspections} total` : ''}. Bukan metrik fleet-wide.` : 'Rasio akan dihitung dari hasil inspeksi nyata.'}</p>
            </div>
          </section>
          <section className="metrics-grid" aria-label="Metrik operasional">
            <div className="metric-card"><span className="metric-index">01 / TOTAL</span><ScanSearch size={20} aria-hidden="true" /><strong>{totalInspections.toLocaleString('id-ID')}</strong><h3>Total inspeksi</h3><p>Seluruh catatan di database</p></div>
            <div className="metric-card"><span className="metric-index">02 / FLEET</span><Camera size={20} aria-hidden="true" /><strong>{connected}<small> / {cameras.length}</small></strong><h3>Stasiun terhubung</h3><p>Status koneksi administratif</p></div>
            <div className="metric-card metric-card--warning"><span className="metric-index">03 / TINJAU</span><CircleAlert size={20} aria-hidden="true" /><strong>{inspections.length ? sampleCounts.REVIEW : '-'}</strong><h3>Perlu tinjauan</h3><p>Dalam {inspections.length || 0} sampel terbaru</p></div>
            <div className="metric-card metric-card--danger"><span className="metric-index">04 / GAGAL</span><CircleAlert size={20} aria-hidden="true" /><strong>{inspections.length ? sampleCounts.FAIL : '-'}</strong><h3>Keputusan gagal</h3><p>Dalam {inspections.length || 0} sampel terbaru</p></div>
          </section>
          {(!cameras.length || !inspections.length) && (
            <section className="setup-banner">
              <div><p className="eyebrow">PANDUAN AWAL</p><h2>Siapkan alur kerja Anda.</h2><p>Tiga langkah untuk menghasilkan keputusan yang dapat ditelusuri.</p></div>
              <ol className="setup-steps">
                <li className={cameras.length ? 'setup-step--done' : ''}><span>{cameras.length ? <Check size={17} aria-hidden="true" /> : '01'}</span><div><strong>Tambahkan stasiun</strong><small>{cameras.length ? `${cameras.length} stasiun terdaftar` : 'Pilih sumber unggah gambar'}</small></div>{user?.role === 'admin' && <Link to="/cameras" aria-label="Buka pengelolaan stasiun"><ArrowUpRight size={18} aria-hidden="true" /></Link>}</li>
                <li className={inspections.length ? 'setup-step--done' : ''}><span>{inspections.length ? <Check size={17} aria-hidden="true" /> : '02'}</span><div><strong>Pilih model aktif</strong><small>Pastikan artefak dan konfigurasi sudah valid</small></div><Link to="/models" aria-label="Buka registry model"><ArrowUpRight size={18} aria-hidden="true" /></Link></li>
                <li><span>03</span><div><strong>Unggah & evaluasi</strong><small>Simpan gambar dan hasilnya sebagai bukti</small></div><Link to="/inspections/new" aria-label="Mulai inspeksi"><ArrowUpRight size={18} aria-hidden="true" /></Link></li>
              </ol>
            </section>
          )}
          <div className="overview-columns">
            <section className="panel recent-panel">
              <header className="panel-header"><div><p className="eyebrow">CATATAN TERBARU</p><h2>Inspeksi terbaru</h2></div><Link to="/inspections" className="text-link">Lihat semua<ArrowUpRight size={15} aria-hidden="true" /></Link></header>
              {inspections.length ? <InspectionTable inspections={inspections.slice(0, 6)} cameras={cameras} compact /> : <EmptyState title="Belum ada jejak inspeksi" description="Hasil yang Anda proses akan tersusun kronologis di sini." action={<Link className="button button--secondary" to="/inspections/new">Buat inspeksi</Link>} />}
            </section>
            <section className="panel fleet-panel">
              <header className="panel-header"><div><p className="eyebrow">STATUS STASIUN</p><h2>Stasiun kerja</h2></div><Link to="/cameras" className="icon-button" aria-label="Kelola stasiun"><Settings2 size={18} aria-hidden="true" /></Link></header>
              {cameras.length ? <ul className="fleet-list">{cameras.slice(0, 5).map((camera) => <li key={camera.id}><span className="fleet-code">{cameraCode(camera.id)}</span><div><Link to={`/cameras?station=${camera.id}`}>{camera.name}</Link><small>{camera.last_inspection_at ? formatDate(camera.last_inspection_at) : 'Belum ada inspeksi'}</small></div><StatusBadge value={camera.status} /></li>)}</ul> : <EmptyState title="Belum ada stasiun" description={user?.role === 'admin' ? 'Daftarkan stasiun pertama untuk memulai.' : 'Administrator perlu mendaftarkan stasiun.'} />}
              <div className="fleet-footer"><span className="status-dot" aria-hidden="true" />Data tersinkron dengan API</div>
            </section>
          </div>
        </>
      )}
    </div>
  )
}

function inspectionCodeForTitle(id: number) {
  return `INS-${String(id).padStart(6, '0')}`
}

function ActiveBatchBanner({ canOperate }: { canOperate: boolean }) {
  const toast = useToast()
  const batches = useApi(() => batchApi.list(), [])
  const { onInspection } = useLiveFeed()
  const [lotCode, setLotCode] = useState('')
  const [skuCode, setSkuCode] = useState('BIS-COK-100G')
  const [shift, setShift] = useState('SHIFT_1')
  const [busy, setBusy] = useState(false)

  const reloadBatches = batches.reload
  useEffect(() => onInspection(() => { reloadBatches() }), [onInspection, reloadBatches])

  const activeBatch = (batches.data ?? []).find((b) =>
    ['ACTIVE', 'active', 'PAUSED', 'paused'].includes(b.status),
  )

  const startBatch = async (e: FormEvent) => {
    e.preventDefault()
    const code = lotCode.trim() || `LOT-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}-A1`
    setBusy(true)
    try {
      await batchApi.create({
        batch_code: code,
        sku_code: skuCode.trim() || 'BIS-COK-100G',
        line_name: 'Line 01 - Food Packaging',
        shift,
        target_quantity: 5000,
      })
      setLotCode('')
      batches.reload()
      toast(`Batch produksi "${code}" dimulai (${shift}).`)
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setBusy(false)
    }
  }

  const togglePause = async () => {
    if (!activeBatch) return
    setBusy(true)
    try {
      if (activeBatch.status.toUpperCase() === 'PAUSED') {
        await batchApi.resume(activeBatch.id)
        toast(`Batch ${activeBatch.batch_code} dilanjutkan (ACTIVE).`)
      } else {
        await batchApi.pause(activeBatch.id)
        toast(`Batch ${activeBatch.batch_code} dijeda (PAUSED).`)
      }
      batches.reload()
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setBusy(false)
    }
  }

  const closeActiveBatch = async () => {
    if (!activeBatch) return
    setBusy(true)
    try {
      await batchApi.close(activeBatch.id)
      batches.reload()
      toast(`Batch ${activeBatch.batch_code} ditutup (CLOSED). Siap untuk laporan audit PDF/CSV.`)
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel batch-banner" aria-label="Batch Produksi Aktif" data-testid="active-batch-banner">
      <div className="batch-banner-inner">
        <div>
          <p className="eyebrow">
            <PackageCheck size={14} aria-hidden="true" />
            BATCH PRODUKSI AKTIF / KETELUSURAN LOT HACCP
          </p>
          {batches.isLoading ? (
            <p className="batch-banner-meta">Memeriksa status batch produksi aktif…</p>
          ) : batches.error ? (
            <p className="batch-banner-meta document-error" role="alert">
              Gagal memuat batch aktif: {batches.error}{' '}
              <button type="button" className="citation-link" onClick={batches.reload}>Muat ulang</button>
            </p>
          ) : activeBatch ? (
            <div>
              <div className="batch-banner-title-row">
                <strong>{activeBatch.batch_code}</strong>
                <StatusBadge value={activeBatch.status.toUpperCase() === 'PAUSED' ? 'inactive' : 'active'} />
                <span>SKU: <code>{activeBatch.sku_code}</code> · {activeBatch.line_name ?? 'Line 01'} · <strong>{activeBatch.shift}</strong></span>
              </div>
              <small className="batch-banner-meta">
                Progres: <strong>{activeBatch.total_inspected} / {activeBatch.target_quantity}</strong> pcs ·
                First Pass Yield: <strong>{activeBatch.first_pass_yield_pct ?? activeBatch.yield_pct}%</strong> (PASS: {activeBatch.total_pass} · FAIL: {activeBatch.total_fail} · REVIEW: {activeBatch.total_review})
              </small>
            </div>
          ) : (
            <p className="batch-banner-meta">
              Belum ada batch produksi aktif pada shift ini. Mulai batch baru agar seluruh inspeksi tertaut otomatis.
            </p>
          )}
        </div>

        {canOperate && !batches.isLoading && !batches.error && (
          <div className="batch-banner-actions">
            {activeBatch ? (
              <>
                <Button variant="secondary" loading={busy} onClick={togglePause}>
                  {activeBatch.status.toUpperCase() === 'PAUSED' ? <Play size={15} aria-hidden="true" /> : <Pause size={15} aria-hidden="true" />}
                  {activeBatch.status.toUpperCase() === 'PAUSED' ? 'Lanjutkan Batch' : 'Jeda Batch'}
                </Button>
                <Button variant="secondary" loading={busy} onClick={closeActiveBatch}>
                  <Square size={15} aria-hidden="true" />Tutup Batch
                </Button>
                <Link to="/batches" className="button button--secondary">
                  Audit PDF/CSV
                </Link>
              </>
            ) : (
              <form onSubmit={startBatch} className="inline-batch-form">
                <input
                  value={lotCode}
                  onChange={(e) => setLotCode(e.target.value)}
                  placeholder="Kode Lot (mis. LOT-20261117-A1)"
                  aria-label="Kode Lot Batch"
                />
                <input
                  value={skuCode}
                  onChange={(e) => setSkuCode(e.target.value)}
                  placeholder="SKU"
                  aria-label="SKU Batch"
                  className="inline-batch-sku"
                />
                <select value={shift} onChange={(e) => setShift(e.target.value)} aria-label="Shift Produksi">
                  <option value="SHIFT_1">SHIFT_1</option>
                  <option value="SHIFT_2">SHIFT_2</option>
                  <option value="SHIFT_3">SHIFT_3</option>
                </select>
                <Button type="submit" loading={busy}>
                  <Plus size={15} aria-hidden="true" />Mulai Batch
                </Button>
              </form>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
