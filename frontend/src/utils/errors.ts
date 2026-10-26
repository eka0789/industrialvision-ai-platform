import axios from 'axios'

const detailMessages: Record<string, string> = {
  'Incorrect username or password': 'Username atau password tidak sesuai.',
  'User is inactive': 'Akun ini tidak aktif. Hubungi administrator.',
  'Active camera not found': 'Stasiun tidak ditemukan atau sedang nonaktif.',
  'Model version not found': 'Versi model tidak ditemukan.',
  'Camera not found': 'Stasiun tidak ditemukan.',
  'Camera is not active': 'Stasiun sedang nonaktif.',
  'Model name already exists': 'Nama model sudah digunakan.',
  'Username already exists': 'Username sudah digunakan.',
  'Email already exists': 'Email sudah digunakan.',
  'Inspection not found': 'Data inspeksi tidak ditemukan.',
}

export function errorMessage(error: unknown, fallback = 'Terjadi kendala. Silakan coba lagi.') {
  if (!axios.isAxiosError(error)) return fallback
  if (error.code === 'ECONNABORTED') return 'Server membutuhkan waktu terlalu lama. Coba kembali.'
  if (!error.response) return 'Tidak dapat terhubung ke server. Periksa koneksi dan status backend.'
  const detail: unknown = error.response.data?.detail
  if (typeof detail === 'string') return detailMessages[detail] ?? detail
  if (Array.isArray(detail)) {
    return detail.map((item: { msg?: string }) => item.msg?.replace(/^Value error, /, '') ?? 'Periksa kembali isian Anda.').join(' ')
  }
  if (error.response.status === 403) return 'Anda tidak memiliki izin untuk tindakan ini.'
  if (error.response.status === 429) return 'Terlalu banyak permintaan. Tunggu sebentar.'
  if (error.response.status >= 500) return 'Server sedang mengalami kendala. Coba beberapa saat lagi.'
  return fallback
}
