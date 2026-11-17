import { useState } from 'react'
import type { FormEvent } from 'react'
import { Bot, Check, CircleAlert, History, Inbox, LoaderCircle, Send, X } from 'lucide-react'
import { agentApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { AgentRun, IncidentTriageResult } from '../types'
import { errorMessage } from '../utils/errors'
import { formatDate } from '../utils/format'

export default function AgentPage() {
  const { user } = useAuth()
  const toast = useToast()
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [current, setCurrent] = useState<AgentRun | null>(null)
  const [triageResult, setTriageResult] = useState<IncidentTriageResult | null>(null)
  const runs = useApi(() => agentApi.runs(), [current?.id])
  const approvals = useApi(() => agentApi.approvals('pending'), [current?.id, triageResult?.approval_id])
  const [decidingId, setDecidingId] = useState<number | null>(null)

  const canApprove = user?.role === 'owner' || user?.role === 'admin' || user?.role === 'ai_engineer'

  const triggerIncidentTriage = async (
    incidentType: 'FOREIGN_MATTER' | 'LENS_BLUR_OR_FOG' | 'PSI_DRIFT' | 'MISSING_EXP_DATE' | 'SEAL_DEFECT',
  ) => {
    setAsking(true)
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
      setAsking(false)
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
        actions={
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <Button
              variant="secondary"
              loading={asking}
              onClick={() => triggerIncidentTriage('FOREIGN_MATTER')}
            >
              <CircleAlert size={16} />Analisis & Tindakan Korektif AI (Foreign Matter)
            </Button>
            <Button
              variant="secondary"
              loading={asking}
              onClick={() => triggerIncidentTriage('LENS_BLUR_OR_FOG')}
            >
              <Bot size={16} />Analisis & Tindakan Korektif AI (Lens Blur / Drift)
            </Button>
            <Button
              variant="secondary"
              loading={asking}
              onClick={async () => {
                setAsking(true)
                try {
                  const res = await agentApi.evaluateDefectSpike({ defect_category: 'seal_leak', window_minutes: 5, spike_threshold: 3 })
                  toast(res.recommended_action, res.spike_detected ? 'warning' : 'info')
                  approvals.reload()
                  runs.reload()
                } catch (reason) {
                  toast(errorMessage(reason), 'warning')
                } finally {
                  setAsking(false)
                }
              }}
            >
              <Bot size={16} />Cek Lonjakan Seal Defect (5 Menit)
            </Button>
          </div>
        }
      />

      {triageResult && (
        <section className="panel" aria-label="Hasil Triase Insiden Proaktif" style={{ marginBottom: '1.25rem', padding: '1rem', borderLeft: '4px solid #f97316' }}>
          <h2 style={{ marginTop: 0 }}>Triase Insiden Proaktif: {triageResult.incident_type}</h2>
          <p>{triageResult.sop_answer}</p>
          {!!triageResult.sop_citations.length && (
            <small style={{ display: 'block', marginBottom: '0.5rem' }}>
              Sitasi SOP RAG (1:1): {triageResult.sop_citations.map((c) => `[${c.index}] doc #${c.document_id}`).join(', ')}
            </small>
          )}
          {triageResult.approval_id && (
            <p style={{ margin: 0 }}>
              <strong>Mitigasi HITL Disiapkan:</strong> ID Persetujuan #{triageResult.approval_id} menunggu persetujuan di Kotak Persetujuan di bawah.
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
