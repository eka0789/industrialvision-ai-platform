import { ArrowLeft } from 'lucide-react'
import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return <div className="not-found"><p className="eyebrow">ERROR / 404</p><span className="not-found-code">404</span><h1>Koordinat tidak ditemukan.</h1><p>Halaman yang Anda tuju tidak tersedia atau sudah dipindahkan.</p><Link to="/" className="button button--primary"><ArrowLeft size={16} />Kembali ke ruang kendali</Link></div>
}
