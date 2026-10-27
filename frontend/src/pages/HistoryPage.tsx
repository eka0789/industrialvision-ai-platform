import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowLeft, ArrowRight, Filter, RotateCcw, Search, SlidersHorizontal } from 'lucide-react'
import { Link } from 'react-router-dom'
import { cameraApi, inspectionApi } from '../api'
import { InspectionTable } from '../components/InspectionTable'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useLiveFeed } from '../hooks/useLiveFeed'
import type { Decision } from '../types'

export default function HistoryPage() {
  const [page, setPage] = useState(1)
  const [decision, setDecision] = useState<Decision | ''>('')
  const [cameraId, setCameraId] = useState('')
  const [batchInput, setBatchInput] = useState('')
  const [batch, setBatch] = useState('')
  const [pendingResults, setPendingResults] = useState(0)
  const reloadTimer = useRef<number | undefined>(undefined)
  const cameras = useApi(cameraApi.list)
  const history = useApi(() => inspectionApi.list({ page, page_size: 12, decision, camera_id: cameraId ? Number(cameraId) : undefined, batch_id: batch || undefined }), [page, decision, cameraId, batch])
  const data = history.data
  const first = data?.total ? (data.page - 1) * data.page_size + 1 : 0
  const last = data ? Math.min(data.page * data.page_size, data.total) : 0
  const filtering = Boolean(decision || cameraId || batch)
  const { status: liveStatus, onInspection } = useLiveFeed()
  const reloadRef = useRef(history.reload)
  useEffect(() => { reloadRef.current = history.reload })

  useEffect(() => onInspection(() => {
    if (page === 1 && !filtering) {
      window.clearTimeout(reloadTimer.current)
      reloadTimer.current = window.setTimeout(() => { reloadRef.current() }, 600)
    } else {
      setPendingResults((count) => count + 1)
    }
  }), [onInspection, page, filtering])

  const loadPending = () => {
    window.clearTimeout(reloadTimer.current)
    setPendingResults(0)
    history.reload()
  }

  const search = (event: FormEvent) => { event.preventDefault(); setPage(1); setBatch(batchInput.trim()) }
  const reset = () => { setDecision(''); setCameraId(''); setBatch(''); setBatchInput(''); setPage(1) }

  return (
    <div className="page history-page">
      <PageHeader eyebrow="TRACEABILITY / INSPECTION LOG" title={<>Jejak yang bisa<br /><span className="text-muted">dipertanggungjawabkan.</span></>}
        description="Telusuri keputusan, temuan, dan bukti visual dari setiap sampel yang telah diperiksa."
        actions={<Link to="/inspections/new" className="button button--primary">Inspeksi baru<ArrowRight size={16} /></Link>} />
      <section className="filter-panel" aria-label="Filter riwayat inspeksi">
        <div className="filter-panel-title"><SlidersHorizontal size={17} /><span>FILTER CATATAN</span>{filtering && <i title="Filter aktif" />}</div>
        <form className="filter-controls" onSubmit={search}>
          <label className="field"><span>Keputusan</span><select value={decision} onChange={(event) => { setDecision(event.target.value as Decision | ''); setPage(1) }}><option value="">Semua keputusan</option><option value="PASS">Lulus</option><option value="FAIL">Gagal</option><option value="REVIEW">Perlu tinjauan</option><option value="ERROR">Error</option></select></label>
          <label className="field"><span>Stasiun</span><select value={cameraId} onChange={(event) => { setCameraId(event.target.value); setPage(1) }}><option value="">Semua stasiun</option>{cameras.data?.map((camera) => <option value={camera.id} key={camera.id}>{camera.name}</option>)}</select></label>
          <label className="field filter-batch"><span>ID batch persis</span><div className="input-with-icon"><Search size={16} /><input value={batchInput} onChange={(event) => setBatchInput(event.target.value)} placeholder="Cari ID batch…" maxLength={200} /></div></label>
          <Button type="submit" variant="secondary"><Filter size={16} />Terapkan</Button>
          {filtering && <Button type="button" variant="ghost" onClick={reset}><RotateCcw size={15} />Reset</Button>}
        </form>
      </section>
      <section className="panel history-panel">
        <header className="panel-header"><div><p className="eyebrow">DATABASE / RECORDS</p><h2>Log inspeksi</h2></div><div className="panel-header-side">{pendingResults > 0 && <button className="new-results-chip" onClick={loadPending}>+{pendingResults} hasil baru — muat ulang</button>}{liveStatus === 'live' && pendingResults === 0 && <span className="live-chip"><i aria-hidden="true" />LIVE</span>}{data && <span className="record-range">{first}–{last} <span>dari {data.total}</span></span>}</div></header>
        {history.isLoading && <LoadingBlock label="Membaca catatan inspeksi…" />}
        {history.error && <ErrorState message={history.error} retry={history.reload} />}
        {!history.isLoading && !history.error && data?.items.length === 0 && <EmptyState title={filtering ? 'Tidak ada catatan yang cocok' : 'Belum ada riwayat inspeksi'} description={filtering ? 'Ubah filter atau reset pencarian untuk melihat catatan lainnya.' : 'Jalankan inspeksi pertama untuk mulai membangun jejak kualitas.'} action={filtering ? <Button variant="secondary" onClick={reset}>Reset filter</Button> : <Link className="button button--primary" to="/inspections/new">Mulai inspeksi<ArrowRight size={16} /></Link>} />}
        {!history.isLoading && !history.error && data && data.items.length > 0 && <InspectionTable inspections={data.items} cameras={cameras.data ?? []} />}
        {data && data.total > 0 && <footer className="pagination"><span>Halaman {data.page} dari {Math.ceil(data.total / data.page_size)}</span><div><Button variant="secondary" disabled={data.page <= 1 || history.isLoading} onClick={() => setPage((value) => Math.max(1, value - 1))}><ArrowLeft size={15} />Sebelumnya</Button><Button variant="secondary" disabled={!data.has_more || history.isLoading} onClick={() => setPage((value) => value + 1)}>Berikutnya<ArrowRight size={15} /></Button></div></footer>}
      </section>
    </div>
  )
}
