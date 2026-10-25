"""Benchmark CLI (T23): ``python -m app.benchmark.cli``.

Benchmarks a registered model version (model registry row + artifact under
``MODEL_ROOT``) on an identical input with warmup, and writes a JSON report.

Exit codes: 0 = completed report written; 2 = benchmark failed for any reason
(invalid arguments, unknown model, unsafe/mismatched artifact, engine failure,
failed inference) — in that case **no report file is written**, matching the
T21 rule that failed runs never produce metrics.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXIT_FAILURE = 2
DEFAULT_REPORT_DIR = Path("data/runs/benchmarks")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.benchmark.cli",
        description=(
            "Benchmark inference on an identical input with warmup iterations and "
            "sample-count-gated average/P50/P95/P99 (T23)."
        ),
    )
    parser.add_argument("--model-id", type=int, required=True, help="Registered model version ID")
    parser.add_argument(
        "--input",
        default=None,
        help="Input image path (default: deterministic synthetic input from --seed; "
        "synthetic inputs are labeled synthetic, never industrial data)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Synthetic input seed (ignored with --input)")
    parser.add_argument("--width", type=int, default=640, help="Synthetic input width in pixels")
    parser.add_argument("--height", type=int, default=640, help="Synthetic input height in pixels")
    parser.add_argument("--iterations", type=int, default=100, help="Measured iterations (default: 100)")
    parser.add_argument("--warmup", type=int, default=5, help="Warmup iterations excluded from stats (default: 5)")
    parser.add_argument(
        "--min-samples",
        type=int,
        default=30,
        help="Average/P50/P95/P99 are withheld below this many samples (default: 30)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Report JSON path (default: data/runs/benchmarks/benchmark-<timestamp>.json)",
    )
    return parser


def resolve_model_artifact(model: Any, *, model_root: Any = None) -> tuple[Path, str]:
    """Resolve a registered artifact strictly under MODEL_ROOT and verify it.

    Returns the resolved path and its server-computed SHA-256. Raises
    ``BenchmarkError`` when the registry row lacks a reference, the reference
    is unsafe/missing, or the file's checksum no longer matches the registered
    value — a swapped artifact must never be benchmarked under the old
    model version's identity.
    """
    from app.benchmark.runner import BenchmarkError
    from app.services.model_registry import (
        ArtifactReferenceError,
        checksum_artifact,
        resolve_artifact_reference,
    )

    reference = getattr(model, "source_path", None)
    if not reference:
        raise BenchmarkError("Model version has no artifact reference (source_path)")
    try:
        path = resolve_artifact_reference(reference, model_root=model_root)
    except ArtifactReferenceError as exc:
        raise BenchmarkError(str(exc)) from exc
    try:
        checksum = checksum_artifact(path)
    except OSError as exc:
        raise BenchmarkError(f"Artifact could not be read for checksumming: {exc}") from exc
    registered = getattr(model, "checksum", None)
    if registered and registered != checksum:
        raise BenchmarkError(
            "Artifact checksum does not match the registry value; refusing to "
            "benchmark an unidentified artifact"
        )
    return path, checksum


def default_report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return DEFAULT_REPORT_DIR / f"benchmark-{stamp}.json"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.iterations < 1:
        print("error: --iterations must be >= 1", file=sys.stderr)
        return EXIT_FAILURE
    if args.warmup < 0:
        print("error: --warmup must be >= 0", file=sys.stderr)
        return EXIT_FAILURE
    if args.min_samples < 1:
        print("error: --min-samples must be >= 1", file=sys.stderr)
        return EXIT_FAILURE
    if args.width < 1 or args.height < 1:
        print("error: --width and --height must be >= 1", file=sys.stderr)
        return EXIT_FAILURE

    try:
        from app.benchmark.runner import BenchmarkError, load_input, run_benchmark, write_report
        from app.db.models import ModelVersion
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            model = db.get(ModelVersion, args.model_id)
            if model is None:
                raise BenchmarkError(f"Model version {args.model_id} not found in the registry")
            artifact_path, checksum = resolve_model_artifact(model)
            engine_meta: dict[str, Any] = {
                "model_version_id": model.id,
                "model_name": model.name,
                "model_version": model.version,
                "framework": model.framework,
                "framework_version": model.framework_version,
                "task_type": model.task_type,
                "device": model.device,
                "artifact_reference": model.source_path,
                "artifact_id": model.artifact_id,
                "artifact_sha256": checksum,
                "config": dict(model.config_json or {}),
            }
            try:
                from app.vision.factory import create_detection_engine

                engine = create_detection_engine(model)
            except Exception as exc:
                raise BenchmarkError(f"Engine creation failed: {exc}") from exc
        finally:
            db.close()

        input_array, input_meta = load_input(
            path=args.input,
            seed=None if args.input is not None else args.seed,
            width=args.width,
            height=args.height,
        )
        report = run_benchmark(
            lambda frame: engine.predict(frame),
            input_array=input_array,
            input_meta=input_meta,
            iterations=args.iterations,
            warmup=args.warmup,
            min_samples=args.min_samples,
            engine_meta=engine_meta,
        )
        out = write_report(report, Path(args.out) if args.out else default_report_path())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("no benchmark report was written", file=sys.stderr)
        return EXIT_FAILURE

    measurements = report["measurements"]
    stats = measurements["stats"]
    print(f"benchmark completed: {out}")
    print(
        f"samples={measurements['sample_count']} warmup={args.warmup} "
        f"batch_size={report['protocol']['batch_size']} "
        f"input={input_meta['source']} sha256:{input_meta['sha256']}"
    )
    if stats is not None:
        print(f"average_ms = {stats['average_ms']:.3f}")
        print(f"p50_ms = {stats['p50_ms']:.3f}")
        print(f"p95_ms = {stats['p95_ms']:.3f}")
        print(f"p99_ms = {stats['p99_ms']:.3f}")
    else:
        print(f"stats withheld: {measurements['stats_withheld_reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
