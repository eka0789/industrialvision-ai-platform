import { useState } from 'react'
import type { FormEvent } from 'react'
import { Activity, Bot, Check, CircleAlert, History, Inbox, LoaderCircle, Send, ShieldAlert, X } from 'lucide-react'
import { agentApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { AgentRun, DefectSpikeEvaluation, IncidentTriageResult } from '../types'
import { errorMessage } from '../utils/errors'
import { formatDate } from '../utils/format'

type ProactiveAction = 'FOREIGN_MATTER' | 'LENS_BLUR_OR_FOG' | 'SEAL_SPIKE'

export default function AgentPage() {
  const { user } = useAuth()
  const toast = useToast()
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [runningAction, setRunningAction] = useState<ProactiveAction | null>(null)
  const [current, setCurrent] = useState<AgentRun | null>(null)
  const [triageResult, setTriageResult] = useState<IncidentTriageResult | null>(null)
  const [spikeResult, setSpikeResult] = useState<DefectSpikeEvaluation | null>(null)
  const runs = useApi(() => agentApi.runs(), [current?.id])
  const approvals = useApi(
    () => agentApi.approvals('pending'),
    [current?.id, triageResult?.approval_id, spikeResult?.approval_id],
  )
  const [decidingId, setDecidingId] = useState<number | null>(null)

  const canApprove = user?.role === 'owner' || user?.role === 'admin' || user?.role === 'ai_engineer'

  const triggerIncidentTriage = async (
    incidentType: 'FOREIGN_MATTER' | 'LENS_BLUR_OR_FOG' | 'PSI_DRIFT' | 'MISSING_EXP_DATE' | 'SEAL_DEFECT',
  ) => {
    setRunningAction(incidentType === 'FOREIGN_MATTER' ? 'FOREIGN_MATTER' : 'LENS_BLUR_OR_FOG')
    try {
      const res = await agentApi.triageIncident({ incident_type: incidentType })
      setTriageResult(res)
      toast(
        res.approval_id
          ? `Analisis SOP selesai & tindakan HITL #${res.approval_id} telah disiapkan.`
          : `Analisis SOP selesai untuk insiden ${incidentType}.`,
      )
      approvals.reload()
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setRunningAction(null)
    }
  }

  const evaluateSealSpike = async () => {
    setRunningAction('SEAL_SPIKE')
    try {
      const res = await agentApi.evaluateDefectSpike({
        defect_category: 'seal_leak',
        window_minutes: 5,
        spike_threshold: 3,
      })
      setSpikeResult(res)
      toast(res.recommended_action, res.spike_detected ? 'warning' : 'info')
      approvals.reload()
      runs.reload()
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setRunningAction(null)
    }
  }

  const ask = async (event: FormEvent) => {
    event.preventDefault()
    if (!question.trim()) return
    setAsking(true)
    try {
      setCurrent(await agentApi.chat(question.trim()))
      setQuestion('')
      runs.reload()
      approvals.reload()
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setAsking(false)
    }
  }

  const decide = async (id: number, approve: boolean) => {
    setDecidingId(id)
    try {
      if (approve) {
        const result = await agentApi.approve(id)
        toast(result.status === 'executed' ? 'Disetujui dan dieksekusi.' : `Status: ${result.status}`)
      } else {
        await agentApi.reject(id)
        toast('Permintaan ditolak.')
      }
      approvals.reload()
      setCurrent(null)
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setDecidingId(null)
    }
  }

  return (
    <div className="page agent-page">
      <PageHeader
        eyebrow="OPERATIONS AGENT & HACCP CCP ASSISTANT"
        title={<>Asisten operasi.<br /><span className="text-muted">Tindakan berdampak selalu butuh setuju manusia.</span></>}
        description="Ajukan pertanyaan operasional atau deteksi otomatis lonjakan FAIL 5 menit (Seal Defect) yang berkorelasi dengan suhu Heat Sealer PdM & SOP HACCP di RAG."
      />

      <section className="panel triage-panel" aria-labelledby="triage-heading">
        <header className="triage-panel-header">
          <div>
            <p className="eyebrow"><span aria-hidden="true" />PROACTIVE TRIAGE / HACCP CCP</p>
            <h2 id="triage-heading"><ShieldAlert size={18} /> Deteksi proaktif &amp; tindakan korektif</h2>
          </div>
          <p className="triage-panel-note">Setiap rekomendasi korektif tetap menunggu persetujuan manusia di Kotak Persetujuan.</p>
        </header>

        <div className="triage-grid">
          <article className="triage-card">
            <div className="triage-card-body">
              <span className="triage-card-tag">CCP-01 · KONTAK PRODUK</span>
              <h3>Foreign Matter</h3>
              <p>Analisis temuan benda asing terhadap SOP HACCP &amp; siapkan tindakan mitigasi HITL.</p>
            </div>
            <Button
              variant="secondary"
              loading={runningAction === 'FOREIGN_MATTER'}
              disabled={runningAction !== null}
              onClick={() => triggerIncidentTriage('FOREIGN_MATTER')}
            >
              <CircleAlert size={16} />Analisis &amp; Tindakan Korektif
            </Button>
          </article>

          <article className="triage-card">
            <div className="triage-card-body">
              <span className="triage-card-tag">OPTIK · STASIUN KAMERA</span>
              <h3>Lens Blur / Drift</h3>
              <p>Evaluasi penurunan ketajaman lensa atau kabut pada kamera inspeksi lini produksi.</p>
            </div>
            <Button
              variant="secondary"
              loading={runningAction === 'LENS_BLUR_OR_FOG'}
              disabled={runningAction !== null}
              onClick={() => triggerIncidentTriage('LENS_BLUR_OR_FOG')}
            >
              <Bot size={16} />Analisis &amp; Tindakan Korektif
            </Button>
          </article>

          <article className="triage-card">
            <div className="triage-card-body">
              <span className="triage-card-tag">SEAL INTEGRITY · PDM</span>
              <h3>Lonjakan Seal Defect</h3>
              <p>Deteksi lonjakan FAIL 5 menit yang berkorelasi dengan suhu Heat Sealer PdM.</p>
            </div>
            <Button
              variant="secondary"
              loading={runningAction === 'SEAL_SPIKE'}
              disabled={runningAction !== null}
              onClick={evaluateSealSpike}
            >
              <Activity size={16} />Cek Lonjakan (5 Menit)
            </Button>
          </article>
        </div>
      </section>

      {triageResult && (
        <section className="panel triage-result" aria-label="Hasil Triase Insiden Proaktif">
          <div className="triage-result-head">
            <div>
              <span className="triage-card-tag">HASIL TRIASE PROAKTIF</span>
              <h2>{triageResult.incident_type.replaceAll('_', ' ')}</h2>
            </div>
            <button
              type="button"
              className="icon-button"
              aria-label="Tutup hasil triase"
              onClick={() => setTriageResult(null)}
            >
              <X size={16} />
            </button>
          </div>
          <p className="triage-result-body">{triageResult.sop_answer}</p>
          {!!triageResult.sop_citations.length && (
            <p className="triage-result-meta">
              Sitasi SOP RAG (1:1): {triageResult.sop_citations.map((c) => `[${c.index}] doc #${c.document_id}`).join(', ')}
            </p>
          )}
          {triageResult.approval_id && (
            <p className="triage-result-hitl">
              <strong>Mitigasi HITL Disiapkan:</strong> ID Persetujuan #{triageResult.approval_id} menunggu persetujuan di Kotak Persetujuan di bawah.
            </p>
          )}
        </section>
      )}

      {spikeResult && (
        <section
          className={`panel triage-result ${spikeResult.spike_detected ? 'triage-result--danger' : 'triage-result--ok'}`}
          aria-label="Hasil Evaluasi Lonjakan Seal Defect"
        >
          <div className="triage-result-head">
            <div>
              <span className="triage-card-tag">
                EVALUASI LONJAKAN ({spikeResult.window_minutes} MENIT) · {spikeResult.defect_category.toUpperCase()}
              </span>
              <h2>
                {spikeResult.spike_detected
                  ? `Lonjakan terdeteksi (${spikeResult.matched_fail_count}/${spikeResult.spike_threshold} FAIL)`
                  : `Tidak ada lonjakan (${spikeResult.matched_fail_count}/${spikeResult.spike_threshold} FAIL)`}
              </h2>
            </div>
            <button
              type="button"
              className="icon-button"
              aria-label="Tutup hasil evaluasi lonjakan"
              onClick={() => setSpikeResult(null)}
            >
              <X size={16} />
            </button>
          </div>
          <p className="triage-result-body">{spikeResult.recommended_action}</p>
          {spikeResult.approval_id && (
            <p className="triage-result-hitl">
              <strong>Mitigasi HITL Disiapkan:</strong> ID Persetujuan #{spikeResult.approval_id} menunggu persetujuan di Kotak Persetujuan di bawah.
            </p>
          )}
        </section>
      )}

      <section className="agent-chat" aria-labelledby="agent-chat-heading">
        <h2 id="agent-chat-heading"><Bot size={18} /> Tanya agen</h2>
        <form className="chat-form" onSubmit={ask}>
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="mis. bagaimana status mesin #1? atau: inspeksi terakhir"
            aria-label="Pertanyaan untuk agen"
            maxLength={2000}
          />
          <Button type="submit" loading={asking} disabled={!question.trim()}><Send size={16} />Kirim</Button>
        </form>
        {asking && <p className="chat-status" role="status"><LoaderCircle className="spin" size={16} /> Agen berpikir…</p>}
        {current && (
          <div className="chat-answer" data-testid="agent-answer">
            {current.status === 'failed' ? (
              <p className="refusal" role="alert"><CircleAlert size={16} /> Run gagal (aman): {current.error}</p>
            ) : (
              <p className="answer-body">{current.response}</p>
            )}
            <p className="chat-meta">
              Penyedia perencana: <strong>{current.provider}</strong>
              {current.provider === 'mock-demo' ? ' (perencana aturan lokal — tanpa LLM eksternal)' : ''} · {current.duration_ms ?? '—'} ms
            </p>
            {!!current.tool_logs?.length && (
              <details className="tool-log">
                <summary>Log alat ({current.tool_logs.length})</summary>
                <ul>
                  {current.tool_logs.map((log, index) => (
                    <li key={index}>
                      <StatusBadge value={log.status === 'ok' ? 'active' : log.status === 'refused' ? 'inactive' : 'error'} />
                      <span className="tool-name">{log.tool}</span>
                      <span className="tool-duration">{log.duration_ms} ms</span>
                      {log.error && <span className="document-error">{log.error}</span>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </section>

      {canApprove && (
        <section className="approval-section" aria-labelledby="approval-heading">
          <h2 id="approval-heading"><Inbox size={18} /> Kotak persetujuan</h2>
          {approvals.isLoading && <LoadingBlock label="Membaca persetujuan…" />}
          {approvals.error && <ErrorState message={approvals.error} retry={approvals.reload} />}
          {!approvals.isLoading && !approvals.error && !approvals.data?.length && (
            <EmptyState title="Tidak ada persetujuan menunggu" description="Tindakan agen yang berdampak (mis. membuat permintaan perawatan) akan muncul di sini." />
          )}
          <ul className="request-list">
            {(approvals.data ?? []).map((approval) => (
              <li className="request-item" key={approval.id}>
                <div className="request-info">
                  <span className="priority priority--high">{approval.tool_name}</span>
                  <div>
                    <strong>{String((approval.args as { title?: string } | null)?.title ?? approval.tool_name)}</strong>
                    <small>diminta oleh pengguna #{approval.requested_by_user_id} · kedaluwarsa {formatDate(approval.expires_at)}</small>
                  </div>
                </div>
                <div className="request-actions">
                  <Button loading={decidingId === approval.id} onClick={() => decide(approval.id, true)}><Check size={15} />Setujui & eksekusi</Button>
                  <Button variant="secondary" disabled={decidingId === approval.id} onClick={() => decide(approval.id, false)}><X size={15} />Tolak</Button>
                </div>
              </li>
            ))}
          </ul>
          <p className="page-footnote"><span>*</span> Eksekusi dijalankan dengan identitas pemberi persetujuan; pengaju tidak dapat menyetujui sendiri.</p>
        </section>
      )}

      <section className="run-history" aria-labelledby="run-history-heading">
        <h2 id="run-history-heading"><History size={18} /> Riwayat run</h2>
        {runs.isLoading && <LoadingBlock label="Membaca riwayat…" />}
        {runs.error && <ErrorState message={runs.error} retry={runs.reload} />}
        {!runs.isLoading && !runs.error && !runs.data?.length && <EmptyState title="Belum ada run" description="Ajukan pertanyaan pertama Anda." />}
        <ul className="document-list">
          {(runs.data ?? []).map((run) => (
            <li className="document-item" key={run.id}>
              <div className="document-info">
                <strong>{run.question}</strong>
                <small>{formatDate(run.created_at)} · {run.duration_ms ?? '—'} ms</small>
              </div>
              <StatusBadge value={run.status === 'completed' ? 'active' : run.status === 'failed' ? 'error' : run.status} />
              <Button variant="secondary" onClick={() => setCurrent(run)}>Detail</Button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
