# Benchmark protocol and report template (T23)

This document defines **how a benchmark in this project is run and reported**.
The accompanying code is `app.benchmark.runner` (protocol enforcement) and
`python -m app.benchmark.cli` (entry point). Numbers produced by this protocol
are this project's measurements; numbers copied from elsewhere must be labeled
with their source and never mixed into a report.

## What is measured (and what is not)

- **Measured:** wall-clock milliseconds around one `engine.predict(frame)`
  call (`time.perf_counter`), one sample per measured iteration.
- **Not measured / not claimed:** accuracy, throughput under real production
  load, GPU behavior, or any result on an industrial dataset. This workspace
  has no validated industrial model or dataset; a report produced here proves
  the protocol ran, not industrial capability.

## Controlled variables — required for any comparison

Two reports may only be compared side by side when **every** protocol field
matches. If any value differs, the numbers are not comparable and must be
labeled as separate conditions.

| Variable | Report field | Rule |
|---|---|---|
| Input identity | `protocol.input.sha256` | SHA-256 over the decoded pixel bytes fed to the engine. The runner re-verifies it after the run; a mutating engine fails the benchmark. |
| Input source | `protocol.input.source` | `file` (path recorded) or `synthetic` (seed recorded). Synthetic inputs are labeled synthetic — never presented as industrial data. |
| Warmup | `protocol.warmup_iterations` | Excluded from every statistic. |
| Iterations | `protocol.iterations` | Counted measurements after warmup. |
| Batch size | `protocol.batch_size` | Current adapters are single-frame; `batch_size > 1` is rejected rather than simulated. |
| Resolution | `protocol.input.width/height`, `engine.config.image_size` | Input pixels plus the engine's configured inference size. |
| Model version | `engine.model_version_id`, `engine.model_version`, `engine.artifact_sha256` | Registry identity plus the **server-computed SHA-256 verified at run time**; a checksum mismatch aborts the run. |
| Thresholds/config | `engine.config` | Whatever the registry row carries is recorded verbatim. |
| Hardware/software | `environment` | Platform, Python, CPU model/cores, RAM, plus `measurements` unit. |
| Sample rule | `protocol.min_samples_for_stats` | Statistics are gated by this count. |

## Running a benchmark

```bash
cd backend
# Registered model, deterministic synthetic input (labeled synthetic):
python -m app.benchmark.cli --model-id 1 --iterations 100 --warmup 5 --min-samples 30
# Registered model, real input file:
python -m app.benchmark.cli --model-id 1 --input samples/part.png --out data/runs/benchmarks/part-001.json
```

Exit codes: `0` — completed report written; `2` — benchmark failed (unknown
model, unsafe or checksum-mismatched artifact, engine failure, failed or
invalid inference, bad arguments). **Failed runs write no report file**, so no
consumer can mistake a failure for measurements.

## Reporting rules (enforced in code)

1. `average`, `p50`, `p95`, `p99` are reported **only** when
   `sample_count >= min_samples_for_stats` (default 30). Below that they are
   `null` with an explicit `measurements.stats_withheld_reason`.
2. `sample_count`, `min_ms`, `max_ms`, and the raw `samples_ms` array are
   always present — they are observed facts, not estimates.
3. No number may be copied from another run, averaged across runs, rounded up,
   or filled in "for illustration". A missing value stays `null` with a reason.
4. `write_report()` refuses reports whose status is not `completed` or whose
   stats violate rule 1.
5. Failed runs exit 2 without a report (T21 parity: no metrics from a failed run).

## Report template

```json
{
  "report_version": 1,
  "status": "completed",
  "created_at": "2026-10-24T12:00:00+00:00",
  "protocol": {
    "input": {
      "source": "synthetic",
      "seed": 42,
      "sha256": "<64 hex chars of decoded pixels>",
      "width": 640,
      "height": 640,
      "channels": 3,
      "dtype": "uint8"
    },
    "iterations": 100,
    "warmup_iterations": 5,
    "batch_size": 1,
    "min_samples_for_stats": 30,
    "clock": "time.perf_counter"
  },
  "engine": {
    "model_version_id": 1,
    "model_name": "example-detector",
    "model_version": "1.0.0",
    "framework": "onnx",
    "framework_version": null,
    "task_type": "object_detection",
    "device": "cpu",
    "artifact_reference": "detectors/v1/model.onnx",
    "artifact_id": "sha256:<hash>",
    "artifact_sha256": "<hash verified at run time>",
    "config": {"image_size": 640, "confidence_threshold": 0.25}
  },
  "environment": {
    "platform": "Linux-...",
    "machine": "x86_64",
    "python_version": "3.12.3",
    "python_implementation": "CPython",
    "cpu_model": "...",
    "cpu_count": 8,
    "cpu_cores_physical": 4,
    "total_ram_bytes": 16777216000
  },
  "measurements": {
    "unit": "ms",
    "sample_count": 100,
    "min_ms": 11.9,
    "max_ms": 40.2,
    "samples_ms": ["<one wall-clock ms value per measured iteration>"],
    "stats": {"average_ms": 13.1, "p50_ms": 12.8, "p95_ms": 15.9, "p99_ms": 21.4},
    "stats_withheld_reason": null
  },
  "last_inference": {"detection_count": 2}
}
```

When fewer than `min_samples_for_stats` samples exist, the same template
applies with:

```json
"measurements": {
  "unit": "ms",
  "sample_count": 12,
  "min_ms": 12.1,
  "max_ms": 18.7,
  "samples_ms": ["..."],
  "stats": null,
  "stats_withheld_reason": "fewer than 30 samples (collected 12); average and percentiles are withheld"
}
```

## Comparison template

Fill one row per report; leave cells blank (not zero, not estimated) when a
protocol value is unknown — an incomplete row means "not comparable".

| Report | input sha256 (first 12) | model (id @ artifact sha256, first 12) | iterations / warmup | resolution | hardware | n | avg ms | P50 | P95 | P99 |
|---|---|---|---|---|---|---|---|---|---|---|
| `benchmark-<id>.json` | | | | | | | | | | |

## Known limitations

- This workspace has no real model artifact: the missing-model path is what is
  tested here (clean exit 2, no report). Real artifact timings must come from
  runs against a registered, checksum-verified model on the target hardware.
- psutil/GPU runtimes are absent in this workspace; `environment` reflects the
  machine that ran the benchmark and must be read alongside the numbers.
