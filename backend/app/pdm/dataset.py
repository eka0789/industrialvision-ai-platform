"""PdM dataset generation/validation — AI4I 2020 schema mirror.

This module generates a **synthetic mirror** of the AI4I 2020 Predictive
Maintenance dataset (UCI, CC BY 4.0): five sensor features and failure modes
(TWF/HDF/PWF/OSF/RNF) using the generative thresholds documented in the
original dataset description. It is NOT real fleet data; every artifact and
report produced from it must be labeled `synthetic-mirror`.

If a real CSV exists at ``PDM_DATASET_CSV`` (default
``data/pdm/ai4i2020.csv``) with the expected columns, it is used verbatim
instead — the loader validates the schema and fails loudly on mismatch.

Usage::

    python -m app.pdm.dataset --rows 5000 --seed 42 --out-dir data/pdm/runs
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings

FEATURES = [
    "Air temperature [K]",
    "Process temperature [K]",
    "Rotational speed [rpm]",
    "Torque [Nm]",
    "Tool wear [min]",
]
FOOD_FEATURES = [
    "sealer_temp_c",
    "pneumatic_pressure_bar",
    "conveyor_speed_mpm",
    "motor_current_amp",
    "vibration_mm_s",
    "ambient_humidity_pct",
]
FAILURE_FLAG = "Machine failure"
FAILURE_MODES = ["TWF", "HDF", "PWF", "OSF", "RNF"]
FOOD_FAILURE_MODES = ["SEAL_TEMP_LOW", "SEAL_TEMP_HIGH", "PNEUMATIC_DROP", "CONVEYOR_OVERLOAD", "BEARING_VIBRATION_HIGH"]
COLUMNS = ["UDI", "Product ID", "Type", *FEATURES, FAILURE_FLAG, *FAILURE_MODES]

# AI4I 2020 documented generative thresholds (mirror, not measured facts):
# - TWF: tool wear between 200-240 min OR random failure 0.4%
# - HDF: (process temp - air temp) < 8.6 K AND rotational speed < 1380 rpm
# - PWF: power = torque * rpm (rad/s) outside [3500 W, 5000 W]
# - OSF: tool wear * torque > 11,000/12,000/13,000 minNm for L/M/H product grade
# - RNF: 0.1% random
PWF_MIN_W = 3500.0
PWF_MAX_W = 5000.0
HDF_DELTA_K = 8.6
HDF_RPM = 1380.0
TWF_MIN_MIN = 200.0
TWF_MAX_MIN = 240.0
OSF_THRESHOLD_BY_TYPE = {"L": 11000.0, "M": 12000.0, "H": 13000.0}

DATASET_KIND_SYNTHETIC = "synthetic-mirror"
DATASET_KIND_REAL = "real-csv"


@dataclass
class DatasetBundle:
    kind: str
    source: str
    sha256: str
    rows: int
    train_path: str
    val_path: str
    test_path: str
    split_sha256: dict


def dataset_csv_path() -> Path:
    return Path(settings.PDM_DATASET_CSV)


def load_or_generate(rows: int = 5000, seed: int = 42) -> tuple[list[dict], str, str]:
    """Return (rows, kind, source). Real CSV wins when present and valid."""
    csv_path = dataset_csv_path()
    if csv_path.is_file():
        data = _load_real_csv(csv_path)
        return data, DATASET_KIND_REAL, str(csv_path)
    return _generate(rows, seed), DATASET_KIND_SYNTHETIC, f"synthetic:{rows}rows:seed{seed}"


def _generate(rows: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    out: list[dict] = []
    base_temp = 298.0
    for i in range(rows):
        product_type = rng.choices(["L", "M", "H"], weights=[60, 30, 10])[0]
        air = base_temp + rng.gauss(0, 1.8) - 2 + rng.random() * 2
        process = air + rng.uniform(8, 11)
        rpm = rng.gauss(1500, 120)
        torque = rng.gauss(40, 12) + (3500 / max(rpm, 1) - 40) * 0.4
        tool_wear = min(250.0, max(0.0, rng.uniform(0, 250) * (0.4 if product_type == "H" else 1.0)))

        twf = 1 if (TWF_MIN_MIN <= tool_wear <= TWF_MAX_MIN or rng.random() < 0.004) else 0
        hdf = 1 if ((process - air) < HDF_DELTA_K and rpm < HDF_RPM) else 0
        power = torque * rpm * 2 * 3.14159 / 60
        pwf = 1 if (power < PWF_MIN_W or power > PWF_MAX_W) else 0
        osf = 1 if (tool_wear * torque > OSF_THRESHOLD_BY_TYPE[product_type]) else 0
        rnf = 1 if rng.random() < 0.001 else 0
        failure = 1 if (twf or hdf or pwf or osf or rnf) else 0

        out.append(
            {
                "UDI": i + 1,
                "Product ID": f"{product_type}{i + 1}",
                "Type": product_type,
                FEATURES[0]: round(air, 2),
                FEATURES[1]: round(process, 2),
                FEATURES[2]: round(rpm, 1),
                FEATURES[3]: round(torque, 2),
                FEATURES[4]: round(tool_wear, 1),
                FAILURE_FLAG: failure,
                "TWF": twf,
                "HDF": hdf,
                "PWF": pwf,
                "OSF": osf,
                "RNF": rnf,
            }
        )
    return out


def _load_real_csv(path: Path) -> list[dict]:
    """Load a real AI4I-format CSV; schema mismatch is a hard error."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [column for column in COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Real dataset {path} is missing columns: {missing}")
        rows = []
        for raw in reader:
            row = {column: raw[column] for column in COLUMNS}
            for column in [*FEATURES, FAILURE_FLAG, *FAILURE_MODES]:
                row[column] = float(row[column])
            row["UDI"] = int(float(row["UDI"]))
            rows.append(row)
    if not rows:
        raise ValueError(f"Real dataset {path} contains no rows")
    return rows


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_split_bundle(
    out_dir: Path,
    rows: int = 5000,
    seed: int = 42,
    val_frac: float = 0.15,
    test_frac: float = 0.15,
) -> DatasetBundle:
    """Generate/load rows, write a deterministic stratified split + manifest."""
    data, kind, source = load_or_generate(rows=rows, seed=seed)
    rng = random.Random(seed + 1)

    failures = [row for row in data if row[FAILURE_FLAG] == 1]
    normals = [row for row in data if row[FAILURE_FLAG] == 0]

    def split(pool: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
        rng.shuffle(pool)
        n_val = int(len(pool) * val_frac)
        n_test = int(len(pool) * test_frac)
        return pool[n_val + n_test :], pool[:n_val], pool[n_val : n_val + n_test]

    tr_f, va_f, te_f = split(list(failures))
    tr_n, va_n, te_n = split(list(normals))

    train = sorted(tr_f + tr_n, key=lambda r: r["UDI"])
    val = sorted(va_f + va_n, key=lambda r: r["UDI"])
    test = sorted(te_f + te_n, key=lambda r: r["UDI"])

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, split_rows in (("train", train), ("val", val), ("test", test)):
        path = out_dir / f"pdm_{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(split_rows)
        paths[name] = path

    bundle = DatasetBundle(
        kind=kind,
        source=source,
        sha256=_sha256_of(paths["train"]),
        rows=len(data),
        train_path=str(paths["train"]),
        val_path=str(paths["val"]),
        test_path=str(paths["test"]),
        split_sha256={name: _sha256_of(path) for name, path in paths.items()},
    )
    manifest = {**asdict(bundle), "columns": COLUMNS, "generated_at": datetime.now(timezone.utc).isoformat()}
    (out_dir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.pdm.dataset")
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", type=str, default=str(dataset_csv_path().parent / "runs"))
    args = parser.parse_args()

    bundle = write_split_bundle(Path(args.out_dir), rows=args.rows, seed=args.seed)
    print(
        json.dumps(
            {
                "kind": bundle.kind,
                "rows": bundle.rows,
                "train": bundle.train_path,
                "val": bundle.val_path,
                "test": bundle.test_path,
                "split_sha256": bundle.split_sha256,
            },
            indent=2,
        )
    )
    return 0


def generate_food_pdm_dataset(rows: int = 1000, seed: int = 42) -> list[dict]:
    """Generate synthetic Food Processing & Packaging Equipment telemetry rows (Fase 5)."""
    from app.pdm.schemas import FOOD_MACHINE_TYPES, evaluate_food_equipment_anomalies

    rng = random.Random(seed)
    out: list[dict] = []
    for i in range(rows):
        m_type = rng.choice(FOOD_MACHINE_TYPES)
        # 82% normal operation, 18% realistic thermal/pneumatic/bearing anomaly
        is_anomaly_injection = rng.random() < 0.18
        sealer_temp_c = round(rng.uniform(122.0, 138.0) if (is_anomaly_injection and rng.random() < 0.5) else rng.uniform(145.0, 180.0), 1)
        pneumatic_bar = round(rng.uniform(4.1, 5.2) if (is_anomaly_injection and rng.random() < 0.4) else rng.uniform(5.7, 6.8), 2)
        speed_mpm = round(rng.gauss(120.0, 8.0), 1)
        current_amp = round(rng.uniform(14.5, 18.0) if (is_anomaly_injection and rng.random() < 0.25) else rng.uniform(6.5, 11.5), 2)
        vibration_mm_s = round(rng.uniform(4.8, 7.5) if (is_anomaly_injection and rng.random() < 0.25) else rng.uniform(0.8, 3.2), 2)
        humidity_pct = round(rng.uniform(45.0, 72.0), 1)

        sample = {
            "UDI": i + 1,
            "machine_type": m_type,
            "sealer_temp_c": sealer_temp_c,
            "pneumatic_pressure_bar": pneumatic_bar,
            "conveyor_speed_mpm": speed_mpm,
            "motor_current_amp": current_amp,
            "vibration_mm_s": vibration_mm_s,
            "ambient_humidity_pct": humidity_pct,
        }
        modes = evaluate_food_equipment_anomalies(sample)
        sample["failure_modes"] = modes
        sample[FAILURE_FLAG] = 1 if modes else 0
        out.append(sample)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
