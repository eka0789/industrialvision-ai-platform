import { useState } from 'react'
import type { FormEvent } from 'react'
import { Bot, Check, CircleAlert, History, Inbox, LoaderCircle, Send, X } from 'lucide-react'
import { agentApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { AgentRun } from '../types'
import { errorMessage } from '../utils/errors'
import { formatDate } from '../utils/format'

export default function AgentPage() {
  const { user } = useAuth()
  const toast = useToast()
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [current, setCurrent] = useState<AgentRun | null>(null)
  const runs = useApi(() => agentApi.runs(), [current?.id])
  const approvals = useApi(() => agentApi.approvals('pending'), [current?.id])
  const [decidingId, setDecidingId] = useState<number | null>(null)

  const canApprove = user?.role === 'owner' || user?.role === 'admin'

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
        eyebrow="OPERATIONS AGENT"
        title={<>Asisten operasi.<br /><span className="text-muted">Tindakan berdampak selalu butuh setuju manusia.</span></>}
        description="Ajukan pertanyaan operasional; agen merencanakan dan menjalankan alat (pencarian knowledge, status mesin, prediksi kegagalan, analitik) dengan batas iterasi dan timeout per alat."
      />

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
