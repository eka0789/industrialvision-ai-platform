import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { BookOpen, FileText, FileUp, LoaderCircle, MessageCircleQuestion, Send, Trash2, X } from 'lucide-react'
import { ragApi } from '../api'
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, StatusBadge } from '../components/Ui'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { RagChatResponse, RagChunk, RagCitation } from '../types'
import { errorMessage } from '../utils/errors'
import { formatDate } from '../utils/format'

function renderAnswer(answer: string, citations: RagCitation[]) {
  // Inline [n] citations become links to the source chunk viewer.
  const parts = answer.split(/(\[\d{1,2}\])/g)
  return parts.map((part, index) => {
    const match = part.match(/^\[(\d{1,2})\]$/)
    if (!match) return <span key={index}>{part}</span>
    const citation = citations.find((item) => item.index === Number(match[1]))
    if (!citation) return <span key={index}>{part}</span>
    return (
      <button
        key={index}
        className="citation-link"
        title={`${citation.snippet}…`}
        onClick={() => document.getElementById(`chunk-${citation.chunk_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
      >
        [{citation.index}]
      </button>
    )
  })
}

export default function KnowledgePage() {
  const { user } = useAuth()
  const toast = useToast()
  const documents = useApi(() => ragApi.documents(), [])
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [chat, setChat] = useState<RagChatResponse | null>(null)
  const [chunksFor, setChunksFor] = useState<number | null>(null)
  const chunks = useApi(() => (chunksFor ? ragApi.chunks(chunksFor) : Promise.resolve([])), [chunksFor])
  const fileInput = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)

  const canUpload = user?.role === 'owner' || user?.role === 'admin' || user?.role === 'operator' || user?.role === 'ai_engineer'
  const canDelete = user?.role === 'owner' || user?.role === 'admin'

  const ask = async (event: FormEvent) => {
    event.preventDefault()
    if (!question.trim()) return
    setAsking(true)
    try {
      setChat(await ragApi.chat(question.trim()))
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setAsking(false)
    }
  }

  const upload = async (file: File) => {
    setUploading(true)
    try {
      await ragApi.upload(file)
      toast('Dokumen diunggah — pemrosesan berjalan di latar belakang.')
      documents.reload()
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const remove = async (id: number) => {
    try {
      await ragApi.delete(id)
      toast('Dokumen dihapus.')
      documents.reload()
      if (chunksFor === id) setChunksFor(null)
    } catch (reason) {
      toast(errorMessage(reason), 'warning')
    }
  }

  return (
    <div className="page knowledge-page">
      <PageHeader
        eyebrow="KNOWLEDGE AI"
        title={<>Tanya dokumentasi pabrik.<br /><span className="text-muted">Jawaban selalu dengan sumber.</span></>}
        description="Unggah manual/SOP (TXT, CSV, PDF, DOCX), lalu ajukan pertanyaan. Ketika sumber tidak ditemukan, asisten menolak menjawab daripada mengarang."
        actions={canUpload ? (
          <>
            <Button
              variant="secondary"
              loading={uploading}
              onClick={async () => {
                setUploading(true)
                try {
                  const res = await ragApi.seedFoodSops()
                  toast(`Berhasil memuat ${res.documents.length} dokumen SOP Standar Pangan (HACCP / Washdown / Packaging).`)
                  documents.reload()
                } catch (reason) {
                  toast(errorMessage(reason), 'warning')
                } finally {
                  setUploading(false)
                }
              }}
            >
              <BookOpen size={16} />Muat SOP Pangan (HACCP)
            </Button>
            <Button loading={uploading} onClick={() => fileInput.current?.click()}><FileUp size={17} />Unggah dokumen</Button>
          </>
        ) : undefined}
      />
      <input
        ref={fileInput}
        type="file"
        accept=".txt,.md,.log,.csv,.pdf,.docx"
        hidden
        aria-label="Pilih dokumen untuk diunggah"
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) void upload(file)
        }}
      />

      <section className="chat-section" aria-labelledby="chat-heading">
        <h2 id="chat-heading"><MessageCircleQuestion size={18} /> Tanya basis pengetahuan</h2>
        <form className="chat-form" onSubmit={ask}>
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="mis. Kapan helm wajib diperiksa?"
            aria-label="Pertanyaan"
            maxLength={2000}
          />
          <Button type="submit" loading={asking} disabled={!question.trim()}><Send size={16} />Tanya</Button>
        </form>
        {asking && <p className="chat-status" role="status"><LoaderCircle className="spin" size={16} /> Mencari sumber…</p>}
        {chat && (
          <div className={`chat-answer ${chat.status === 'refused' ? 'chat-answer--refused' : ''}`} data-testid="chat-answer">
            {chat.status === 'refused' ? (
              <p className="refusal" role="alert">{chat.answer}</p>
            ) : (
              <p className="answer-body">{renderAnswer(chat.answer, chat.citations)}</p>
            )}
            <p className="chat-meta">
              Penyedia jawaban: <strong>{chat.provider}</strong>
              {chat.provider === 'mock-demo' ? ' (jawaban ekstraktif lokal — tanpa LLM eksternal)' : ''}
            </p>
            {!!chat.citations.length && (
              <ul className="citation-list">
                {chat.citations.map((citation) => (
                  <li key={citation.index}>
                    <button
                      className="text-link"
                      onClick={() => {
                        setChunksFor(citation.document_id)
                        setTimeout(() => document.getElementById(`chunk-${citation.chunk_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 400)
                      }}
                    >
                      Sumber [{citation.index}] — dokumen #{citation.document_id}{citation.page ? `, hal. ${citation.page}` : ''}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <section className="document-section" aria-labelledby="documents-heading">
        <h2 id="documents-heading"><BookOpen size={18} /> Dokumen</h2>
        {documents.isLoading && <LoadingBlock label="Membaca daftar dokumen…" />}
        {documents.error && <ErrorState message={documents.error} retry={documents.reload} />}
        {!documents.isLoading && !documents.error && !documents.data?.length && (
          <EmptyState title="Belum ada dokumen" description={canUpload ? 'Unggah SOP atau manual pertama untuk memulai.' : 'Operator atau engineer perlu mengunggah dokumen terlebih dahulu.'} />
        )}
        {!!documents.data?.length && (
          <ul className="document-list">
            {documents.data.map((document) => (
              <li className="document-item" key={document.id} data-testid={`document-${document.id}`}>
                <FileText size={18} />
                <div className="document-info">
                  <strong>{document.title}</strong>
                  <small>
                    {document.chunk_count} potongan · {document.embedding_provider ?? '—'} (dim {document.embedding_dim ?? '—'})
                    {document.created_at ? ` · ${formatDate(document.created_at)}` : ''}
                  </small>
                  {document.status === 'failed' && <small className="document-error" role="alert">Gagal: {document.error}</small>}
                </div>
                <StatusBadge value={document.status} />
                <div className="document-actions">
                  <Button variant="secondary" onClick={() => setChunksFor(chunksFor === document.id ? null : document.id)}>
                    {chunksFor === document.id ? 'Tutup' : 'Lihat potongan'}
                  </Button>
                  {canDelete && (
                    <button className="icon-button" aria-label={`Hapus ${document.title}`} onClick={() => remove(document.id)}>
                      <Trash2 size={16} />
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {chunksFor !== null && (
        <section className="chunk-section" aria-labelledby="chunks-heading">
          <div className="chunk-head">
            <h2 id="chunks-heading">Potongan dokumen #{chunksFor}</h2>
            <button className="icon-button" aria-label="Tutup potongan" onClick={() => setChunksFor(null)}><X size={16} /></button>
          </div>
          {chunks.isLoading && <LoadingBlock label="Membaca potongan…" />}
          {!chunks.isLoading && !chunks.data?.length && <EmptyState title="Belum ada potongan" description="Dokumen belum selesai diproses atau tidak memuat teks." />}
          <ul className="chunk-list">
            {(chunks.data ?? []).map((chunk: RagChunk) => (
              <li id={`chunk-${chunk.id}`} className="chunk-item" key={chunk.id}>
                <span className="chunk-index">#{chunk.ordinal}{chunk.page ? ` · hal. ${chunk.page}` : ''}</span>
                <p>{chunk.content}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="page-footnote"><span>*</span> Jawaban dibatasi pada isi dokumen organisasi Anda; setiap kutipan [n] tertaut ke potongan sumber yang nyata.</p>
    </div>
  )
}
