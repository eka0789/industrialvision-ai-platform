import { useState } from 'react'
import type { FormEvent } from 'react'
import { Activity, Check, CircleAlert, Cog, Plus, X } from 'lucide-react'
import { pdmApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { Machine, PdmPrediction } from '../types'
import { errorMessage } from '../utils/errors'
import { formatDate } from '../utils/format'

const priorityLabels: Record<string, string> = {
  low: 'Rendah',
  medium: 'Sedang',
  high: 'Tinggi',
  critical: 'Kritis',
}

const modeLabels: Record<string, string> = {
  model: 'Model terdaftar',
  'rules-only-fallback': 'Aturan ambang (fallback)',
  error: 'Gagal — aman',
}

const machineTypeLabels: Record<string, string> = {
  HEAT_SEALER: 'Heat Sealer Kemasan (HEAT_SEALER)',
  FLOW_WRAPPER: 'Horizontal Flow Wrapper (FLOW_WRAPPER)',
  CONVEYOR_DRIVE: 'Motor & Drive Konveyor (CONVEYOR_DRIVE)',
  BAKING_OVEN: 'Oven Pemanggang (BAKING_OVEN)',
  CHILLER_TUNNEL: 'Chiller / Cold Chain Tunnel (CHILLER_TUNNEL)',
  food_packaging: 'Flow-Wrap / Heat Sealer Pangan',
  baking_oven: 'Oven Pemanggang (Baking Oven)',
  chiller: 'Chiller / Cold Chain',
  conveyor: 'Conveyor Lini Kemasan',
}

function healthTone(health: number | null): string {
  if (health === null) return 'card-metric--muted'
  if (health >= 0.75) return 'card-metric--ok'
  if (health >= 0.5) return 'card-metric--warning'
  return 'card-metric--danger'
}

export default function MaintenancePage() {
  const { user } = useAuth()
  const toast = useToast()
  const machines = useApi(() => pdmApi.machines(), [])
  const requests = useApi(() => pdmApi.requests(), [])
  const [predictingId, setPredictingId] = useState<number | null>(null)
  const [prediction, setPrediction] = useState<PdmPrediction | null>(null)
  const [creating, setCreating] = useState(false)
  const [decidingId, setDecidingId] = useState<number | null>(null)

  const canWrite = user?.role === 'owner' || user?.role === 'admin' || user?.role === 'operator' || user?.role === 'ai_engineer'
  const canApprove = user?.role === 'owner' || user?.role === 'admin'
  const pending = (requests.data ?? []).filter((request) => request.status === 'pending')

  const predict = async (machine: Machine) => {
    setPredictingId(machine.id)
    try {
      setPrediction(await pdmApi.predict(machine.id))
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setPredictingId(null)
    }
  }

  const decide = async (id: number, approve: boolean) => {
    setDecidingId(id)
    try {
      if (approve) {
        await pdmApi.approveRequest(id)
        toast('Permintaan disetujui.')
      } else {
        await pdmApi.rejectRequest(id)
        toast('Permintaan ditolak.')
      }
      requests.reload()
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setDecidingId(null)
    }
  }

  return (
    <div className="page maintenance-page">
      <PageHeader
        eyebrow="PERAWATAN PREDIKTIF"
        title={<>Rawat sebelum rusak.<br /><span className="text-muted">Keputusan tetap di tangan manusia.</span></>}
        description="Kesehatan mesin dari sensor (skema AI4I 2020). Prediksi bersifat dukungan keputusan; persetujuan perawatan selalu memerlukan persetujuan manusia."
        actions={canWrite ? <Button onClick={() => setCreating(true)}><Plus size={17} />Daftarkan mesin</Button> : undefined}
      />

      {machines.isLoading && <LoadingBlock label="Membaca daftar mesin…" />}
      {machines.error && <ErrorState message={machines.error} retry={machines.reload} />}
      {!machines.isLoading && !machines.error && !machines.data?.length && (
        <EmptyState
          title="Belum ada mesin terdaftar"
          description={canWrite ? 'Daftarkan mesin pertama lalu kirim data sensor untuk mulai dipantau.' : 'Administrator perlu mendaftarkan mesin terlebih dahulu.'}
          action={canWrite ? <Button onClick={() => setCreating(true)}><Plus size={17} />Daftarkan mesin</Button> : undefined}
        />
      )}

      {!machines.isLoading && !machines.error && !!machines.data?.length && (
        <div className="machine-grid">
          {machines.data.map((machine) => (
            <article className="machine-card" key={machine.id} data-testid={`machine-${machine.code}`}>
              <div className="machine-card-top">
                <span className="machine-code" aria-hidden="true"><Cog size={16} />{machine.code}</span>
                <StatusBadge value={machine.status} />
              </div>
              <h2>{machine.name}</h2>
              <p className="machine-type">{machineTypeLabels[machine.machine_type] ?? machine.machine_type}</p>
              <div className={`card-metric ${healthTone(null)}`} aria-live="polite">
                <Activity size={18} />
                <div>
                  <strong>Indikator Mesin Pangan (Sealer Temp, Air Pressure, Motor Load, Vibration)</strong>
                  <span>Tekan "Prediksi" atau "Korelasi Vision ↔ PdM" untuk evaluasi real-time.</span>
                </div>
              </div>
              <footer className="machine-card-actions" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                <Button variant="secondary" loading={predictingId === machine.id} onClick={() => predict(machine)}>
                  Prediksi
                </Button>
                {canWrite && (
                  <>
                    <Button
                      variant="secondary"
                      onClick={async () => {
                        try {
                          await pdmApi.addReading(machine.id, {
                            sealer_temp_c: 128.0,
                            pneumatic_pressure_bar: 4.5,
                            zone_temp_c: 185.0,
                            conveyor_speed_mpm: 120.0,
                            motor_current_amp: 8.6,
                            vibration_mm_s: 2.1,
                            ambient_humidity_pct: 62.0,
                          })
                          toast('Data sensor Heat Sealer (128°C) & Pneumatik (4.5 bar) terekam.')
                          await predict(machine)
                        } catch (reason) {
                          toast(errorMessage(reason), 'warning')
                        }
                      }}
                    >
                      Simulasi Sealer Drop (128°C)
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={async () => {
                        try {
                          const res = await pdmApi.correlate(machine.id, true)
                          if (res.correlated) {
                            toast(`Korelasi terdeteksi: ${res.diagnosis}`)
                            requests.reload()
                          } else {
                            toast(res.diagnosis ?? 'Tidak ada lonjakan SEAL_DEFECT berkorelasi.')
                          }
                          await predict(machine)
                        } catch (reason) {
                          toast(errorMessage(reason), 'warning')
                        }
                      }}
                    >
                      Korelasi Vision ↔ PdM
                    </Button>
                  </>
                )}
              </footer>
            </article>
          ))}
        </div>
      )}

      {prediction && <PredictionPanel prediction={prediction} onClose={() => setPrediction(null)} />}

      <section className="request-section" aria-labelledby="request-heading">
        <h2 id="request-heading">Antrean permintaan perawatan</h2>
        {requests.isLoading && <LoadingBlock label="Membaca antrean…" />}
        {requests.error && <ErrorState message={requests.error} retry={requests.reload} />}
        {!requests.isLoading && !requests.error && !pending.length && (
          <EmptyState title="Tidak ada permintaan menunggu" description="Permintaan baru dari operator akan muncul di sini untuk persetujuan." />
        )}
        {!!pending.length && (
          <ul className="request-list">
            {pending.map((request) => (
              <li className="request-item" key={request.id}>
                <div className="request-info">
                  <span className={`priority priority--${request.priority}`}>{priorityLabels[request.priority] ?? request.priority}</span>
                  <div>
                    <strong>{request.title}</strong>
                    <small>Mesin #{request.machine_id} · diajukan {formatDate(request.created_at)}</small>
                  </div>
                </div>
                {canApprove && (
                  <div className="request-actions">
                    <Button loading={decidingId === request.id} onClick={() => decide(request.id, true)}><Check size={15} />Setujui</Button>
                    <Button variant="secondary" disabled={decidingId === request.id} onClick={() => decide(request.id, false)}><X size={15} />Tolak</Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {!canApprove && !!pending.length && (
          <p className="page-footnote"><span>*</span> Persetujuan memerlukan peran owner atau admin (pengesahan manusia).</p>
        )}
      </section>
      {creating && (
        <MachineFormModal
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false)
            machines.reload()
            toast('Mesin berhasil didaftarkan.')
          }}
        />
      )}
    </div>
  )
}

function PredictionPanel({ prediction, onClose }: { prediction: PdmPrediction; onClose: () => void }) {
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="prediction-title">
      <div className="modal prediction-modal">
        <div className="modal-head">
          <h2 id="prediction-title">Prediksi mesin #{prediction.machine_id}</h2>
          <button className="icon-button" onClick={onClose} aria-label="Tutup prediksi"><X size={18} /></button>
        </div>
        {prediction.mode === 'error' ? (
          <div className="card-metric card-metric--danger" role="alert">
            <CircleAlert size={18} />
            <div>
              <strong>Prediksi gagal (aman)</strong>
              <span>{prediction.error}</span>
            </div>
          </div>
        ) : (
          <>
            <p className="prediction-mode">Sumber: {modeLabels[prediction.mode] ?? prediction.mode}{prediction.note ? ` — ${prediction.note}` : ''}</p>
            <dl className="prediction-grid">
              <div><dt>Skor kesehatan</dt><dd>{prediction.health !== null ? `${Math.round(prediction.health * 100)}%` : '—'}</dd></div>
              <div><dt>Probabilitas kegagalan</dt><dd>{prediction.failure_probability !== null ? `${(prediction.failure_probability * 100).toFixed(1)}%` : '— (aturan ambang)'}</dd></div>
              <div><dt>Sisa umur (RUL)</dt><dd>{prediction.rul_minutes !== null ? `${prediction.rul_minutes.toFixed(0)} menit` : '—'}</dd></div>
              <div><dt>Rekomendasi</dt><dd>{prediction.recommendation}</dd></div>
            </dl>
            {!!prediction.failure_modes.length && (
              <p className="prediction-modes">Mode kegagalan terpantau: {prediction.failure_modes.join(', ')}</p>
            )}
            {prediction.food_features && (
              <dl className="prediction-grid" style={{ marginTop: '0.6rem' }}>
                <div><dt>Sealer Temp</dt><dd>{prediction.food_features.sealer_temp_c ?? '—'} °C</dd></div>
                <div><dt>Air Pressure</dt><dd>{prediction.food_features.pneumatic_pressure_bar ?? '—'} bar</dd></div>
                <div><dt>Motor Load</dt><dd>{prediction.food_features.motor_current_amp ?? prediction.food_features.motor_current_a ?? '—'} A</dd></div>
                <div><dt>Vibration</dt><dd>{prediction.food_features.vibration_mm_s ?? '—'} mm/s</dd></div>
                <div><dt>Conveyor Speed</dt><dd>{prediction.food_features.conveyor_speed_mpm ?? '—'} m/min</dd></div>
                <div><dt>Ambient Humidity</dt><dd>{prediction.food_features.ambient_humidity_pct ?? '—'} %</dd></div>
              </dl>
            )}
            {prediction.vision_correlation && (
              <div className={`card-metric ${prediction.vision_correlation.correlated ? 'card-metric--danger' : 'card-metric--ok'}`} style={{ marginTop: '0.75rem' }}>
                <CircleAlert size={18} />
                <div>
                  <strong>
                    {prediction.vision_correlation.correlated
                      ? 'Badge Korelasi Vision AI ↔ PdM: ANOMALI TERKONFIRMASI'
                      : 'Badge Korelasi Vision AI ↔ PdM: Normal'}
                  </strong>
                  <span>{prediction.vision_correlation.diagnosis ?? 'Tidak ada lonjakan SEAL_DEFECT berkorelasi.'}</span>
                </div>
              </div>
            )}
          </>
        )}
        <p className="page-footnote"><span>*</span> Skor kesehatan = 1 − probabilitas kegagalan (mode model), atau jumlah ambang terpicu (mode aturan). Bukan pengganti keputusan teknisi.</p>
      </div>
    </div>
  )
}

function MachineFormModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [type, setType] = useState('HEAT_SEALER')
  const [saving, setSaving] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    try {
      await pdmApi.createMachine({ name, code, machine_type: type })
      onSaved()
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="machine-form-title">
      <div className="modal">
        <div className="modal-head">
          <h2 id="machine-form-title">Daftarkan mesin</h2>
          <button className="icon-button" onClick={onClose} aria-label="Tutup formulir"><X size={18} /></button>
        </div>
        <form onSubmit={submit} className="form-stack">
          <label>
            Nama mesin
            <input value={name} onChange={(event) => setName(event.target.value)} required minLength={1} maxLength={160} aria-label="Nama mesin" />
          </label>
          <label>
            Kode mesin
            <input value={code} onChange={(event) => setCode(event.target.value)} required minLength={1} maxLength={80} aria-label="Kode mesin" placeholder="mis. FW-SEAL-01" />
          </label>
          <label>
            Tipe
            <select value={type} onChange={(event) => setType(event.target.value)} aria-label="Tipe mesin">
              <option value="HEAT_SEALER">Heat Sealer Kemasan (HEAT_SEALER)</option>
              <option value="FLOW_WRAPPER">Horizontal Flow Wrapper (FLOW_WRAPPER)</option>
              <option value="CONVEYOR_DRIVE">Motor & Drive Konveyor (CONVEYOR_DRIVE)</option>
              <option value="BAKING_OVEN">Oven Pemanggang (BAKING_OVEN)</option>
              <option value="CHILLER_TUNNEL">Chiller / Cold Chain Tunnel (CHILLER_TUNNEL)</option>
              <option value="food_packaging">Flow-Wrap / Heat Sealer Pangan (Legacy)</option>
              <option value="press">Press</option>
              <option value="cnc">CNC</option>
              <option value="robot">Robot</option>
              <option value="generic">Umum</option>
            </select>
          </label>
          <div className="modal-actions">
            <Button type="button" variant="secondary" onClick={onClose}>Batal</Button>
            <Button type="submit" loading={saving}>Simpan</Button>
          </div>
        </form>
      </div>
    </div>
  )
}
