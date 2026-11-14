# Panduan Lengkap Penggunaan — IndustrialVision AI

Panduan ini menjalankan aplikasi lewat **Docker Compose (mode produksi lokal)**.
Semua halaman konsol memakai Bahasa Indonesia.

---

## 1. Akses Aplikasi

| Item | Nilai |
|---|---|
| URL aplikasi (SPA) | **https://localhost:8443** |
| API (health check) | http://127.0.0.1:8000/health |
| Grafana (monitoring) | http://127.0.0.1:3001 |
| Prometheus | http://127.0.0.1:9090 |

> Sertifikat TLS bersifat *self-signed* untuk penggunaan lokal — browser akan
> menampilkan peringatan; klik **Advanced → Proceed** untuk melanjutkan.

### Akun demo (sudah ter-provision di stack yang berjalan)

| Username | Password | Peran | Bisa apa |
|---|---|---|---|
| `smokeadmin` | `SmokeAdmin!2026` | admin | Semua fitur operasional + kelola pengguna |
| `smokeowner` | `SmokeOwner!2026` | owner | Semua fitur + menyetujui owner/admin baru |
| `ui_operator` | `isolated-ui-password-2026` | operator | Inspeksi, baca semua, ajukan perawatan |
| `viewer` *(lihat §6)* | — | viewer | Hanya baca (butuh di-seed lokal, §6) |

> Akun ini dibuat lewat CLI (produksi tidak pernah menyimpan kredensial bawaan
> di kode). Ganti password untuk penggunaan serius: login → peran admin →
> `/admin` (saat ini reset password dilakukan via CLI di dalam container).

---

## 2. Menjalankan / Menghentikan Stack

```bash
# siapkan environment sekali (nilai contoh untuk lokal)
POSTGRES_PASSWORD=... JWT_SECRET=... GRAFANA_ADMIN_PASSWORD=...   # lihat .env.example

# jalankan semua layanan (backend, worker, db, redis, frontend, prometheus, grafana)
docker compose up -d

# status kesehatan
docker compose ps

# hentikan (data tetap tersimpan di volume)
docker compose down

# hentikan + hapus data (HATI-HATI)
docker compose down -v
```

Migration (`alembic upgrade head`) berjalan otomatis saat container backend start
dengan `APP_ENV=production`.

---

## 3. Tur Halaman (sesuai menu kiri)

1. **Ringkasan** (`/`) — indeks kualitas, status stasiun, feed inspeksi langsung
   (WebSocket). Badge **"Feed langsung aktif"** berarti koneksi WS sehat.
2. **Inspeksi baru** (`/inspections/new`) — unggah gambar produk (JPEG/PNG/WebP
   ≤ 100 MB), pilih stasiun + model, lalu jalankan inspeksi. Keputusan
   (`PASS/FAIL/REVIEW/ERROR`) selalu disertai alasan.
3. **Riwayat inspeksi** (`/inspections`) — filter per keputusan/stasiun/batch,
   klik baris untuk detail + bukti visual.
4. **Stasiun & kamera** (`/cameras`) — daftarkan stasiun (simulasi/file/webcam/
   RTSP), mulai/hentikan, kelola sesi inspeksi, inspeksi file video.
5. **Registry model** (`/models`) — daftarkan artefak ONNX (checksum SHA-256
   dihitung server), aktif/nonaktifkan, tahapan dev/staging/production, evaluasi.
6. **Perawatan prediktif** (`/maintenance`) — daftarkan mesin, kirim data sensor
   (skema AI4I 2020), tekan **Prediksi** untuk skor kesehatan + rekomendasi;
   ajukan & setujui permintaan perawatan.
7. **Knowledge AI** (`/knowledge`) — unggah SOP/manual (TXT/CSV/PDF/DOCX ≤ 25 MB),
   tanya pertanyaan; jawaban selalu membawa sitasi `[n]` yang tertaut ke potongan
   sumber. Jika sumber tidak relevan, asisten **menolak menjawab**.
