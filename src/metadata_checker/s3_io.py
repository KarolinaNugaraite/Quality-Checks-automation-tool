from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .xml_payload import parse_xml_payload_as_record


@dataclass
class S3ObjectRecord:
    key: str
    payload: dict[str, Any]


def _new_s3_client(profile_name: str | None = None):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is not installed. Install requirements before using S3 mode."
        ) from exc

    if profile_name:
        return boto3.Session(profile_name=profile_name).client("s3")
    return boto3.client("s3")


def list_sampled_json_keys(
    bucket: str,
    prefix: str,
    sample_size: int,
    random_seed: int,
    profile_name: str | None = None,
) -> list[str]:
    s3 = _new_s3_client(profile_name=profile_name)
    paginator = s3.get_paginator("list_objects_v2")

    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj.get("Key", "")
            if key.endswith(".json") or key.endswith(".xml"):
                keys.append(key)

    if not keys:
        return []

    sample_size = max(1, min(sample_size, len(keys)))
    rng = random.Random(random_seed)
    return rng.sample(keys, sample_size)


def list_json_keys_by_date_range(
    bucket: str,
    prefix: str,
    start_date: str,
    end_date: str,
    profile_name: str | None = None,
) -> list[str]:
    """
    List JSON/XML keys from S3 that were modified within the specified date range.
    
    Args:
        bucket: S3 bucket name
        prefix: S3 prefix to search
        start_date: Start date (YYYY-MM-DD format)
        end_date: End date (YYYY-MM-DD format)
    
    Returns:
        List of S3 keys matching the criteria
    """
    s3 = _new_s3_client(profile_name=profile_name)
    paginator = s3.get_paginator("list_objects_v2")

    # Parse dates to datetime objects (at start/end of day in UTC)
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj.get("Key", "")
            last_modified = obj.get("LastModified")

            if (not key.endswith(".json") and not key.endswith(".xml")) or not last_modified:
                continue

            # Check if LastModified is within date range
            if start_dt <= last_modified <= end_dt:
                keys.append(key)

    return keys


def read_json_objects(
    bucket: str, keys: list[str], profile_name: str | None = None
) -> list[S3ObjectRecord]:
    if not keys:
        return []

    s3 = _new_s3_client(profile_name=profile_name)
    records: list[S3ObjectRecord] = []

    for key in keys:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        if key.endswith(".json"):
            payload = json.loads(body)
        elif key.endswith(".xml"):
            payload = parse_xml_payload_as_record(body)
        else:
            continue

        if isinstance(payload, list):
            for idx, item in enumerate(payload):
                if isinstance(item, dict):
                    records.append(S3ObjectRecord(key=f"{key}#{idx}", payload=item))
        elif isinstance(payload, dict):
            records.append(S3ObjectRecord(key=key, payload=payload))

    return records

