import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, Download, Filter, Plus, RotateCcw, Search, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { Link } from 'react-router-dom'
import { batchApi, cameraApi, inspectionApi } from '../api'
import { InspectionTable } from '../components/InspectionTable'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useLiveFeed } from '../hooks/useLiveFeed'
import { useToast } from '../hooks/useToast'
import type { Decision } from '../types'
import { errorMessage } from '../utils/errors'

export default function HistoryPage() {
  const { user } = useAuth()
  const toast = useToast()
  const [page, setPage] = useState(1)
  const [decision, setDecision] = useState<Decision | ''>('')
  const [cameraId, setCameraId] = useState('')
  const [batchInput, setBatchInput] = useState('')
  const [batch, setBatch] = useState('')
  const [pendingResults, setPendingResults] = useState(0)
  const [newBatchCode, setNewBatchCode] = useState('')
  const [newBatchSku, setNewBatchSku] = useState('BIS-COK-100G')
  const [newBatchShift, setNewBatchShift] = useState('Shift 1')
  const reloadTimer = useRef<number | undefined>(undefined)
  const cameras = useApi(cameraApi.list)
  const batches = useApi(() => batchApi.list(), [])
  const history = useApi(() => inspectionApi.list({ page, page_size: 12, decision, camera_id: cameraId ? Number(cameraId) : undefined, batch_id: batch || undefined }), [page, decision, cameraId, batch])
  const data = history.data
  const first = data?.total ? (data.page - 1) * data.page_size + 1 : 0
  const last = data ? Math.min(data.page * data.page_size, data.total) : 0
  const filtering = Boolean(decision || cameraId || batch)
  const { status: liveStatus, onInspection } = useLiveFeed()
  const reloadRef = useRef(history.reload)
  useEffect(() => { reloadRef.current = history.reload })
  const canSignQa = user?.role === 'owner' || user?.role === 'admin'
  const canWriteBatch = user?.role === 'owner' || user?.role === 'admin' || user?.role === 'operator' || user?.role === 'ai_engineer'

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

  const createBatch = async (e: FormEvent) => {
    e.preventDefault()
    if (!newBatchCode.trim()) return
    try {
      await batchApi.create({
        batch_code: newBatchCode.trim(),
        sku_code: newBatchSku.trim() || 'BIS-COK-100G',
        shift: newBatchShift,
        target_qty: 5000,
      })
      setNewBatchCode('')
      batches.reload()
      toast('Production Batch baru berhasil dimulai.')
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    }
  }

  const signBatch = async (id: number) => {
    try {
      await batchApi.sign(id, 'Disahkan oleh QA Supervisor — Sesuai Standar HACCP / ISO 22000 / BPOM')
      batches.reload()
      toast('Tanda tangan digital SHA-256 QA Supervisor berhasil dibubuhkan.')
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    }
  }

  const downloadBatchReport = async (id: number, batchCode: string, format: 'pdf' | 'csv') => {
    try {
      const blob = await batchApi.exportReport(id, format)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `batch-audit-${batchCode}.${format}`
      a.click()
      URL.revokeObjectURL(url)
      toast(`Laporan Audit Batch (${format.toUpperCase()}) berhasil diunduh.`)
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    }
  }

  return (
    <div className="page history-page">
      <PageHeader eyebrow="TRACEABILITY / INSPECTION LOG & HACCP AUDIT" title={<>Jejak yang bisa<br /><span className="text-muted">dipertanggungjawabkan.</span></>}
        description="Telusuri keputusan per Production Batch/Shift, retensi bukti audit (100% FAIL 2 tahun, 1-in-500 PASS 7 hari), dan unduh laporan resmi berstempel tanda tangan digital QA Supervisor."
        actions={<Link to="/inspections/new" className="button button--primary">Inspeksi baru<ArrowRight size={16} /></Link>} />

      <section className="panel" aria-label="Manajemen Production Batch & Audit Pangan" style={{ marginBottom: '1.25rem' }}>
        <header className="panel-header">
          <div>
            <p className="eyebrow">HACCP / ISO 22000 / FSSC 22000 / BPOM</p>
            <h2>Production Batch & Laporan Audit QA</h2>
          </div>
          {canWriteBatch && (
            <form onSubmit={createBatch} style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
              <input
                value={newBatchCode}
                onChange={(e) => setNewBatchCode(e.target.value)}
                placeholder="Kode Batch baru (mis. BATCH-20261117-S1)"
                aria-label="Kode Batch baru"
              />
              <input
                value={newBatchSku}
                onChange={(e) => setNewBatchSku(e.target.value)}
                placeholder="SKU (mis. BIS-COK-100G)"
                aria-label="Kode SKU Batch"
                style={{ width: '140px' }}
              />
              <select value={newBatchShift} onChange={(e) => setNewBatchShift(e.target.value)} aria-label="Pilih Shift">
                <option value="Shift 1">Shift 1</option>
                <option value="Shift 2">Shift 2</option>
                <option value="Shift 3">Shift 3</option>
              </select>
              <Button type="submit" variant="secondary"><Plus size={15} />Mulai Batch</Button>
            </form>
          )}
        </header>
        {!!(batches.data ?? []).length && (
          <div style={{ display: 'grid', gap: '0.75rem', padding: '0.75rem 1rem' }}>
            {(batches.data ?? []).slice(0, 5).map((b) => (
              <div key={b.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem', borderBottom: '1px solid var(--border, #2a2e37)', paddingBottom: '0.65rem' }}>
                <div>
                  <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                    <strong>{b.batch_code}</strong>
                    <StatusBadge value={b.status === 'quarantined' ? 'error' : b.status === 'completed' ? 'connected' : 'active'} />
                    <span>SKU: <code>{b.sku_code}</code> · {b.shift}</span>
                  </div>
                  <small style={{ display: 'block', marginTop: '0.25rem' }}>
                    Total: <strong>{b.total_inspected}</strong> (PASS: {b.total_pass} / Yield {b.yield_pct}% · FAIL: {b.total_fail} · REVIEW: {b.total_review})
                    {' | '}Defect: Seal Leak: {b.defect_counts.seal_leak ?? 0}, No Exp Date: {b.defect_counts.missing_exp_date ?? 0}, Foreign: {b.defect_counts.foreign_matter ?? 0}, Burnt: {b.defect_counts.burnt ?? 0}
                  </small>
                  {b.qa_signature_hash && (
                    <small style={{ display: 'block', marginTop: '0.15rem' }}>
                      <ShieldCheck size={13} style={{ verticalAlign: 'middle', marginRight: '4px' }} />
                      QA Signed oleh <strong>{b.qa_signed_by_username}</strong> · SHA-256: <code>{b.qa_signature_hash.slice(0, 16)}…</code>
                    </small>
                  )}
                </div>
                <div style={{ display: 'flex', gap: '0.45rem', flexWrap: 'wrap' }}>
                  <Button variant="secondary" onClick={() => { setBatchInput(b.batch_code); setBatch(b.batch_code); setPage(1) }}>
                    Filter Log
                  </Button>
                  {canSignQa && !b.qa_signature_hash && (
                    <Button variant="secondary" onClick={() => signBatch(b.id)}>
                      <CheckCircle2 size={15} />Tanda Tangan QA
                    </Button>
                  )}
                  <Button variant="secondary" onClick={() => downloadBatchReport(b.id, b.batch_code, 'pdf')}>
                    <Download size={15} />Export PDF
                  </Button>
                  <Button variant="secondary" onClick={() => downloadBatchReport(b.id, b.batch_code, 'csv')}>
                    <Download size={15} />Export CSV
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
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
