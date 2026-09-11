from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .checker import MetadataChecker, summarize_findings
from .config import load_config
from .ingestion_test_runner import run_ingestion_test_suite, write_test_report


def _write_report(output_path: str, payload: dict[str, Any]) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_records(
    checker: MetadataChecker,
    mode: str,
    local_path: str | None,
    bucket: str | None,
    prefix: str | None,
    sample_size: int,
    seed: int,
) -> list[tuple[str, dict[str, Any]]]:
    if mode == "local":
        if not local_path:
            raise ValueError("--local-path is required for local mode")
        return checker.load_records_from_local(local_path, sample_size=sample_size, seed=seed)

    if not bucket or not prefix:
        raise ValueError("--bucket and --prefix are required for s3 mode")
    return checker.load_records_from_s3(bucket=bucket, prefix=prefix, sample_size=sample_size, seed=seed)


def run_original(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    checker = MetadataChecker(cfg)

    records = _load_records(
        checker,
        mode=args.mode,
        local_path=args.local_path,
        bucket=args.bucket,
        prefix=args.prefix,
        sample_size=args.sample_size or cfg["sample_size"],
        seed=args.seed or cfg["random_seed"],
    )

    findings = checker.run_original_checks(records)
    report = {
        "metadata_type": "original",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records_checked": len(records),
        "summary": summarize_findings(findings),
        "findings": findings,
    }
    _write_report(args.output, report)
    print(f"Original checks complete. Findings: {len(findings)}. Report: {args.output}")
    if report["summary"].get("has_errors"):
        raise SystemExit(1)


def run_converted(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    checker = MetadataChecker(cfg)

    original_records = _load_records(
        checker,
        mode=args.mode,
        local_path=args.original_local_path,
        bucket=args.bucket,
        prefix=args.original_prefix,
        sample_size=args.sample_size or cfg["sample_size"],
        seed=args.seed or cfg["random_seed"],
    )

    converted_records = _load_records(
        checker,
        mode=args.mode,
        local_path=args.converted_local_path,
        bucket=args.bucket,
        prefix=args.converted_prefix,
        sample_size=args.sample_size or cfg["sample_size"],
        # Use the same seed as originals so sampled pairs are comparable.
        seed=args.seed or cfg["random_seed"],
    )

    findings = checker.run_converted_checks(original_records=original_records, converted_records=converted_records)
    report = {
        "metadata_type": "converted",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "original_records_checked": len(original_records),
        "converted_records_checked": len(converted_records),
        "summary": summarize_findings(findings),
        "findings": findings,
    }
    _write_report(args.output, report)
    print(f"Converted checks complete. Findings: {len(findings)}. Report: {args.output}")
    if report["summary"].get("has_errors"):
        raise SystemExit(1)


def run_ingestion_tests(args: argparse.Namespace) -> None:
    payload = run_ingestion_test_suite(
        tests_config_path=args.tests_config,
        original_local_path=args.original_local_path,
        converted_local_path=args.converted_local_path,
        baseline_converted_local_path=args.baseline_converted_local_path,
    )
    write_test_report(args.output, payload)

    summary = payload.get("summary", {})
    print(
        "Ingestion tests complete. "
        f"passed={summary.get('passed', 0)} "
        f"failed={summary.get('failed', 0)} "
        f"skipped={summary.get('skipped', 0)}. "
        f"Report: {args.output}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Original/Converted metadata checker")
    parser.add_argument("--config", default="configs/rules.example.yaml", help="Path to config YAML")

    subparsers = parser.add_subparsers(dest="command", required=True)

    original = subparsers.add_parser("run-original", help="Run original metadata checks")
    original.add_argument("--mode", choices=["local", "s3"], default="local")
    original.add_argument("--local-path", help="Local folder with original JSON files")
    original.add_argument("--bucket", help="S3 bucket name")
    original.add_argument("--prefix", help="S3 prefix for original metadata")
    original.add_argument("--sample-size", type=int, help="How many JSON files to sample")
    original.add_argument("--seed", type=int, help="Random seed")
    original.add_argument("--output", default="reports/original_report.json")
    original.set_defaults(func=run_original)

    converted = subparsers.add_parser("run-converted", help="Run converted metadata checks")
    converted.add_argument("--mode", choices=["local", "s3"], default="local")
    converted.add_argument("--original-local-path", help="Local folder with original JSON files")
    converted.add_argument("--converted-local-path", help="Local folder with converted JSON files")
    converted.add_argument("--bucket", help="S3 bucket name")
    converted.add_argument("--original-prefix", help="S3 prefix for original metadata")
    converted.add_argument("--converted-prefix", help="S3 prefix for converted metadata")
    converted.add_argument("--sample-size", type=int, help="How many JSON files to sample")
    converted.add_argument("--seed", type=int, help="Random seed")
    converted.add_argument("--output", default="reports/converted_report.json")
    converted.set_defaults(func=run_converted)

    ingestion_tests = subparsers.add_parser(
        "run-ingestion-tests",
        help="Run YAML-defined ingestion assertions across many local metadata files",
    )
    ingestion_tests.add_argument(
        "--tests-config",
        default="configs/providers/nrk-tv/nrk-tv.repeating_ingestion.tests.yaml",
        help="Path to ingestion tests YAML",
    )
    ingestion_tests.add_argument(
        "--original-local-path",
        required=True,
        help="Local file/folder with original metadata JSON",
    )
    ingestion_tests.add_argument(
        "--converted-local-path",
        required=True,
        help="Local file/folder with converted metadata JSON",
    )
    ingestion_tests.add_argument(
        "--baseline-converted-local-path",
        help="Optional baseline converted dataset path for regression assertions",
    )
    ingestion_tests.add_argument(
        "--output",
        default="reports/nrk_repeating_ingestion_test_report.json",
        help="Output JSON report path",
    )
    ingestion_tests.set_defaults(func=run_ingestion_tests)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
