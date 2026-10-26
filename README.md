# IndustrialVision AI

<p align="left">
  <img src="https://img.shields.io/badge/Status-Active%20Development-1c211b?style=for-the-badge&logoColor=d6f779" alt="Status" />
  <img src="https://img.shields.io/badge/Frontend-React%2019%20%7C%20TypeScript-1c211b?style=for-the-badge&logo=react&logoColor=61dafb" alt="Frontend" />
  <img src="https://img.shields.io/badge/Backend-FastAPI%20%7C%20Python%203.13-1c211b?style=for-the-badge&logo=fastapi&logoColor=009688" alt="Backend" />
  <img src="https://img.shields.io/badge/Database-SQLite%20%2F%20PostgreSQL%2016-1c211b?style=for-the-badge&logo=postgresql&logoColor=4169e1" alt="Database" />
  <img src="https://img.shields.io/badge/Accessibility-WCAG%202.1%20AA%20Compliant-1c211b?style=for-the-badge&logoColor=d6f779" alt="WCAG AA" />
  <img src="https://img.shields.io/badge/Tests-212%20Passing-1c211b?style=for-the-badge&logoColor=d6f779" alt="Tests" />
</p>

**IndustrialVision AI** adalah platform inspeksi visual berbasis *computer vision* untuk lingkungan manufaktur dan kendali mutu (*quality control*). Sistem ini mengintegrasikan pengujian citra permukaan, inferensi model (ONNX Runtime, Ultralytics YOLO adapter, PaDiM/PatchCore adapter), pencatatan jejak keputusan (*traceability*), penyimpanan bukti visual berautentikasi, serta konsol operator web responsif bertema **Industrial Instrument Panel**.

> **Catatan Kesiapan Produksi:** Repositori ini menyediakan fondasi perangkat lunak modular yang telah lulus uji integrasi dan kontrak API lokal. Penggunaan di lantai produksi nyata tetap memerlukan bobot model industri terlatih, kalibrasi ambang batas cacat, serta validasi pada perangkat keras kamera/GPU target.

---

## Daftar Isi

