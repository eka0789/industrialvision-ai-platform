import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { ArrowRight, Eye, EyeOff, LockKeyhole, ShieldCheck, UserRound } from 'lucide-react'
import { Brand } from '../components/Brand'
import { Button } from '../components/Ui'
import { useAuth } from '../hooks/useAuth'
import { errorMessage } from '../utils/errors'

export default function LoginPage() {
  const { user, login, sessionExpired } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  if (user) return <Navigate to="/" replace />

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username.trim(), password, remember)
      const destination = (location.state as { from?: string } | null)?.from ?? '/'
      navigate(destination, { replace: true })
    } catch (reason) {
      setError(errorMessage(reason, 'Autentikasi gagal. Periksa kembali kredensial Anda.'))
    } finally { setLoading(false) }
  }

  return (
    <main className="login-page">
      <section className="login-editorial" aria-label="IndustrialVision AI">
        <div className="login-brand"><Brand /></div>
        <div className="login-edition">CONTROL SYSTEM <span>ED. 001 / 2026</span></div>
        <div className="login-statement">
          <p className="eyebrow eyebrow--light"><span />VISUAL QUALITY INTELLIGENCE</p>
          <h1>Presisi,<br />di setiap<br /><em>piksel.</em></h1>
          <p>Satu ruang untuk melihat, menguji, dan mempertanggungjawabkan setiap keputusan kualitas.</p>
        </div>
        <div className="login-schematic" aria-hidden="true">
          <span className="schematic-corner schematic-corner--tl" /><span className="schematic-corner schematic-corner--tr" />
          <span className="schematic-corner schematic-corner--bl" /><span className="schematic-corner schematic-corner--br" />
          <div className="schematic-target"><i /><i /></div><span className="schematic-line" />
          <small>OBJECT / INSPECTION</small><b>IV—01</b>
        </div>
        <div className="login-editorial-footer"><span>MANUFACTURING / COMPUTER VISION</span><span className="mono">— 6°12′S 106°49′E</span></div>
      </section>
      <section className="login-form-section">
        <div className="login-form-wrap">
          <div className="login-form-heading"><span className="section-number">/ 01</span><p className="eyebrow">OPERATOR ACCESS</p><h2>Selamat datang<br />kembali.</h2><p>Masuk untuk membuka ruang kendali Anda.</p></div>
          {sessionExpired && <div className="inline-notice inline-notice--warning" role="status">Sesi Anda telah berakhir. Silakan masuk kembali.</div>}
          <form className="login-form" onSubmit={submit}>
            <label className="field"><span>Username</span><div className="input-with-icon"><UserRound size={18} /><input name="username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Nama pengguna" required autoFocus maxLength={80} /></div></label>
            <label className="field"><span>Password</span><div className="input-with-icon"><LockKeyhole size={18} /><input type={showPassword ? 'text' : 'password'} name="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password Anda" required maxLength={128} /><button type="button" className="icon-button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? 'Sembunyikan password' : 'Tampilkan password'}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></div></label>
            <label className="checkbox-field"><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} /><span>Ingat sesi di perangkat ini</span></label>
            {error && <div className="form-error" role="alert"><span>!</span>{error}</div>}
            <Button type="submit" className="login-submit" loading={loading}>{loading ? 'Memverifikasi…' : 'Masuk ke ruang kendali'}<ArrowRight size={19} /></Button>
          </form>
          <div className="login-security"><ShieldCheck size={18} /><p>Akses mengikuti peran pengguna. Sesi dibatasi waktu oleh server.</p></div>
          <p className="login-help">Belum memiliki akses? <span>Hubungi administrator sistem Anda.</span></p>
        </div>
        <footer className="login-footer"><span>IV.OS / SECURE ACCESS</span><span>© 2026 INDUSTRIALVISION</span></footer>
      </section>
    </main>
  )
}
