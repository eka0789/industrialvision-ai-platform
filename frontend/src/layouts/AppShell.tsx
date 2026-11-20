import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Activity,
  BookOpen,
  Bot,
  Boxes,
  ChevronRight,
  CircleGauge,
  Cctv,
  FileCheck2,
  FlaskConical,
  History,
  Layers,
  LogOut,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Sun,
  Users,
  Wrench,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { systemApi } from '../api'
import { Brand } from '../components/Brand'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../hooks/useAuth'
import { useLiveFeed } from '../hooks/useLiveFeed'
import { useTheme } from '../hooks/useTheme'
import { useToast } from '../hooks/useToast'

interface NavItem {
  to: string
  label: string
  index: string
  icon: LucideIcon
  end?: boolean
}

interface NavGroup {
  id: string
  label: string
  items: NavItem[]
}

const navGroups: NavGroup[] = [
  {
    id: 'operations',
    label: 'OPERASI LINI',
    items: [
      { to: '/', label: 'Ringkasan', index: '01', icon: CircleGauge, end: true },
      { to: '/inspections/new', label: 'Inspeksi baru', index: '02', icon: Plus },
      { to: '/inspections', label: 'Riwayat inspeksi', index: '03', icon: History, end: true },
      { to: '/cameras', label: 'Stasiun & kamera', index: '04', icon: Cctv },
    ],
  },
  {
    id: 'quality',
    label: 'KUALITAS & KEPATUHAN',
    items: [
      { to: '/recipes', label: 'SKU & Multi-Zone ROI', index: '05', icon: Layers },
      { to: '/batches', label: 'Batch & Audit', index: '06', icon: FileCheck2 },
    ],
  },
  {
    id: 'intelligence',
    label: 'AI & PREDIKTIF',
    items: [
      { to: '/models', label: 'Registry model', index: '07', icon: Boxes },
      { to: '/maintenance', label: 'Perawatan prediktif', index: '08', icon: Wrench },
      { to: '/knowledge', label: 'Knowledge AI', index: '09', icon: BookOpen },
      { to: '/agent', label: 'Operations Agent', index: '10', icon: Bot },
      { to: '/monitoring', label: 'Monitoring', index: '11', icon: Activity },
      { to: '/lab', label: 'ML Lab', index: '12', icon: FlaskConical },
    ],
  },
]

function getInitialCollapsed(): boolean {
  try {
    return window.localStorage.getItem('iv-sidebar-collapsed') === '1'
  } catch {
    return false
  }
}

