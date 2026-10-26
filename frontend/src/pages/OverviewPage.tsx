import { ArrowRight, ArrowUpRight, Camera, Check, CircleAlert, ScanSearch, Settings2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { cameraApi, inspectionApi } from '../api'
import { InspectionTable } from '../components/InspectionTable'
import { EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import type { Decision } from '../types'
import { cameraCode, formatDate, formatPercent } from '../utils/format'

export default function OverviewPage() {
  const { user } = useAuth()
  const { data, error, isLoading, reload } = useApi(() => Promise.all([
    cameraApi.list(), inspectionApi.list({ page_size: 100 }),
  ]), [])
  const cameras = data?.[0] ?? []
  const history = data?.[1]
  const inspections = history?.items ?? []
  const sampleCounts = inspections.reduce<Record<Decision, number>>((counts, inspection) => {
    counts[inspection.decision] += 1
    return counts
  }, { PASS: 0, FAIL: 0, REVIEW: 0, ERROR: 0 })
  const passRatio = inspections.length ? sampleCounts.PASS / inspections.length : 0
  const connected = cameras.filter((camera) => camera.status === 'connected').length
  const circumference = 2 * Math.PI * 70

  return (
    <div className="page overview-page">
      <PageHeader eyebrow="OPERATIONS / OVERVIEW" title={<>Ruang kendali<span className="title-dot">.</span></>}
        description={`Selamat datang, ${user?.username}. Berikut aktivitas quality inspection Anda.`}
        actions={<Link to="/inspections/new" className="button button--primary"><ScanSearch size={17} />Mulai inspeksi<ArrowUpRight size={16} /></Link>} />
      {isLoading && <LoadingBlock label="Menyusun data operasional…" />}
      {error && <ErrorState message={error} retry={reload} />}
      {!isLoading && !error && history && (
        <>
          <section className="overview-hero" aria-label="Ringkasan kualitas">
            <div className="overview-hero-copy">
              <p className="eyebrow eyebrow--light"><span />QUALITY / AT A GLANCE</p>
              <h2>{inspections.length ? <>Mutu terlihat.<br /><em>Keputusan terukur.</em></> : <>Setiap keputusan<br /><em>dimulai di sini.</em></>}</h2>
              <p className="hero-description">{inspections.length ? 'Pantau hasil, telusuri bukti, dan ambil tindakan dari satu ruang kerja.' : 'Hubungkan stasiun, pilih model, lalu jalankan inspeksi gambar pertama Anda.'}</p>
              <Link className="hero-link" to={inspections.length ? '/inspections' : '/inspections/new'}>{inspections.length ? 'Telusuri seluruh inspeksi' : 'Jalankan inspeksi pertama'}<ArrowRight size={17} /></Link>
              <div className="decision-tape-wrap">
                <div className="decision-tape-label"><span>JEJAK KEPUTUSAN</span><span>{Math.min(32, inspections.length)} SAMPEL TERBARU</span></div>
                {inspections.length ? <div className="decision-tape" aria-label="Urutan keputusan terbaru">
                  {inspections.slice(0, 32).reverse().map((inspection) => <span key={inspection.id} className={`decision-tick decision-tick--${inspection.decision.toLowerCase()}`} title={`${inspectionCodeForTitle(inspection.id)}: ${inspection.decision}`} />)}
                </div> : <div className="decision-tape decision-tape--empty" aria-hidden="true">{Array.from({ length: 24 }, (_, index) => <span key={index} />)}</div>}
              </div>
            </div>
            <div className="quality-index">
              <span className="quality-index-label">QUALITY INDEX / SAMPEL</span>
              <div className="quality-ring-wrap">
                <svg className="quality-ring" viewBox="0 0 180 180" role="img" aria-label={inspections.length ? `${formatPercent(passRatio)} lulus dari ${inspections.length} inspeksi terbaru` : 'Belum ada data rasio kelulusan'}>
                  <circle className="quality-ring-track" cx="90" cy="90" r="70" />
                  {inspections.length > 0 && <circle className="quality-ring-value" cx="90" cy="90" r="70" strokeDasharray={`${passRatio * circumference} ${circumference}`} />}
                </svg>
                <div className="quality-ring-text"><strong>{inspections.length ? formatPercent(passRatio) : '—'}</strong><span>{inspections.length ? 'LULUS' : 'MENUNGGU DATA'}</span></div>
                <span className="quality-cross quality-cross--one" aria-hidden="true">+</span><span className="quality-cross quality-cross--two" aria-hidden="true">+</span>
              </div>
              <p>{inspections.length ? `Dari ${inspections.length} inspeksi terbaru${history.total > inspections.length ? ` / ${history.total} total` : ''}. Bukan metrik fleet-wide.` : 'Rasio akan dihitung dari hasil inspeksi nyata.'}</p>
            </div>
          </section>
          <section className="metrics-grid" aria-label="Metrik operasional">
            <div className="metric-card"><span className="metric-index">01 / TOTAL</span><ScanSearch size={20} /><strong>{history.total.toLocaleString('id-ID')}</strong><h3>Total inspeksi</h3><p>Seluruh catatan di database</p></div>
            <div className="metric-card"><span className="metric-index">02 / FLEET</span><Camera size={20} /><strong>{connected}<small> / {cameras.length}</small></strong><h3>Stasiun terhubung</h3><p>Status koneksi administratif</p></div>
            <div className="metric-card metric-card--warning"><span className="metric-index">03 / REVIEW</span><CircleAlert size={20} /><strong>{inspections.length ? sampleCounts.REVIEW : '—'}</strong><h3>Perlu tinjauan</h3><p>Dalam {inspections.length || 0} sampel terbaru</p></div>
            <div className="metric-card metric-card--danger"><span className="metric-index">04 / REJECT</span><CircleAlert size={20} /><strong>{inspections.length ? sampleCounts.FAIL : '—'}</strong><h3>Keputusan gagal</h3><p>Dalam {inspections.length || 0} sampel terbaru</p></div>
          </section>
          {(!cameras.length || !inspections.length) && (
            <section className="setup-banner">
              <div><p className="eyebrow">FIRST RUN / CHECKLIST</p><h2>Siapkan alur kerja Anda.</h2><p>Tiga langkah untuk menghasilkan keputusan yang dapat ditelusuri.</p></div>
              <ol className="setup-steps">
                <li className={cameras.length ? 'setup-step--done' : ''}><span>{cameras.length ? <Check size={17} /> : '01'}</span><div><strong>Tambahkan stasiun</strong><small>{cameras.length ? `${cameras.length} stasiun terdaftar` : 'Pilih sumber unggah gambar'}</small></div>{user?.role === 'admin' && <Link to="/cameras" aria-label="Buka pengelolaan stasiun"><ArrowUpRight size={18} /></Link>}</li>
                <li className={inspections.length ? 'setup-step--done' : ''}><span>{inspections.length ? <Check size={17} /> : '02'}</span><div><strong>Pilih model aktif</strong><small>Pastikan artefak dan konfigurasi sudah valid</small></div><Link to="/models" aria-label="Buka registry model"><ArrowUpRight size={18} /></Link></li>
                <li><span>03</span><div><strong>Unggah & evaluasi</strong><small>Simpan gambar dan hasilnya sebagai bukti</small></div><Link to="/inspections/new" aria-label="Mulai inspeksi"><ArrowUpRight size={18} /></Link></li>
              </ol>
            </section>
          )}
          <div className="overview-columns">
            <section className="panel recent-panel">
              <header className="panel-header"><div><p className="eyebrow">LATEST RECORDS</p><h2>Inspeksi terbaru</h2></div><Link to="/inspections" className="text-link">Lihat semua<ArrowUpRight size={15} /></Link></header>
              {inspections.length ? <InspectionTable inspections={inspections.slice(0, 6)} cameras={cameras} compact /> : <EmptyState title="Belum ada jejak inspeksi" description="Hasil yang Anda proses akan tersusun kronologis di sini." action={<Link className="button button--secondary" to="/inspections/new">Buat inspeksi<ArrowRight size={16} /></Link>} />}
            </section>
            <section className="panel fleet-panel">
              <header className="panel-header"><div><p className="eyebrow">STATION STATUS</p><h2>Stasiun kerja</h2></div><Link to="/cameras" className="icon-button" aria-label="Kelola stasiun"><Settings2 size={18} /></Link></header>
              {cameras.length ? <ul className="fleet-list">{cameras.slice(0, 5).map((camera) => <li key={camera.id}><span className="fleet-code">{cameraCode(camera.id)}</span><div><Link to={`/cameras?station=${camera.id}`}>{camera.name}</Link><small>{camera.last_inspection_at ? formatDate(camera.last_inspection_at) : 'Belum ada inspeksi'}</small></div><StatusBadge value={camera.status} /></li>)}</ul> : <EmptyState title="Belum ada stasiun" description={user?.role === 'admin' ? 'Daftarkan stasiun pertama untuk memulai.' : 'Administrator perlu mendaftarkan stasiun.'} />}
              <div className="fleet-footer"><span className="status-dot" />Data tersinkron dengan API</div>
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
