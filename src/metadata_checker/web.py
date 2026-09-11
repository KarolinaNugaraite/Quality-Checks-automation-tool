from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import threading
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import shutil

import requests
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font
from io import BytesIO

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, make_response, send_file

from .checker import CHECK_ID_TO_ERROR_CODE, MetadataChecker, summarize_findings
from .config import load_config
from .xml_payload import parse_xml_payload_as_record
from .grouping_check import (
    parse_episode_files,
    parse_episode_files_with_debug,
    GroupingApiClient,
    build_report,
)
from .ingestion_test_runner import run_ingestion_test_suite_from_records


ALLOW_UNMAPPED_PROVIDER_FALLBACK = os.getenv(
    "ALLOW_UNMAPPED_PROVIDER_FALLBACK", "false"
).lower() in {"1", "true", "yes", "on"}
MAX_CONTENT_LENGTH_MB = int(os.getenv("MAX_CONTENT_LENGTH_MB", "300"))
GROUPING_ACCESS_TOKEN_CACHE_TTL_SECONDS = int(
    os.getenv("GROUPING_ACCESS_TOKEN_CACHE_TTL_SECONDS", "180")
)
GROUPING_REFRESH_EXCHANGE_LOCK_TIMEOUT_SECONDS = int(
    os.getenv("GROUPING_REFRESH_EXCHANGE_LOCK_TIMEOUT_SECONDS", "20")
)

logger = logging.getLogger(__name__)

# Path to save uploaded files
UPLOAD_FOLDER = Path(__file__).parent.parent.parent / "sample_data"
SAVE_UPLOADED_FILES = os.getenv("SAVE_UPLOADED_FILES", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_GROUPING_ACCESS_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_GROUPING_ACCESS_TOKEN_CACHE_LOCK = threading.Lock()
_GROUPING_REFRESH_TOKEN_LOCKS: dict[str, threading.Lock] = {}
_PROVIDER_CHANNELS_CACHE_TTL_SECONDS = int(
    os.getenv("PROVIDER_CHANNELS_CACHE_TTL_SECONDS", "300")
)
_PROVIDER_CHANNELS_CACHE: dict[tuple[str, str, str, str, str], tuple[list[str], float, str]] = {}
_PROVIDER_CHANNELS_CACHE_LOCK = threading.Lock()
PILOT_IG_API_BASE = os.getenv(
    "PILOT_IG_API_BASE", "https://inspector-gadget-api.pilot.tvm.telia.com"
).rstrip("/")
ADMINPORTAL_PILOT_BASE = os.getenv(
    "ADMINPORTAL_PILOT_BASE",
    "https://adminportal-pilot.tvm.telia.com/adminportal/rest/inspectorgadgetservice/rest",
).rstrip("/")
PROVIDER_CHANNELS_PAGE_SIZE = max(
    100, int(os.getenv("PROVIDER_CHANNELS_PAGE_SIZE", "500"))
)
PROVIDER_CHANNELS_LOGS_MAX_PAGES = max(
    1, int(os.getenv("PROVIDER_CHANNELS_LOGS_MAX_PAGES", "3"))
)
PROVIDER_CHANNELS_ALERTS_MAX_PAGES = max(
    1, int(os.getenv("PROVIDER_CHANNELS_ALERTS_MAX_PAGES", "3"))
)


def _looks_like_expired_sso_profile_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "error when retrieving token from sso" in text
        or "token has expired and refresh failed" in text
        or "sso token" in text and "expired" in text
    )


