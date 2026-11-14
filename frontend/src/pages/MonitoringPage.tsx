import { Activity, Boxes, Database, Radio, RefreshCw, ServerCog } from 'lucide-react'
import { modelApi, systemApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import type { ModelVersion } from '../types'

const stageLabels: Record<string, string> = {
  development: 'Pengembangan',
  staging: 'Staging',
  production: 'Produksi',
}

export default function MonitoringPage() {
  const health = useApi(() => systemApi.health(), [])
  const models = useApi(() => modelApi.list(), [])

  return (
    <div className="page monitoring-page">
      <PageHeader
        eyebrow="MONITORING"
        title={<>Kesehatan platform.<br /><span className="text-muted">Transparan sampai ke bus dan worker.</span></>}
        description="Mode aktif event bus & worker, status basis data, tahapan model registry, dan drift fitur (PSI) dari pemeriksaan terjadwal."
        actions={<Button variant="secondary" onClick={() => { void health.reload(); void models.reload() }}><RefreshCw size={16} />Muat ulang</Button>}
      />

      {health.isLoading && <LoadingBlock label="Membaca status layanan…" />}
      {health.error && <ErrorState message={health.error} retry={health.reload} />}

      {health.data && (
        <div className="monitor-grid">
          <div className={`monitor-card ${health.data.status === 'ok' ? 'monitor-card--ok' : 'monitor-card--warn'}`}>
            <Activity size={20} />
            <div>
              <h3>Status keseluruhan</h3>
              <StatusBadge value={health.data.status === 'ok' ? 'active' : 'error'} />
              <small>versi {health.data.version} · {health.data.environment}</small>
            </div>
          </div>
          <div className="monitor-card">
            <Database size={20} />
            <div>
              <h3>Basis data</h3>
              <StatusBadge value={health.data.database === 'ok' ? 'active' : 'error'} />
              <small>{health.data.database === 'ok' ? 'Koneksi normal' : 'Koneksi bermasalah'}</small>
            </div>
          </div>
          <div className="monitor-card">
            <Radio size={20} />
            <div>
              <h3>Event bus</h3>
              <StatusBadge value={health.data.event_bus?.connected ? 'active' : 'error'} />
              <small>
                mode <strong>{health.data.event_bus?.mode ?? '—'}</strong>
                {health.data.event_bus?.configured ? ' (Redis dikonfigurasi)' : ' (memori proses)'}
                {health.data.event_bus?.mode === 'redis' && !health.data.event_bus?.connected ? ' — terdegradasi ke lokal' : ''}
              </small>
            </div>
          </div>
          <div className="monitor-card">
            <ServerCog size={20} />
            <div>
              <h3>Worker antrean</h3>
              <StatusBadge value={health.data.worker?.degraded ? 'error' : 'active'} />
              <small>
                mode <strong>{health.data.worker?.mode ?? '—'}</strong> · dead-letter {health.data.worker?.dead_letter_count ?? 0}
              </small>
            </div>
          </div>
        </div>
      )}

      <section className="stage-section" aria-labelledby="stage-heading">
        <h2 id="stage-heading"><Boxes size={18} /> Tahapan model registry</h2>
        {models.isLoading && <LoadingBlock label="Membaca registry…" />}
        {models.error && <ErrorState message={models.error} retry={models.reload} />}
        {!models.isLoading && !models.error && !models.data?.items.length && (
          <EmptyState title="Belum ada model" description="Registrasikan model untuk memantau tahapannya." />
        )}
        <ul className="document-list">
          {(models.data?.items ?? []).map((model: ModelVersion & { stage?: string }) => (
            <li className="document-item" key={model.id}>
              <div className="document-info">
                <strong>{model.name}</strong>
                <small>
                  {model.task_type ?? 'tugas belum diatur'} · checksum {model.checksum ? `${model.checksum.slice(0, 12)}…` : '—'} ·
                  tahap sebelumnya: {model.previous_stage ? stageLabels[model.previous_stage] ?? model.previous_stage : '—'}
                </small>
              </div>
              <StatusBadge value={model.stage === 'production' ? 'active' : model.stage === 'staging' ? 'warning' : 'inactive'} />
              <span className="stage-label">{stageLabels[model.stage ?? 'development'] ?? model.stage}</span>
            </li>
          ))}
        </ul>
        <p className="page-footnote"><span>*</span> Drift PSI per fitur diekspos sebagai metrik <code>iv_model_drift_psi</code> (Prometheus/Grafana) oleh tugas terjadwal; model tanpa statistik referensi pelatihan dilaporkan sebagai galat eksplisit, bukan nol.</p>
      </section>
    </div>
  )
}