8. **Operations Agent** (`/agent`) — ajukan pertanyaan operasional; agen
   menjalankan alat (kb_search, status mesin, prediksi kegagalan, analitik).
   Tindakan berdampak (mis. buat permintaan perawatan) butuh **Setujui & eksekusi**
   dari pemilik/admin di kotak persetujuan.
9. **Monitoring** (`/monitoring`) — mode event bus (redis/memori), mode worker
   (rq), dead-letter, status DB, tahapan model.
10. **ML Lab** (`/lab`) — simulator Q-learning edukasi (murni browser, tidak
    terhubung produksi) + browser eksperimen read-only.
11. **Akses pengguna** (`/admin`, khusus admin/owner) — undang pengguna baru ke
    organisasi, aktif/nonaktifkan.

---

## 4. Alur End-to-End yang Disarankan untuk Dicoba (± 5 menit)

1. Login sebagai **smokeadmin**.
2. **Perawatan prediktif** → *Daftarkan mesin* (kode `TEST-01`, tipe umum) →
   klik **Prediksi** → baca modal: mode `rules-only-fallback` (belum ada model
   terdaftar) + skor kesehatan dari ambang AI4I yang terdokumentasi.
3. Di halaman yang sama, antrean **Antrean permintaan perawatan** → **Setujui**
   salah satu permintaan (jika ada yang pending dari operator).
4. **Operations Agent** → tanya `show latest inspections` → buka **Log alat**
   untuk melihat alat yang dijalankan + durasinya.
5. **Knowledge AI** → tanya sesuai isi SOP yang sudah diunggah
   (`berapa ketebalan solder paste yang acceptable?`) → amati sitasi `[1]`.
6. **Monitoring** → pastikan `Event bus: redis` + `Worker: rq`, dead-letter 0.
7. **Grafana** → login `admin` / nilai `GRAFANA_ADMIN_PASSWORD` → dashboard
   *IndustrialVision — Platform Overview*.

---

## 5. Demo Data Lengkap (mode pengembangan, bukan container produksi)

Untuk menjelajah dengan 150+ inspeksi berbukti visual + 5 akun demo:

```bash
cd backend
DATABASE_URL=sqlite:///./demo.db python -m alembic upgrade head
DATABASE_URL=sqlite:///./demo.db python -m app.seed          # produksi menolak ini
DATABASE_URL=sqlite:///./demo.db python -m uvicorn app.main:app --port 8001

cd ../frontend
VITE_PROXY_TARGET=http://127.0.0.1:8001 npm run dev          # buka http://localhost:5173
```

Akun demo hasil seed: `owner/Owner!Vision2026` · `admin/Admin!Vision2026` ·
`operator1/Operator!2026` · `aiengineer/Engineer!2026` · `viewer/Viewer!Vision2026`.

---

## 6. Matikan/Mulai Ulang Satu Layanan & Troubleshooting Cepat

| Gejala | Perintah |
|---|---|
| Backend unhealthy | `docker logs industrialvision-backend --tail 50` |
| Reset salah satu layanan | `docker compose restart <layanan>` |
| `/health` menunjukkan bus terputus | cek `docker logs industrialvision-redis`; bus otomatis memulihkan diri |
| Worker mati | `docker compose restart worker` — pekerjaan antrean tidak hilang (disimpan Redis) |
| Lupa password admin | buat ulang via CLI di container (`python -m app.cli create-admin --username …`) |
| Port 8443 dipakai aplikasi lain | `PUBLIC_HTTPS_PORT=9443 docker compose up -d frontend` |

Aturan keamanan yang selalu aktif: produksi menolak seed/demo feed, brute-force
login terkunci otomatis, semua tindakan sensitif tercatat di `AuditLog`, dan
tenant terisolasi (data organisasi A tidak terlihat oleh organisasi B).
