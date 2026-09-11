#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests

TOKEN_URL = "https://sso.tvm.telia.com/realms/tvm/protocol/openid-connect/token"
ADMINPORTAL_BASE = "https://adminportal-pilot.tvm.telia.com/adminportal/rest/inspectorgadgetservice/rest"
DEFAULT_COUNTRIES = ["SE", "NO", "FI", "DK"]


def exchange_refresh_token(refresh_token: str) -> str:
    payload = {
        "grant_type": "refresh_token",
        "client_id": "inspector-gadget",
        "refresh_token": refresh_token,
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=25)
    response.raise_for_status()
    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("Token exchange did not return access_token")
    return str(access_token)


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def fetch_channel_configs(access_token: str, country: str) -> list[dict[str, Any]]:
    url = f"{ADMINPORTAL_BASE}/v1/config/channels/{country}"
    response = requests.get(url, headers=auth_headers(access_token), timeout=30)
    if response.status_code == 401:
        response.raise_for_status()
    if 400 <= response.status_code < 500:
        return []
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else payload

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        out: list[dict[str, Any]] = []
        for key, value in data.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("id", key)
                out.append(item)
        return out
    return []


def fetch_channels_listing(access_token: str, country: str, page_size: int = 500) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 0
    while True:
        url = f"{ADMINPORTAL_BASE}/v1/channels"
        params = {"country": country, "size": page_size, "page": page}
        response = requests.get(url, headers=auth_headers(access_token), params=params, timeout=30)
        if response.status_code == 401:
            response.raise_for_status()
        if 400 <= response.status_code < 500:
            break
        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, list):
            items = [x for x in payload if isinstance(x, dict)]
        elif isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                items = [x for x in data if isinstance(x, dict)]
            elif isinstance(data, dict):
                values = data.get("items") or data.get("content") or []
                items = [x for x in values if isinstance(x, dict)] if isinstance(values, list) else []
            else:
                items = []
        else:
            items = []

        if not items:
            break

        out.extend(items)
        if len(items) < page_size:
            break
        page += 1

    return out


