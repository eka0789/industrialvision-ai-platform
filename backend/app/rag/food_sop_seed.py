"""Bundled Food Safety & HACCP SOP Seed for Knowledge AI / RAG (Fase 6: `food_sop_seed.py`).

Provides bilingual (Bahasa Indonesia & English) standard operating procedures
for the Food Manufacturing Division ready for BM25 + Vector RRF ingestion with
strict 1:1 citation enforcement:
- SOP-QA-01: Penanganan Deviasi Heat-Seal & Kebocoran Kemasan (CCP)
- SOP-QA-02: Tindakan Korektif Tanggal Kedaluwarsa (Expired Date) Tidak Terbaca / Hilang
- SOP-SAN-03: Prosedur Washdown, Sanitasi Lensa Kamera, & Pencegahan Kondensasi Uap
- SOP-FOD-04: Protokol Karantina Batch Saat Terdeteksi Kontaminasi Benda Asing (Foreign Matter)
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import RagDocument
from app.rag.ingestion import ingest_document, store_original

FOOD_SOP_SEED_DOCUMENTS: list[dict[str, str]] = [
    {
        "code": "SOP-QA-01",
        "title": "SOP-QA-01: Penanganan Deviasi Heat-Seal & Kebocoran Kemasan (CCP)",
        "filename": "SOP-QA-01-Heat-Seal-CCP.md",
        "content": (
            "# SOP-QA-01: Penanganan Deviasi Heat-Seal & Kebocoran Kemasan (CCP) / "
            "Heat-Seal Deviation & Package Leak Handling (Critical Control Point)\n\n"
            "## Pasal 1 — Tujuan & Standar Kepatuhan (Purpose & Compliance)\n"
            "Prosedur ini mengatur pengendalian titik kritis (HACCP CCP-1, ISO 22000, FSSC 22000, BPOM) "
            "terhadap cacat segel kemasan (`SEAL_DEFECT` / `seal_leak`) pada lini pengemasan makanan "
            "(biscuit, snack, dairy, beverage, frozen food). Kebocoran segel berisiko menimbulkan "
            "kontaminasi mikrobiologi dan ketengikan oksidatif.\n\n"
            "## Pasal 2 — Ambang Batas Operasional & Deteksi Vision AI (Operational Thresholds)\n"
            "1. Suhu elemen pemanas (`sealer_temp_c`) pada mesin `HEAT_SEALER` / `FLOW_WRAPPER` wajib "
            "berada pada rentang normal 140°C hingga 185°C (setpoint nominal 165°C).\n"
            "2. Tekanan udara aktuator jepit (`pneumatic_pressure_bar`) wajib dijaga pada 5.5 hingga 7.0 bar.\n"
            "3. Kamera inspeksi memeriksa zona ROI segel atas/bawah menggunakan model Anomaly Segmentation "
            "(PatchCore threshold 0.58) dan Object Detection (`SEAL_DEFECT`).\n\n"
            "## Pasal 3 — Tindakan Korektif Lonjakan Cacat Segel (5-Minute / 2-Minute Spike Rule)\n"
            "Apabila terdeteksi lonjakan `SEAL_DEFECT` (> 3 kemasan FAIL dalam 2–5 menit berturut-turut): "
            "1. Sistem mengorelasikan temuan Vision AI dengan telemetri `HEAT_SEALER` (`sealer_temp_c` < 140°C "
            "atau `pneumatic_pressure_bar` < 5.5 bar). "
            "2. Ajukan tiket pemeliharaan (`create_maintenance_request`) dengan persetujuan manusia (HITL) "
            "untuk kalibrasi PID thermocouple sealer bar dan pembersihan residu film plastik pada jaw sealer. "
            "3. Lakukan uji kebocoran vakum (*vacuum leak test*) pada sub-lot produksi 5 menit terakhir."
        ),
    },
    {
        "code": "SOP-QA-02",
        "title": "SOP-QA-02: Tindakan Korektif Tanggal Kedaluwarsa (Expired Date) Tidak Terbaca / Hilang",
        "filename": "SOP-QA-02-Expired-Date-OCR.md",
        "content": (
            "# SOP-QA-02: Tindakan Korektif Tanggal Kedaluwarsa (Expired Date) Tidak Terbaca / Hilang / "
            "Corrective Action for Missing or Illegible Expiry Date (MISSING_EXP_DATE)\n\n"
            "## Pasal 1 — Regulasi Pelabelan Pangan (Food Labeling Regulation)\n"
            "Setiap kemasan pangan olahan wajib mencantumkan tanggal kedaluwarsa (*Best Before / EXP Date*) "
            "dan kode Batch/Lot yang terbaca jelas sesuai regulasi BPOM dan standar ketertelusuran HACCP. "
            "Kemasan dengan cacat `MISSING_EXP_DATE` dilarang keras diedarkan ke pasar.\n\n"
            "## Pasal 2 — Verifikasi OCR Multi-Zone ROI (OCR Expiry Verification)\n"
            "Zona `ocr_expiry` pada `InspectionRecipe` memeriksa keterbacaan tanggal kedaluwarsa menggunakan "
            "mesin OCR dengan pola regex standar `EXP DD/MM/YYYY` atau `YYYY-MM-DD` dan tingkat keyakinan "
            "minimum (`confidence_threshold` >= 0.70). Jika teks kosong atau tidak sesuai format, sistem "
            "menetapkan keputusan `FAIL` dengan kategori `MISSING_EXP_DATE` dan mengaktifkan aktuator rejector PLC.\n\n"
            "## Pasal 3 — Tindakan Korektif & Penahanan Batch (Batch Hold Corrective Action)\n"
            "Apabila terjadi `MISSING_EXP_DATE` beruntun (>= 3 kemasan berturut-turut): "
            "1. Periksa level tinta ribbon/inkjet coder (CIJ/TIJ), kebersihan printhead, dan sinkronisasi encoder konveyor. "
            "2. Operations Agent menyusun draf penahanan batch (`draft_batch_hold_notice`) untuk produk yang "
            "diproduksi sejak pemeriksaan kode terakhir yang valid. "
            "3. Status batch hanya dapat diubah menjadi `HOLD` setelah disetujui oleh `admin` atau `owner` (QA Manager)."
        ),
    },
    {
        "code": "SOP-SAN-03",
        "title": "SOP-SAN-03: Prosedur Washdown, Sanitasi Lensa Kamera, & Pencegahan Kondensasi Uap",
        "filename": "SOP-SAN-03-Washdown-Sanitasi-Lensa.md",
        "content": (
            "# SOP-SAN-03: Prosedur Washdown, Sanitasi Lensa Kamera, & Pencegahan Kondensasi Uap / "
            "Washdown Procedure, Camera Lens Sanitation, & Steam Condensation Prevention\n\n"
            "## Pasal 1 — Perlindungan Lensa Saat Pencucian Sanitasi (IP69K Washdown Guard)\n"
            "Lini pabrik makanan menjalani sanitasi tekanan tinggi (*high-pressure washdown*), paparan uap "
            "panas oven/perebusan, serta cipratan minyak/gula. Sebelum inferensi model dijalankan, modul "
            "`quality_guard` menghitung skor ketajaman *Laplacian Variance* (< 5 ms).\n\n"
            "## Pasal 2 — Penanganan LENS_BLUR_OR_FOG & Kegagalan Pencahayaan\n"
            "1. Jika *Laplacian Variance* turun di bawah `blur_threshold` (default 80.0), frame ditandai "
            "sebagai `LENS_BLUR_OR_FOG`, kamera otomatis masuk status `DEGRADED`, dan sistem memblokir "
            "keputusan `PASS` palsu (fail-safe `ERROR` / `REVIEW`).\n"
            "2. Operator wajib membersihkan kaca pelindung enclosure kamera IP69K menggunakan tisu bebas serat "
            "(*lint-free microfiber*) dan alkohol isopropil 70% *food-grade*, serta memastikan pemanas "
            "anti-embun (*defogger heater*) berfungsi.\n\n"
            "## Pasal 3 — Kompensasi Pencahayaan & Penyesuaian Threshold Resep (Drift & Lighting Adjustment)\n"
            "1. Aktifkan *CLAHE Glare Suppression* pada kanal Luminance (LAB) dan normalisasi Gamma/White Balance "
            "untuk meredam pantulan spekular kemasan plastik metalik atau aluminium foil.\n"
            "2. Jika terjadi pergeseran distribusi citra (*PSI data drift* melewati ambang batas) akibat pergantian "
            "lampu lini atau variasi warna bahan baku, gunakan tool `propose_recipe_threshold_adjustment` yang "
            "wajib disetujui oleh `ai_engineer` atau `admin` sebelum nilai ambang resep diperbarui."
        ),
    },
    {
        "code": "SOP-FOD-04",
        "title": "SOP-FOD-04: Protokol Karantina Batch Saat Terdeteksi Kontaminasi Benda Asing (Foreign Matter)",
        "filename": "SOP-FOD-04-Karantina-Foreign-Matter.md",
        "content": (
            "# SOP-FOD-04: Protokol Karantina Batch Saat Terdeteksi Kontaminasi Benda Asing (Foreign Matter) / "
            "Batch Quarantine Protocol Upon Foreign Matter Detection (FOREIGN_MATTER)\n\n"
            "## Pasal 1 — Klasifikasi Bahaya Fisik Keamanan Pangan (Physical Hazard CCP)\n"
            "Temuan kontaminasi benda asing (`FOREIGN_MATTER` seperti serpihan logam, pecahan kaca, plastik keras, "
            "serangga, atau serpihan karet sabuk konveyor) merupakan bahaya fisik kritis dalam sistem HACCP dan "
            "FSSC 22000 dengan toleransi nol (*Zero Tolerance*).\n\n"
            "## Pasal 2 — Isolasi Bukti & Penendangan Real-Time (Immediate Rejection & Evidence Lock)\n"
            "1. Saat kamera mendeteksi `FOREIGN_MATTER`, OT Bridge langsung memicu sinyal `REJECT` ke PLC "
            "(pneumatic air-blast / diverter arm) sesuai `reject_delay_ms` untuk memisahkan produk ke kotak "
            "reject terkunci (*locked reject bin*).\n"
            "2. Citra bukti (`FAIL`) wajib disimpan 100% dalam resolusi penuh dengan perlindungan anti-hapus "
            "selama masa retensi audit (minimal 365 hari).\n\n"
            "## Pasal 3 — Protokol Karantina Batch (Mandatory Batch Hold Notice)\n"
            "1. Setiap deteksi `FOREIGN_MATTER` wajib memicu triase insiden proaktif oleh Operations Agent.\n"
            "2. Operations Agent memanggil `draft_batch_hold_notice(batch_id, reason, affected_qty)` untuk "
            "menyusun dokumen penahanan lot produksi yang terdampak.\n"
            "3. Pengubahan status `ProductionBatch` menjadi `HOLD` memerlukan persetujuan eksplisit (*Human-in-the-Loop*) "
            "dari pengguna dengan peran `admin` atau `owner`. Lot yang berstatus `HOLD` dilarang dikirim ke gudang "
            "distribusi hingga inspeksi ulang 100% dan investigasi akar masalah (CAPA) diselesaikan."
        ),
    },
]


def seed_food_safety_sops(db: Session, org_id: int = 1, user_id: int | None = None) -> list[dict[str, Any]]:
    """Idempotently store and ingest the 4 bundled Food Safety & HACCP SOP documents into RAG."""
    results: list[dict[str, Any]] = []
    for spec in FOOD_SOP_SEED_DOCUMENTS:
        content_bytes = spec["content"].encode("utf-8")
        sha = hashlib.sha256(content_bytes).hexdigest()
        existing = (
            db.query(RagDocument)
            .filter(RagDocument.org_id == org_id, RagDocument.sha256 == sha)
            .first()
        )
        if existing and existing.status == "ready" and existing.chunk_count > 0:
            results.append(
                {
                    "id": existing.id,
                    "code": spec["code"],
                    "title": existing.title,
                    "status": existing.status,
                    "chunks": existing.chunk_count,
                    "seeded": False,
                }
            )
            continue

        stored_name = store_original(org_id, spec["filename"], content_bytes)
        if existing is None:
            doc = RagDocument(
                org_id=org_id,
                title=spec["title"],
                filename=stored_name,
                mime_type="text/plain",
                size_bytes=len(content_bytes),
                sha256=sha,
                status="pending",
                uploaded_by_user_id=user_id,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
        else:
            doc = existing
            doc.filename = stored_name
            db.commit()

        ingest_document(db, doc.id)
        db.refresh(doc)
        results.append(
            {
                "id": doc.id,
                "code": spec["code"],
                "title": doc.title,
                "status": doc.status,
                "chunks": doc.chunk_count,
                "seeded": True,
            }
        )
    return results
