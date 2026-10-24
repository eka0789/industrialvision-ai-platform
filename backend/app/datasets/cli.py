"""Command-line dataset validation (T18).

Usage (from ``backend/``)::

    python -m app.datasets.cli DATASET_ROOT --report report.json --num-classes 6

Exit codes: 0 = report written and no errors, 1 = validation errors found,
2 = invalid usage or dataset root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.datasets.validation import DEFAULT_ISSUE_LIMIT, build_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.datasets.cli",
        description="Validate a YOLO-style dataset and emit a machine-readable JSON report.",
    )
    parser.add_argument("root", help="Dataset root containing images/ and labels/")
    parser.add_argument("--report", required=True, help="Path to write the JSON report to")
    parser.add_argument("--num-classes", type=int, default=None, help="Expected number of classes")
    parser.add_argument("--seed", type=int, default=42, help="Split seed (deterministic)")
    parser.add_argument(
        "--ratios",
        default="0.7,0.2,0.1",
        help="Comma-separated split ratios summing to 1 (default: 0.7,0.2,0.1)",
    )
    parser.add_argument(
        "--split-names",
        default=None,
        help="Comma-separated split names matching --ratios (default: train,val,test)",
    )
    parser.add_argument("--no-split", action="store_true", help="Skip split assignment")
    parser.add_argument("--checksums", action="store_true", help="Include SHA-256 checksums per file")
    parser.add_argument(
        "--no-decode-check",
        action="store_true",
        help="Skip pixel-level decode check (signature/size checks still run)",
    )
    parser.add_argument(
        "--issue-limit",
        type=int,
        default=DEFAULT_ISSUE_LIMIT,
        help="Maximum number of issues embedded in the report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        ratios = tuple(float(value) for value in args.ratios.split(",") if value.strip())
        split_names = (
            tuple(value.strip() for value in args.split_names.split(",") if value.strip())
            if args.split_names
            else None
        )
        report = build_report(
            args.root,
            num_classes=args.num_classes,
            seed=args.seed,
            ratios=ratios,
            split_names=split_names,
            perform_split=not args.no_split,
            with_checksums=args.checksums,
            decode_check=not args.no_decode_check,
            issue_limit=args.issue_limit,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    summary = report["summary"]
    print(
        f"validated {summary['images']} images / {summary['labels']} labels: "
        f"{summary['errors']} error(s), {summary['warnings']} warning(s); "
        f"report -> {report_path}"
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
