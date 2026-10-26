import { useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, Check, Eye, EyeOff, KeyRound, ShieldCheck, UserPlus, Users } from 'lucide-react'
import { adminApi } from '../api'
import { Button, PageHeader } from '../components/Ui'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import type { Role, User } from '../types'
import { errorMessage } from '../utils/errors'

const roleDescriptions: { role: Role; name: string; description: string; permissions: string[] }[] = [
  { role: 'operator', name: 'Operator', description: 'Menjalankan pekerjaan inspeksi harian.', permissions: ['Unggah & proses inspeksi', 'Lihat riwayat dan bukti', 'Ubah status koneksi stasiun'] },
  { role: 'ai_engineer', name: 'AI Engineer', description: 'Menjaga registry dan penggunaan model.', permissions: ['Seluruh akses inspeksi', 'Registrasi & aktivasi model', 'Tanpa akses kelola pengguna'] },
  { role: 'admin', name: 'Administrator', description: 'Mengelola akses dan konfigurasi sistem.', permissions: ['Seluruh akses operasional', 'Tambah & edit stasiun', 'Buat akun pengguna'] },
]

export default function AdminPage() {
  const { user: currentUser } = useAuth()
  const toast = useToast()
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [role, setRole] = useState<Role>('operator')
  const [showPassword, setShowPassword] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState<User | null>(null)
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setCreated(null)
    if (password !== confirmation) { setError('Konfirmasi password belum sama.'); return }
    setSaving(true)
    try {
      const createdUser = await adminApi.createUser({ username: username.trim(), email: email.trim() || undefined, password, role })
      setCreated(createdUser); setUsername(''); setEmail(''); setPassword(''); setConfirmation(''); setRole('operator')
      toast(`Akses untuk ${createdUser.username} berhasil dibuat.`)
    } catch (reason) { setError(errorMessage(reason, 'Pengguna tidak dapat ditambahkan.')) }
    finally { setSaving(false) }
  }
  return (
    <div className="page admin-page">
      <PageHeader eyebrow="ADMINISTRATION / ACCESS CONTROL" title={<>Akses yang tepat.<br /><span className="text-muted">Tanpa berlebihan.</span></>} description="Buat akun dengan tanggung jawab yang jelas dan hak akses sesuai peran." />
      <div className="admin-layout">
        <section className="roles-section"><div className="section-heading"><Users size={22} /><div><h2>Peran dalam sistem</h2><p>Batas akses diterapkan oleh backend, bukan hanya disembunyikan di tampilan.</p></div></div><div className="role-list">{roleDescriptions.map((item, index) => <article className={`role-card ${currentUser?.role === item.role ? 'role-card--current' : ''}`} key={item.role}><div className="role-card-index">0{index + 1}</div><div><div className="role-card-heading"><h3>{item.name}</h3>{currentUser?.role === item.role && <span>PERAN ANDA</span>}</div><p>{item.description}</p><ul>{item.permissions.map((permission) => <li key={permission}><Check size={14} />{permission}</li>)}</ul></div></article>)}</div><div className="inline-notice"><ShieldCheck size={18} /><p>Daftar dan penonaktifan pengguna belum disediakan oleh API. Hubungi administrator sistem untuk pengelolaan lanjutan.</p></div></section>
        <section className="create-user-panel"><div className="create-user-icon"><UserPlus size={24} /></div><p className="eyebrow eyebrow--light">NEW / USER ACCESS</p><h2>Tambah pengguna.</h2><p>Buat kredensial awal dan bagikan melalui kanal yang aman.</p><form onSubmit={submit} className="create-user-form"><label className="field field--dark"><span>Username <b>*</b></span><input required autoComplete="off" minLength={3} maxLength={80} value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Minimal 3 karakter" /></label><label className="field field--dark"><span>Email <small>opsional</small></span><input type="email" autoComplete="off" maxLength={255} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="nama@perusahaan.com" /></label><label className="field field--dark"><span>Peran akses</span><select value={role} onChange={(event) => setRole(event.target.value as Role)}>{roleDescriptions.map((item) => <option key={item.role} value={item.role}>{item.name}</option>)}</select></label><label className="field field--dark"><span>Password awal <b>*</b></span><div className="input-with-icon"><KeyRound size={17} /><input type={showPassword ? 'text' : 'password'} required autoComplete="new-password" minLength={12} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Minimal 12 karakter" /><button type="button" className="icon-button" aria-label={showPassword ? 'Sembunyikan password' : 'Tampilkan password'} onClick={() => setShowPassword((value) => !value)}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label><label className="field field--dark"><span>Ulangi password <b>*</b></span><input type={showPassword ? 'text' : 'password'} required autoComplete="new-password" minLength={12} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>{password && password.length < 12 && <p className="password-hint">{12 - password.length} karakter lagi untuk memenuhi panjang minimum.</p>}{error && <div className="form-error" role="alert">{error}</div>}{created && <div className="success-message" role="status"><Check size={17} /><span>Akun <strong>{created.username}</strong> aktif sebagai {created.role.replace('_', ' ')}.</span></div>}<Button type="submit" variant="lime" loading={saving}>Buat akses pengguna<ArrowRight size={17} /></Button></form></section>
      </div>
    </div>
  )
}
