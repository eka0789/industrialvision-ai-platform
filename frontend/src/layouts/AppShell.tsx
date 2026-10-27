import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Boxes, ChevronRight, CircleGauge, Cctv, History, LogOut, Menu, Plus, Users, X } from 'lucide-react'
import { systemApi } from '../api'
import { Brand } from '../components/Brand'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useLiveFeed } from '../hooks/useLiveFeed'
import { useToast } from '../hooks/useToast'

const navigation = [
  { to: '/', label: 'Ringkasan', index: '01', icon: CircleGauge, end: true },
  { to: '/inspections/new', label: 'Inspeksi baru', index: '02', icon: Plus },
  { to: '/inspections', label: 'Riwayat inspeksi', index: '03', icon: History, end: true },
  { to: '/cameras', label: 'Stasiun & kamera', index: '04', icon: Cctv },
  { to: '/models', label: 'Registry model', index: '05', icon: Boxes },
]

export default function AppShell() {
  const { user, logout } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const location = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)
  const health = useApi(systemApi.health)
  const { status: liveStatus } = useLiveFeed()
  const liveLabel = liveStatus === 'live'
    ? 'Feed langsung aktif'
    : liveStatus === 'connecting'
      ? 'Menyambung feed…'
      : 'Feed langsung nonaktif'
  const items = user?.role === 'admin' ? [...navigation, { to: '/admin', label: 'Akses pengguna', index: '06', icon: Users }] : navigation

  const closeMenu = () => setMenuOpen(false)

  const signOut = async () => {
    setLoggingOut(true)
    try {
      await logout()
      navigate('/login', { replace: true })
    } catch {
      toast('Sesi lokal telah ditutup.', 'info')
      navigate('/login', { replace: true })
    } finally { setLoggingOut(false) }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Lewati ke konten utama</a>
      {menuOpen && <button className="nav-scrim" aria-label="Tutup navigasi" onClick={() => setMenuOpen(false)} />}
      <aside className={`sidebar ${menuOpen ? 'sidebar--open' : ''}`} aria-label="Navigasi utama">
        <div className="sidebar-brand"><Brand /><button className="icon-button mobile-nav-close" aria-label="Tutup menu" onClick={() => setMenuOpen(false)}><X size={20} /></button></div>
        <div className="sidebar-section-label">RUANG KERJA <span>IV—01</span></div>
        <nav className="main-nav">
          {items.map(({ to, label, index, icon: Icon, ...item }) => (
            <NavLink key={to} to={to} onClick={closeMenu} end={'end' in item && item.end} className={({ isActive }) => isActive ? 'nav-link nav-link--active' : 'nav-link'}>
              <span className="nav-index">{index}</span><Icon size={18} strokeWidth={1.7} /><span>{label}</span><ChevronRight className="nav-arrow" size={14} />
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-note"><span className="crosshair" aria-hidden="true" /><p>Keputusan yang baik dimulai dari bukti yang baik.</p><small>QUALITY / TRACEABILITY</small></div>
        <div className="user-panel">
          <span className="user-avatar" aria-hidden="true">{user?.username.slice(0, 2).toUpperCase()}</span>
          <div><strong>{user?.username}</strong><span>{user?.role.replace('_', ' ')}</span></div>
          <button className="icon-button" onClick={signOut} disabled={loggingOut} aria-label="Keluar dari aplikasi" title="Keluar"><LogOut size={17} /></button>
        </div>
      </aside>
      <div className="app-body">
        <header className="topbar">
          <button className="icon-button menu-toggle" aria-label="Buka navigasi" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)}><Menu size={22} /></button>
          <div className="topbar-path"><span>IV / </span><strong>{items.find((item) => item.to === location.pathname)?.label ?? 'Ruang kendali'}</strong></div>
          <div className="topbar-right">
            <span className={`live-status ${liveStatus === 'live' ? 'live-status--on' : ''}`} role="status" title={liveLabel}>
              <i aria-hidden="true" /><span>{liveLabel}</span>
            </span>
            <button className={`api-status ${health.data?.status === 'ok' ? 'api-status--online' : ''}`} onClick={health.reload} title="Periksa ulang koneksi API">
              <i aria-hidden="true" /><span>{health.isLoading ? 'Memeriksa API' : health.data?.status === 'ok' ? 'Sistem terhubung' : health.data ? 'Sistem perlu perhatian' : 'API tidak terhubung'}</span><span className="topbar-version">v{health.data?.version ?? '—'}</span>
            </button>
          </div>
        </header>
        <main id="main-content" className="main-content"><Outlet /></main>
        <footer className="app-footer"><span>INDUSTRIALVISION AI</span><span>Bukti visual. Keputusan terukur.</span><span>IV.OS / {health.data?.environment ?? 'LOCAL'}</span></footer>
      </div>
    </div>
  )
}
