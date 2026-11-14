import { useCallback, useMemo, useState } from 'react'
import { FlaskConical, Play, RotateCcw, Table2 } from 'lucide-react'
import { trainingApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { formatDate } from '../utils/format'

/* ── G1: client-side Q-learning maintenance-scheduling simulator ──────────
 *
 * EDUCATIONAL SIMULATION — NOT CONNECTED TO PRODUCTION DECISIONS.
 * Everything runs in this browser tab; no API calls, no backend state.
 * Environment: a machine with discrete health 0..HEALTH-1 that degrades each
 * step; the agent chooses wait / inspect / overhaul. Rewards encode downtime
 * and maintenance cost trade-offs. This is a teaching toy, not an optimizer.
 */

const HEALTH = 6
const ACTIONS = ['Tunggu', 'Inspeksi', 'Overhaul'] as const
type Action = (typeof ACTIONS)[number]
const COSTS = [0, 2, 10]
const BREAKDOWN_PENALTY = 40
const EPISODES = 400
const STEPS = 60

interface SimResult {
  rewards: number[]
  qTable: number[][]
  policy: Action[]
  breakdowns: number
}

function simulate(seed: number, alpha: number, epsilon: number): SimResult {
  // Deterministic PRNG (mulberry32) so reruns with the same seed reproduce.
  let state = seed >>> 0
  const rand = () => {
    state = (state + 0x6d2b79f5) | 0
    let t = Math.imul(state ^ (state >>> 15), 1 | state)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  const q: number[][] = Array.from({ length: HEALTH }, () => [0, 0, 0])
  const rewards: number[] = []
  let breakdowns = 0

  const stepEnv = (health: number, action: Action): { next: number; reward: number; broke: boolean } => {
    if (action === 'Overhaul') return { next: HEALTH - 1, reward: -COSTS[2], broke: false }
    if (action === 'Inspeksi') return { next: Math.max(0, health - (rand() < 0.55 ? 1 : 0)), reward: -COSTS[1], broke: false }
    // Wait: degrades; breakdown chance grows as health drops.
    const breakChance = [0.02, 0.05, 0.1, 0.2, 0.4, 0][health]
    if (rand() < breakChance) return { next: HEALTH - 1, reward: -BREAKDOWN_PENALTY, broke: true }
    return { next: Math.max(0, health - 1), reward: -COSTS[0], broke: false }
  }

  for (let episode = 0; episode < EPISODES; episode += 1) {
    let health = HEALTH - 1
    let total = 0
    for (let step = 0; step < STEPS; step += 1) {
      const explore = rand() < epsilon * (1 - episode / EPISODES)
      const actionIndex = explore ? Math.floor(rand() * 3) : q[health].indexOf(Math.max(...q[health]))
      const action = ACTIONS[actionIndex]
      const { next, reward, broke } = stepEnv(health, action)
      if (broke) breakdowns += 1
      q[health][actionIndex] += alpha * (reward + 0.95 * Math.max(...q[next]) - q[health][actionIndex])
      health = next
      total += reward
    }
    rewards.push(total)
  }

  const policy = q.map((row) => ACTIONS[row.indexOf(Math.max(...row))])
  return { rewards, qTable: q, policy, breakdowns }
}

function Simulator() {
  const [seed, setSeed] = useState(7)
  const [alpha, setAlpha] = useState(0.2)
  const [epsilon, setEpsilon] = useState(0.3)
  const [result, setResult] = useState<SimResult | null>(null)

  const run = useCallback(() => setResult(simulate(seed, alpha, epsilon)), [seed, alpha, epsilon])
  const chart = useMemo(() => {
    if (!result) return null
    const window = 20
    const series: number[] = []
    for (let i = 0; i + window <= result.rewards.length; i += window) {
      series.push(result.rewards.slice(i, i + window).reduce((sum, value) => sum + value, 0) / window)
    }
    const min = Math.min(...series)
    const max = Math.max(...series)
    return series.map((value, index) => ({
      x: (index / (series.length - 1 || 1)) * 100,
      y: 100 - ((value - min) / (max - min || 1)) * 80 - 10,
      value,
    }))
  }, [result])

  return (
    <section className="sim-section" aria-labelledby="sim-heading">
      <h2 id="sim-heading"><FlaskConical size={18} /> Simulasi penjadwalan perawatan (Q-learning)</h2>
      <div className="sim-warning" role="note">
        <strong>Simulasi edukasi — TIDAK terhubung ke keputusan produksi.</strong> Semua komputasi
        berjalan di browser Anda; tidak ada data pabrik atau backend yang dilibatkan.
      </div>
      <div className="sim-controls">
        <label>Seed <input type="number" value={seed} min={0} max={999999} onChange={(e) => setSeed(Number(e.target.value))} aria-label="Seed simulasi" /></label>
        <label>Learning rate α <input type="number" step="0.05" min="0.05" max="1" value={alpha} onChange={(e) => setAlpha(Number(e.target.value))} aria-label="Learning rate" /></label>
        <label>Eksplorasi ε <input type="number" step="0.05" min="0" max="1" value={epsilon} onChange={(e) => setEpsilon(Number(e.target.value))} aria-label="Eksplorasi" /></label>
        <Button onClick={run}><Play size={15} />Jalankan {EPISODES} episode</Button>
        <Button variant="secondary" onClick={() => setResult(null)}><RotateCcw size={15} />Reset</Button>
      </div>
      {result && (
        <div className="sim-results" data-testid="sim-results">
          <p>
            Kebijakan yang dipelajari per tingkat kesehatan (0 = hampir rusak … {HEALTH - 1} = sehat):{' '}
            {result.policy.map((action, index) => (
              <span key={index} className="policy-chip">{index}: {action}</span>
            ))}
          </p>
          <p><strong>Total breakdown:</strong> {result.breakdowns} dari {EPISODES * STEPS} langkah</p>
          {chart && (
            <svg className="sim-chart" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Rata-rata reward per jendela episode">
              {chart.map((point, index) => (
                <circle key={index} cx={point.x} cy={point.y} r={0.9} fill="currentColor" />
              ))}
            </svg>
          )}
          <p className="page-footnote"><span>*</span> Sumbu: rata-rata reward per 20 episode (naik = agen belajar menghindari breakdown mahal). Reward lingkungan adalah buatan, bukan data pabrik.</p>
        </div>
      )}
    </section>
  )
}

/* ── G2: read-only experiment browser over the training-run lifecycle ───── */

function ExperimentBrowser() {
  const runs = useApi(() => trainingApi.runs(), [])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = (runs.data ?? []).find((run) => run.run_id === selectedId) ?? null

  return (
    <section className="experiment-section" aria-labelledby="experiment-heading">
      <h2 id="experiment-heading"><Table2 size={18} /> Browser eksperimen (read-only)</h2>
      {runs.isLoading && <LoadingBlock label="Membaca direktori run…" />}
      {runs.error && <ErrorState message={runs.error} retry={runs.reload} />}
      {!runs.isLoading && !runs.error && !runs.data?.length && (
        <EmptyState title="Belum ada run tercatat" description="Jalankan python -m app.pdm.train atau pipeline pelatihan lain untuk mengisi direktori run." />
      )}
      {!!runs.data?.length && (
        <table className="experiment-table">
          <thead>
            <tr>
              <th scope="col">Run</th><th scope="col">Tugas</th><th scope="col">Seed</th>
              <th scope="col">Status</th><th scope="col">Selesai</th><th scope="col" />
            </tr>
          </thead>
          <tbody>
            {runs.data.map((run) => (
              <tr key={run.run_id}>
                <td className="mono">{run.run_id}</td>
                <td>{run.task}</td>
                <td>{run.seed}</td>
                <td><StatusBadge value={run.status === 'completed' ? 'active' : run.status === 'failed' ? 'error' : 'warning'} /></td>
                <td>{run.completed_at ? formatDate(run.completed_at) : '—'}</td>
                <td><Button variant="secondary" onClick={() => setSelectedId(run.run_id)}>Detail</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {selected && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="run-detail-title">
          <div className="modal">
            <div className="modal-head">
              <h2 id="run-detail-title" className="mono">{selected.run_id}</h2>
              <button className="icon-button" aria-label="Tutup detail run" onClick={() => setSelectedId(null)}>✕</button>
            </div>
            <dl className="prediction-grid">
              <div><dt>Status</dt><dd>{selected.status}</dd></div>
              <div><dt>Seed</dt><dd>{selected.seed}</dd></div>
              <div><dt>Dataset</dt><dd>{selected.dataset?.name ?? '—'}</dd></div>
              <div><dt>Fingerprint</dt><dd className="mono" style={{ fontSize: 12 }}>{selected.dataset?.fingerprint ?? '—'}</dd></div>
            </dl>
            {selected.metrics && (
              <pre className="chunk-item" style={{ overflow: 'auto' }}>{JSON.stringify(selected.metrics, null, 2)}</pre>
            )}
            {selected.reason && <p className="document-error" role="alert">Alasan gagal: {selected.reason}</p>}
            <h3>Artefak & checksum</h3>
            <ul className="chunk-list">
              {(selected.checkpoints ?? []).map((checkpoint) => (
                <li className="chunk-item" key={checkpoint.path}>
                  <span className="chunk-index mono">{checkpoint.path}</span>
                  <p className="mono" style={{ fontSize: 12 }}>
                    {checkpoint.exists ? `sha256: ${checkpoint.sha256} · ${checkpoint.size_bytes} B` : 'file tidak ada di disk'}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  )
}

export default function LabPage() {
  return (
    <div className="page lab-page">
      <PageHeader
        eyebrow="ML LABORATORY"
        title={<>Area eksperimen.<br /><span className="text-muted">Belajar tanpa menyentuh produksi.</span></>}
        description="Simulator RL edukasi murni sisi-browser, dan browser eksperimen read-only di atas data lifecycle pelatihan yang sudah ada."
      />
      <Simulator />
      <ExperimentBrowser />
    </div>
  )
}
