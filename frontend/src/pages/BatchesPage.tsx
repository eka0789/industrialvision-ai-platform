import { useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, Download, FileSpreadsheet, FileText, Pause, Play, Plus, ShieldCheck, Square } from 'lucide-react'
import { batchApi, skuApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { ProductionBatch } from '../types'
import { errorMessage } from '../utils/errors'
import { formatDate } from '../utils/format'

export default function BatchesPage() {
  const { user } = useAuth()
  const toast = useToast()
  const batches = useApi(() => batchApi.list(), [])
  const skus = useApi(() => skuApi.list(), [])

  const [batchCode, setBatchCode] = useState('')
  const [selectedSkuCode, setSelectedSkuCode] = useState('BIS-COK-100G')
  const [lineName, setLineName] = useState('Line 01 - Biscuit Flow-Wrap')
  const [shift, setShift] = useState('SHIFT_1')
  const [targetQuantity, setTargetQuantity] = useState(5000)
  const [creating, setCreating] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)

  const canWrite = user?.role === 'owner' || user?.role === 'admin' || user?.role === 'operator' || user?.role === 'ai_engineer'
  const canSignQa = user?.role === 'owner' || user?.role === 'admin'

  const startNewBatch = async (e: FormEvent) => {
    e.preventDefault()
    const code = batchCode.trim() || `LOT-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}-A1`
    setCreating(true)
    try {
      const matchedSku = (skus.data ?? []).find((s) => s.sku_code === selectedSkuCode)
      await batchApi.create({
        batch_code: code,
        sku_id: matchedSku?.id ?? null,
        sku_code: selectedSkuCode.trim() || 'BIS-COK-100G',
        line_name: lineName.trim() || 'Line 01',
        shift,
        target_quantity: Number(targetQuantity) || 1000,
      })
      setBatchCode('')
      batches.reload()
      toast(`Batch produksi "${code}" dimulai pada ${shift}.`)
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setCreating(false)
    }
  }

  const handleLifecycle = async (batch: ProductionBatch, action: 'pause' | 'resume' | 'close' | 'sign') => {
    setBusyId(batch.id)
    try {
      if (action === 'pause') {
        await batchApi.pause(batch.id)
        toast(`Batch ${batch.batch_code} dijeda (PAUSED).`)
      } else if (action === 'resume') {
        await batchApi.resume(batch.id)
        toast(`Batch ${batch.batch_code} dilanjutkan (ACTIVE).`)
      } else if (action === 'close') {
        await batchApi.close(batch.id)
        toast(`Batch ${batch.batch_code} ditutup (CLOSED).`)
      } else if (action === 'sign') {
        await batchApi.sign(batch.id, 'Audit terverifikasi sesuai standar HACCP / ISO 22000 / FSSC 22000 / BPOM')
        toast(`Tanda tangan digital SHA-256 QA Supervisor berhasil dibubuhkan pada ${batch.batch_code}.`)
      }
      batches.reload()
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setBusyId(null)
    }
  }

  const downloadAuditReport = async (batch: ProductionBatch, format: 'pdf' | 'csv') => {
    setBusyId(batch.id)
    try {
      const blob = await batchApi.exportReport(batch.id, format)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `batch-audit-${batch.batch_code}.${format}`
      a.click()
      URL.revokeObjectURL(url)
      toast(`Laporan Audit HACCP (${format.toUpperCase()}) untuk ${batch.batch_code} berhasil diunduh.`)
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="page batches-page">
      <PageHeader
        eyebrow="KEAMANAN PANGAN & KETELUSURAN HACCP"
        title={<>Batch & Laporan Audit.<br /><span className="text-muted">Bukti siap audit untuk setiap lot.</span></>}
        description="Kelola siklus hidup batch/lot per shift, pantau First Pass Yield (%), kebijakan retensi bukti (100% FAIL vs 1-in-100 PASS), dan unduh laporan resmi PDF/CSV."
      />

      {canWrite && (
        <section className="panel panel--padded" aria-label="Mulai Batch Produksi Baru" style={{ marginBottom: '20px' }}>
          <header className="panel-header panel-header--flush">
            <div>
              <p className="eyebrow">BATCH PRODUKSI BARU</p>
              <h2>Mulai Batch / Shift Produksi</h2>
            </div>
          </header>
          <form onSubmit={startNewBatch} className="form-grid form-grid--two">
            <label className="field">
              <span>Kode Batch / Lot <b>*</b></span>
              <input
                required
                value={batchCode}
                onChange={(e) => setBatchCode(e.target.value)}
                placeholder="mis. LOT-20261117-A1"
              />
            </label>
            <label className="field">
              <span>Product SKU</span>
              <input
                list="sku-options"
                value={selectedSkuCode}
                onChange={(e) => setSelectedSkuCode(e.target.value)}
                placeholder="mis. BIS-COK-100G"
              />
              <datalist id="sku-options">
                {(skus.data ?? []).map((s) => (
                  <option key={s.id} value={s.sku_code}>{s.name}</option>
                ))}
              </datalist>
            </label>
            <label className="field">
              <span>Nama Lini Produksi</span>
              <input
                value={lineName}
                onChange={(e) => setLineName(e.target.value)}
                placeholder="Line 01 - Biscuit Flow-Wrap"
              />
            </label>
            <label className="field">
              <span>Shift & Target Kuantitas</span>
              <div className="ot-inline-row">
                <select value={shift} onChange={(e) => setShift(e.target.value)} aria-label="Shift">
                  <option value="SHIFT_1">SHIFT_1 (Pagi)</option>
                  <option value="SHIFT_2">SHIFT_2 (Siang)</option>
                  <option value="SHIFT_3">SHIFT_3 (Malam)</option>
                </select>
                <input
                  type="number"
                  min="1"
                  max="10000000"
                  value={targetQuantity}
                  onChange={(e) => setTargetQuantity(Number(e.target.value))}
                  aria-label="Target Kuantitas"
                />
              </div>
            </label>
            <div className="field field--full form-actions-end">
              <Button type="submit" loading={creating}>
                <Plus size={16} aria-hidden="true" />Mulai Batch Aktif
              </Button>
            </div>
          </form>
        </section>
      )}

      <section className="panel" aria-label="Daftar Batch & Laporan Audit">
        <header className="panel-header">
          <div>
            <p className="eyebrow">HACCP / ISO 22000 / FSSC 22000 / BPOM</p>
            <h2>Riwayat Lot Produksi & Unduh Laporan Audit</h2>
          </div>
        </header>

        {batches.isLoading && <LoadingBlock label="Memuat data batch produksi…" />}
        {batches.error && <ErrorState message={batches.error} retry={batches.reload} />}
        {!batches.isLoading && !batches.error && !(batches.data ?? []).length && (
          <EmptyState title="Belum ada batch produksi" description="Mulai batch produksi pertama di atas untuk melacak First Pass Yield dan bukti audit." />
        )}

        {!!(batches.data ?? []).length && (
          <div className="batch-list">
            {(batches.data ?? []).map((b) => {
              const normStatus = b.status.toUpperCase()
              const isRunning = normStatus === 'ACTIVE'
              const isPaused = normStatus === 'PAUSED'
              return (
                <article className="batch-card" key={b.id} data-testid={`batch-row-${b.batch_code}`}>
                  <div className="batch-card-info">
                    <div className="batch-card-head">
                      <strong>{b.batch_code}</strong>
                      <StatusBadge value={isRunning ? 'active' : isPaused ? 'inactive' : 'connected'} />
                      <span>SKU: <code>{b.sku_code}</code> · {b.line_name ?? 'Line 01'} · <strong>{b.shift}</strong></span>
                    </div>
                    <p className="batch-card-summary">
                      Total Produksi: <strong>{b.total_inspected} / {b.target_quantity ?? b.target_qty}</strong> pcs ·
                      First Pass Yield: <strong>{b.first_pass_yield_pct ?? b.yield_pct}%</strong> (PASS: {b.total_pass} · FAIL: {b.total_fail} · REVIEW: {b.total_review})
                    </p>
                    <small className="batch-card-defects">
                      Rincian Cacat:
                      {' '}SEAL_DEFECT: <strong>{b.defect_counts.SEAL_DEFECT ?? b.defect_counts.seal_leak ?? 0}</strong> ·
                      {' '}MISSING_EXP_DATE: <strong>{b.defect_counts.MISSING_EXP_DATE ?? b.defect_counts.missing_exp_date ?? 0}</strong> ·
                      {' '}BURNT_OR_BROKEN: <strong>{b.defect_counts.BURNT_OR_BROKEN ?? b.defect_counts.burnt ?? 0}</strong> ·
                      {' '}FOREIGN_MATTER: <strong>{b.defect_counts.FOREIGN_MATTER ?? b.defect_counts.foreign_matter ?? 0}</strong>
                    </small>
                    <small className="batch-card-times">
                      Mulai: {formatDate(b.started_at)} · Selesai: {b.closed_at ? formatDate(b.closed_at) : 'Masih Berjalan'}
                    </small>
                    {b.qa_signature_hash && (
                      <small className="batch-card-signed">
                        <ShieldCheck size={14} aria-hidden="true" />
                        <span>Terverifikasi QA Supervisor (<strong>{b.qa_signed_by_username}</strong>) · SHA-256: <code>{b.qa_signature_hash.slice(0, 20)}…</code></span>
                      </small>
                    )}
                  </div>

                  <div className="batch-card-actions">
                    {canWrite && isRunning && (
                      <Button variant="secondary" loading={busyId === b.id} onClick={() => handleLifecycle(b, 'pause')}>
                        <Pause size={15} aria-hidden="true" />Jeda
                      </Button>
                    )}
                    {canWrite && isPaused && (
                      <Button variant="secondary" loading={busyId === b.id} onClick={() => handleLifecycle(b, 'resume')}>
                        <Play size={15} aria-hidden="true" />Lanjutkan
                      </Button>
                    )}
                    {canWrite && (isRunning || isPaused) && (
                      <Button variant="secondary" loading={busyId === b.id} onClick={() => handleLifecycle(b, 'close')}>
                        <Square size={15} aria-hidden="true" />Tutup Batch
                      </Button>
                    )}
                    {canSignQa && !b.qa_signature_hash && (
                      <Button variant="secondary" loading={busyId === b.id} onClick={() => handleLifecycle(b, 'sign')}>
                        <CheckCircle2 size={15} aria-hidden="true" />Verifikasi QA
                      </Button>
                    )}
                    <Button variant="secondary" loading={busyId === b.id} onClick={() => downloadAuditReport(b, 'pdf')}>
                      <FileText size={15} aria-hidden="true" /><Download size={14} aria-hidden="true" />Unduh PDF
                    </Button>
                    <Button variant="secondary" loading={busyId === b.id} onClick={() => downloadAuditReport(b, 'csv')}>
                      <FileSpreadsheet size={15} aria-hidden="true" /><Download size={14} aria-hidden="true" />Unduh CSV
                    </Button>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
