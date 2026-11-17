# IndustrialVision AI — Factory Pilot & Production Commissioning Runbook

Panduan bertahap untuk membawa **IndustrialVision AI** dari lingkungan pengembangan ke lini produksi pabrik secara aman.

---

## Fase 1 — Persiapan Lingkungan & Dependensi Industri

1. **Siapkan server/edge PC dengan Docker & TLS:**
   - Ikuti konfigurasi pada [`docker-compose.yml`](../docker-compose.yml) (PostgreSQL 18 + Redis 7 + FastAPI + RQ Worker + Nginx TLS).
   - Pastikan variabel wajib di `.env` terisi aman: `APP_ENV=production`, `JWT_SECRET` (minimal 32 karakter acak), `POSTGRES_PASSWORD`, `DEMO_LIVE_FEED=false`.
2. **Pasang dependensi perangkat keras industri (bila menggunakan PLC/OCR/GenICam/YOLO langsung di host/image):**
   ```bash
   pip install -r backend/requirements.txt -r backend/requirements-industrial.txt
   ```
   - Untuk OCR tanggal kedaluwarsa (`app/vision/ocr.py`), pasang binary OS `tesseract-ocr`.
   - Untuk kamera GigE Vision / USB3 Vision (`source_type="genicam"`), pasang library GenTL `.cti` dari vendor kamera (mis. Basler pylon atau Hikrobot MVS).

---

## Fase 2 — Shadow Mode Pilot (Wajib Sebelum Aktuasi Rejector)

Jalankan lini pilot dalam **OT Shadow Mode** (`ot_shadow_mode: true`) selama minimal 1–2 minggu produksi normal:
- Kamera membaca produk dan model AI mencatat keputusan (`PASS` / `FAIL` / `REVIEW`), `trigger_to_reject_ms`, dan *evidence* cacat.
- **Aktuator PLC tidak akan ditembakkan fisik** — setiap event `FAIL`/`ERROR` dicatat dengan `rejector_actuated=false`, `would_actuate=true`, dan `transport_status="shadow_mode_suppressed"`.

Aktifkan Shadow Mode melalui endpoint konfigurasi OT (`owner`, `admin`, atau `ai_engineer`):
```bash
PATCH /api/v1/ot/cameras/{camera_id}/config
{
  "ot_protocol": "modbus_tcp",
  "ot_host": "192.168.10.50",
  "ot_port": 502,
  "trigger_register": 1,
  "reject_coil": 100,
  "reject_delay_ms": 120,
  "reject_pulse_ms": 45,
  "ot_shadow_mode": true
}
```

---

## Fase 3 — Pengumpulan Dataset, Pelatihan, & Kalibrasi Threshold

1. Kumpulkan gambar produk nyata (OK dan berbagai kelas cacat NG) dari kamera pabrik selama *Shadow Mode*.
2. Latih model deteksi/anomali dengan data pabrik Anda sendiri (jangan gunakan dataset benchmark berlisensi non-komersial untuk produksi).
3. Lakukan *threshold sweep* untuk menyeimbangkan **Escape Rate** (cacat lolos) vs **False Reject Rate** (produk bagus ditolak):
   ```bash
   cd backend
   python -m app.training.calibrate \
     --predictions preds/val \
     --ground-truth ../datasets/plant_val/labels \
     --split-file val.txt \
     --iou 0.5 \
     --thresholds 0.20,0.30,0.40,0.50,0.60,0.70
   ```
4. Verifikasi laporan evaluasi (`python -m app.training.evaluate`) sebelum mempromosikan model ke stage `production` di Model Registry.

---

## Fase 4 — Uji Hardware-in-the-Loop (HIL) & Cutover Produksi

1. **Uji pulsa PLC terkontrol (`test-pulse`):**
   - Pastikan area konveyor aman dari operator.
   - Uji pulsa tunggal via `POST /api/v1/ot/cameras/{camera_id}/test-pulse` dan ukur *travel time* dari sensor fotoelektrik ke nosel *rejector* untuk mengkalibrasi `reject_delay_ms` dan `reject_pulse_ms`.
2. **Isolasi Keselamatan Mesin (Safety PLC):**
   - *Emergency Stop*, interlock pintu pengaman, dan relai keselamatan fisik **harus tetap ditangani oleh Safety PLC terpisah**, bukan oleh perangkat lunak AI.
3. **Nonaktifkan Shadow Mode:**
   - Setelah akurasi model, latensi (`trigger_to_reject_ms`), dan *timing* mekanik terverifikasi, ubah `"ot_shadow_mode": false` pada `PATCH /api/v1/ot/cameras/{camera_id}/config`.

---

## Fase 5 — Backup Rutin & Monitoring Operasional

* **Backup Database + Evidence sekaligus:**
  ```bash
  ./scripts/backup.sh backup --output-dir /mnt/backup-nas --evidence --retention-days 30
  ```
* **Pantau Metrik:** Pastikan Prometheus + Grafana memantau `iv_model_drift_psi`, `iv_quality_guard_*`, serta alert `ot.plc_connection_error` dan `ot.conveyor_latency_exceeded`.