def _first_present(mapping: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def extract_provider_keys(record: dict[str, Any]) -> list[str]:
    candidates: list[str] = []

    provider_keys = [
        "providerId",
        "provider",
        "channelProvider",
        "adapterId",
        "adapter",
        "sourceAdapterId",
        "owner",
        "source",
    ]

    direct = _first_present(record, provider_keys)
    if isinstance(direct, str):
        candidates.append(direct)
    elif isinstance(direct, list):
        candidates.extend(str(x) for x in direct if x)

    for nested_key in ["configuration", "config", "metadata"]:
        nested = record.get(nested_key)
        if isinstance(nested, dict):
            nested_direct = _first_present(nested, provider_keys)
            if isinstance(nested_direct, str):
                candidates.append(nested_direct)
            elif isinstance(nested_direct, list):
                candidates.extend(str(x) for x in nested_direct if x)

    return [c.strip().lower() for c in candidates if isinstance(c, str) and c.strip()]


def extract_channel_id(record: dict[str, Any]) -> str | None:
    channel = _first_present(record, ["id", "channelId", "externalName", "nodeName", "name"])
    if channel and isinstance(channel, str) and channel.strip():
        return channel.strip()

    for nested_key in ["configuration", "config"]:
        nested = record.get(nested_key)
        if isinstance(nested, dict):
            channel_nested = _first_present(nested, ["id", "channelId", "externalName", "nodeName", "name"])
            if channel_nested and isinstance(channel_nested, str) and channel_nested.strip():
                return channel_nested.strip()

    return None


def slugify_channel_key(channel_id: str) -> str:
    key = channel_id.strip().lower()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    key = key.strip("-")
    return key or "channel"


def merge_with_s3_config(
    s3_config_path: Path,
    provider_channels: dict[str, list[str]],
) -> dict[str, Any]:
    config = json.loads(s3_config_path.read_text())

    for provider_id, channels in provider_channels.items():
        if provider_id not in config:
            continue

        provider_cfg = config.get(provider_id)
        if not isinstance(provider_cfg, dict):
            continue

        base_bucket = str(provider_cfg.get("bucket", ""))
        base_original = str(provider_cfg.get("original_prefix", "")).rstrip("/")
        base_converted = str(provider_cfg.get("converted_prefix", "")).rstrip("/")

        by_channel: dict[str, Any] = {}
        for channel_id in channels:
            key = slugify_channel_key(channel_id)
            if key in by_channel:
                suffix = 2
                while f"{key}-{suffix}" in by_channel:
                    suffix += 1
                key = f"{key}-{suffix}"

            by_channel[key] = {
                "label": channel_id,
                "bucket": base_bucket,
                "original_prefix": f"{base_original}/{key}/" if base_original else "",
                "converted_prefix": f"{base_converted}/{key}/" if base_converted else "",
            }

        provider_cfg["by_channel"] = by_channel

    return config


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract provider->channels from pilot adminportal and merge into providers_s3_config.json"
    )
    parser.add_argument(
        "--refresh-token",
        default=os.getenv("KEYCLOAK_REFRESH_TOKEN", ""),
        help="Keycloak refresh token (or set KEYCLOAK_REFRESH_TOKEN env var)",
    )
    parser.add_argument(
        "--providers-list",
        default="src/metadata_checker/static/providers_list.json",
        help="Path to providers_list.json",
    )
    parser.add_argument(
        "--s3-config",
        default="src/metadata_checker/static/providers_s3_config.json",
        help="Path to providers_s3_config.json",
    )
    parser.add_argument(
        "--out-json",
        default="reports/pilot_provider_channels.json",
        help="Where to save raw provider->channels mapping",
    )
    parser.add_argument(
        "--countries",
        nargs="*",
        default=DEFAULT_COUNTRIES,
        help="Country codes to scan (default: SE NO FI DK)",
    )
    args = parser.parse_args()

    refresh_token = args.refresh_token.strip()
    if not refresh_token:
        raise SystemExit(
            "Missing refresh token. Pass --refresh-token or set KEYCLOAK_REFRESH_TOKEN in your shell."
        )

    providers = json.loads(Path(args.providers_list).read_text())
    provider_ids = {
        str(item.get("providerId", "")).strip().lower()
        for item in providers
        if isinstance(item, dict) and item.get("providerId")
    }

    access_token = exchange_refresh_token(refresh_token)

    provider_to_channels: dict[str, set[str]] = defaultdict(set)

    skipped_countries: list[str] = []

    for country in [c.upper() for c in args.countries if str(c).strip()]:
        config_records = fetch_channel_configs(access_token, country)
        listing_records = fetch_channels_listing(access_token, country)

        if not config_records and not listing_records:
            skipped_countries.append(country)

        for record in [*config_records, *listing_records]:
            channel_id = extract_channel_id(record)
            if not channel_id:
                continue
            provider_keys = extract_provider_keys(record)
            for provider_key in provider_keys:
                if provider_key in provider_ids:
                    provider_to_channels[provider_key].add(channel_id)

    raw_out = {k: sorted(v) for k, v in sorted(provider_to_channels.items())}
    out_json_path = Path(args.out_json)
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.write_text(json.dumps(raw_out, indent=2))

    merged = merge_with_s3_config(Path(args.s3_config), raw_out)
    Path(args.s3_config).write_text(json.dumps(merged, indent=2) + "\n")

    print(f"Providers with channels: {len(raw_out)}")
    for provider_id, channels in raw_out.items():
        print(f"- {provider_id}: {len(channels)}")
    if skipped_countries:
        print("Skipped countries with no usable channel data:", ", ".join(skipped_countries))
    print(f"Wrote raw mapping to: {out_json_path}")
    print(f"Updated: {args.s3_config}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