def _grouping_token_cache_key(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def _grouping_get_cached_access_token(refresh_token: str) -> str | None:
    key = _grouping_token_cache_key(refresh_token)
    now = time.time()
    with _GROUPING_ACCESS_TOKEN_CACHE_LOCK:
        cached = _GROUPING_ACCESS_TOKEN_CACHE.get(key)
        if not cached:
            return None
        token, expires_at = cached
        if now >= expires_at:
            _GROUPING_ACCESS_TOKEN_CACHE.pop(key, None)
            return None
        return token


def _grouping_set_cached_access_token(refresh_token: str, access_token: str) -> None:
    key = _grouping_token_cache_key(refresh_token)
    expires_at = time.time() + max(30, GROUPING_ACCESS_TOKEN_CACHE_TTL_SECONDS)
    with _GROUPING_ACCESS_TOKEN_CACHE_LOCK:
        _GROUPING_ACCESS_TOKEN_CACHE[key] = (access_token, expires_at)


def _grouping_get_refresh_exchange_lock(refresh_token: str) -> threading.Lock:
    key = _grouping_token_cache_key(refresh_token)
    with _GROUPING_ACCESS_TOKEN_CACHE_LOCK:
        lock = _GROUPING_REFRESH_TOKEN_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _GROUPING_REFRESH_TOKEN_LOCKS[key] = lock
        return lock


def _grouping_get_or_exchange_access_token(refresh_token: str) -> str:
    cached = _grouping_get_cached_access_token(refresh_token)
    if cached:
        return cached

    token_lock = _grouping_get_refresh_exchange_lock(refresh_token)
    acquired = token_lock.acquire(timeout=GROUPING_REFRESH_EXCHANGE_LOCK_TIMEOUT_SECONDS)
    if not acquired:
        raise TimeoutError(
            "Timed out waiting for another grouping token exchange to finish."
        )

    try:
        # Double-check cache after waiting for the single-flight lock.
        cached_after_wait = _grouping_get_cached_access_token(refresh_token)
        if cached_after_wait:
            return cached_after_wait

        client = GroupingApiClient(refresh_token=refresh_token)
        access_token = client.exchange_token()
        _grouping_set_cached_access_token(refresh_token, access_token)
        return access_token
    finally:
        token_lock.release()


def _extract_channel_names_from_payload(payload: Any) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            data = data.get("items")
        elif isinstance(data.get("content"), list):
            data = data.get("content")

    if not isinstance(data, list):
        return []

    names: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw = (
            item.get("externalName")
            or item.get("channelId")
            or item.get("id")
            or item.get("nodeName")
            or item.get("name")
        )
        if isinstance(raw, str) and raw.strip():
            names.append(raw.strip())
    return names


def _extract_provider_candidates(record: dict[str, Any]) -> set[str]:
    candidates: set[str] = set()
    keys = [
        "providerId",
        "providerIds",
        "provider",
        "channelProvider",
        "adapterId",
        "adapterIds",
        "adapter",
        "adapters",
        "sourceAdapterId",
        "sourceAdapterIds",
        "owner",
        "source",
        "sources",
    ]

    def _collect(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            candidates.add(normalize_provider_id(value))
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    candidates.add(normalize_provider_id(item))
                elif isinstance(item, dict):
                    for dict_key in ("id", "name", "adapterId", "providerId", "value"):
                        nested_value = item.get(dict_key)
                        if isinstance(nested_value, str) and nested_value.strip():
                            candidates.add(normalize_provider_id(nested_value))
        if isinstance(value, dict):
            for dict_key in ("id", "name", "adapterId", "providerId", "value"):
                nested_value = value.get(dict_key)
                if isinstance(nested_value, str) and nested_value.strip():
                    candidates.add(normalize_provider_id(nested_value))

    for key in keys:
        _collect(record.get(key))

    for nested_key in ("configuration", "config", "metadata", "pipeline", "channel"):
        nested = record.get(nested_key)
        if isinstance(nested, dict):
            for key in keys:
                _collect(nested.get(key))

    return candidates


def _extract_provider_candidates_deep(record: Any, max_depth: int = 8) -> set[str]:
    """Best-effort recursive provider extraction for adminportal payload variants."""
    out: set[str] = set()
    providerish = {"provider", "adapter", "source", "owner", "pipeline"}

    def _visit(node: Any, depth: int, parent_key: str = "") -> None:
        if depth > max_depth:
            return
        if isinstance(node, str):
            text = node.strip()
            if not text:
                return
            compact_parent = re.sub(r"[^a-z]", "", parent_key.lower())
            if any(token in compact_parent for token in providerish):
                out.add(normalize_provider_id(text))
            return

        if isinstance(node, list):
            for item in node:
                _visit(item, depth + 1, parent_key)
            return

        if isinstance(node, dict):
            for key, value in node.items():
                key_text = str(key)
                _visit(value, depth + 1, key_text)
                # Frequent shape: [{id/name/value}] under adapters/providers arrays.
                if isinstance(value, dict):
                    for nested_key in ("id", "name", "value", "providerId", "adapterId"):
                        nested_value = value.get(nested_key)
                        if isinstance(nested_value, str) and nested_value.strip():
                            compact_key = re.sub(r"[^a-z]", "", key_text.lower())
                            if any(token in compact_key for token in providerish):
                                out.add(normalize_provider_id(nested_value))

    _visit(record, 0)
    return {candidate for candidate in out if candidate}


def _extract_channel_id(record: dict[str, Any]) -> str | None:
    for key in ("externalName", "channelId", "id", "nodeName", "name"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for nested_key in ("configuration", "config"):
        nested = record.get(nested_key)
        if isinstance(nested, dict):
            for key in ("externalName", "channelId", "id", "nodeName", "name"):
                value = nested.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _provider_compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_provider_id(value or ""))


def _provider_base(value: str) -> str:
    normalized = normalize_provider_id(value or "")
    if not normalized:
        return ""
    return re.sub(r"-(epg|vod|svod|event|deeplink|linear)$", "", normalized)


@lru_cache(maxsize=1)
def _load_providers_list_index() -> dict[str, dict[str, Any]]:
    """Load providers list once and index provider ids to names/types."""
    path = Path(__file__).parent / "static" / "providers_list.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    out: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, list):
        return out

    for item in payload:
        if not isinstance(item, dict):
            continue
        provider_id = normalize_provider_id(str(item.get("providerId") or ""))
        if not provider_id:
            continue
        provider_name = str(item.get("providerName") or "").strip()
        raw_types = item.get("types")
        types = (
            [str(t).strip().lower() for t in raw_types if str(t).strip()]
            if isinstance(raw_types, list)
            else []
        )
        out[provider_id] = {
            "provider_id": provider_id,
            "provider_name": provider_name,
            "types": types,
        }

    return out


# Metadata types whose content is organized by broadcast channel. VOD/event
# adapters have no channel entries in adminportal (they're organized by
# store/product instead), so a provider-channels lookup for those will
# always come back empty — that's expected, not a fetch failure.
_CHANNEL_BASED_METADATA_TYPES = {"epg"}


def _provider_is_channel_based(provider_id: str) -> bool:
    """Return False only when the provider's declared types are known and
    exclude 'epg' (i.e. it's a vod/event-only adapter with no channel concept)."""
    index = _load_providers_list_index()
    meta = index.get(normalize_provider_id(provider_id))
    if not meta:
        return True
    types = meta.get("types") or []
    if not types:
        return True
    return bool(_CHANNEL_BASED_METADATA_TYPES.intersection(types))


def _expand_provider_alias_hints(
    provider_id: str,
    provider_name: str | None = None,
    extra_aliases: list[str] | None = None,
) -> list[str]:
    """Build alias hints from provider family and provider display names."""
    aliases: set[str] = set(extra_aliases or [])
    normalized_id = normalize_provider_id(provider_id)
    if normalized_id:
        aliases.add(normalized_id)
    if provider_name:
        aliases.add(str(provider_name).strip())

    providers_index = _load_providers_list_index()
    if not providers_index:
        return [alias for alias in aliases if str(alias).strip()]

    target_base = _provider_base(normalized_id)
    if not target_base:
        return [alias for alias in aliases if str(alias).strip()]

    for other_id, meta in providers_index.items():
        if _provider_base(other_id) != target_base:
            continue
        aliases.add(other_id)
        other_name = str(meta.get("provider_name") or "").strip()
        if other_name:
            aliases.add(other_name)

    return [alias for alias in aliases if str(alias).strip()]


def _provider_aliases(
    provider_id: str,
    provider_name: str | None = None,
    extra_aliases: list[str] | None = None,
) -> set[str]:
    normalized = normalize_provider_id(provider_id)
    if not normalized:
        return set()

    aliases: set[str] = {normalized}
    aliases.add(normalized.replace("_", "-"))
    aliases.add(normalized.replace(".", "-"))
    aliases.add(_provider_base(normalized))

    # Common alias handling for Viaplay naming variants.
    if "viaplay" in normalized:
        aliases |= {
            "viaplay",
            "viaplay-epg",
            "viaplay_swe",
            "viaplay_fin",
            "viaplay_nor",
        }

    if provider_name:
        normalized_name = normalize_provider_id(provider_name)
        if normalized_name:
            aliases.add(normalized_name)
            aliases.add(normalized_name.replace("_", "-"))
            aliases.add(normalized_name.replace(" ", "-"))
            aliases.add(_provider_base(normalized_name))

    for alias in extra_aliases or []:
        normalized_alias = normalize_provider_id(alias)
        if not normalized_alias:
            continue
        aliases.add(normalized_alias)
        aliases.add(normalized_alias.replace("_", "-"))
        aliases.add(normalized_alias.replace(" ", "-"))
        aliases.add(_provider_base(normalized_alias))

    return {alias for alias in aliases if alias}


def _filter_channels_for_country(channels: set[str], country: str) -> list[str]:
    if not channels:
        return []

    country_prefix = f"{country.strip().lower()}."
    unique = sorted({str(channel).strip() for channel in channels if str(channel).strip()})

    # IG calls are already country-scoped. Keep all discovered channels, but
    # rank country-prefixed IDs first for cleaner UX.
    return sorted(
        unique,
        key=lambda value: (0 if value.lower().startswith(country_prefix) else 1, value.lower()),
    )


def _provider_matches_target(
    candidates: set[str],
    target_provider: str,
    target_provider_name: str | None = None,
    target_provider_aliases: list[str] | None = None,
) -> bool:
    if not candidates:
        return False

    target_aliases = _provider_aliases(target_provider, target_provider_name, target_provider_aliases)
    if not target_aliases:
        return False

    normalized_candidates = {
        normalize_provider_id(candidate)
        for candidate in candidates
        if isinstance(candidate, str) and candidate.strip()
    }

    if normalized_candidates & target_aliases:
        return True

    target_compacts = {_provider_compact(alias) for alias in target_aliases if alias}
    candidate_compacts = {_provider_compact(candidate) for candidate in normalized_candidates}

    target_compacts.discard("")
    candidate_compacts.discard("")

    # Accept prefix/suffix matches for adapters that append environment/type labels.
    for candidate in candidate_compacts:
        for target in target_compacts:
            if candidate == target:
                return True
            if candidate.startswith(target) or target.startswith(candidate):
                return True

    return False


_CHANNEL_ID_TOKEN_STOPWORDS = {
    "se", "no", "fi", "dk", "channel", "channels", "config", "com", "tv",
}


def _provider_candidates_from_text(value: str) -> set[str]:
    """Infer provider-ish tokens directly from a channel id/name.

    Adminportal frequently leaves `channelProvider`/adapter fields null
    (e.g. every Viaplay channel record), so relying solely on explicit
    provider fields silently loses real channels. Channel ids are almost
    always namespaced by provider (e.g. "se.viaplay.crime",
    "se.tv4.channel.TV4"), so tokenizing the id is a reliable last-resort
    signal.
    """
    if not isinstance(value, str) or not value.strip():
        return set()
    tokens = re.split(r"[^a-z0-9]+", value.strip().lower())
    return {
        token
        for token in tokens
        if token and token not in _CHANNEL_ID_TOKEN_STOPWORDS and not token.isdigit()
    }


def _fetch_adminportal_channels(
    country: str,
    provider_id: str,
    access_token: str,
    provider_name: str | None = None,
    provider_aliases: list[str] | None = None,
) -> list[str]:
    response = requests.get(
        f"{ADMINPORTAL_PILOT_BASE}/v1/config/channels/{country}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=25,
    )
    if response.status_code >= 400:
        return []

    try:
        payload = response.json()
    except ValueError:
        return []

    data = payload.get("data") if isinstance(payload, dict) else payload
    records: list[dict[str, Any]] = []
    if isinstance(data, list):
        records = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("id", key)
                records.append(item)

    out: set[str] = set()
    for record in records:
        channel_id = _extract_channel_id(record)
        candidates = _extract_provider_candidates(record)
        if not candidates:
            candidates = _extract_provider_candidates_deep(record)
        if not candidates and channel_id:
            candidates = _provider_candidates_from_text(channel_id)
        if not _provider_matches_target(candidates, provider_id, provider_name, provider_aliases):
            continue
        if channel_id:
            out.add(channel_id)
    return sorted(out)


def _fetch_adminportal_listing_channels(
    country: str,
    provider_id: str,
    access_token: str,
    provider_name: str | None = None,
    provider_aliases: list[str] | None = None,
) -> list[str]:
    out: set[str] = set()
    page = 0
    page_size = PROVIDER_CHANNELS_PAGE_SIZE

    while True:
        response = requests.get(
            f"{ADMINPORTAL_PILOT_BASE}/v1/channels",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"country": country, "size": page_size, "page": page},
            timeout=25,
        )
        if response.status_code >= 400:
            break

        try:
            payload = response.json()
        except ValueError:
            break

        if isinstance(payload, dict) and isinstance(payload.get("content"), list):
            # The real shape (confirmed live) is a Spring Page response:
            # {"content": [{"channel": {...}, "providerConfiguration": {"adapterId": "..."}}], "last": bool, ...}
            # The old {"data": [...]} assumption below never actually matches
            # this endpoint's real payload, so this source was a silent no-op.
            entries = [x for x in payload["content"] if isinstance(x, dict)]
            is_last_page = bool(payload.get("last", True)) or len(entries) < page_size
        elif isinstance(payload, list):
            entries = [x for x in payload if isinstance(x, dict)]
            is_last_page = len(entries) < page_size
        elif isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                entries = [x for x in data if isinstance(x, dict)]
            elif isinstance(data, dict):
                values = data.get("items") or data.get("content") or []
                entries = [x for x in values if isinstance(x, dict)] if isinstance(values, list) else []
            else:
                entries = []
            is_last_page = len(entries) < page_size
        else:
            entries = []
            is_last_page = True

        if not entries:
            break

        for entry in entries:
            # Each list entry wraps the channel record plus a sibling
            # providerConfiguration that carries the exact adapterId — the
            # most reliable signal available (more reliable than the plain
            # channelProvider field, which is null for most Viaplay/Simply
            # TV channels).
            record = entry.get("channel") if isinstance(entry.get("channel"), dict) else entry
            channel_id = _extract_channel_id(record)

            candidates: set[str] = set()
            provider_configuration = entry.get("providerConfiguration")
            if isinstance(provider_configuration, dict):
                adapter_id = provider_configuration.get("adapterId")
                if isinstance(adapter_id, str) and adapter_id.strip():
                    candidates.add(normalize_provider_id(adapter_id))

            if not candidates:
                candidates = _extract_provider_candidates(record)
            if not candidates:
                candidates = _extract_provider_candidates_deep(record)
            if not candidates and channel_id:
                candidates = _provider_candidates_from_text(channel_id)

            if not _provider_matches_target(candidates, provider_id, provider_name, provider_aliases):
                continue
            if channel_id:
                out.add(channel_id)

        if is_last_page:
            break
        page += 1

    return sorted(out)


def _fetch_provider_channels_from_pilot(
    country: str,
    provider_id: str,
    access_token: str | None = None,
    refresh_token: str | None = None,
    provider_name: str | None = None,
    provider_aliases: list[str] | None = None,
) -> tuple[list[str], str]:
    normalized_country = country.strip().upper()
    normalized_provider = normalize_provider_id(provider_id)

    if not normalized_country or not normalized_provider:
        return [], "invalid-params"

    collected: set[str] = set()

    # Source 0: authenticated adminportal config endpoint (full channel inventory).
    adminportal_token = access_token
    token_exchange_failed = False
    if not adminportal_token and refresh_token:
        try:
            adminportal_token = _grouping_get_or_exchange_access_token(refresh_token)
        except Exception as exc:
            logger.warning("Adminportal token exchange failed for provider channels: %s", exc)
            adminportal_token = None
            token_exchange_failed = True

    if not adminportal_token:
        return [], "auth-failed" if token_exchange_failed else "auth-required"

    if adminportal_token:
        try:
            for name in _fetch_adminportal_channels(
                country=normalized_country,
                provider_id=normalized_provider,
                access_token=adminportal_token,
                provider_name=provider_name,
                provider_aliases=provider_aliases,
            ):
                collected.add(name)
        except Exception as exc:
            logger.warning(
                "Adminportal channel fetch failed for %s/%s: %s",
                normalized_country,
                normalized_provider,
                exc,
            )

        try:
            for name in _fetch_adminportal_listing_channels(
                country=normalized_country,
                provider_id=normalized_provider,
                access_token=adminportal_token,
                provider_name=provider_name,
                provider_aliases=provider_aliases,
            ):
                collected.add(name)
        except Exception as exc:
            logger.warning(
                "Adminportal listing fetch failed for %s/%s: %s",
                normalized_country,
                normalized_provider,
                exc,
            )

    if collected:
        return _filter_channels_for_country(collected, normalized_country), "adminportal-config+channels"

    return [], "adminportal-config+channels"


PROVIDER_CONFIG_MAP: dict[str, str] = {
    "viaplay": "configs/providers/viaplay/viaplay.rules.yaml",
    "viaplay-svod": "configs/providers/viaplay/viaplay.rules.yaml",
        "viaplay-epg": "configs/providers/viaplay/viaplay-epg.rules.yaml",
    "viaplay-deeplink-vod": "configs/providers/viaplay/viaplay.rules.yaml",
    "viaplay-deeplink-event": "configs/providers/viaplay/viaplay-event.rules.yaml",
    "nrk-tv-vod": "configs/providers/nrk-tv/nrk-tv.rules.yaml",
    "norway-tp-vod": "configs/providers/nrk-tv/nrk-tv.rules.yaml",
    "norway-tp-epg": "configs/providers/nrk-tv/nrk-tv.rules.yaml",
    "tv4-media-vod": "configs/providers/tv4-media/tv4-media-vod.rules.yaml",
    "tv4-vod": "configs/providers/tv4-media/tv4-media-vod.rules.yaml",
    "tv4-media-epg": "configs/providers/tv4-media/tv4-media-epg.rules.yaml",
    "tv4-epg": "configs/providers/tv4-media/tv4-media-epg.rules.yaml",
    "simply-tv": "configs/providers/simply-tv/simply-tv-epg.rules.yaml",
    "simply-tv-epg": "configs/providers/simply-tv/simply-tv-epg.rules.yaml",
}


def normalize_provider_id(provider_id: str) -> str:
    return provider_id.strip().lower()


def resolve_provider_config_path(provider_id: str) -> tuple[str | None, str]:
    normalized_provider_id = normalize_provider_id(provider_id)
    if normalized_provider_id in PROVIDER_CONFIG_MAP:
        return PROVIDER_CONFIG_MAP[normalized_provider_id], "explicit-map"

    if "viaplay" in normalized_provider_id:
        return PROVIDER_CONFIG_MAP["viaplay"], "pattern-map"

    return None, "unmapped"


def enabled_error_codes_for_config(
    cfg: dict[str, Any],
    include_converted: bool,
) -> list[str]:
    """Return unique error codes for checks enabled by the selected provider config."""
    disabled_original = set(cfg.get("disabled_original_checks", []))
    disabled_converted = set(cfg.get("disabled_converted_checks", []))
    codes: set[str] = set()

    for check_id, error_code in CHECK_ID_TO_ERROR_CODE.items():
        if check_id.startswith("orig_") and check_id not in disabled_original:
            codes.add(error_code)
        elif (
            include_converted
            and check_id.startswith("conv_")
            and check_id not in disabled_converted
        ):
            codes.add(error_code)

    return sorted(codes)


def build_analytics_field_coverage(
    records: list[tuple[str, dict[str, Any]]],
    findings: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Summarize passed and failed configured fields for each source channel."""
    configured_fields = cfg.get("analytics_matrix_fields", [])
    if not isinstance(configured_fields, list):
        configured_fields = []

    fields = [
        {
            "label": str(field.get("label") or "").strip(),
            "error_codes": [str(code) for code in field.get("error_codes", [])],
        }
        for field in configured_fields
        if isinstance(field, dict) and str(field.get("label") or "").strip()
    ]

    def channel_for_file_ref(file_ref: str) -> str:
        text = str(file_ref or "").strip()
        if " / " in text:
            return text.split(" / ", 1)[0].strip() or "General"
        parts = [part for part in text.replace("\\", "/").split("/") if part]
        if len(parts) >= 2:
            return parts[-2].replace("_", " ").replace("-", " ").title()
        return "General"

    totals: dict[str, int] = {}
    for file_ref, _record in records:
        channel = channel_for_file_ref(file_ref)
        totals[channel] = totals.get(channel, 0) + 1

    failed_keys: dict[str, dict[str, set[tuple[str, str]]]] = {
        channel: {field["label"]: set() for field in fields}
        for channel in totals
    }
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        error_code = str(finding.get("errorCode") or finding.get("check_id") or "")
        file_ref = str(finding.get("file_ref") or "")
        content_id = str(finding.get("content_id") or "")
        channel = channel_for_file_ref(file_ref)
        if channel not in totals:
            continue
        location_key = (file_ref, content_id)
        for field in fields:
            if error_code in field["error_codes"]:
                failed_keys[channel][field["label"]].add(location_key)

    channels: list[dict[str, Any]] = []
    for channel, total in sorted(totals.items()):
        field_stats = {}
        for field in fields:
            failed = len(failed_keys[channel][field["label"]])
            field_stats[field["label"]] = {
                "checked": total,
                "passed": max(total - failed, 0),
                "failed": failed,
            }
        channels.append({"channel": channel, "records": total, "fields": field_stats})

    return {"fields": fields, "channels": channels}


@lru_cache(maxsize=32)
def build_checker_for_config(config_path: str | None) -> MetadataChecker:
    cfg = load_config(config_path)
    return MetadataChecker(cfg)


def save_uploaded_file(file_obj: Any, prefix: str = "") -> None:
    """Save uploaded file to sample_data folder for reference."""
    if not file_obj or not file_obj.filename:
        return
    
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    
    filename = secure_filename(file_obj.filename)
    if prefix:
        filename = f"{prefix}_{filename}"
    
    file_path = UPLOAD_FOLDER / filename
    file_obj.save(str(file_path))


def save_uploaded_bytes(filename: str, payload: bytes, prefix: str = "") -> None:
    """Save uploaded bytes to sample_data folder for reference."""
    if not filename:
        return

    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(filename)
    if prefix:
        safe_name = f"{prefix}_{safe_name}"

    (UPLOAD_FOLDER / safe_name).write_bytes(payload)


def parse_uploaded_records(
    file_obj: Any,
    record_prefix: str,
    save_prefix: str,
    provider_id: str = "",
) -> list[tuple[str, dict[str, Any]]]:
    """Parse uploaded JSON file into normalized (record_key, dict_payload) tuples."""
    if not file_obj or not file_obj.filename:
        return []

    filename = str(file_obj.filename or "")
    path_parts = [part for part in filename.replace("\\", "/").split("/") if part]
    basename = path_parts[-1] if path_parts else filename
    if (
        not basename
        or basename.startswith(".")
        or any(part.startswith(".") or part == "__MACOSX" for part in path_parts)
        or not basename.lower().endswith((".json", ".xml"))
    ):
        return []

    raw = file_obj.read() or b""
    if SAVE_UPLOADED_FILES and raw:
        save_uploaded_bytes(file_obj.filename, raw, prefix=save_prefix)

    decoded: str
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"Failed to parse {save_prefix} {file_obj.filename}: {str(e)}") from e

    payload: Any
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        if str(file_obj.filename or "").lower().endswith(".xml"):
            payload = _parse_xml_payload_as_record(decoded)
        else:
            raise ValueError(
                f"Failed to parse {save_prefix} {file_obj.filename}: expected JSON, got invalid payload"
            )

    normalized_provider_id = normalize_provider_id(provider_id)
    if normalized_provider_id in {"tv4-media-epg", "tv4-epg"}:
        return _normalize_tv4_epg_records(payload, record_prefix, file_obj.filename)
    if normalized_provider_id in {"simply-tv", "simply-tv-epg"}:
        return _normalize_simply_tv_epg_records(payload, record_prefix, file_obj.filename)

    records: list[tuple[str, dict[str, Any]]] = []
    if isinstance(payload, list):
        for idx, item in enumerate(payload):
            if isinstance(item, dict):
                records.append((f"{record_prefix}_{file_obj.filename}#{idx}", item))
    elif isinstance(payload, dict):
        records.append((f"{record_prefix}_{file_obj.filename}", payload))

    return records


def _normalize_tv4_epg_records(
    payload: Any,
    record_prefix: str,
    filename: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Flatten TV4's data[].broadcasts[] schedule shape for generic EPG checks."""
    if isinstance(payload, dict) and isinstance(payload.get("_source"), dict):
        payload = payload["_source"]

    schedules = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(schedules, list):
        return []

    def _display_channel_name(channel_block: dict[str, Any], fallback_path: str) -> str:
        raw = (
            channel_block.get("display_name")
            or channel_block.get("name")
            or fallback_path.replace("_", " ").replace("-", " ")
        )
        text = str(raw or "").strip()
        if not text:
            return "Unknown Channel"
        normalized_upper = text.upper()
        if normalized_upper.startswith("TV4") or normalized_upper.startswith("MTV"):
            return text
        return " ".join(part.capitalize() for part in text.replace("_", " ").split())

    def _display_file_ref(channel_block: dict[str, Any], raw_filename: str) -> str:
        clean = str(raw_filename or "").replace("\\", "/")
        for prefix in ("original/", "converted/", "original_", "converted_"):
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
        clean = clean.split("/")[-1] or clean
        channel_name = _display_channel_name(channel_block, clean.rsplit(".", 1)[0])
        return f"{channel_name} / {clean}"

    related = payload.get("related") if isinstance(payload, dict) else {}
    programs = related.get("programs") if isinstance(related, dict) else []
    programs_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(programs, list):
        for program in programs:
            if not isinstance(program, dict):
                continue
            for key in ("external_reference", "program_external_reference", "id"):
                program_id = str(program.get(key) or "").strip()
                if program_id:
                    programs_by_id[program_id] = program

    records: list[tuple[str, dict[str, Any]]] = []
    for schedule_index, schedule in enumerate(schedules):
        if not isinstance(schedule, dict):
            continue
        channel = schedule.get("channel") if isinstance(schedule.get("channel"), dict) else {}
        broadcasts = schedule.get("broadcasts")
        if not isinstance(broadcasts, list):
            continue

        for broadcast_index, broadcast in enumerate(broadcasts):
            if not isinstance(broadcast, dict):
                continue
            content_id = str(broadcast.get("program_external_reference") or "").strip()
            broadcast_identifier = str(
                broadcast.get("tx_external_reference")
                or broadcast.get("txb_external_reference")
                or broadcast.get("broadcast_external_reference")
                or broadcast.get("broadcastId")
                or broadcast.get("id")
                or content_id
                or f"{schedule_index}.{broadcast_index}"
            ).strip()
            public = broadcast.get("public") or {}
            planned = broadcast.get("planned") or {}
            channel_identifier = (
                channel.get("id")
                or channel.get("name")
                or channel.get("display_name")
            )
            status_text = " ".join(
                str(broadcast.get(key) or "").lower()
                for key in ("type", "status")
            )
            normalized_broadcast = {
                **broadcast,
                "channelId": channel_identifier,
                "contentId": content_id,
                "displayTime": {
                    "start": public.get("start") or planned.get("start"),
                    "end": public.get("end") or planned.get("end"),
                },
                "live": "live" in status_text,
                "broadcastRights": {
                    "hide": (broadcast.get("embargo") or {}).get("hide"),
                    "type": broadcast.get("type"),
                    "status": broadcast.get("status"),
                },
                "product_code": broadcast.get("product_code"),
                "relatedProgram": programs_by_id.get(content_id),
            }

            records.append((
                _display_file_ref(channel, filename),
                {
                "type": "episode",
                "contentId": content_id,
                "title": broadcast.get("title"),
                "channel": channel,
                "channelId": channel.get("id") or channel.get("name") or channel.get("display_name"),
                "product_code": broadcast.get("product_code"),
                "relatedProgram": programs_by_id.get(content_id),
                "broadcasts": [normalized_broadcast],
                },
            ))

    return records


def _normalize_simply_tv_epg_records(
    payload: Any,
    record_prefix: str,
    filename: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Join Simply TV channels, programs, and listings into checker records."""
    if not isinstance(payload, dict):
        return []

    channels = {
        str(item.get("id")): item
        for item in payload.get("channels", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    programs = {
        str(item.get("id")): item
        for item in payload.get("programs", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    listings = payload.get("listings", [])
    if not isinstance(listings, list):
        return []

    grouped: dict[str, dict[str, Any]] = {}
    for index, listing in enumerate(listings):
        if not isinstance(listing, dict):
            continue

        program = programs.get(str(listing.get("program_id")), {})
        channel = channels.get(str(listing.get("channel_id")), {})
        schedule = listing.get("schedule") if isinstance(listing.get("schedule"), dict) else {}
        qualifiers = listing.get("qualifiers") if isinstance(listing.get("qualifiers"), dict) else {}
        attributes = program.get("attributes") if isinstance(program.get("attributes"), dict) else {}
        episode = attributes.get("episode") if isinstance(attributes.get("episode"), dict) else {}
        parental_rating = qualifiers.get("parental_rating")
        channel_id = channel.get("id") or listing.get("channel_id")
        program_id = program.get("id") or listing.get("program_id")
        broadcast_ids = listing.get("broadcast_ids") if isinstance(listing.get("broadcast_ids"), dict) else {}
        broadcast_id = listing.get("id") or broadcast_ids.get("event")

        titles = program.get("titles") if isinstance(program.get("titles"), list) else []
        descriptions = program.get("descriptions") if isinstance(program.get("descriptions"), list) else []
        genres = program.get("genres") if isinstance(program.get("genres"), list) else []
        normalized_images = []
        for image in program.get("images", []) if isinstance(program.get("images"), list) else []:
            if not isinstance(image, dict):
                continue
            normalized_image = dict(image)
            normalized_image["type"] = (
                "content" if image.get("level") in {"show", "episode"} else image.get("level")
            )
            resolution = str(image.get("cropped_resolution") or image.get("original_resolution") or "")
            if "x" in resolution:
                width, height = resolution.lower().split("x", 1)
                if width.isdigit() and height.isdigit():
                    normalized_image["width"] = int(width)
                    normalized_image["height"] = int(height)
            normalized_images.append(normalized_image)
        normalized_program = {
            **program,
            "contentId": str(program_id) if program_id is not None else None,
            "title": titles,
            "descriptions": descriptions,
            "images": normalized_images,
            "genres": [item.get("name") for item in genres if isinstance(item, dict) and item.get("name")],
            "ageRating": str(parental_rating.get("value")) if isinstance(parental_rating, dict) and parental_rating.get("value") is not None else None,
            "productionYear": attributes.get("release_year"),
            "episodeNumber": episode.get("number"),
            "seasonNumber": episode.get("season"),
            "seriesId": program.get("series_id"),
            "channelId": channel_id,
        }
        normalized_broadcast = {
            "broadcastId": str(broadcast_id) if broadcast_id is not None else None,
            "contentId": str(program_id) if program_id is not None else None,
            "channelId": channel_id,
            "countryCode": channel.get("country"),
            "languageCode": channel.get("language"),
            "displayTime": {
                "start": schedule.get("start_time"),
                "end": schedule.get("end_time"),
            },
            "live": qualifiers.get("live", False),
            "broadcastRights": parental_rating if isinstance(parental_rating, dict) else {},
            "qualifiers": qualifiers,
        }
        channel_key = str(channel_id or "unknown")
        if channel_key not in grouped:
            grouped[channel_key] = {
                "channel": channel,
                "channelId": channel_id,
                "broadcasts": [],
                "contents": [],
            }
        grouped[channel_key]["broadcasts"].append(normalized_broadcast)
        grouped[channel_key]["contents"].append(normalized_program)

    records: list[tuple[str, dict[str, Any]]] = []
    for group in grouped.values():
        channel = group["channel"]
        channel_name = channel.get("name") or str(group["channelId"] or "Unknown Channel")
        records.append((
            f"{channel_name} / {filename}",
            {
                "type": "epg",
                "channel": channel,
                "channelId": group["channelId"],
                "broadcasts": group["broadcasts"],
                "contents": group["contents"],
            },
        ))

    return records


def _parse_xml_payload_as_record(xml_text: str) -> dict[str, Any]:
    return parse_xml_payload_as_record(xml_text)


def create_app(config_path: str | None = None) -> Flask:
    """Factory to create and configure Flask app."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH_MB * 1024 * 1024
    app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(32))
    checker = build_checker_for_config(config_path)

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_upload(_: RequestEntityTooLarge) -> tuple[dict[str, Any], int]:
        max_mb = max(1, int(app.config.get("MAX_CONTENT_LENGTH", 0) / (1024 * 1024)))
        return {
            "error": f"Upload payload too large. Maximum request size is {max_mb} MB. Please split into smaller batches or use run_batch_upload.py.",
        }, 413

    @lru_cache(maxsize=1)
    def load_provider_s3_defaults() -> dict[str, Any]:
        root = Path(__file__).parent.parent.parent
        legacy_path = root / "configs" / "providers_s3_config.json"
        static_path = Path(__file__).parent / "static" / "providers_s3_config.json"

        merged: dict[str, Any] = {}

        for path in (legacy_path, static_path):
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                merged.update(payload)

        return merged

    def merge_static_channels(country: str, provider_id: str, live_channels: list[str]) -> tuple[list[str], bool]:
        defaults = load_provider_s3_defaults().get(provider_id, {})
        by_channel = defaults.get("by_channel") if isinstance(defaults, dict) else None
        if not isinstance(by_channel, dict):
            return sorted({c for c in live_channels if isinstance(c, str) and c.strip()}), False

        country_prefix = f"{country.lower()}."
        static_channels: set[str] = set()

        for value in by_channel.values():
            if not isinstance(value, dict):
                continue
            label = value.get("label")
            if not isinstance(label, str):
                continue
            normalized = label.strip()
            if not normalized:
                continue
            # Country-scoped labels are preferred; keep generic labels too.
            if normalized.lower().startswith(country_prefix) or "." not in normalized:
                static_channels.add(normalized)

        merged = {c for c in live_channels if isinstance(c, str) and c.strip()} | static_channels
        return sorted(merged), bool(static_channels)

    @app.route("/", methods=["GET"])
    def index() -> str:
        static_root = Path(__file__).parent / "static"
        asset_mtimes = []
        for filename in ("app.js", "style.css"):
            file_path = static_root / filename
            if file_path.exists():
                asset_mtimes.append(str(int(file_path.stat().st_mtime)))
        static_version = "-".join(asset_mtimes) if asset_mtimes else "1"
        response = make_response(render_template("index.html", static_version=static_version))
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.after_request
    def disable_static_asset_cache(response: Any) -> Any:
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response
    
    @app.route("/api/delete-folder", methods=["DELETE"])
    def delete_uploaded_files() -> Any:
        """Empty the folder contents but keep the folder itself."""
        data = request.get_json()
        folder_id = data.get("folderId") 
        
        if not folder_id or ".." in folder_id:
            return jsonify({"error": "Invalid folderId"}), 400
        
        folder_path = Path(f"/Users/hlk454/Desktop/Work/new channel integrations/vol2/{folder_id}")

        try:
            if folder_path.exists() and folder_path.is_dir():
                # Loop through everything inside the folder
                for item in folder_path.iterdir():
                    if item.is_file() or item.is_symlink():
                        item.unlink()  # Deletes individual file
                    elif item.is_dir():
                        shutil.rmtree(item)  # Deletes subfolders if any
                
                return jsonify({"message": "Folder contents emptied successfully"}), 200
            else:
                # If the root folder doesn't exist, create it so it's ready for uploads
                folder_path.mkdir(parents=True, exist_ok=True)
                return jsonify({"message": "Folder created and is empty"}), 200
        except Exception as e:
            return jsonify({"error": f"Failed to empty files: {str(e)}"}), 500
    @app.route("/api/check/unified", methods=["POST"])
    def check_unified() -> dict[str, Any]:
        """Unified check endpoint: original + optional converted."""
        raw_provider_id = request.form.get("provider_id", "")
        provider_id = normalize_provider_id(raw_provider_id)
        if not provider_id:
            return {"error": "provider_id is required"}, 400

        reference_date = request.form.get("reference_date", "").strip() or None

        provider_config_path, provider_resolution = resolve_provider_config_path(provider_id)
        if not provider_config_path and not ALLOW_UNMAPPED_PROVIDER_FALLBACK:
            return {
                "error": "No provider config mapping found for provider_id",
                "provider_id": provider_id,
                "supported_provider_ids": sorted(PROVIDER_CONFIG_MAP.keys()),
                "hint": "Set ALLOW_UNMAPPED_PROVIDER_FALLBACK=true to use the app default config.",
            }, 400

        if provider_config_path:
            cfg = load_config(provider_config_path)
            provider_checker = MetadataChecker(cfg, reference_date=reference_date)
        else:
            provider_checker = MetadataChecker(checker.cfg, reference_date=reference_date)
            provider_resolution = "default-fallback"

        if "original_files" not in request.files:
            return {"error": "Original files are required"}, 400

        original_files = request.files.getlist("original_files")
        converted_files = request.files.getlist("converted_files")

        if not original_files:
            return {"error": "At least one original file must be selected"}, 400

        original_records: list[tuple[str, dict[str, Any]]] = []
        converted_records: list[tuple[str, dict[str, Any]]] = []

        # Load original files directly from uploaded bytes (faster than temp-file roundtrip)
        for file_obj in original_files:
            try:
                original_records.extend(
                    parse_uploaded_records(
                        file_obj=file_obj,
                        record_prefix="original",
                        save_prefix="original",
                        provider_id=provider_id,
                    )
                )
            except ValueError as e:
                return {"error": str(e)}, 400

        if not original_records:
            return {"error": "No valid JSON records found in original files"}, 400

        # Load converted files if provided
        has_converted = False
        if converted_files and len(converted_files) > 0:
            for file_obj in converted_files:
                try:
                    converted_records.extend(
                        parse_uploaded_records(
                            file_obj=file_obj,
                            record_prefix="converted",
                            save_prefix="converted",
                        )
                    )
                except ValueError as e:
                    return {"error": str(e)}, 400

            if converted_records:
                has_converted = True
                # Debug: expose first converted record keys so schema can be mapped.
                first_conv = converted_records[0][1] if converted_records else {}
                first_keys = list(first_conv.keys())[:20]
                nested = {k: (list(v[0].keys()) if isinstance(v, list) and v and isinstance(v[0], dict) else type(v).__name__)
                          for k, v in list(first_conv.items())[:12]}

        # Run original checks
        findings = provider_checker.run_original_checks(original_records)

        # Run converted checks if converted files provided
        if has_converted:
            converted_findings = provider_checker.run_converted_checks(
                original_records=original_records,
                converted_records=converted_records,
            )
            findings.extend(converted_findings)

        response = {
            "metadata_type": "unified",
            "provider_id": provider_id,
            "provider_config_path": provider_config_path,
            "provider_resolution": provider_resolution,
            "unmapped_provider_fallback_enabled": ALLOW_UNMAPPED_PROVIDER_FALLBACK,
            "original_records_checked": len(original_records),
            "converted_records_checked": len(converted_records) if has_converted else 0,
            "summary": summarize_findings(findings),
            "findings": findings,
            "content_titles": provider_checker.content_titles,
            "enabled_error_codes": enabled_error_codes_for_config(
                provider_checker.cfg,
                include_converted=has_converted,
            ),
            "analytics_matrix_fields": provider_checker.cfg.get(
                "analytics_matrix_fields", []
            ),
            "analytics_field_coverage": build_analytics_field_coverage(
                original_records,
                findings,
                provider_checker.cfg,
            ),
        }

        if has_converted and converted_records:
            first_conv = converted_records[0][1]
            response["_debug_converted_schema"] = {
                "top_level_keys": list(first_conv.keys())[:20],
                "nested_shapes": {
                    k: (list(v[0].keys()) if isinstance(v, list) and v and isinstance(v[0], dict)
                        else (list(v.keys()) if isinstance(v, dict) else type(v).__name__))
                    for k, v in list(first_conv.items())[:15]
                },
            }

        # Include reference date in response
        if reference_date:
            response["reference_date"] = reference_date
        
        return response

    @app.route("/api/ingestion-tests", methods=["POST"])
    def run_ingestion_tests_api() -> Any:
        tests_config_path = (
            request.form.get(
                "tests_config",
                "configs/providers/nrk-tv/nrk-tv.repeating_ingestion.tests.yaml",
            )
            .strip()
        )

        if "original_files" not in request.files:
            return {"error": "Original files are required"}, 400
        if "converted_files" not in request.files:
            return {"error": "Converted files are required"}, 400

        original_files = request.files.getlist("original_files")
        converted_files = request.files.getlist("converted_files")
        baseline_files = request.files.getlist("baseline_converted_files")

        if not original_files:
            return {"error": "At least one original file must be selected"}, 400
        if not converted_files:
            return {"error": "At least one converted file must be selected"}, 400

        original_records_wrapped: list[tuple[str, dict[str, Any]]] = []
        converted_records_wrapped: list[tuple[str, dict[str, Any]]] = []
        baseline_records_wrapped: list[tuple[str, dict[str, Any]]] = []

        for file_obj in original_files:
            try:
                original_records_wrapped.extend(
                    parse_uploaded_records(
                        file_obj=file_obj,
                        record_prefix="original",
                        save_prefix="original",
                    )
                )
            except ValueError as e:
                return {"error": str(e)}, 400

        for file_obj in converted_files:
            try:
                converted_records_wrapped.extend(
                    parse_uploaded_records(
                        file_obj=file_obj,
                        record_prefix="converted",
                        save_prefix="converted",
                    )
                )
            except ValueError as e:
                return {"error": str(e)}, 400

        for file_obj in baseline_files:
            try:
                baseline_records_wrapped.extend(
                    parse_uploaded_records(
                        file_obj=file_obj,
                        record_prefix="baseline_converted",
                        save_prefix="baseline_converted",
                    )
                )
            except ValueError as e:
                return {"error": str(e)}, 400

        original_records = [record for _, record in original_records_wrapped]
        converted_records = [record for _, record in converted_records_wrapped]
        baseline_records = [record for _, record in baseline_records_wrapped] if baseline_records_wrapped else None

        try:
            report = run_ingestion_test_suite_from_records(
                tests_config_path=tests_config_path,
                original_records=original_records,
                converted_records=converted_records,
                baseline_converted_records=baseline_records,
                inputs_meta={
                    "original_local_path": None,
                    "converted_local_path": None,
                    "baseline_converted_local_path": None,
                },
            )
            return jsonify(report)
        except Exception as e:
            return {"error": f"Failed to run ingestion tests: {str(e)}"}, 500

    @app.route("/api/check/original", methods=["POST"])
    def check_original() -> dict[str, Any]:
        """Upload original metadata JSON and run checks."""
        raw_provider_id = request.form.get("provider_id", "")
        provider_id = normalize_provider_id(raw_provider_id)
        provider_checker = checker
        provider_config_path: str | None = None
        provider_resolution = "default"

        if provider_id:
            provider_config_path, provider_resolution = resolve_provider_config_path(provider_id)
            if not provider_config_path and not ALLOW_UNMAPPED_PROVIDER_FALLBACK:
                return {
                    "error": "No provider config mapping found for provider_id",
                    "provider_id": provider_id,
                    "supported_provider_ids": sorted(PROVIDER_CONFIG_MAP.keys()),
                    "hint": "Set ALLOW_UNMAPPED_PROVIDER_FALLBACK=true to use the app default config.",
                }, 400

            if provider_config_path:
                provider_checker = build_checker_for_config(provider_config_path)
            else:
                provider_resolution = "default-fallback"

        if "files" not in request.files:
            return {"error": "No files uploaded"}, 400

        files = request.files.getlist("files")
        if not files:
            return {"error": "No files selected"}, 400

        records: list[tuple[str, dict[str, Any]]] = []
        for file_obj in files:
            try:
                records.extend(
                    parse_uploaded_records(
                        file_obj=file_obj,
                        record_prefix="original",
                        save_prefix="original",
                    )
                )
            except ValueError as e:
                return {"error": str(e)}, 400

        if not records:
            return {"error": "No valid JSON/XML records found in uploaded files"}, 400

        findings = provider_checker.run_original_checks(records)
        return {
            "metadata_type": "original",
            "provider_id": provider_id or None,
            "provider_config_path": provider_config_path,
            "provider_resolution": provider_resolution,
            "records_checked": len(records),
            "summary": summarize_findings(findings),
            "findings": findings,
            "content_titles": provider_checker.content_titles,
        }

    @app.route("/api/export/findings-excel", methods=["POST"])
    def export_findings_excel() -> Any:
        """Build an .xlsx workbook of findings, one sheet per error code.

        Expects JSON body: {"findings": [...raw per-occurrence findings...],
        "content_titles": {content_id: title, ...}}. Columns: File Name,
        Content Name, Content ID, Message.
        """
        data = request.get_json(silent=True) or {}
        findings = data.get("findings") or []
        content_titles = data.get("content_titles") or {}

        if not isinstance(findings, list) or not findings:
            return jsonify({"error": "No findings provided to export."}), 400

        # Group occurrences per error code, deduping identical file/content rows.
        sheets: dict[str, dict[str, Any]] = {}
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            code = str(finding.get("errorCode") or finding.get("check_id") or "UNKNOWN")
            sheet = sheets.setdefault(code, {"message": finding.get("message") or "", "rows": [], "seen": set()})
            file_ref = str(finding.get("file_ref") or "-")
            content_id = str(finding.get("content_id") or "")
            dedupe_key = (file_ref, content_id)
            if dedupe_key in sheet["seen"]:
                continue
            sheet["seen"].add(dedupe_key)
            sheet["rows"].append((file_ref, content_titles.get(content_id, ""), content_id))

        workbook = Workbook()
        workbook.remove(workbook.active)

        used_sheet_names: set[str] = set()
        for code in sorted(sheets.keys()):
            sheet_info = sheets[code]
            # Excel sheet names: max 31 chars, no []:*?/\\, must be unique.
            base_name = re.sub(r"[\[\]:*?/\\]", "_", code)[:31] or "Sheet"
            sheet_name = base_name
            suffix = 1
            while sheet_name in used_sheet_names:
                suffix_str = f"_{suffix}"
                sheet_name = f"{base_name[: 31 - len(suffix_str)]}{suffix_str}"
                suffix += 1
            used_sheet_names.add(sheet_name)

            ws = workbook.create_sheet(title=sheet_name)
            headers = ["File Name", "Content Name", "Content ID"]
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for row in sheet_info["rows"]:
                ws.append(list(row))
            for col_idx, header in enumerate(headers, start=1):
                max_len = max([len(header)] + [len(str(r[col_idx - 1])) for r in sheet_info["rows"]])
                ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 80)

        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        return send_file(
            buffer,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="metadata_findings.xlsx",
        )

    @app.route("/api/check/converted", methods=["POST"])
    def check_converted() -> dict[str, Any]:
        """Upload original and converted metadata JSONs and run comparison checks."""
        raw_provider_id = request.form.get("provider_id", "")
        provider_id = normalize_provider_id(raw_provider_id)
        provider_checker = checker
        provider_config_path: str | None = None
        provider_resolution = "default"

        if provider_id:
            provider_config_path, provider_resolution = resolve_provider_config_path(provider_id)
            if not provider_config_path and not ALLOW_UNMAPPED_PROVIDER_FALLBACK:
                return {
                    "error": "No provider config mapping found for provider_id",
                    "provider_id": provider_id,
                    "supported_provider_ids": sorted(PROVIDER_CONFIG_MAP.keys()),
                    "hint": "Set ALLOW_UNMAPPED_PROVIDER_FALLBACK=true to use the app default config.",
                }, 400

            if provider_config_path:
                provider_checker = build_checker_for_config(provider_config_path)
            else:
                provider_resolution = "default-fallback"

        if "original_files" not in request.files or "converted_files" not in request.files:
            return {"error": "Both original and converted files are required"}, 400

        original_files = request.files.getlist("original_files")
        converted_files = request.files.getlist("converted_files")

        if not original_files or not converted_files:
            return {"error": "Both original and converted files must be selected"}, 400

        original_records: list[tuple[str, dict[str, Any]]] = []
        converted_records: list[tuple[str, dict[str, Any]]] = []

        for file_obj in original_files:
            try:
                original_records.extend(
                    parse_uploaded_records(
                        file_obj=file_obj,
                        record_prefix="original",
                        save_prefix="original",
                    )
                )
            except ValueError as e:
                return {"error": str(e)}, 400

        for file_obj in converted_files:
            try:
                converted_records.extend(
                    parse_uploaded_records(
                        file_obj=file_obj,
                        record_prefix="converted",
                        save_prefix="converted",
                    )
                )
            except ValueError as e:
                return {"error": str(e)}, 400

        if not original_records or not converted_records:
            return {"error": "No valid JSON/XML records found in one or both file sets"}, 400

        findings = provider_checker.run_converted_checks(
            original_records=original_records,
            converted_records=converted_records,
        )

        return {
            "metadata_type": "converted",
            "provider_id": provider_id or None,
            "provider_config_path": provider_config_path,
            "provider_resolution": provider_resolution,
            "original_records_checked": len(original_records),
            "converted_records_checked": len(converted_records),
            "summary": summarize_findings(findings),
            "findings": findings,
            "content_titles": provider_checker.content_titles,
        }

    @app.route("/api/check/s3", methods=["POST"])
    def check_s3() -> dict[str, Any]:
        """Run checks against S3-hosted metadata files filtered by date range."""
        data = request.get_json()
        if not data:
            return {"error": "Request body must be JSON"}, 400

        provider_id = normalize_provider_id(data.get("provider_id", ""))
        bucket = data.get("bucket", "").strip()
        original_prefix = data.get("original_prefix", "").strip()
        converted_prefix = data.get("converted_prefix", "").strip()
        start_date = data.get("start_date", "").strip()
        end_date = data.get("end_date", "").strip()
        reference_date = data.get("reference_date", "").strip() or None
        aws_profile = data.get("aws_profile", "").strip() or None

        if not provider_id:
            return {"error": "provider_id is required"}, 400
        if not bucket:
            return {"error": "bucket is required"}, 400
        if not original_prefix:
            return {"error": "original_prefix is required"}, 400
        if not start_date or not end_date:
            return {"error": "start_date and end_date are required (YYYY-MM-DD format)"}, 400

        provider_config_path, provider_resolution = resolve_provider_config_path(provider_id)
        if not provider_config_path and not ALLOW_UNMAPPED_PROVIDER_FALLBACK:
            return {
                "error": "No provider config mapping found for provider_id",
                "provider_id": provider_id,
                "supported_provider_ids": sorted(PROVIDER_CONFIG_MAP.keys()),
                "hint": "Set ALLOW_UNMAPPED_PROVIDER_FALLBACK=true to use the app default config.",
            }, 400

        if provider_config_path:
            cfg = load_config(provider_config_path)
            provider_checker = MetadataChecker(cfg, reference_date=reference_date)
        else:
            cfg = load_config(None)
            provider_checker = MetadataChecker(cfg, reference_date=reference_date)
            provider_resolution = "default-fallback"

        effective_profile = aws_profile
        aws_profile_fallback_used = False
        original_prefix_used = original_prefix
        converted_prefix_used = converted_prefix
        prefix_fallback_used = False
        original_from_converted_fallback_used = False

        def _load_s3_records_with_profile_fallback(prefix: str, label: str) -> list[tuple[str, dict[str, Any]]]:
            nonlocal effective_profile, aws_profile_fallback_used
            try:
                return provider_checker.load_records_from_s3_by_date_range(
                    bucket=bucket,
                    prefix=prefix,
                    start_date=start_date,
                    end_date=end_date,
                    profile_name=effective_profile,
                )
            except Exception as exc:
                if effective_profile and _looks_like_expired_sso_profile_error(exc):
                    logger.warning(
                        "S3 %s load failed with expired SSO profile '%s'; retrying with default AWS credentials.",
                        label,
                        effective_profile,
                    )
                    try:
                        records = provider_checker.load_records_from_s3_by_date_range(
                            bucket=bucket,
                            prefix=prefix,
                            start_date=start_date,
                            end_date=end_date,
                            profile_name=None,
                        )
                        aws_profile_fallback_used = True
                        effective_profile = None
                        return records
                    except Exception as retry_exc:
                        retry_text = str(retry_exc)
                        return_error = (
                            f"Failed to load {label} records from S3: {str(exc)}. "
                            f"Retry without profile also failed: {retry_text}"
                        )

                        retry_text_lower = retry_text.lower()
                        if (
                            "aws login" in retry_text_lower
                            or "credentials have changed" in retry_text_lower
                            or "session has expired" in retry_text_lower
                        ):
                            if effective_profile:
                                return_error += (
                                    " Reauthenticate both credential chains: "
                                    f"run 'aws sso login --profile {effective_profile}' "
                                    "and then run 'aws login' for default credentials."
                                )
                            else:
                                return_error += (
                                    " Reauthenticate default credentials by running "
                                    "'aws login' (or your company equivalent), then retry."
                                )

                        raise RuntimeError(return_error) from retry_exc

                raise RuntimeError(f"Failed to load {label} records from S3: {str(exc)}") from exc

        # Load from S3 by date range
        try:
            original_records = _load_s3_records_with_profile_fallback(
                prefix=original_prefix,
                label="original",
            )
        except Exception as e:
            return {"error": str(e)}, 400

        # Some providers use a common base prefix while channel-level mappings
        # are only logical selectors. If a channel prefix returns no data,
        # retry once with the provider's base original prefix.
        if not original_records:
            defaults = load_provider_s3_defaults().get(provider_id, {})
            base_original_prefix = ""
            if isinstance(defaults, dict):
                base_original_prefix = str(defaults.get("original_prefix", "")).strip()

            if base_original_prefix and base_original_prefix != original_prefix:
                try:
                    fallback_original_records = _load_s3_records_with_profile_fallback(
                        prefix=base_original_prefix,
                        label="original",
                    )
                except Exception:
                    fallback_original_records = []

                if fallback_original_records:
                    original_records = fallback_original_records
                    original_prefix_used = base_original_prefix
                    prefix_fallback_used = True

        # If original is still empty but converted prefix is available, use
        # converted dataset as source so users can still run checks on EPG
        # environments that store only converted artifacts.
        if not original_records and converted_prefix:
            try:
                converted_as_original = _load_s3_records_with_profile_fallback(
                    prefix=converted_prefix,
                    label="converted",
                )
            except Exception:
                converted_as_original = []

            if converted_as_original:
                original_records = converted_as_original
                original_prefix_used = converted_prefix
                original_from_converted_fallback_used = True

        if not original_records:
            return {
                "error": (
                    "No valid JSON records found in original S3 prefix for the specified date range. "
                    f"Checked prefix='{original_prefix}'"
                    + (
                        f" and fallback prefix='{original_prefix_used}'"
                        if original_prefix_used != original_prefix
                        else ""
                    )
                    + f" in bucket='{bucket}' for {start_date} to {end_date}."
                )
            }, 400

        # Run original checks
        findings = provider_checker.run_original_checks(original_records)

        # Run converted checks if converted_prefix provided
        if converted_prefix:
            try:
                converted_records = _load_s3_records_with_profile_fallback(
                    prefix=converted_prefix,
                    label="converted",
                )
            except Exception as e:
                return {"error": str(e)}, 400

            if not converted_records:
                defaults = load_provider_s3_defaults().get(provider_id, {})
                base_converted_prefix = ""
                if isinstance(defaults, dict):
                    base_converted_prefix = str(defaults.get("converted_prefix", "")).strip()

                if base_converted_prefix and base_converted_prefix != converted_prefix:
                    try:
                        fallback_converted_records = _load_s3_records_with_profile_fallback(
                            prefix=base_converted_prefix,
                            label="converted",
                        )
                    except Exception:
                        fallback_converted_records = []

                    if fallback_converted_records:
                        converted_records = fallback_converted_records
                        converted_prefix_used = base_converted_prefix
                        prefix_fallback_used = True

            # Avoid comparing a dataset to itself when original fallback uses
            # the same converted prefix.
            if original_from_converted_fallback_used and original_prefix_used == converted_prefix_used:
                converted_records = []

            if converted_records:
                converted_findings = provider_checker.run_converted_checks(
                    original_records=original_records,
                    converted_records=converted_records,
                )
                findings.extend(converted_findings)

            return {
                "metadata_type": "s3-unified",
                "provider_id": provider_id,
                "provider_config_path": provider_config_path,
                "provider_resolution": provider_resolution,
                "bucket": bucket,
                "original_prefix": original_prefix,
                "converted_prefix": converted_prefix,
                "original_prefix_used": original_prefix_used,
                "converted_prefix_used": converted_prefix_used,
                "prefix_fallback_used": prefix_fallback_used,
                "original_from_converted_fallback_used": original_from_converted_fallback_used,
                "date_range": f"{start_date} to {end_date}",
                "original_records_checked": len(original_records),
                "converted_records_checked": len(converted_records),
                "aws_profile_requested": aws_profile,
                "aws_profile_used": effective_profile,
                "aws_profile_fallback_used": aws_profile_fallback_used,
                "summary": summarize_findings(findings),
                "findings": findings,
                "content_titles": provider_checker.content_titles,
                # Pass optional reference_date as a normal dict entry.
                **({"reference_date": reference_date} if reference_date else {}),
            }
        else:
            return {
                "metadata_type": "s3-original",
                "provider_id": provider_id,
                "provider_config_path": provider_config_path,
                "provider_resolution": provider_resolution,
                "bucket": bucket,
                "original_prefix": original_prefix,
                "original_prefix_used": original_prefix_used,
                "prefix_fallback_used": prefix_fallback_used,
                "original_from_converted_fallback_used": original_from_converted_fallback_used,
                "date_range": f"{start_date} to {end_date}",
                "original_records_checked": len(original_records),
                "converted_records_checked": 0,
                "aws_profile_requested": aws_profile,
                "aws_profile_used": effective_profile,
                "aws_profile_fallback_used": aws_profile_fallback_used,
                "summary": summarize_findings(findings),
                "findings": findings,
                "content_titles": provider_checker.content_titles,
                # Pass optional reference_date as a normal dict entry.
                **({"reference_date": reference_date} if reference_date else {}),
            }

    @app.route("/api/provider-channels", methods=["GET"])
    def api_provider_channels() -> Any:
        country = (request.args.get("country") or "").strip().upper()
        provider_id = normalize_provider_id(request.args.get("provider_id", ""))
        provider_name = (request.args.get("provider_name") or "").strip()
        provider_aliases_raw = (request.args.get("provider_aliases") or "").strip()
        provider_aliases = [
            alias.strip()
            for alias in provider_aliases_raw.split("|")
            if alias and alias.strip()
        ]
        provider_aliases = _expand_provider_alias_hints(
            provider_id=provider_id,
            provider_name=provider_name or None,
            extra_aliases=provider_aliases,
        )
        access_token = (request.headers.get("X-Access-Token") or request.args.get("access_token") or "").strip()
        refresh_token = (request.headers.get("X-Refresh-Token") or request.args.get("refresh_token") or "").strip()
        auth_mode = "auth" if (access_token or refresh_token) else "public"

        if not country:
            return jsonify({"error": "country is required"}), 400
        if not provider_id:
            return jsonify({"error": "provider_id is required"}), 400
        if not access_token and not refresh_token:
            return jsonify({"error": "Refresh/access token is required to fetch channels from IG."}), 401

        provider_name_key = normalize_provider_id(provider_name)
        token_fingerprint = ""
        if auth_mode == "auth":
            # Keep cache entries scoped per credential chain while never storing raw secrets.
            token_seed = access_token or refresh_token
            token_fingerprint = hashlib.sha256(token_seed.encode("utf-8")).hexdigest()[:16]

        cache_key = (country, provider_id, provider_name_key, auth_mode, token_fingerprint)
        now = time.time()

        with _PROVIDER_CHANNELS_CACHE_LOCK:
            cached = _PROVIDER_CHANNELS_CACHE.get(cache_key)
            if cached:
                channels, expires_at, source = cached
                if now < expires_at:
                    merged_channels, has_static = merge_static_channels(country, provider_id, channels)
                    response = {
                        "country": country,
                        "provider_id": provider_id,
                        "channels": merged_channels,
                        "source": f"{source}+static-config" if has_static else source,
                        "cached": True,
                        "auth_mode": auth_mode,
                    }
                    if not merged_channels and not _provider_is_channel_based(provider_id):
                        response["channel_based"] = False
                        response["note"] = (
                            "This adapter's content isn't organized by broadcast channel "
                            "(it's VOD/event-based), so no channels are expected here."
                        )
                    return jsonify(response)

        try:
            channels, source = _fetch_provider_channels_from_pilot(
                country,
                provider_id,
                access_token=access_token or None,
                refresh_token=refresh_token or None,
                provider_name=provider_name or None,
                provider_aliases=provider_aliases,
            )
        except Exception as exc:
            logger.warning(
                "Failed to fetch provider channels for %s/%s: %s",
                country,
                provider_id,
                exc,
            )
            return jsonify({"error": "Failed to fetch channels from pilot"}), 502

        if source == "auth-required":
            return jsonify({"error": "Refresh/access token is required to fetch channels from IG."}), 401
        if source == "auth-failed":
            return jsonify({"error": "Token is invalid or expired. Paste a fresh Inspector Gadget refresh token and retry."}), 401

        # Do not cache empty channel lists — they are often transient/match-related and
        # should be re-queried on next request instead of sticking for full TTL.
        if channels:
            with _PROVIDER_CHANNELS_CACHE_LOCK:
                _PROVIDER_CHANNELS_CACHE[cache_key] = (
                    channels,
                    now + max(30, _PROVIDER_CHANNELS_CACHE_TTL_SECONDS),
                    source,
                )

        merged_channels, has_static = merge_static_channels(country, provider_id, channels)

        response = {
            "country": country,
            "provider_id": provider_id,
            "channels": merged_channels,
            "source": f"{source}+static-config" if has_static else source,
            "cached": False,
            "auth_mode": auth_mode,
        }
        if not merged_channels and not _provider_is_channel_based(provider_id):
            response["channel_based"] = False
            response["note"] = (
                "This adapter's content isn't organized by broadcast channel "
                "(it's VOD/event-based), so no channels are expected here."
            )

        return jsonify(response)

    @app.route("/api/edit/ungrouped-series", methods=["GET"])
    def get_ungrouped_series() -> dict[str, Any]:
        """Query ungrouped series from converted metadata in S3, scoped by adapter ID."""
        provider_id = normalize_provider_id(request.args.get("provider_id", ""))
        adapter_id = request.args.get("adapter_id", "viaplay-deeplink-vod").strip() or "viaplay-deeplink-vod"

        bucket = request.args.get("bucket", "").strip()
        converted_prefix = request.args.get("converted_prefix", "").strip()
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()
        aws_profile = request.args.get("aws_profile", "").strip() or None

        page_raw = request.args.get("page", "0").strip()
        size_raw = request.args.get("size", "50").strip()

        try:
            page = max(0, int(page_raw))
            size = max(1, int(size_raw))
        except ValueError:
            return {"error": "page and size must be integers"}, 400

        provider_checker = checker
        provider_config_path: str | None = None
        provider_resolution = "default"

        if provider_id:
            provider_config_path, provider_resolution = resolve_provider_config_path(provider_id)
            if provider_config_path:
                provider_checker = build_checker_for_config(provider_config_path)

            defaults = load_provider_s3_defaults().get(provider_id, {})
            if isinstance(defaults, dict):
                bucket = bucket or str(defaults.get("bucket", "")).strip()
                converted_prefix = converted_prefix or str(defaults.get("converted_prefix", "")).strip()
                if aws_profile is None:
                    aws_profile = str(defaults.get("aws_profile", "")).strip() or None

        if not bucket:
            return {"error": "bucket is required (or provide provider_id with configured defaults)"}, 400
        if not converted_prefix:
            return {"error": "converted_prefix is required (or provide provider_id with configured defaults)"}, 400
        if not start_date or not end_date:
            return {"error": "start_date and end_date are required (YYYY-MM-DD format)"}, 400

        try:
            converted_records = provider_checker.load_records_from_s3_by_date_range(
                bucket=bucket,
                prefix=converted_prefix,
                start_date=start_date,
                end_date=end_date,
                profile_name=aws_profile,
            )
        except Exception as e:
            return {"error": f"Failed to load converted records from S3: {str(e)}"}, 400

        result = provider_checker.find_ungrouped_series_in_converted(
            converted_records=converted_records,
            adapter_id=adapter_id,
            page=page,
            size=size,
        )

        return {
            "metadata_type": "ungrouped-series",
            "provider_id": provider_id or None,
            "provider_config_path": provider_config_path,
            "provider_resolution": provider_resolution,
            "adapter_id": adapter_id,
            "bucket": bucket,
            "converted_prefix": converted_prefix,
            "date_range": f"{start_date} to {end_date}",
            "count": result["count"],
            "rows": result["rows"],
        }

    # ------------------------------------------------------------------ #
    def _is_event_provider(provider_id: str) -> bool:
        """Check if provider is event-type (not vod/episode-based)."""
        normalized = normalize_provider_id(provider_id)
        return "-event" in normalized or normalized.endswith("-event")

    # Grouping check routes                                                #
    # ------------------------------------------------------------------ #

    @app.route("/grouping-check", methods=["GET"])
    def grouping_check_form() -> Any:
        return render_template("grouping_check.html", error=None, report=None)

    @app.route("/grouping-check", methods=["POST"])
    def grouping_check_run() -> Any:
        provider_id: str = (request.form.get("provider_id") or "viaplay-deeplink-vod").strip()
        
        # Events don't support grouping (no episode/season/series structure)
        if _is_event_provider(provider_id):
            return render_template(
                "grouping_check.html",
                error="Grouping check is not available for event providers. Events are standalone and don't have episode/season structure.",
                report=None,
            )
        
        refresh_token: str = (request.form.get("refresh_token") or "").strip()
        if not refresh_token:
            return render_template(
                "grouping_check.html",
                error="Please provide an Inspector Gadget refresh token.",
                report=None,
            )

        # Validate token by attempting exchange.
        client = GroupingApiClient(refresh_token)
        try:
            client.exchange_token()
        except Exception:
            return render_template(
                "grouping_check.html",
                error="Token is invalid or expired — get a fresh one from Inspector Gadget and try again.",
                report=None,
            )

        uploaded = request.files.getlist("files")
        if not uploaded or all(f.filename == "" for f in uploaded):
            return render_template(
                "grouping_check.html",
                error="Please upload at least one JSON file.",
                report=None,
            )

        episodes, debug_info = parse_episode_files_with_debug(uploaded)
        if not episodes:
            details = (
                f"files={debug_info.get('files_total', 0)}, "
                f"json_ok={debug_info.get('files_json_ok', 0)}, "
                f"json_failed={debug_info.get('files_json_failed', 0)}, "
                f"episode_like={debug_info.get('episode_like_records', 0)}, "
                f"missing_series_link={debug_info.get('missing_series_link', 0)}, "
                f"missing_episode_id={debug_info.get('missing_episode_id', 0)}"
            )
            return render_template(
                "grouping_check.html",
                error=(
                    "No episode records with series linkage were found. "
                    "Upload converted Viaplay EPG JSON files that contain episodic content "
                    "(for example, a crime channel day). Original XML files will not produce grouping rows. "
                    f"Debug: {details}."
                ),
                report=None,
            )

        guids = [ep.guid for ep in episodes]
        results = client.check_guids(guids)
        report = build_report(episodes, results)

        # Store full report in session for download endpoint.
        session["grouping_report"] = report

        return render_template("grouping_check.html", error=None, report=report)

    @app.route("/grouping-check/download", methods=["GET"])
    def grouping_check_download() -> Any:
        report = session.get("grouping_report")
        if not report:
            return redirect(url_for("grouping_check_form"))

        resp = make_response(json.dumps(report, indent=2, ensure_ascii=False))
        resp.headers["Content-Type"] = "application/json"
        resp.headers["Content-Disposition"] = (
            "attachment; filename=grouping_check_report.json"
        )
        return resp

    # ------------------------------------------------------------------ #
    # Grouping check — JSON API (used by the inline widget on index.html) #
    # ------------------------------------------------------------------ #

    @app.route("/api/grouping-check", methods=["POST"])
    def api_grouping_check() -> Any:
        access_token: str = (request.form.get("access_token") or "").strip()
        refresh_token: str = (request.form.get("refresh_token") or "").strip()
        country_code: str = (request.form.get("country_code") or "FI").strip().upper() or "FI"

        if access_token:
            client = GroupingApiClient(access_token=access_token, country_code=country_code)
        else:
            if not refresh_token:
                return jsonify({"error": "Please provide an Inspector Gadget refresh token."}), 400

            try:
                exchanged_access_token = _grouping_get_or_exchange_access_token(refresh_token)
                client = GroupingApiClient(access_token=exchanged_access_token, country_code=country_code)
            except Exception as exc:
                logger.warning("Grouping token exchange failed: %s", exc)
                return jsonify({
                    "error": (
                        "Token is invalid or expired — get a fresh one from Inspector Gadget "
                        f"and try again. Details: {exc}"
                    )
                }), 401

        uploaded = request.files.getlist("files")
        if not uploaded or all(f.filename == "" for f in uploaded):
            return jsonify({"error": "Please upload at least one JSON file."}), 400

        episodes, debug_info = parse_episode_files_with_debug(uploaded)
        if not episodes:
            return jsonify({
                "error": (
                    "No episode records with series linkage were found. "
                    "Upload converted Viaplay EPG JSON files that contain episodic content "
                    "(for example, a crime channel day). Original XML files will not produce grouping rows."
                ),
                "debug": debug_info,
            }), 422

        guids = [ep.guid for ep in episodes]
        results = client.check_guids(guids)
        report = build_report(episodes, results)
        return jsonify(report)

    @app.route("/api/grouping-check/token", methods=["POST"])
    def api_grouping_check_token() -> Any:
        refresh_token: str = (request.form.get("refresh_token") or "").strip()
        if not refresh_token:
            return jsonify({"error": "Please provide an Inspector Gadget refresh token."}), 400

        started_at = time.time()
        try:
            access_token = _grouping_get_or_exchange_access_token(refresh_token)
        except Exception as exc:
            elapsed_ms = int((time.time() - started_at) * 1000)
            logger.warning(
                "Grouping token exchange failed after %sms: %s", elapsed_ms, exc
            )
            return jsonify({
                "error": (
                    "Token is invalid or expired — get a fresh one from Inspector Gadget "
                    f"and try again. Details: {exc}"
                )
            }), 401

        elapsed_ms = int((time.time() - started_at) * 1000)
        logger.warning("Grouping token exchange took %sms", elapsed_ms)
        return jsonify({"access_token": access_token})

    @app.route("/api/grouping-check/s3", methods=["POST"])
    def api_grouping_check_s3() -> Any:
        data = request.get_json(silent=True) or {}

        access_token: str = str(data.get("access_token") or "").strip()
        refresh_token: str = str(data.get("refresh_token") or "").strip()
        country_code: str = str(data.get("country_code") or "FI").strip().upper() or "FI"

        provider_id: str = normalize_provider_id(str(data.get("provider_id") or ""))
        bucket = str(data.get("bucket") or "").strip()
        converted_prefix = str(data.get("converted_prefix") or "").strip()
        start_date = str(data.get("start_date") or "").strip()
        end_date = str(data.get("end_date") or "").strip()
        aws_profile = str(data.get("aws_profile") or "").strip() or None

        if not provider_id:
            return jsonify({"error": "provider_id is required."}), 400
        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required (YYYY-MM-DD)."}), 400

        defaults = load_provider_s3_defaults().get(provider_id, {})
        if isinstance(defaults, dict):
            bucket = bucket or str(defaults.get("bucket", "")).strip()
            converted_prefix = converted_prefix or str(defaults.get("converted_prefix", "")).strip()
            if aws_profile is None:
                aws_profile = str(defaults.get("aws_profile", "")).strip() or None

        if not bucket:
            return jsonify({"error": "bucket is required."}), 400
        if not converted_prefix:
            return jsonify({"error": "converted_prefix is required."}), 400

        if access_token:
            client = GroupingApiClient(access_token=access_token, country_code=country_code)
        else:
            if not refresh_token:
                return jsonify({"error": "Please provide an Inspector Gadget refresh token."}), 400
            try:
                exchanged_access_token = _grouping_get_or_exchange_access_token(refresh_token)
                client = GroupingApiClient(access_token=exchanged_access_token, country_code=country_code)
            except Exception as exc:
                logger.warning("Grouping token exchange failed for S3 flow: %s", exc)
                return jsonify({
                    "error": (
                        "Token is invalid or expired — get a fresh one from Inspector Gadget "
                        f"and try again. Details: {exc}"
                    )
                }), 401

        provider_config_path, _ = resolve_provider_config_path(provider_id)
        if provider_config_path:
            cfg = load_config(provider_config_path)
            provider_checker = MetadataChecker(cfg)
        else:
            provider_checker = checker

        effective_profile = aws_profile

        def _load_converted_records_with_profile_fallback() -> list[tuple[str, dict[str, Any]]]:
            nonlocal effective_profile
            try:
                return provider_checker.load_records_from_s3_by_date_range(
                    bucket=bucket,
                    prefix=converted_prefix,
                    start_date=start_date,
                    end_date=end_date,
                    profile_name=effective_profile,
                )
            except Exception as exc:
                if effective_profile and _looks_like_expired_sso_profile_error(exc):
                    records = provider_checker.load_records_from_s3_by_date_range(
                        bucket=bucket,
                        prefix=converted_prefix,
                        start_date=start_date,
                        end_date=end_date,
                        profile_name=None,
                    )
                    effective_profile = None
                    return records
                raise

        try:
            converted_records = _load_converted_records_with_profile_fallback()
        except Exception as exc:
            return jsonify({"error": f"Failed to load converted records from S3: {exc}"}), 400

        if not converted_records:
            return jsonify({
                "error": (
                    "No converted records found in S3 for the selected date range. "
                    f"prefix='{converted_prefix}', bucket='{bucket}', range={start_date}..{end_date}."
                )
            }), 400

        class _GroupingMemoryFile:
            def __init__(self, filename: str, payload: Any) -> None:
                self.filename = filename
                self._bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            def read(self) -> bytes:
                return self._bytes

        grouped_payloads: dict[str, list[dict[str, Any]]] = {}
        for key, payload in converted_records:
            if not isinstance(payload, dict):
                continue
            base_key = str(key).split("#", 1)[0]
            grouped_payloads.setdefault(base_key, []).append(payload)

        file_objects: list[Any] = [
            _GroupingMemoryFile(filename=filename, payload=payloads)
            for filename, payloads in grouped_payloads.items()
        ]

        episodes, debug_info = parse_episode_files_with_debug(file_objects)
        if not episodes:
            return jsonify({
                "error": (
                    "No episode records with series linkage were found in converted S3 metadata. "
                    "Verify converted_prefix points to episodic EPG JSON (for example se.viaplay.crime), "
                    "and inspect the debug payload for why records were filtered out."
                ),
                "debug": debug_info,
            }), 422

        guids = [ep.guid for ep in episodes]
        results = client.check_guids(guids)
        report = build_report(episodes, results)
        report["s3_context"] = {
            "bucket": bucket,
            "converted_prefix": converted_prefix,
            "start_date": start_date,
            "end_date": end_date,
            "provider_id": provider_id,
            "aws_profile_requested": aws_profile,
            "aws_profile_used": effective_profile,
            "source_records": len(converted_records),
        }
        return jsonify(report)

    @app.route("/api/check/image-source", methods=["POST"])
    def api_check_image_source() -> Any:
        """
        Live cross-source check for findings that may be false positives due
        to IG's content-matcher merging multiple provider sources (EPG,
        SVOD, deeplink-VOD, ...) into one universal media:

          - MISSING_16X9_IMAGE / MISSING_2X3_IMAGE -> pass `ratio` (e.g. "2:3")
          - MISSING_TITLE / MISSING_DESCRIPTION / MISSING_GENRE / UNMAPPED_GENRE
            -> pass `field` ("title" | "description" | "genre")

        If the missing ratio/field is present via a *different* matched
        source, GraphQL/the media page will still show it and the finding is
        a per-source gap rather than a real end-user-visible problem.

        Accepts either a refresh_token (exchanged + cached, same as the
        grouping check) or an already-exchanged access_token.
        """
        content_id: str = (request.form.get("content_id") or "").strip()
        ratio: str = (request.form.get("ratio") or "").strip()
        field: str = (request.form.get("field") or "").strip().lower()
        country_code: str = (request.form.get("country_code") or "SE").strip().upper() or "SE"
        refresh_token: str = (request.form.get("refresh_token") or "").strip()
        access_token: str = (request.form.get("access_token") or "").strip()

        if not content_id:
            return jsonify({"error": "content_id is required."}), 400
        if not ratio and not field:
            return jsonify({"error": "Either ratio or field is required."}), 400
        if field and field not in ("title", "description", "genre"):
            return jsonify({"error": f"Unsupported field '{field}'."}), 400

        try:
            if access_token:
                token = access_token
            elif refresh_token:
                token = _grouping_get_or_exchange_access_token(refresh_token)
            else:
                return jsonify({"error": "Please provide an Inspector Gadget refresh token."}), 400
        except Exception as exc:
            return jsonify({
                "error": (
                    "Token is invalid or expired — get a fresh one from Inspector Gadget "
                    f"and try again. Details: {exc}"
                )
            }), 401

        client = GroupingApiClient(access_token=token, country_code=country_code)
        try:
            if field:
                result = client.check_field_presence(token, content_id, field)
            else:
                result = client.check_image_ratio(token, content_id, ratio)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 502
            if status in (401, 403):
                return jsonify({
                    "error": "Inspector Gadget rejected the access token (expired?). Please re-authenticate."
                }), 401
            return jsonify({"error": f"Inspector Gadget returned HTTP {status}."}), 502
        except Exception as exc:
            return jsonify({"error": f"Request to Inspector Gadget failed: {exc}"}), 502

        return jsonify(result)

    return app


if __name__ == "__main__":
    app = create_app()
    # threaded=True lets the dev server handle multiple requests concurrently
    # (token exchange, grouping-check chunks, etc.) instead of queuing them
    # one-at-a-time on a single worker thread.
    app.run(debug=True, host="127.0.0.1", port=5000, threaded=True)
