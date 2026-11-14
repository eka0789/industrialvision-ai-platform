"""Anomaly memory-bank training (F3) via the existing training-run lifecycle.

PatchCore/PaDiM memory banks are trained from normal samples when the
optional anomaly runtime (torch/anomalib-family adapters in
``app/vision/anomaly_adapters.py``) is available. **Missing runtime → the run
is marked ``failed`` with a reason and NO metrics file** — the existing
artifact-assertion rule, unchanged.

Usage::

    python -m app.training.train_anomaly --run-dir <dir> --normal-dir <images> --seed 7
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from app.training.runs import TrainingRun


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.training.train_anomaly")
    parser.add_argument("--run-dir-base", type=str, default="data/anomaly_runs")
    parser.add_argument("--normal-dir", type=str, required=True, help="directory of NORMAL samples only")
    parser.add_argument("--detector", type=str, default="patchcore", choices=["patchcore", "padim"])
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()

    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = f"anomaly-{args.detector}-{stamp}-s{args.seed}"
    dataset_identity = {
        "kind": "local-normal-samples",
        "name": Path(args.normal_dir).name,
        "source": args.normal_dir,
        "fingerprint": f"normal-dir:{Path(args.normal_dir).resolve()}",
        "split": "normal-only",
    }
    run = TrainingRun.create(
        Path(args.run_dir_base),
        task=f"anomaly_{args.detector}_training",
        seed=args.seed,
        config={"detector": args.detector, "normal_dir": args.normal_dir},
        dataset=dataset_identity,
        run_id=run_id,
    )
    try:
        run.start()
        import cv2

        from app.vision.anomaly_adapters import PatchCoreAdapter, PaDiMAdapter

        adapters = {"patchcore": PatchCoreAdapter, "padim": PaDiMAdapter}
        try:
            detector = adapters[args.detector]()
        except RuntimeError as exc:
            # The compatibility gate refused (missing torch/feature deps): the
            # run FAILS with the reason — no metrics file, existing rule.
            raise RuntimeError(f"anomaly runtime unavailable: {exc}") from exc

        normal_dir = Path(args.normal_dir)
        paths = sorted(p for p in normal_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}) if normal_dir.is_dir() else []
        if not paths:
            raise RuntimeError(f"no normal sample images found in {args.normal_dir}")
        images = []
        for path in paths:
            image = cv2.imread(str(path))
            if image is None:
                raise RuntimeError(f"unreadable normal sample: {path.name}")
            images.append(image)

        detector.fit(images)

        checkpoint_path = run.run_dir / checkpoint_rel
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        bank = getattr(detector, "memory_bank", None)
        if bank is None:
            raise RuntimeError("trained detector produced no memory bank")
        np.savez_compressed(checkpoint_path, memory_bank=bank, threshold=detector.threshold)
        checkpoint_rel = "models/memory_bank.npz"

        # No metrics are fabricated: memory-bank training reports counts only,
        # and quality metrics come from a separate evaluation run (existing rule).
        run.complete(
            checkpoints=[checkpoint_rel],
            metrics={"normal_samples_used": len(images), "detector": args.detector},
        )
        run.log(json.dumps({"detector": args.detector, "samples": len(images)}, indent=2))
        print(json.dumps({"run_id": run_id, "status": "completed", "samples": len(images)}))
        return 0
    except Exception as exc:  # noqa: BLE001 - fail-safe: failed run, no metrics lie
        run.fail(str(exc)[:1000])
        print(json.dumps({"run_id": run_id, "status": "failed", "reason": str(exc)[:200]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