1. [Fitur Utama](#fitur-utama)
2. [Arsitektur Sistem](#arsitektur-sistem)
3. [Teknologi yang Digunakan](#teknologi-yang-digunakan)
4. [Prasyarat Sistem](#prasyarat-sistem)
5. [Panduan Menjalankan Aplikasi (Local Development)](#panduan-menjalankan-aplikasi-local-development)
   - [1. Clone & Setup Dependensi](#1-clone--setup-dependensi)
   - [2. Konfigurasi Environment](#2-konfigurasi-environment)
   - [3. Migrasi Database & Bootstrap Admin](#3-migrasi-database--bootstrap-admin)
   - [4. Menjalankan Backend API](#4-menjalankan-backend-api)
   - [5. Menjalankan Frontend Console](#5-menjalankan-frontend-console)
6. [Alur Penggunaan Operasional](#alur-penggunaan-operasional)
7. [Struktur Proyek](#struktur-proyek)
8. [Referensi REST & WebSocket API](#referensi-rest--websocket-api)
9. [Variabel Environment](#variabel-environment)
10. [Pengujian & Verifikasi Kualitas](#pengujian--verifikasi-kualitas)
11. [Deployment dengan Docker Compose](#deployment-dengan-docker-compose)
12. [Troubleshooting](#troubleshooting)
13. [Keamanan & Batasan Sistem](#keamanan--batasan-sistem)
14. [Lisensi & Kontribusi](#lisensi--kontribusi)

---

## Fitur Utama

- **Konsol Operator Industrial (Anti-AI Slop UI):**
  Antarmuka berkarakter kuat menggunakan palet warna grafit (`#1c211b`), *warm off-white* (`#f3f4ed`), dan *signal lime* (`#d6f779`). Dilengkapi tipografi lokal (*Space Grotesk*, *Manrope*, *IBM Plex Mono*), garis bidik registrasi (*corner viewfinders*), skala ukur (*evidence ruler*), dan pita riwayat keputusan (*decision tape*).
- **Alur Inspeksi Citra & Bukti Visual:**
  Unggah gambar sampel produk (JPEG, PNG, WebP hingga 100 MB). Keputusan kualitas (`PASS`, `FAIL`, `REVIEW`, `ERROR`) dihasilkan bersama latensi inferensi numerik, alasan keputusan, dan overlay *bounding box* SVG yang terkunci presisi pada piksel gambar nyata.
- **Penyimpanan Bukti Aman (*Evidence Storage*):**
  Gambar bukti disimpan dengan resolusi jalur sisi server yang aman dari serangan *path traversal*. Pengambilan bukti visual melalui endpoint `GET /inspections/{id}/evidence` wajib memiliki JWT aktif dan disajikan sebagai blob lokal dengan header `nosniff`.
- **Registry & Manajemen Model AI:**
  Pencatatan versi model (ONNX, YOLO), *task type* (deteksi objek, anomali citra, segmentasi anomali), kelas cacat, ambang batas *confidence*, *checksum* SHA-256 otomatis dari artefak server, serta riwayat metrik evaluasi. Menegakkan aturan satu model aktif per kategori tugas.
- **Manajemen Stasiun Inspeksi (Fleet):**
  Pendaftaran stasiun kerja dengan sumber fleksibel (*browser upload*, RTSP, USB webcam, file lokal, simulasi) dengan redaksi otomatis kredensial URL RTSP.
- **Kontrol Akses Berbasis Peran (RBAC):**
  Pemisahan hak akses ketat di level API dan antarmuka web:
  - `admin`: konfigurasi penuh, pembuatan stasiun, penambahan akun pengguna.
  - `ai_engineer`: seluruh akses operasional, registrasi model, aktivasi/evaluasi model.
  - `operator`: eksekusi inspeksi harian, peninjauan bukti, perubahan status operasional stasiun.
- **Aksesibilitas & Standar Mobile:**
  Lolos audit otomatis **WCAG 2.1 AA** (rasio kontras teks, fokus keyboard, modal native dengan *focus containment*) serta layout adaptif penuh pada viewport mobile (390 px) tanpa *horizontal overflow*.

---

## Arsitektur Sistem

Platform mengadopsi pola **Modular Monolith** dengan pemisahan tegas antara logika domain, adapter perangkat keras/model, dan lapisan penyajian:

```text
[ Kamera / File / Browser Upload ]
                 │
                 ▼
[ Fastify / Starlette Proxy (Vite Dev / Nginx Production) ]
                 │
                 ▼
[ FastAPI Application Core (Python 3.13) ]
  ├── Auth & RBAC (PyJWT, Password Hashing)
  ├── Security Hardening (Upload Bounds, Path Sanitization, Secret Redaction)
  ├── Camera Service & Capture Worker (Bounding Deque, RTSP Redaction)
  ├── Inference & Anomaly Engine (ONNX Runtime, NumPy Adapters)
  ├── Decision Engine (Rules-based Evaluation: PASS / FAIL / REVIEW / ERROR)
  └── Evidence Storage (Server-rooted, Collision-safe UUIDs)
                 │
        ┌────────┴────────┐
        ▼                 ▼
[ SQLite / PostgreSQL ] [ File Storage (data/evidence) ]
        ▲
        │
[ React 19 Frontend Console (SPA + TailwindCSS Tokens) ]
  ├── AuthProvider & Route Guards (useRequireRole)
  ├── Operations Overview & Quality Index Gauge
  ├── Inspection Workbench & Precision SVG Detection Canvas
  ├── Filterable Traceability History & JSON Data Export
  └── Stations Fleet & Model Registry Consoles
```

---

## Teknologi yang Digunakan

### Backend & AI Runtime
- **Bahasa & Framework:** Python 3.13+, FastAPI, Starlette, Uvicorn
- **ORM & Migrasi:** SQLAlchemy 2.0 (Synchronous Engine), Alembic
- **Visi Komputer & Inferensi:** OpenCV Headless, Pillow, NumPy, ONNX Runtime, ONNX
- **Keamanan & Autentikasi:** PyJWT, Passlib (Argon2 / BCrypt)

### Frontend Console
- **Framework:** React 19, TypeScript 5.8+, Vite 8
- **Routing & State:** React Router 7, Context API, Hooks Kustom (`useAuth`, `useApi`, `useToast`)
- **Desain & Ikon:** Token CSS Kustom, Tailwind CSS v4, Lucide React
- **Tipografi:** `@fontsource-variable/space-grotesk`, `@fontsource-variable/manrope`, `@fontsource/ibm-plex-mono`
- **Testing Otomatis:** Playwright, `@axe-core/playwright`

### Infrastruktur & Deployment
- **Kontainerisasi:** Docker Multi-stage Builds, Docker Compose
- **Web Server Produksi:** Nginx Unprivileged (`nginxinc/nginx-unprivileged:alpine-slim`)
- **Database Produksi:** PostgreSQL 16 Alpine

---

## Prasyarat Sistem

Sebelum memulai, pastikan perangkat Anda memiliki:

- **Python:** Versi 3.11 s/d 3.13 (disarankan 3.13).
- **Node.js:** Versi 20.x atau 22.x LTS (disertai `npm` 10+).
- **Git:** Versi terbaru.
- **Docker & Docker Compose:** *(Opsional, jika ingin menjalankan deployment kontainer).*
- **OS:** Windows 10/11, macOS, atau Linux (Ubuntu 22.04+).

---

## Panduan Menjalankan Aplikasi (Local Development)

### 1. Clone & Setup Dependensi

```bash
# Clone repositori
git clone https://github.com/user/industrialvision-ai-platform.git
cd industrialvision-ai-platform

# Install dependensi backend Python
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt

# Install dependensi frontend React
npm --prefix frontend install
```

### 2. Konfigurasi Environment

Salin file contoh konfigurasi ke root dan direktori frontend:

```bash
# Environment utama backend
cp .env.example .env

# Environment frontend
cp frontend/.env.example frontend/.env
```

Untuk pengujian lokal, nilai default pada `.env.example` (SQLite di `./industrialvision_ai.db`) sudah siap digunakan langsung.

### 3. Migrasi Database & Bootstrap Admin

Jalankan migrasi skema tabel database dan buat akun administrator pertama Anda:

```bash
cd backend

# Terapkan migrasi database Alembic
alembic upgrade head

# Buat admin pertama (perintah ini hanya berlaku jika database belum memiliki admin)
python -m app.cli create-admin --username admin_pabrik
```
*Sistem akan meminta password interaktif (wajib minimal 12 karakter).*

> **Penting Mengenai Akun Admin:**  
> Perintah `python -m app.cli create-admin` **hanya bisa dijalankan satu kali** saat instalasi awal. Jika database sudah memiliki admin, pembuatan akun berikutnya (operator, engineer, maupun admin tambahan) wajib dilakukan melalui antarmuka web pada menu **Akses Pengguna** (`/admin`) setelah Anda login.

### 4. Menjalankan Backend API

Dari folder `backend`:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend aktif pada:
- **API Base:** [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Dokumentasi Interaktif Swagger:** [http://127.0.0.1:8000/api/docs](http://127.0.0.1:8000/api/docs)
- **OpenAPI JSON:** [http://127.0.0.1:8000/api/openapi.json](http://127.0.0.1:8000/api/openapi.json)

### 5. Menjalankan Frontend Console

Buka terminal baru di folder proyek root:

```bash
cd frontend
npm run dev
```

Buka peramban Anda di [http://127.0.0.1:5173](http://127.0.0.1:5173).

> **Alur Proxy Vite:**  
> Frontend dikonfigurasi menggunakan proxy edge `/api/v1` yang secara otomatis meneruskan permintaan ke backend di port `8000`. Hal ini mencegah konflik antara routing SPA frontend (seperti rute `/cameras`) dan endpoint REST API backend.

---

## Alur Penggunaan Operasional

Setelah masuk ke aplikasi, ikuti langkah berikut untuk memulai pengujian:

1. **Masuk ke Ruang Kendali:**  
   Buka `/login` dan masuk menggunakan akun administrator Anda.
2. **Daftarkan Stasiun Kerja:**  
   Buka menu **Stasiun & kamera** (`/cameras`), klik **Tambah stasiun**, beri nama (misal `Line 01 - Visual Stamping`), dan pilih jenis sumber **Unggah gambar**.
3. **Registrasi Artefak Model:**  
   Buka menu **Registry model** (`/models`). Pastikan file model ONNX berada di folder `backend/models/`. Masukkan nama file (contoh `detector.onnx`), definisikan kelas cacat, konfigurasi format output (`xyxy_conf_class_nx6`), lalu aktifkan model.
4. **Jalankan Inspeksi Sampel:**  
   Buka menu **Inspeksi baru** (`/inspections/new`), pilih stasiun dan model aktif, lalu unggah citra komponen produk. Sistem akan melakukan inferensi, menyimpan bukti asli, dan menampilkan kotak temuan cacat.
5. **Audit Jejak Kualitas:**  
   Buka menu **Riwayat inspeksi** (`/inspections`) untuk melihat log keputusan, memfilter berdasarkan keputusan atau batch, dan mengunduh rekaman audit dalam format JSON.

---

## Struktur Proyek

```text
industrialvision-ai-platform/
├── .github/workflows/          # CI/CD GitHub Actions (lint, test, build, compose audit)
├── backend/                    # Core API, domain service, dan engine visi komputer
│   ├── app/
│   │   ├── api/v1/             # Router REST API (auth, cameras, inspections, models, admin)
│   │   ├── core/               # Konfigurasi Pydantic, security, logging sanitizer, constants
│   │   ├── db/                 # Model SQLAlchemy, session factory, base metadata
│   │   ├── schemas/            # Skema validasi request/response Pydantic
│   │   ├── services/           # Logika bisnis: decision, evidence storage, camera worker
│   │   └── vision/             # Adapter inference (ONNX Runtime, YOLO, PatchCore/PaDiM)
│   ├── migrations/             # Lingkungan migrasi schema database Alembic
│   ├── tests/                  # 212 unit & integration tests berbasis unittest
│   ├── alembic.ini             # Konfigurasi migrasi database
│   ├── Dockerfile              # Multi-stage image build backend (non-root appuser)
│   └── requirements.txt        # Dependensi Python terpadu
├── frontend/                   # Konsol antarmuka operator web
│   ├── public/                 # Favicon dan aset statis
│   ├── src/
│   │   ├── api/                # Klien HTTP Axios dengan interseptor JWT bearer & auto-logout
│   │   ├── components/         # Komponen UI: Brand, EvidenceImage, InspectionTable, Modal, dll.
│   │   ├── hooks/              # Custom hooks: useAuth, useApi, useToast
│   │   ├── layouts/            # AppShell dengan navigasi industrial & drawer mobile
│   │   ├── pages/              # Halaman: Overview, NewInspection, History, Cameras, Models, Admin
│   │   ├── styles/             # Desain sistem modular: app.css, pages.css, responsive.css
│   │   ├── types/              # Deklarasi tipe TypeScript terpadu
│   │   ├── utils/              # Formatter data dan parser error multibahasa
│   │   ├── App.tsx             # Routing & guard otorisasi peran
│   │   ├── index.css           # Token warna, tipografi, dan variabel CSS
│   │   └── main.tsx            # Entry point React 19
│   ├── nginx.conf              # Konfigurasi reverse proxy, SPA fallback, & security headers
│   ├── Dockerfile              # Multi-stage build Nginx unprivileged
│   └── package.json            # Dependensi React, TypeScript, dan Vite
├── docs/                       # Dokumentasi arsitektur, dataset, operasi, & release gate
├── docker-compose.yml          # Orkestrasi multi-kontainer (PostgreSQL, Backend, Frontend)
├── .env.example                # Template variabel environment
└── README.md                   # Dokumentasi teknis proyek
```

---

## Referensi REST & WebSocket API

Seluruh endpoint bisnis mewajibkan header `Authorization: Bearer <token_jwt>` kecuali endpoint publik.

| Modul | Method | Endpoint | Akses Peran | Deskripsi |
|---|---|---|---|---|
| **System** | `GET` | `/health` | Publik | Status server, versi, koneksi DB, dan stasiun aktif. |
| **Auth** | `POST` | `/auth/login` | Publik | Otentikasi username & password, mengembalikan token JWT. |
| **Auth** | `GET` | `/auth/me` | Semua Peran | Profil dan hak akses pengguna yang sedang login. |
| **Auth** | `POST` | `/auth/logout` | Publik | Invalidasi sesi di sisi klien (stateless). |
| **Cameras** | `GET` | `/cameras` | Semua Peran | Daftar seluruh stasiun kerja inspeksi. |
| **Cameras** | `POST` | `/cameras` | `admin` | Mendaftarkan stasiun kerja baru. |
| **Cameras** | `GET` | `/cameras/{id}` | Semua Peran | Informasi rinci dan spesifikasi stasiun. |
| **Cameras** | `PUT` | `/cameras/{id}` | `admin` | Memperbarui nama, resolusi, FPS, atau status stasiun. |
| **Cameras** | `DELETE` | `/cameras/{id}` | `admin` | Menghapus stasiun kerja dari sistem. |
| **Cameras** | `POST` | `/cameras/{id}/start` | `admin`, `operator` | Mengubah status stasiun menjadi `connected`. |
| **Cameras** | `POST` | `/cameras/{id}/stop` | `admin`, `operator` | Mengubah status stasiun menjadi `disconnected`. |
| **Inspections**| `POST`| `/inspections/image` | Semua Peran | Unggah gambar sampel multipart & jalankan inferensi. |
| **Inspections**| `GET` | `/inspections` | Semua Peran | Riwayat inspeksi terpaginasi dengan filter keputusan/batch. |
| **Inspections**| `GET` | `/inspections/{id}` | Semua Peran | Detail hasil, latensi, alasan keputusan, & temuan cacat. |
| **Inspections**| `GET` | `/inspections/{id}/evidence` | Semua Peran | Mengambil file citra bukti asli secara aman (blob). |
| **Models** | `GET` | `/models/` | Semua Peran | Katalog model AI terdaftar dengan filter task type. |
| **Models** | `POST` | `/models/` | `admin`, `ai_engineer` | Registrasi metadata artefak model server. |
| **Models** | `POST` | `/models/{id}/activate` | `admin`, `ai_engineer` | Mengaktifkan model untuk inferensi aktif. |
| **Models** | `POST` | `/models/{id}/deactivate` | `admin`, `ai_engineer` | Menonaktifkan model dari antrean inferensi. |
| **Models** | `GET` | `/models/{id}/evaluations` | Semua Peran | Rekam jejak evaluasi performa model. |
| **Admin** | `POST` | `/admin/users` | `admin` | Membuat akun operator atau engineer baru. |
| **Stream** | `WS` | `/ws/cameras/{id}/stream` | JWT Valid | Stream metadata event kamera realtime. |

---

## Variabel Environment

| Variabel | Deskripsi | Default / Contoh | Diperlukan di Produksi? |
|---|---|---|:---:|
| `APP_ENV` | Mode lingkungan aplikasi (`development` / `production`). | `development` | Ya |
| `DEBUG` | Mengaktifkan logging debug dan echo query SQL. | `false` | Ya (wajib `false`) |
| `HOST` | Alamat binding host Uvicorn. | `127.0.0.1` | Ya |
| `PORT` | Port server backend. | `8000` | Ya |
| `DATABASE_URL` | URL koneksi SQLAlchemy sinkron. | `sqlite:///./industrialvision_ai.db` | Ya (wajib PostgreSQL) |
| `JWT_SECRET` | Kunci rahasia hashing token JWT (min. 32 karakter). | `dev-only-insecure-secret...` | Ya (wajib acak & unik) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Masa berlaku sesi token dalam menit. | `15` | Ya |
| `CORS_ORIGINS` | Daftar origin browser yang diizinkan (dipisah koma). | `http://localhost:5173,http://localhost:3000` | Ya (tanpa wildcard `*`) |
| `MAX_UPLOAD_BYTES` | Batas maksimum ukuran file gambar (byte). | `104857600` (100 MB) | Opsional |
| `EVIDENCE_ROOT` | Direktori penyimpanan fisik bukti visual. | `data/evidence` | Opsional |
| `MODEL_ROOT` | Direktori penyimpanan artefak bobot model. | `models` | Opsional |

---

## Pengujian & Verifikasi Kualitas

Proyek ini menerapkan verifikasi ketat sebelum kode dapat di-merge atau dirilis:

```bash
# 1. Jalankan 212 Unit & Integration Tests Backend
cd backend
python -m unittest discover -s tests -v

# 2. Verifikasi Kompilasi Kode Python
python -m compileall -q app migrations tests

# 3. Linter Kode Frontend (Oxlint)
npm --prefix frontend run lint

# 4. Typecheck TypeScript & Build Produksi Frontend
npm --prefix frontend run build
```

Hasil verifikasi lokal terkini:
- **Backend:** 212 tests lulus (0 gagal, 0 error).
- **Frontend Build:** Bundle produksi terkompilasi bersih tanpa *type error*.
- **Aksesibilitas:** 0 pelanggaran standar WCAG 2.1 AA di seluruh rute utama.
- **Responsif:** 0 *horizontal overflow* pada pengujian layar mobile 390 px.

---

## Deployment dengan Docker Compose

Untuk menjalankan lingkungan produksi terisolasi dengan PostgreSQL 16, backend mandiri, dan frontend Nginx unprivileged:

```bash
# 1. Pastikan variabel JWT_SECRET dan POSTGRES_PASSWORD telah diatur di .env
export JWT_SECRET="kunci-rahasia-produksi-minimal-tiga-puluh-dua-karakter"
export POSTGRES_PASSWORD="PasswordKuatPostgres2026!"

# 2. Bangun dan jalankan seluruh container
docker compose up -d --build

# 3. Periksa status kontainer
docker compose ps
```

Layanan akan berjalan pada:
- **Frontend Console:** [http://localhost:8080](http://localhost:8080)
- **Backend Internal:** `http://backend:8000` (dapat diakses internal oleh frontend proxy)
- **Database:** PostgreSQL terisolasi di jaringan internal Docker tanpa port publik terbuka ke host.

---

## Troubleshooting

### 1. `An administrator already exists; use the admin user-management API`
Perintah `python -m app.cli create-admin` hanya berlaku untuk pembuatan admin pertama kali saat database masih kosong. Jika database lokal sudah memiliki admin, silakan login di antarmuka web dengan kredensial tersebut, lalu buka menu **Akses pengguna** (`/admin`) untuk membuat user baru.

### 2. Status API Menampilkan "Sistem perlu perhatian" / Database Degraded
Pastikan database SQLite dapat diakses atau layanan PostgreSQL sudah aktif. Pengecekan kesehatan dapat diverifikasi langsung melalui:
```bash
curl http://127.0.0.1:8000/health
```
Respons normal: `{"status": "ok", "database": "ok", ...}`.

### 3. Citra Bukti Inspeksi Tidak Tampil di Peramban
Pastikan folder `data/evidence/` memiliki izin baca/tulis bagi proses yang menjalankan backend. Endpoint bukti visual memerlukan token bearer yang sah.

### 4. Gambar Bounding Box Tidak Sejajar
Bounding box SVG dihitung berdasarkan dimensi alami citra (*natural dimensions*) dan diselaraskan ke kontainer gambar render secara proporsional. Pastikan gambar tidak mengalami manipulasi rasio aspek di luar CSS yang ditentukan.

---

## Keamanan & Batasan Sistem

- **Sanitasi Kredensial:** URL kamera berprotokol RTSP secara otomatis disanitasi dari username/password sebelum disimpan atau ditampilkan di log/API.
- **Pencegahan Path Traversal:** Permintaan file model dan file bukti visual divalidasi ketat terhadap direktori dasar yang telah ditentukan (`MODEL_ROOT` dan `EVIDENCE_ROOT`). Penggunaan `..` atau penamaan path absolut akan ditolak dengan kode `422/404`.
- **Keterbatasan Live Stream:** Streaming video kamera langsung melalui antarmuka browser saat ini belum mencakup pipeline decoding frame WebRTC/RTSP end-to-end; alur kerja produksi yang divalidasi adalah *image acquisition & upload*.
- **Peringatan Model:** Mengaktifkan model di antarmuka hanya menandakan ketersediaan artefak untuk inferensi, bukan jaminan bahwa model tersebut telah divalidasi akurasinya untuk mendeteksi cacat fisik industri.

---

## Lisensi & Kontribusi

Proyek ini dilisensikan di bawah ketentuan lisensi internal / proprietary. Seluruh kontribusi kode wajib mematuhi standar pemeriksaan kualitas, lulus seluruh suite pengujian unit, serta mempertahankan rasio kontras aksesibilitas antarmuka.