export default function AppShell() {
  const { user, logout } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const location = useLocation()
  const { theme, toggleTheme } = useTheme()
  const [menuOpen, setMenuOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(getInitialCollapsed)
  const [loggingOut, setLoggingOut] = useState(false)
  const menuToggleRef = useRef<HTMLButtonElement>(null)
  const health = useApi(systemApi.health)
  const { status: liveStatus } = useLiveFeed()

  const liveLabel = liveStatus === 'live'
    ? 'Feed langsung aktif'
    : liveStatus === 'connecting'
      ? 'Menyambung feed…'
      : 'Feed langsung nonaktif'

  const groups: NavGroup[] = user?.role === 'owner' || user?.role === 'admin'
    ? [
        ...navGroups,
        {
          id: 'admin',
          label: 'ADMINISTRASI',
          items: [{ to: '/admin', label: 'Akses pengguna', index: '13', icon: Users }],
        },
      ]
    : navGroups

  const flatItems = groups.flatMap((group) => group.items)
  const currentRouteLabel = location.pathname.startsWith('/inspections/') && location.pathname !== '/inspections/new'
    ? 'Detail inspeksi'
    : flatItems.find((item) => item.to === location.pathname)?.label ?? 'Ruang kendali'

  useEffect(() => {
    document.title = `${currentRouteLabel} | IndustrialVision AI`
  }, [currentRouteLabel])

  useEffect(() => {
    try {
      window.localStorage.setItem('iv-sidebar-collapsed', collapsed ? '1' : '0')
    } catch {
      // Ignore storage access errors.
    }
  }, [collapsed])

  useEffect(() => {
    if (!menuOpen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
        menuToggleRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [menuOpen])

  const closeMenu = () => {
    setMenuOpen(false)
    menuToggleRef.current?.focus()
  }

  const signOut = async () => {
    setLoggingOut(true)
    try {
      await logout()
      navigate('/login', { replace: true })
    } catch {
      toast('Sesi lokal telah ditutup.', 'info')
      navigate('/login', { replace: true })
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <div className={`app-shell ${collapsed ? 'app-shell--collapsed' : ''}`}>
      <a className="skip-link" href="#main-content">Lewati ke konten utama</a>
      {menuOpen && (
        <button type="button" className="nav-scrim" aria-label="Tutup navigasi" onClick={closeMenu} />
      )}
      <aside className={`sidebar ${menuOpen ? 'sidebar--open' : ''} ${collapsed ? 'sidebar--collapsed' : ''}`} aria-label="Navigasi utama">
        <div className="sidebar-brand">
          <Brand compact={collapsed} />
          <button
            type="button"
            className="icon-button sidebar-collapse-btn"
            aria-label={collapsed ? 'Perluas sidebar' : 'Ringkas sidebar'}
            aria-expanded={!collapsed}
            title={collapsed ? 'Perluas sidebar' : 'Ringkas sidebar'}
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? <PanelLeftOpen size={18} aria-hidden="true" /> : <PanelLeftClose size={18} aria-hidden="true" />}
          </button>
          <button type="button" className="icon-button mobile-nav-close" aria-label="Tutup menu" onClick={closeMenu}>
            <X size={20} aria-hidden="true" />
          </button>
        </div>

        <nav className="main-nav" aria-label="Menu ruang kerja">
          {groups.map((group) => (
            <div className="nav-group" key={group.id}>
              <div className="sidebar-section-label">
                <span>{group.label}</span>
              </div>
              {group.items.map(({ to, label, index, icon: Icon, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  onClick={() => setMenuOpen(false)}
                  end={end}
                  title={collapsed ? label : undefined}
                  aria-label={collapsed ? label : undefined}
                  className={({ isActive }) => (isActive ? 'nav-link nav-link--active' : 'nav-link')}
                >
                  <span className="nav-index" aria-hidden="true">{index}</span>
                  <Icon size={18} strokeWidth={1.7} aria-hidden="true" />
                  <span className="nav-label">{label}</span>
                  <ChevronRight className="nav-arrow" size={14} aria-hidden="true" />
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-spacer" />
        <div className="sidebar-note">
          <span className="crosshair" aria-hidden="true" />
          <p>Keputusan yang baik dimulai dari bukti yang baik.</p>
          <small>QUALITY / TRACEABILITY</small>
        </div>
        <div className="user-panel">
          <span className="user-avatar" aria-hidden="true">{user?.username.slice(0, 2).toUpperCase()}</span>
          <div className="user-meta">
            <strong>{user?.username}</strong>
            <span>{user?.role.replace('_', ' ')}</span>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={signOut}
            disabled={loggingOut}
            aria-label="Keluar dari aplikasi"
            title="Keluar"
          >
            <LogOut size={17} aria-hidden="true" />
          </button>
        </div>
      </aside>

      <div className="app-body">
        <header className="topbar">
          <button
            ref={menuToggleRef}
            type="button"
            className="menu-toggle"
            aria-label="Buka navigasi"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          >
            <Menu size={20} aria-hidden="true" />
            <span>Menu</span>
          </button>
          <div className="topbar-path">
            <span>IV / </span>
            <strong>{currentRouteLabel}</strong>
          </div>
          <div className="topbar-right">
            <span className={`live-status ${liveStatus === 'live' ? 'live-status--on' : ''}`} role="status" title={liveLabel}>
              <i aria-hidden="true" />
              <span>{liveLabel}</span>
            </span>
            <button
              type="button"
              className={`api-status ${health.data?.status === 'ok' ? 'api-status--online' : ''}`}
              onClick={health.reload}
              title="Periksa ulang koneksi API"
            >
              <i aria-hidden="true" />
              <span>
                {health.isLoading
                  ? 'Memeriksa API'
                  : health.data?.status === 'ok'
                    ? 'Sistem terhubung'
                    : health.data
                      ? 'Sistem perlu perhatian'
                      : 'API tidak terhubung'}
              </span>
              <span className="topbar-version">v{health.data?.version ?? '-'}</span>
            </button>
            <button
              type="button"
              className="theme-toggle"
              onClick={toggleTheme}
              aria-label={theme === 'dark' ? 'Aktifkan mode terang' : 'Aktifkan mode gelap'}
              aria-pressed={theme === 'dark'}
              title={theme === 'dark' ? 'Beralih ke mode terang' : 'Beralih ke mode gelap (shift malam)'}
            >
              {theme === 'dark' ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
              <span className="theme-toggle-label">{theme === 'dark' ? 'Terang' : 'Gelap'}</span>
            </button>
          </div>
        </header>
        <main id="main-content" className="main-content"><Outlet /></main>
        <footer className="app-footer">
          <span>INDUSTRIALVISION AI</span>
          <span>Bukti visual. Keputusan terukur.</span>
          <span>IV.OS / {health.data?.environment ?? 'LOCAL'}</span>
        </footer>
      </div>
    </div>
  )
}
