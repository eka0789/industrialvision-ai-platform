"""OCR service (F2) behind the optional tesseract extra.

The adapter conforms to the vision-contract discipline: missing runtime →
explicitly labeled UNAVAILABLE (never a fabricated empty result), engine
errors → structured failure. Synthetic-image tests exercise the contract
with a stub engine; real OCR requires ``pytesseract`` + the tesseract binary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OCR_UNAVAILABLE = "unavailable-missing-tesseract"


@dataclass
class OcrResult:
    status: str  # "ok" | "unavailable" | "error"
    mode: str  # provider label: "tesseract" or OCR_UNAVAILABLE
    text: str
    confidence: float | None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "text": self.text,
            "confidence": self.confidence,
            "error": self.error,
        }


def _engine():
    try:
        import pytesseract  # type: ignore[import-not-found]

        pytesseract.get_tesseract_version()  # binary check
        return pytesseract
    except Exception:  # noqa: BLE001 - missing package OR missing binary
        return None


def run_ocr(image: Any, engine: Any | None = None, lang: str = "eng") -> OcrResult:
    """Run OCR on an image (numpy array or path). Never raises."""
    pytesseract = engine if engine is not None else _engine()
    if pytesseract is None:
        return OcrResult(
            status="unavailable",
            mode=OCR_UNAVAILABLE,
            text="",
            confidence=None,
            error="pytesseract/tesseract not installed; install the optional OCR extra",
        )
    try:
        text = pytesseract.image_to_string(image, lang=lang)
        try:
            data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)
            confidences = [float(c) for c in data.get("conf", []) if c not in ("-1", -1, "") and c is not None]
            confidence = round(sum(confidences) / len(confidences), 1) if confidences else None
        except Exception:  # noqa: BLE001 - confidence is best-effort
            confidence = None
        return OcrResult(status="ok", mode="tesseract", text=text.strip(), confidence=confidence)
    except Exception as exc:  # noqa: BLE001 - engine failure is structured
        return OcrResult(status="error", mode="tesseract", text="", confidence=None, error=str(exc)[:500])
