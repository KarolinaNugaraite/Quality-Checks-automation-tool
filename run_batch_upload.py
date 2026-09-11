#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def collect_json_files(folder: Path) -> list[Path]:
    return sorted([p for p in folder.glob("*.json") if p.is_file()])


def run_batch(
    endpoint: str,
    provider_id: str,
    original_files: list[Path],
    converted_files: list[Path],
) -> tuple[int, dict[str, Any] | None, str]:
    cmd = [
        "curl",
        "-sS",
        "-X",
        "POST",
        endpoint,
        "-F",
        f"provider_id={provider_id}",
    ]

    for file_path in original_files:
        cmd.extend(["-F", f"original_files=@{file_path};type=application/json"])
    for file_path in converted_files:
        cmd.extend(["-F", f"converted_files=@{file_path};type=application/json"])

    proc = subprocess.run(cmd, capture_output=True, text=True)
    raw = (proc.stdout or "").strip()

    if proc.returncode != 0:
        return proc.returncode, None, (proc.stderr or raw or "curl failed")

    try:
        payload = json.loads(raw)
        return 0, payload, ""
    except json.JSONDecodeError:
        return 0, None, raw[:500]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload large original+converted JSON sets in batches to /api/check/unified"
    )
    parser.add_argument("--original-dir", required=True, help="Path to original JSON folder")
    parser.add_argument("--converted-dir", required=True, help="Path to converted JSON folder")
    parser.add_argument("--provider-id", default="viaplay", help="Provider ID for unified endpoint")
    parser.add_argument("--batch-size", type=int, default=500, help="Files per side per request")
    parser.add_argument("--endpoint", default="http://127.0.0.1:5000/api/check/unified", help="Unified endpoint URL")
    parser.add_argument("--output", default="reports/batch_upload_report.json", help="Output report path")
    args = parser.parse_args()

    original_dir = Path(args.original_dir).expanduser().resolve()
    converted_dir = Path(args.converted_dir).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if args.batch_size < 1:
        print("batch-size must be >= 1", file=sys.stderr)
        return 2

    if not original_dir.exists() or not original_dir.is_dir():
        print(f"original-dir not found: {original_dir}", file=sys.stderr)
        return 2
    if not converted_dir.exists() or not converted_dir.is_dir():
        print(f"converted-dir not found: {converted_dir}", file=sys.stderr)
        return 2

    original_files = collect_json_files(original_dir)
    converted_files = collect_json_files(converted_dir)

    if not original_files:
        print(f"No JSON files in original-dir: {original_dir}", file=sys.stderr)
        return 2
    if not converted_files:
        print(f"No JSON files in converted-dir: {converted_dir}", file=sys.stderr)
        return 2

    pair_count = min(len(original_files), len(converted_files))
    if len(original_files) != len(converted_files):
        print(
            "Warning: file counts differ "
            f"(original={len(original_files)}, converted={len(converted_files)}). "
            f"Processing first {pair_count} by sorted order.",
            file=sys.stderr,
        )

    original_files = original_files[:pair_count]
    converted_files = converted_files[:pair_count]

    total_batches = (pair_count + args.batch_size - 1) // args.batch_size
    summary_by_check: Counter[str] = Counter()
    summary_by_severity: Counter[str] = Counter()
    batch_results: list[dict[str, Any]] = []

    total_original_checked = 0
    total_converted_checked = 0
    total_findings = 0

    for batch_idx in range(total_batches):
        start = batch_idx * args.batch_size
        end = min(pair_count, start + args.batch_size)

        o_batch = original_files[start:end]
        c_batch = converted_files[start:end]

        print(f"Batch {batch_idx + 1}/{total_batches}: files {start + 1}-{end}")

        code, payload, err = run_batch(
            endpoint=args.endpoint,
            provider_id=args.provider_id,
            original_files=o_batch,
            converted_files=c_batch,
        )

        if code != 0:
            batch_results.append(
                {
                    "batch": batch_idx + 1,
                    "status": "curl_error",
                    "error": err,
                    "original_files": [str(p) for p in o_batch],
                    "converted_files": [str(p) for p in c_batch],
                }
            )
            continue

        if payload is None:
            batch_results.append(
                {
                    "batch": batch_idx + 1,
                    "status": "invalid_json_response",
                    "error": err,
                    "original_files": [str(p) for p in o_batch],
                    "converted_files": [str(p) for p in c_batch],
                }
            )
            continue

        findings = payload.get("findings", []) if isinstance(payload, dict) else []
        summary = payload.get("summary", {}) if isinstance(payload, dict) else {}

        total_original_checked += int(payload.get("original_records_checked", 0) or 0)
        total_converted_checked += int(payload.get("converted_records_checked", 0) or 0)
        total_findings += int(summary.get("total_findings", 0) or 0)

        for check_id, count in (summary.get("by_check", {}) or {}).items():
            summary_by_check[str(check_id)] += int(count)
        for severity, count in (summary.get("by_severity", {}) or {}).items():
            summary_by_severity[str(severity)] += int(count)

        batch_results.append(
            {
                "batch": batch_idx + 1,
                "status": "ok",
                "original_range": [start + 1, end],
                "original_records_checked": payload.get("original_records_checked", 0),
                "converted_records_checked": payload.get("converted_records_checked", 0),
                "findings_count": len(findings),
                "summary": summary,
            }
        )

    report = {
        "endpoint": args.endpoint,
        "provider_id": args.provider_id,
        "batch_size": args.batch_size,
        "pairs_processed": pair_count,
        "batches": total_batches,
        "totals": {
            "original_records_checked": total_original_checked,
            "converted_records_checked": total_converted_checked,
            "findings": total_findings,
            "by_check": dict(summary_by_check),
            "by_severity": dict(summary_by_severity),
        },
        "batch_results": batch_results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\nDone. Report written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
