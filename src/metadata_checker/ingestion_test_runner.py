from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AssertionResult:
    assertion_id: str
    status: str
    message: str
    details: dict[str, Any] | None = None


@dataclass
class TestCaseResult:
    test_case_id: str
    test_name: str
    status: str
    assertions: list[AssertionResult]


def _read_yaml(path: str) -> dict[str, Any]:
    cfg_file = Path(path)
    if not cfg_file.exists():
        raise FileNotFoundError(f"Tests config file not found: {cfg_file}")

    with cfg_file.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError("Tests config must parse to a dictionary-like object")
    return data


def _read_json_records(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Path does not exist: {p}")

    json_files: list[Path]
    if p.is_file():
        json_files = [p]
    else:
        json_files = sorted([f for f in p.rglob("*.json") if f.is_file()])

    records: list[dict[str, Any]] = []
    for file_path in json_files:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if isinstance(payload, list):
            for idx, item in enumerate(payload):
                if isinstance(item, dict):
                    row = dict(item)
                    row["__file_ref"] = f"{file_path.name}#{idx}"
                    records.append(row)
        elif isinstance(payload, dict):
            row = dict(payload)
            row["__file_ref"] = file_path.name
            records.append(row)

    return records


def _parse_path_tokens(path: str) -> list[str]:
    if not path:
        return []

    out: list[str] = []
    for part in path.split("."):
        if not part:
            continue
        while True:
            m = re.match(r"^([^\[]+)(\[\*\]|\[\d+\])?(.*)$", part)
            if not m:
                break
            key, bracket, rest = m.group(1), m.group(2), m.group(3)
            if key:
                out.append(key)
            if bracket:
                out.append(bracket)
            if not rest:
                break
            part = rest
    return out


def _get_values(obj: Any, dotted_path: str) -> list[Any]:
    tokens = _parse_path_tokens(dotted_path)
    if not tokens:
        return [obj]

    current = [obj]
    for token in tokens:
        nxt: list[Any] = []

        if token == "[*]":
            for item in current:
                if isinstance(item, list):
                    nxt.extend(item)
            current = nxt
            continue

        if token.startswith("[") and token.endswith("]") and token[1:-1].isdigit():
            idx = int(token[1:-1])
            for item in current:
                if isinstance(item, list) and 0 <= idx < len(item):
                    nxt.append(item[idx])
            current = nxt
            continue

        for item in current:
            if isinstance(item, dict) and token in item:
                nxt.append(item[token])
        current = nxt

        if not current:
            return []

    return current


def _get_first(obj: Any, path: str) -> Any:
    vals = _get_values(obj, path)
    return vals[0] if vals else None


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def _to_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _iso8601_duration_to_seconds(value: Any) -> int | None:
    if not isinstance(value, str):
        return None

    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", value.strip())
    if not m:
        return None

    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s


def _ratio_from_dimensions(width: Any, height: Any) -> str | None:
    w = _to_int(width)
    h = _to_int(height)
    if w is None or h is None or w <= 0 or h <= 0:
        return None

    def _gcd(a: int, b: int) -> int:
        while b:
            a, b = b, a % b
        return a

    g = _gcd(w, h)
    return f"{w // g}:{h // g}"


def _render_template(template: str, context: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        val = _get_first(context, key)
        return "" if val is None else str(val)

    return re.sub(r"\{([^{}]+)\}", repl, template)


def _resolve_expected(assertion: dict[str, Any], context: dict[str, Any]) -> Any:
    if "expected_value_from_input" in assertion:
        return _get_first(context, assertion["expected_value_from_input"])
    if "expected_value_template" in assertion:
        return _render_template(assertion["expected_value_template"], context)
    return assertion.get("expected_value")


def _find_target_record(
    test_case: dict[str, Any],
    converted_records: list[dict[str, Any]],
    converted_id_index: dict[str, list[dict[str, Any]]],
    converted_original_index: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    original = (test_case.get("input") or {}).get("original") or {}
    if not isinstance(original, dict):
        original = {}

    program_id = original.get("program_id")
    if isinstance(program_id, str) and program_id.strip():
        by_orig = converted_original_index.get(program_id.strip(), [])
        if by_orig:
            return by_orig[0]

        expected_content_id = f"nrk-tv.content.{program_id.strip()}"
        by_id = converted_id_index.get(expected_content_id, [])
        if by_id:
            return by_id[0]

    # Do not fall back to an unrelated record; this hides matching problems.
    return None


def _run_assertion(
    assertion: dict[str, Any],
    context: dict[str, Any],
    converted_records: list[dict[str, Any]],
    baseline_converted_records: list[dict[str, Any]] | None,
) -> AssertionResult:
    assertion_id = str(assertion.get("assertion_id") or "unnamed_assertion")
    assertion_type = str(assertion.get("assertion_type") or "")
    operator = str(assertion.get("operator") or "")

    if assertion_type == "field":
        path = str(assertion.get("field_path") or "")
        values = _get_values(context, path)
        value = values[0] if values else None

        if operator == "exists":
            ok = any(_is_non_empty(v) for v in values)
            return AssertionResult(assertion_id, "passed" if ok else "failed", "exists check")

        expected = _resolve_expected(assertion, context)

        if operator == "equals":
            ok = value == expected
            return AssertionResult(
                assertion_id,
                "passed" if ok else "failed",
                "equals check",
                {"actual": value, "expected": expected},
            )

        if operator == "contains":
            if isinstance(value, str):
                ok = str(expected) in value
            elif isinstance(value, list):
                ok = expected in value
            else:
                ok = False
            return AssertionResult(
                assertion_id,
                "passed" if ok else "failed",
                "contains check",
                {"actual": value, "expected_contains": expected},
            )

        if operator == "object_has_keys":
            keys = assertion.get("expected_value") or []
            ok = isinstance(value, dict) and all(k in value for k in keys)
            return AssertionResult(
                assertion_id,
                "passed" if ok else "failed",
                "object keys check",
                {"actual_keys": sorted(value.keys()) if isinstance(value, dict) else None, "expected_keys": keys},
            )

    if assertion_type == "collection":
        path = str(assertion.get("field_path") or "")
        values = _get_values(context, path)
        expected = _resolve_expected(assertion, context)
        if operator == "contains":
            ok = expected in values
            return AssertionResult(
                assertion_id,
                "passed" if ok else "failed",
                "collection contains check",
                {"expected": expected, "sample_values": values[:10]},
            )

    if assertion_type == "transform":
        transform = str(assertion.get("transform") or "")
        target_path = str(assertion.get("target_field_path") or "")
        actual = _get_first(context, target_path)

        expected: Any = None
        if transform == "iso8601_duration_to_seconds":
            source_path = str(assertion.get("source_field_path") or "")
            source_value = _get_first(context, source_path)
            expected = _iso8601_duration_to_seconds(source_value)
        elif transform == "width_height_to_aspect_ratio":
            src_paths = assertion.get("source_field_paths") or []
            width = _get_first(context, src_paths[0]) if len(src_paths) > 0 else None
            height = _get_first(context, src_paths[1]) if len(src_paths) > 1 else None
            expected = _ratio_from_dimensions(width, height)

        if operator == "equals_transformed_value":
            ok = actual == expected
            return AssertionResult(
                assertion_id,
                "passed" if ok else "failed",
                "transform equality check",
                {"actual": actual, "expected": expected},
            )

    if assertion_type == "aggregate":
        if operator == "unique":
            path = str(assertion.get("field_path") or "")
            values = [v for rec in converted_records for v in _get_values({"converted": rec}, path)]
            non_empty = [v for v in values if _is_non_empty(v)]
            ok = len(non_empty) == len(set(map(str, non_empty)))
            duplicates = sorted({str(v) for v in non_empty if list(map(str, non_empty)).count(str(v)) > 1})
            return AssertionResult(
                assertion_id,
                "passed" if ok else "failed",
                "uniqueness check",
                {"duplicates": duplicates[:20]},
            )

        if operator == "no_conflicting_key_set":
            key_fields = assertion.get("key_fields") or []
            tuples: list[tuple[Any, ...]] = []
            for rec in converted_records:
                ctx = {"converted": rec}
                key_tuple = tuple(_get_first(ctx, k) for k in key_fields)
                tuples.append(key_tuple)
            ok = len(tuples) == len(set(map(str, tuples)))
            return AssertionResult(assertion_id, "passed" if ok else "failed", "conflicting key set check")

        if operator == "equals":
            path = str(assertion.get("field_path") or "")
            expected = assertion.get("expected_value")
            actual = _get_first(context, path)

            if path.startswith("regression.") and actual is None:
                return AssertionResult(
                    assertion_id,
                    "skipped",
                    "regression baseline missing; aggregate comparison skipped",
                )

            ok = actual == expected
            return AssertionResult(
                assertion_id,
                "passed" if ok else "failed",
                "aggregate equals check",
                {"actual": actual, "expected": expected},
            )

    if assertion_type == "regression" and operator == "unchanged_fields":
        if baseline_converted_records is None:
            return AssertionResult(
                assertion_id,
                "skipped",
                "baseline dataset missing; provide --baseline-converted-local-path to run regression checks",
            )

        field_paths = assertion.get("field_paths") or []

        def key(rec: dict[str, Any]) -> str:
            return str(rec.get("content_id") or rec.get("contentId") or rec.get("original_content_id") or rec.get("originalContentId") or "")

        current_index = {key(rec): rec for rec in converted_records if key(rec)}
        baseline_index = {key(rec): rec for rec in baseline_converted_records if key(rec)}

        changed = 0
        for k, cur in current_index.items():
            base = baseline_index.get(k)
            if not base:
                continue
            for fp in field_paths:
                cur_val = _get_first({"converted": cur}, f"converted.{fp}")
                base_val = _get_first({"converted": base}, f"converted.{fp}")
                if cur_val != base_val:
                    changed += 1
                    break

        context.setdefault("regression", {})["changed_record_count"] = changed
        ok = changed == 0
        return AssertionResult(
            assertion_id,
            "passed" if ok else "failed",
            "regression unchanged fields check",
            {"changed_record_count": changed},
        )

    return AssertionResult(assertion_id, "failed", f"unsupported assertion type/operator: {assertion_type}/{operator}")


def run_ingestion_test_suite(
    tests_config_path: str,
    original_local_path: str,
    converted_local_path: str,
    baseline_converted_local_path: str | None = None,
) -> dict[str, Any]:
    original_records = _read_json_records(original_local_path)
    converted_records = _read_json_records(converted_local_path)
    baseline_converted_records = (
        _read_json_records(baseline_converted_local_path) if baseline_converted_local_path else None
    )

    return run_ingestion_test_suite_from_records(
        tests_config_path=tests_config_path,
        original_records=original_records,
        converted_records=converted_records,
        baseline_converted_records=baseline_converted_records,
        inputs_meta={
            "original_local_path": original_local_path,
            "converted_local_path": converted_local_path,
            "baseline_converted_local_path": baseline_converted_local_path,
        },
    )


def run_ingestion_test_suite_from_records(
    tests_config_path: str,
    original_records: list[dict[str, Any]],
    converted_records: list[dict[str, Any]],
    baseline_converted_records: list[dict[str, Any]] | None = None,
    inputs_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    suite = _read_yaml(tests_config_path)

    tests = suite.get("tests") or []
    if not isinstance(tests, list):
        raise ValueError("tests must be a list")

    converted_original_index: dict[str, list[dict[str, Any]]] = {}
    converted_id_index: dict[str, list[dict[str, Any]]] = {}
    for rec in converted_records:
        original_id = rec.get("original_content_id") or rec.get("originalContentId")
        content_id = rec.get("content_id") or rec.get("contentId")
        if isinstance(original_id, str) and original_id.strip():
            converted_original_index.setdefault(original_id.strip(), []).append(rec)
        if isinstance(content_id, str) and content_id.strip():
            converted_id_index.setdefault(content_id.strip(), []).append(rec)

    test_results: list[TestCaseResult] = []

    for test in tests:
        if not isinstance(test, dict):
            continue

        test_case_id = str(test.get("test_case_id") or "unknown_test_case")
        test_name = str(test.get("test_name") or "unknown_test_name")
        assertions = ((test.get("validation_rules") or {}).get("assertions") or [])

        target_converted = _find_target_record(
            test, converted_records, converted_id_index, converted_original_index
        )

        if target_converted is None:
            # Fail loudly when the intended converted record is missing.
            test_results.append(
                TestCaseResult(
                    test_case_id=test_case_id,
                    test_name=test_name,
                    status="failed",
                    assertions=[
                        AssertionResult(
                            assertion_id="target_record_match",
                            status="failed",
                            message="No matching converted record found for test input.",
                        )
                    ],
                )
            )
            continue

        context: dict[str, Any] = {
            "original": (test.get("input") or {}).get("original") or {},
            "converted": target_converted or {},
            "regression": {},
        }

        # Apply dataset filters when present.
        dataset_filter = ((test.get("validation_rules") or {}).get("dataset_filter") or {})
        filtered_converted = converted_records
        if isinstance(dataset_filter, dict) and dataset_filter:
            provider_id = dataset_filter.get("provider_id")
            source_type = dataset_filter.get("source_type")
            not_asset_type = dataset_filter.get("asset_type_not_equals")

            tmp: list[dict[str, Any]] = []
            for rec in converted_records:
                if provider_id:
                    rec_provider = rec.get("provider_id") or rec.get("providerId") or rec.get("adapterId")
                    if rec_provider != provider_id:
                        continue
                if source_type:
                    rec_source = rec.get("source_type") or rec.get("sourceType")
                    if rec_source != source_type:
                        continue
                if not_asset_type:
                    rec_asset_type = rec.get("asset_type") or rec.get("assetType") or rec.get("@type")
                    if rec_asset_type == not_asset_type:
                        continue
                tmp.append(rec)
            filtered_converted = tmp

        assertion_results: list[AssertionResult] = []
        for assertion in assertions:
            if not isinstance(assertion, dict):
                continue
            ar = _run_assertion(
                assertion=assertion,
                context=context,
                converted_records=filtered_converted,
                baseline_converted_records=baseline_converted_records,
            )
            assertion_results.append(ar)

        statuses = {a.status for a in assertion_results}
        if "failed" in statuses:
            status = "failed"
        elif "passed" in statuses:
            status = "passed"
        else:
            status = "skipped"

        test_results.append(
            TestCaseResult(
                test_case_id=test_case_id,
                test_name=test_name,
                status=status,
                assertions=assertion_results,
            )
        )

    total = len(test_results)
    passed = sum(1 for t in test_results if t.status == "passed")
    failed = sum(1 for t in test_results if t.status == "failed")
    skipped = sum(1 for t in test_results if t.status == "skipped")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": {
            "suite_id": ((suite.get("test_suite") or {}).get("suite_id") or "unknown_suite"),
            "provider_id": ((suite.get("test_suite") or {}).get("provider_id") or "unknown_provider"),
        },
        "inputs": {
            "tests_config_path": tests_config_path,
            "original_local_path": (inputs_meta or {}).get("original_local_path"),
            "converted_local_path": (inputs_meta or {}).get("converted_local_path"),
            "baseline_converted_local_path": (inputs_meta or {}).get("baseline_converted_local_path"),
            "original_records_loaded": len(original_records),
            "converted_records_loaded": len(converted_records),
            "baseline_converted_records_loaded": len(baseline_converted_records or []),
        },
        "summary": {
            "total_test_cases": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "test_results": [
            {
                "test_case_id": t.test_case_id,
                "test_name": t.test_name,
                "status": t.status,
                "assertions": [
                    {
                        "assertion_id": a.assertion_id,
                        "status": a.status,
                        "message": a.message,
                        "details": a.details,
                    }
                    for a in t.assertions
                ],
            }
            for t in test_results
        ],
    }


def write_test_report(output_path: str, payload: dict[str, Any]) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
