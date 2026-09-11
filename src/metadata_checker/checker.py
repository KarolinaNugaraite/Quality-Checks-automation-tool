from __future__ import annotations

import json
import random
import re
from datetime import datetime, date
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .s3_io import list_sampled_json_keys, read_json_objects, list_json_keys_by_date_range



# Canonical readable alert codes
CHECK_ID_TO_ERROR_CODE = {
    # Original checks
    "orig_title_presence": "MISSING_TITLE",  # no title found in content
    "orig_description_presence": "MISSING_DESCRIPTION",  # descriptions missing at all content levels
    "orig_required_languages": "INVALID_LANGUAGE_CODE",  # language code fails validation
    "orig_description_too_short": "DESCRIPTION_TOO_SHORT",  # description is too short
    "orig_description_placeholder": "DESCRIPTION_TOO_SHORT",  # description is too short (placeholder)
    "orig_image_type_invalid": "UNMAPPED_IMAGE_TYPE",  # image name not in the image map
    "orig_image_scope_invalid": "UNMAPPED_IMAGE_TYPE",  # image scope type must be content/season/series
    "orig_image_value_missing": "MISSING_MANDATORY_ATTRIBUTES",  # image URL/value is required
    "orig_missing_image_ratio": "MISSING_16X9_IMAGE",  # no 16:9 showcard image
    "orig_images_missing": "MISSING_16X9_IMAGE",  # no 16:9 showcard image (closest match)
    "orig_genre_presence": "MISSING_GENRE",  # genre missing at all content levels
    "orig_hierarchy_genre_presence": "MISSING_GENRE",  # genre missing at all content levels
    "orig_kids_genre_missing": "UNMAPPED_GENRE",  # VOD genre string not in the genre map
    "orig_title_language_missing_fi": "SCHEMA_ERROR",  # original title languages must include fi for series-id generation
    "orig_production_year_missing": "MISSING_RELEASE_YEAR",  # no production year or release date
    "orig_duration_invalid": "EXCESSIVE_ASSET_DURATION",  # asset duration is unusually long
    "orig_age_rating_missing": "MISSING_AGE_RATING",  # no age rating value present
    "orig_deeplink_missing": "VOD_MISSING_DEEPLINKS",  # deeplinks missing on deeplink-type VOD
    "orig_episode_count_outlier": "EXCESSIVE_ASSET_DURATION",  # closest: asset duration is unusually long
    "orig_group_genre_coverage": "MISSING_GENRE",  # genre missing at all content levels
    "orig_event_future": "SCHEMA_ERROR",  # event starts after reference date (info only)
    "orig_empty_metadata": "SCHEMA_ERROR",  # metadata record is empty or contains only system fields
    "orig_schedule_required_field_missing": "SCHEMA_ERROR",  # required schedule field is missing
    "orig_schedule_original_channel_id_invalid": "SCHEMA_ERROR",  # originalChannelId is outside allowed values
    "orig_schedule_adapter_id_invalid": "SCHEMA_ERROR",  # adapterId does not match expected adapter
    "orig_envelope_required_field_missing": "MISSING_MANDATORY_ATTRIBUTES",
    "orig_content_id_missing": "MISSING_CONTENT_ID",
    "orig_broadcast_required_field_missing": "MISSING_MANDATORY_ATTRIBUTES",
    "orig_broadcast_id_missing": "MISSING_BROADCAST_ID",
    "orig_channel_id_missing": "MISSING_CHANNEL_ID",
    "orig_event_required_field_missing": "MISSING_MANDATORY_ATTRIBUTES",
    "orig_duplicate_broadcast_id": "DUPLICATE_BROADCAST_ID",
    "orig_broadcast_content_reference_invalid": "INVALID_BROADCAST_CONTENT_REFERENCE",
    "orig_epg_from_to_dates_invalid": "INVALID_EPG_FROM_TO_DATES",
    "orig_epg_to_date_format_invalid": "INVALID_EPG_TO_DATE_FORMAT",
    "orig_broadcast_gap_between": "EPG_GAP_BETWEEN_BROADCASTS",
    "orig_broadcast_overlap_start": "EPG_OVERLAPPING_START",
    "orig_broadcast_overlap_end": "EPG_OVERLAPPING_END",
    "orig_broadcast_rerun_live_premiere_conflict": "BROADCAST_RERUN_LIVE_PREMIERE_CONFLICT",
    "orig_schedule_duration_exceeds_24h": "SCHEDULE_DURATION_EXCEEDS_24_HOURS",
    "orig_image_url_invalid": "INVALID_IMAGE_URL",
    "orig_image_dimensions_invalid": "INVALID_IMAGE_DIMENSIONS",
    "orig_country_code_invalid": "INVALID_COUNTRY_CODE",
    "orig_language_code_invalid": "INVALID_LANGUAGE_CODE",
    "orig_vod_deeplinks_for_non_deeplink": "VOD_DEEPLINKS_FOR_NON_DEEPLINK_SOURCE_TYPE",
    "orig_vod_id_invalid": "INVALID_VOD_ID",
    "orig_publishing_rights_missing": "MISSING_PUBLISHING_OR_BROADCAST_RIGHTS",
    "orig_publishing_no_valid_targets": "PUBLISHING_INFO_NO_VALID_TARGETS",

    # Converted checks
    "conv_no_original_match": "COULD_NOT_FIND_MATCHING_PRODUCT",  # no product config matches the asset
    "conv_title_mapping": "MISSING_TITLE",  # no title found in content
    "conv_description_mapping": "MISSING_DESCRIPTION",  # descriptions missing at all content levels
    "conv_language_mapping": "INVALID_LANGUAGE_CODE",  # language code fails validation
    "conv_image_ratio_mapping": "MISSING_16X9_IMAGE",  # no 16:9 showcard image
    "conv_image_type_mapping": "UNMAPPED_IMAGE_TYPE",  # image name not in the image map
    "conv_genre_mapping": "UNMAPPED_GENRE",  # VOD genre string not in the genre map
    "conv_hierarchy_genre_missing": "MISSING_GENRE",  # genre missing at all content levels
    "conv_kids_not_first": "UNMAPPED_GENRE",  # closest: VOD genre string not in the genre map
    "conv_production_year_mapping": "MISSING_RELEASE_YEAR",  # no production year or release date
    "conv_duration_mapping": "EXCESSIVE_ASSET_DURATION",  # asset duration is unusually long
    "conv_age_rating_missing": "MISSING_AGE_RATING",
    "conv_production_year_missing": "MISSING_PRODUCTION_YEAR",
    "conv_credits_missing": "MISSING_CREDITS",
    "conv_deeplink_mapping": "VOD_MISSING_DEEPLINKS",  # deeplinks missing on deeplink-type VOD
    "conv_special_chars": "TEXT_ENCODING_ISSUE",  # mojibake/replacement-char detection
    "conv_event_expired": "EXPIRED_CONTENT",  # event is expired on reference date
    "conv_event_not_yet_valid": "NOT_YET_VALID_CONTENT",  # event is not yet valid on reference date
    "conv_schedule_required_field_missing": "SCHEMA_ERROR",  # required schedule field is missing
    "conv_schedule_original_channel_id_invalid": "SCHEMA_ERROR",  # originalChannelId is outside allowed values
    "conv_schedule_adapter_id_invalid": "SCHEMA_ERROR",  # adapterId does not match expected adapter
    "conv_envelope_required_field_missing": "MISSING_MANDATORY_ATTRIBUTES",
    "conv_content_id_missing": "MISSING_CONTENT_ID",
    "conv_broadcast_required_field_missing": "MISSING_MANDATORY_ATTRIBUTES",
    "conv_broadcast_id_missing": "MISSING_BROADCAST_ID",
    "conv_channel_id_missing": "MISSING_CHANNEL_ID",
    "conv_event_required_field_missing": "MISSING_MANDATORY_ATTRIBUTES",
    "conv_duplicate_broadcast_id": "DUPLICATE_BROADCAST_ID",
    "conv_broadcast_content_reference_invalid": "INVALID_BROADCAST_CONTENT_REFERENCE",
    "conv_epg_from_to_dates_invalid": "INVALID_EPG_FROM_TO_DATES",
    "conv_epg_to_date_format_invalid": "INVALID_EPG_TO_DATE_FORMAT",
    "conv_unexpected_content_shape": "UNEXPECTED_CONTENT_SHAPE",  # channel content type violates configured expectation (e.g. series on a movies-only channel)
    "conv_broadcast_gap_between": "EPG_GAP_BETWEEN_BROADCASTS",
    "conv_broadcast_overlap_start": "EPG_OVERLAPPING_START",
    "conv_broadcast_overlap_end": "EPG_OVERLAPPING_END",
    "conv_broadcast_rerun_live_premiere_conflict": "BROADCAST_RERUN_LIVE_PREMIERE_CONFLICT",
    "conv_schedule_duration_exceeds_24h": "SCHEDULE_DURATION_EXCEEDS_24_HOURS",
    "conv_image_url_invalid": "INVALID_IMAGE_URL",
    "conv_image_dimensions_invalid": "INVALID_IMAGE_DIMENSIONS",
    "conv_country_code_invalid": "INVALID_COUNTRY_CODE",
    "conv_language_code_invalid": "INVALID_LANGUAGE_CODE",
    "conv_vod_deeplinks_for_non_deeplink": "VOD_DEEPLINKS_FOR_NON_DEEPLINK_SOURCE_TYPE",
    "conv_vod_id_invalid": "INVALID_VOD_ID",
    "conv_publishing_rights_missing": "MISSING_PUBLISHING_OR_BROADCAST_RIGHTS",
    "conv_publishing_no_valid_targets": "PUBLISHING_INFO_NO_VALID_TARGETS",
}


def _load_internal_genres() -> set[str]:
    contract_path = Path(__file__).with_name("genre_contract.json")
    try:
        data = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    values = data.get("enum") if isinstance(data, dict) else None
    if not isinstance(values, list):
        return set()
    return {str(v).strip().upper() for v in values if isinstance(v, str) and str(v).strip()}


INTERNAL_GENRES = _load_internal_genres()

ISO639_1_CODES: set[str] = {
    "ar", "bg", "bn", "cs", "da", "de", "el", "en", "es", "et", "fa", "fi", "fr", "he",
    "hi", "hr", "hu", "id", "is", "it", "ja", "ko", "lt", "lv", "ms", "nl", "no", "pl",
    "pt", "ro", "ru", "se", "sk", "sl", "sq", "sr", "sv", "th", "tr", "uk", "ur", "vi", "zh",
}

ISO639_2_TO_1: dict[str, str] = {
    "dan": "da",
    "eng": "en",
    "fin": "fi",
    "swe": "sv",
    "nor": "no",
    "nob": "no",
    "nno": "no",
    "sme": "se",
    "se": "sv",
    "dk": "da",
    "deu": "de",
    "ger": "de",
    "fra": "fr",
    "fre": "fr",
    "spa": "es",
    "ita": "it",
    "rus": "ru",
    "est": "et",
    "lav": "lv",
    "lit": "lt",
    "pol": "pl",
}

# Full ISO 3166-1 alpha-2 code set (all 249 assigned codes), not just the
# markets this project's providers commonly deal with — movies/content can
# legitimately come from any country, so the check must not under-recognize codes.
ISO3166_1_ALPHA2_CODES: set[str] = {
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX",
    "AZ", "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ",
    "BR", "BS", "BT", "BV", "BW", "BY", "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK",
    "CL", "CM", "CN", "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM",
    "DO", "DZ", "EC", "EE", "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK", "FM", "FO", "FR",
    "GA", "GB", "GD", "GE", "GF", "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS",
    "GT", "GU", "GW", "GY", "HK", "HM", "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN",
    "IO", "IQ", "IR", "IS", "IT", "JE", "JM", "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN",
    "KP", "KR", "KW", "KY", "KZ", "LA", "LB", "LC", "LI", "LK", "LR", "LS", "LT", "LU", "LV",
    "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK", "ML", "MM", "MN", "MO", "MP", "MQ",
    "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA", "NC", "NE", "NF", "NG", "NI",
    "NL", "NO", "NP", "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG", "PH", "PK", "PL", "PM",
    "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW", "SA", "SB", "SC",
    "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS", "ST", "SV",
    "SX", "SY", "SZ", "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO", "TR",
    "TT", "TV", "TW", "TZ", "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI",
    "VN", "VU", "WF", "WS", "YE", "YT", "ZA", "ZM", "ZW",
}

# Maps common full English country names (as seen in raw provider feeds, e.g.
# "Russian Federation" instead of "RU") to their ISO 3166-1 alpha-2 code, so the
# country-code check validates the real value instead of flagging non-code formats.
COUNTRY_NAME_TO_ALPHA2: dict[str, str] = {
    "united states": "US", "united states of america": "US", "usa": "US",
    "united kingdom": "GB", "great britain": "GB", "uk": "GB",
    "russian federation": "RU", "russia": "RU",
    "south korea": "KR", "korea, republic of": "KR", "republic of korea": "KR",
    "north korea": "KP", "democratic people's republic of korea": "KP",
    "czech republic": "CZ", "czechia": "CZ",
    "iran": "IR", "iran, islamic republic of": "IR",
    "vietnam": "VN", "viet nam": "VN",
    "syria": "SY", "syrian arab republic": "SY",
    "laos": "LA", "lao people's democratic republic": "LA",
    "moldova": "MD", "republic of moldova": "MD",
    "tanzania": "TZ", "united republic of tanzania": "TZ",
    "bolivia": "BO", "plurinational state of bolivia": "BO",
    "venezuela": "VE", "bolivarian republic of venezuela": "VE",
    "brunei": "BN", "brunei darussalam": "BN",
    "ivory coast": "CI", "cote d'ivoire": "CI", "côte d'ivoire": "CI",
    "cape verde": "CV",
    "swaziland": "SZ", "eswatini": "SZ",
    "macedonia": "MK", "north macedonia": "MK",
    "hong kong": "HK", "taiwan": "TW",
    "palestine": "PS", "state of palestine": "PS",
    "finland": "FI", "sweden": "SE", "norway": "NO", "denmark": "DK", "iceland": "IS",
    "germany": "DE", "france": "FR", "spain": "ES", "italy": "IT", "poland": "PL",
    "netherlands": "NL", "belgium": "BE", "austria": "AT", "switzerland": "CH",
    "portugal": "PT", "greece": "GR", "ireland": "IE", "hungary": "HU", "romania": "RO",
    "bulgaria": "BG", "croatia": "HR", "slovenia": "SI", "slovakia": "SK",
    "estonia": "EE", "latvia": "LV", "lithuania": "LT", "ukraine": "UA", "turkey": "TR",
    "japan": "JP", "china": "CN", "india": "IN", "brazil": "BR", "mexico": "MX",
    "canada": "CA", "australia": "AU", "new zealand": "NZ", "south africa": "ZA",
    "egypt": "EG", "saudi arabia": "SA", "israel": "IL", "thailand": "TH",
    "philippines": "PH", "malaysia": "MY", "indonesia": "ID", "singapore": "SG",
    "argentina": "AR", "chile": "CL", "colombia": "CO", "peru": "PE",
}


def _normalize_country_value(raw: str) -> str:
    """Resolve a raw country value (code or full name) to its ISO alpha-2 code."""
    upper = raw.upper()
    if upper in ISO3166_1_ALPHA2_CODES:
        return upper
    mapped = COUNTRY_NAME_TO_ALPHA2.get(raw.strip().lower())
    return mapped or upper


def _errors_hash(strings: list[str]) -> str:
    """Mirror metadata-ingest alerts errorsHash implementation."""
    hash_value = 0
    for full_text in strings:
        normalized = re.sub(r"[^a-zA-Z]", "", full_text.lower())
        for ch in normalized:
            hash_value = (hash_value << 5) - hash_value + ord(ch)
            # Convert to signed 32-bit integer after each step.
            hash_value &= 0xFFFFFFFF
            if hash_value >= 0x80000000:
                hash_value -= 0x100000000

    # Uint32 conversion + base36
    unsigned_value = hash_value & 0xFFFFFFFF
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if unsigned_value == 0:
        return "0"

    out = ""
    while unsigned_value > 0:
        unsigned_value, rem = divmod(unsigned_value, 36)
        out = digits[rem] + out
    return out

# IG-style severity mapping
SEVERITY_MAP = {
    "high": "ERROR",
    "medium": "WARNING",
    "low": "INFO",
}

@dataclass
class Finding:
    check_id: str
    severity: str
    message: str
    file_ref: str
    content_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        # Canonical error code mapping
        error_code = CHECK_ID_TO_ERROR_CODE.get(self.check_id)

        # Severity mapping by error code/message (from user tables)
        # Default to Error if not specified
        code_severity = {
            # Errors (blocking)
            "SCHEMA_ERROR": "ERROR",
            "MISSING_TITLE": "ERROR",
            "COULD_NOT_FIND_MATCHING_PRODUCT": "ERROR",
            "INVALID_LANGUAGE_CODE": "ERROR",
            "INVALID_IMAGE_URL": "ERROR",
            "INVALID_IMAGE_DIMENSIONS": "ERROR",
            "INVALID_VALID_FROM_TO_DATES": "ERROR",
            "MISSING_MANDATORY_ATTRIBUTES": "ERROR",
            "MISSING_CONTENT_ID": "ERROR",
            "MISSING_BROADCAST_ID": "ERROR",
            "MISSING_CHANNEL_ID": "ERROR",
            "DUPLICATE_BROADCAST_ID": "ERROR",
            "INVALID_BROADCAST_CONTENT_REFERENCE": "ERROR",
            "INVALID_EPG_FROM_TO_DATES": "ERROR",
            "INVALID_EPG_TO_DATE_FORMAT": "ERROR",
            "PUBLISHING_INFO_NO_VALID_TARGETS": "ERROR",
            "MISSING_PUBLISHING_OR_BROADCAST_RIGHTS": "ERROR",
            "MISSING_PROTOCOLS": "ERROR",
            "INVALID_VOD_ID": "ERROR",
            "INVALID_COUNTRY_CODE": "ERROR",
            "INVALID_VALID_FROM_DATE_FORMAT": "ERROR",
            "INVALID_VALID_TO_DATE_FORMAT": "ERROR",
            "INVALID_RELEASE_DATE_FORMAT": "ERROR",
            "VOD_DEEPLINKS_FOR_NON_DEEPLINK_SOURCE_TYPE": "ERROR",
            "VOD_MISSING_DEEPLINKS": "ERROR",
            "INVALID_PUBLISHING_INFO_REFERENCE": "ERROR",

            # Warnings (non-blocking)
            "DESCRIPTION_TOO_SHORT": "WARNING",
            "INVALID_IMAGE_ASPECT_RATIO": "WARNING",
            "INVALID_TARGET_TYPE": "WARNING",
            "CONTENT_NO_16X9_SHOWCARD": "WARNING",
            "MISSING_GENRE": "WARNING",
            "MISSING_DESCRIPTION": "WARNING",
            "MISSING_KEY_LANGUAGE_DESCRIPTION": "WARNING",
            "MISSING_16X9_IMAGE": "WARNING",
            "MISSING_RELEASE_YEAR": "WARNING",
            "MISSING_2X3_IMAGE": "WARNING",
            "TEXT_ENCODING_ISSUE": "WARNING",
            "NUMERIC_EPISODE_TITLE": "WARNING",
            "EXCESSIVE_ASSET_DURATION": "WARNING",
            "MISSING_AGE_RATING": "WARNING",
            "MISSING_PRODUCTION_YEAR": "WARNING",
            "MISSING_CREDITS": "WARNING",
            "EPG_GAP_BETWEEN_BROADCASTS": "WARNING",
            "EPG_OVERLAPPING_START": "WARNING",
            "EPG_OVERLAPPING_END": "WARNING",
            "BROADCAST_RERUN_LIVE_PREMIERE_CONFLICT": "WARNING",
            "SCHEDULE_DURATION_EXCEEDS_24_HOURS": "WARNING",
        }

        # Info (non-blocking, no errorCode)
        info_messages = [
            "Skipping rating system",
            "Load summary",
        ]

        # Special: findings with no errorCode, only message and severity
        no_code_check_ids = [
            # From viaplay-deeplink provider
            "no_validity_found",  # No validity found for asset
            "unexpected_validity_category",  # Expected validity category 'subscription', but found …
            "unmapped_genre",  # Unmapped genre
            "unmapped_sports_genre",  # Unmapped sports genre
            "unknown_platform",  # Unknown platform
            "unmapped_image_type",  # Unmapped image type
            "failed_to_resolve_season",  # Failed to resolve season/series for episode
            "failed_to_resolve_series",  # Failed to resolve season/series for episode
            "bbfc_workaround_applied",  # BBFC age rating workaround applied
            "skipping_rating_system",  # Skipping rating system (info)
            "load_summary",  # Load summary (info)
            # Loader/exception cases
            "could_not_find_matching_product",  # Could not find matching product for asset
            "vod_loader_crashed",  # VOD/Event Loader lambda crashed
            "event_loader_crashed",  # VOD/Event Loader lambda crashed
            "unhandled_converter_exception",  # Unhandled converter exception
        ]

        # If this finding is info-only (no errorCode)
        if self.check_id in no_code_check_ids or any(msg in self.message for msg in info_messages):
            return {
                "check_id": self.check_id,
                "severity": "INFO" if any(msg in self.message for msg in info_messages) else "WARNING",
                "message": self.message,
                "file_ref": self.file_ref,
                "content_id": self.content_id,
            }

        # If we have a canonical error code, use mapped severity if present
        if error_code:
            severity = code_severity.get(error_code, "ERROR")
            return {
                "check_id": self.check_id,
                "errorCode": error_code,
                "severity": severity,
                "message": self.message,
                "file_ref": self.file_ref,
                "content_id": self.content_id,
            }

        # Otherwise, fallback to original logic (hash code, default severity)
        error_code = _errors_hash([self.message])
        ig_severity = SEVERITY_MAP.get(self.severity, self.severity.upper())
        return {
            "check_id": self.check_id,
            "errorCode": error_code,
            "severity": ig_severity,
            "message": self.message,
            "file_ref": self.file_ref,
            "content_id": self.content_id,
        }


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _walk(data: Any):
    if isinstance(data, dict):
        for key, value in data.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(data, list):
        for item in data:
            yield from _walk(item)


def _get_by_path(data: Any, dotted_path: str) -> Any:
    parts = dotted_path.split(".")
    node = data
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


# _find_values_by_key() is called many times per record (once per candidate
# field per check) and a naive implementation re-walks the whole record every
# time, which dominates runtime on large batches. Instead, walk each distinct
# record/payload object once and cache every key -> values it contains,
# keyed by object identity. Callers are responsible for clearing this between
# records (see run_original_checks/run_converted_checks) since a record's
# lifetime ends there and ids can otherwise be reused after garbage collection.
_KEY_INDEX_CACHE: dict[int, dict[str, list[Any]]] = {}


def _clear_key_index_cache() -> None:
    _KEY_INDEX_CACHE.clear()


def _find_values_by_key(data: Any, key_name: str) -> list[Any]:
    index = _KEY_INDEX_CACHE.get(id(data))
    if index is None:
        index = {}
        for key, value in _walk(data):
            index.setdefault(key, []).append(value)
        _KEY_INDEX_CACHE[id(data)] = index
    return list(index.get(key_name, []))


def _first_non_empty(data: dict[str, Any], candidates: list[str]) -> Any:
    for candidate in candidates:
        if "." in candidate:
            val = _get_by_path(data, candidate)
            if not _is_empty(val):
                return val
        else:
            values = _find_values_by_key(data, candidate)
            for val in values:
                if not _is_empty(val):
                    return val
    return None


def _first_value(data: dict[str, Any], candidates: list[str]) -> Any:
    for candidate in candidates:
        if "." in candidate:
            val = _get_by_path(data, candidate)
            if val is not None:
                return val
        else:
            values = _find_values_by_key(data, candidate)
            if values:
                return values[0]
    return None


def _first_non_empty_shallow(data: dict[str, Any], candidates: list[str]) -> Any:
    """Prefer top-level fields; only follow explicit dotted paths."""
    for candidate in candidates:
        if "." in candidate:
            val = _get_by_path(data, candidate)
            if not _is_empty(val):
                return val
        elif candidate in data and not _is_empty(data[candidate]):
            return data[candidate]
    return None


def _first_value_shallow(data: dict[str, Any], candidates: list[str]) -> Any:
    """Return first top-level value (or explicit dotted path), even if empty."""
    for candidate in candidates:
        if "." in candidate:
            val = _get_by_path(data, candidate)
            if val is not None:
                return val
        elif candidate in data:
            return data[candidate]
    return None


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return re.sub(r"\s+", " ", value).strip()


def _normalize_contract_genre(value: Any) -> str:
    text = _normalize_text(value).upper()
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]+", "_", text).strip("_")


def _normalize_language_code(value: Any) -> str:
    code = _normalize_text(value).lower()
    if not code:
        return ""
    # Keep only the primary subtag (sv-SE -> sv, swe_SE -> swe).
    primary = re.split(r"[-_]", code, maxsplit=1)[0]
    return ISO639_2_TO_1.get(primary, primary)


def _lang_map(data: dict[str, Any], description_block_keys: list[str]) -> dict[str, str]:
    block = _first_value(data, description_block_keys)
    if isinstance(block, dict):
        out = {}
        for k, v in block.items():
            if isinstance(v, str):
                lang = _normalize_language_code(k)
                if lang:
                    out[lang] = v
        return out

    if isinstance(block, list):
        out = {}
        for item in block:
            if isinstance(item, dict):
                lang = item.get("lang") or item.get("language") or item.get("locale")
                text = item.get("value") or item.get("text") or item.get("description")
                if isinstance(lang, str) and isinstance(text, str):
                    normalized_lang = _normalize_language_code(lang)
                    if normalized_lang:
                        out[normalized_lang] = text
        return out

    return {}


def _guess_type(record: dict[str, Any], type_keys: list[str]) -> str:
    # Prefer top-level or explicit dotted keys to avoid matching nested objects
    # like image.@type=ImageObject when classifying the content record type.
    value = _first_non_empty_shallow(record, type_keys)
    if value is None:
        value = _first_non_empty(record, type_keys)

    val = _normalize_text(value).lower()
    if "movie" in val or val == "film":
        return "movie"
    if "series" in val or "show" in val:
        return "series"
    if "season" in val:
        return "season"
    if "episode" in val:
        return "episode"
    if "event" in val:
        return "event"

    # Heuristic fallback for event-like source payloads that miss @type.
    if any(
        key in record
        for key in ("startDate", "endDate", "actionAccessSpecification", "isMainEvent", "sport")
    ):
        return "event"

    if not val:
        return "unknown"

    return val


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (ValueError, TypeError):
        return None


def _id_aliases(value: Any) -> list[str]:
    """Generate comparable ID aliases for matching across source/converted schemas."""
    text = _normalize_text(value)
    if not text:
        return []

    aliases: list[str] = [text]

    # Provider IDs can be namespaced, e.g. "viaplay.deeplink-vod-fi.content.20235953".
    # Keep the trailing token as an alias to match source GUID "20235953".
    if "." in text:
        tail = text.split(".")[-1].strip()
        if tail:
            aliases.append(tail)

    # Also accept common separators used by IDs.
    for sep in ("/", ":"):
        if sep in text:
            tail = text.split(sep)[-1].strip()
            if tail:
                aliases.append(tail)

    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for item in aliases:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _calculate_ratio_from_dimensions(width: Any, height: Any) -> str | None:
    """Calculate aspect ratio (e.g., '16:9') from width and height values."""
    w = _safe_int(width)
    h = _safe_int(height)
    if w is None or h is None or w <= 0 or h <= 0:
        return None

    from math import gcd
    divisor = gcd(w, h)
    ratio_w = w // divisor
    ratio_h = h // divisor
    return f"{ratio_w}:{ratio_h}"


def _unwrap_dimension_value(value: Any) -> Any:
    """Unwrap common dimension wrappers like {value: 1920} or nested CanonicalValue shapes."""
    current = value
    seen: set[int] = set()

    while isinstance(current, dict):
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)

        if "value" in current:
            current = current["value"]
            continue
        if "@value" in current:
            current = current["@value"]
            continue
        break

    return current


def _extract_ratio_from_url(value: Any) -> str:
    """Extract aspect ratio from explicit width/height numbers embedded in image URLs or templates."""
    text = _normalize_text(value)
    if not text:
        return ""

    width_match = re.search(r"(?:[?&]|^)width=(\d+)(?:[&#]|$)", text)
    height_match = re.search(r"(?:[?&]|^)height=(\d+)(?:[&#]|$)", text)
    if width_match and height_match:
        return _calculate_ratio_from_dimensions(width_match.group(1), height_match.group(1)) or ""

    return ""


def _normalize_ratio_text(value: Any) -> str:
    """Normalize ratio-like strings (e.g., 16:9, 16/9, 1920x1080) into a:b."""
    text = _normalize_text(value)
    if not text:
        return ""

    match = re.match(r"^\s*(\d+)\s*[:xX/×]\s*(\d+)\s*$", text)
    if not match:
        return ""

    w = _safe_int(match.group(1))
    h = _safe_int(match.group(2))
    if w is None or h is None:
        return ""

    return _calculate_ratio_from_dimensions(w, h) or ""


def _extract_deeplink_from_potential_action(record: dict[str, Any]) -> str | None:
    """Extract deeplink from potentialAction URLs (Viaplay schema)."""
    potential_action = record.get("potentialAction")
    if isinstance(potential_action, list):
        for action in potential_action:
            if isinstance(action, dict):
                targets = action.get("target")
                if isinstance(targets, list):
                    for target in targets:
                        if isinstance(target, dict):
                            url = target.get("urlTemplate") or target.get("url")
                            if isinstance(url, str) and url.strip():
                                return url
    return None


def _primary_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Use nested content payload when present (converted schema), else record itself."""
    # Converted VOD schema: {content: {...}}
    content = record.get("content")
    if isinstance(content, dict):
        return content
    # Converted EPG schema: {contents: [{titles, descriptions, images, genres, ...}]}
    contents = record.get("contents")
    if isinstance(contents, list) and contents and isinstance(contents[0], dict):
        return contents[0]
    return record


def _extract_title_canonical(record: dict[str, Any]) -> str:
    payload = _primary_payload(record)
    titles = payload.get("titles")
    if isinstance(titles, list):
        preferred = None
        fallback = None
        for item in titles:
            if not isinstance(item, dict):
                continue
            value = _normalize_text(item.get("value"))
            if not value:
                continue
            title_type = _normalize_text(item.get("type")).upper()
            if title_type == "FULL":
                preferred = value
                break
            if fallback is None:
                fallback = value
        if preferred:
            return preferred
        if fallback:
            return fallback

    # Handle flat list of titles (e.g. from XML parser that produces a list per element).
    for fallback_key in (
        "title",
        "name",
        "name/original",
        "data.name",
        "related.product.press_sheets.translated_title",
    ):
        raw = payload.get(fallback_key)
        if raw is None and "." in fallback_key:
            raw = _get_by_path(payload, fallback_key)
        if raw is None and "." in fallback_key:
            raw = _get_by_path(record, fallback_key)
        if raw is None:
            continue
        if isinstance(raw, list):
            for item in raw:
                text = _normalize_text(item)
                if text:
                    return text
        else:
            text = _normalize_text(raw)
            if text:
                return text
    return ""


def _extract_title_languages(record: dict[str, Any], title_type: str | None = None) -> list[str]:
    payload = _primary_payload(record)
    raw_titles = payload.get("titles")
    languages: list[str] = []

    if isinstance(raw_titles, list):
        for item in raw_titles:
            if not isinstance(item, dict):
                continue
            item_type = _normalize_text(item.get("type") or item.get("titleType")).upper()
            if title_type and item_type != title_type.upper():
                continue
            language = _normalize_text(
                item.get("lang")
                or item.get("language")
                or item.get("locale")
                or item.get("languageCode")
            ).lower()
            if language:
                # Normalize ISO-639-2/locale forms so fin/swe/etc. match fi/sv checks.
                languages.append(_normalize_language_code(language))
    elif isinstance(raw_titles, dict):
        for language, value in raw_titles.items():
            if isinstance(value, str) and value.strip():
                # Normalize map-style language keys as well.
                languages.append(_normalize_language_code(language))

    seen: set[str] = set()
    out: list[str] = []
    for language in languages:
        if language not in seen:
            seen.add(language)
            out.append(language)
    return out


def _extract_description_canonical(record: dict[str, Any]) -> str:
    payload = _primary_payload(record)
    direct = _normalize_text(payload.get("description") or payload.get("shortDescription"))
    if direct:
        return direct

    def _collect_parent_containers(node: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(node, dict):
            return out
        for key in ("series", "season", "partOfSeries", "partOfSeason"):
            nested = node.get(key)
            if isinstance(nested, dict):
                out.append(nested)
        return out

    def _pick_from_description_block(block: Any) -> str:
        if isinstance(block, list):
            preferred = None
            fallback = None
            for item in block:
                if not isinstance(item, dict):
                    continue
                value = _normalize_text(item.get("value") or item.get("description") or item.get("text"))
                if not value:
                    continue
                desc_type = _normalize_text(item.get("type") or item.get("descriptionType")).upper()
                if desc_type in {"MEDIUM", "SHORT", "FULL"} and preferred is None:
                    preferred = value
                if fallback is None:
                    fallback = value
            return preferred or fallback or ""

        if isinstance(block, dict):
            preferred = None
            fallback = None
            for value in block.values():
                text_value = ""
                if isinstance(value, str):
                    text_value = _normalize_text(value)
                elif isinstance(value, dict):
                    text_value = _normalize_text(
                        value.get("value") or value.get("description") or value.get("text")
                    )
                if not text_value:
                    continue
                if preferred is None:
                    preferred = text_value
                if fallback is None:
                    fallback = text_value
            return preferred or fallback or ""

        return ""

    containers = [payload]
    containers.extend(_collect_parent_containers(payload))

    if payload is not record and isinstance(record, dict):
        containers.append(record)
        containers.extend(_collect_parent_containers(record))

    # De-duplicate containers while preserving order.
    deduped_containers: list[dict[str, Any]] = []
    seen_containers: set[int] = set()
    for container in containers:
        marker = id(container)
        if marker in seen_containers:
            continue
        seen_containers.add(marker)
        deduped_containers.append(container)

    # Check direct singular description fields on all candidate containers first.
    for container in deduped_containers:
        direct_text = _normalize_text(container.get("description") or container.get("shortDescription"))
        if direct_text:
            return direct_text

    description_blocks: list[Any] = []
    for container in deduped_containers:
        description_blocks.extend(
            [
                container.get("descriptions"),
                _get_by_path(container, "localized.descriptions"),
                _get_by_path(container, "i18n.descriptions"),
            ]
        )

    if payload is not record:
        description_blocks.extend(
            [
                _get_by_path(record, "content.series.descriptions"),
                _get_by_path(record, "content.season.descriptions"),
                _get_by_path(record, "content.partOfSeries.descriptions"),
                _get_by_path(record, "content.partOfSeason.descriptions"),
            ]
        )

    for block in description_blocks:
        text = _pick_from_description_block(block)
        if text:
            return text

    # Common provider-specific fallbacks (including TV4 nested press sheets).
    for dotted in (
        "related.product.press_sheets.long_description",
        "related.product.press_sheets.medium_description",
        "related.product.press_sheets.brief_description",
        "related.product.description",
        "data.description",
    ):
        text = _normalize_text(_get_by_path(record, dotted))
        if text:
            return text

    return ""


def _extract_description_from_container(container: dict[str, Any]) -> str:
    if not isinstance(container, dict):
        return ""

    direct = _normalize_text(container.get("description") or container.get("shortDescription"))
    if direct:
        return direct

    block = (
        container.get("descriptions")
        or _get_by_path(container, "localized.descriptions")
        or _get_by_path(container, "i18n.descriptions")
    )

    if isinstance(block, list):
        preferred = None
        fallback = None
        for item in block:
            if not isinstance(item, dict):
                continue
            value = _normalize_text(item.get("value") or item.get("description") or item.get("text"))
            if not value:
                continue
            desc_type = _normalize_text(item.get("type") or item.get("descriptionType")).upper()
            if desc_type in {"MEDIUM", "SHORT", "FULL", "LONG"} and preferred is None:
                preferred = value
            if fallback is None:
                fallback = value
        return preferred or fallback or ""

    if isinstance(block, dict):
        for value in block.values():
            if isinstance(value, str):
                text_value = _normalize_text(value)
            elif isinstance(value, dict):
                text_value = _normalize_text(
                    value.get("value") or value.get("description") or value.get("text")
                )
            else:
                text_value = ""
            if text_value:
                return text_value

    return ""


def _extract_images_canonical(record: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _primary_payload(record)

    def _as_image_list(images: Any) -> list[dict[str, Any]]:
        if isinstance(images, list):
            return [img for img in images if isinstance(img, dict)]
        if isinstance(images, dict):
            return [images]
        return []

    def _collect_parent_containers(node: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(node, dict):
            return out
        for key in ("series", "season", "partOfSeries", "partOfSeason"):
            nested = node.get(key)
            if isinstance(nested, dict):
                out.append(nested)
        return out

    containers = [payload]
    containers.extend(_collect_parent_containers(payload))
    if payload is not record and isinstance(record, dict):
        containers.append(record)
        containers.extend(_collect_parent_containers(record))

    # De-duplicate containers while preserving order.
    deduped_containers: list[dict[str, Any]] = []
    seen_containers: set[int] = set()
    for container in containers:
        marker = id(container)
        if marker in seen_containers:
            continue
        seen_containers.add(marker)
        deduped_containers.append(container)

    collected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for container in deduped_containers:
        images = _as_image_list(container.get("images") or container.get("image"))
        for image in images:
            dedupe_key = (
                _normalize_text(image.get("url") or image.get("urlTemplate") or ""),
                _normalize_text(image.get("filename") or ""),
                _normalize_text(image.get("type") or image.get("imageType") or image.get("name") or ""),
                _extract_ratio_canonical(image),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            collected.append(image)

    return collected


def _extract_ratio_canonical(img: dict[str, Any]) -> str:
    ratio = _normalize_ratio_text(img.get("ratio") or img.get("aspect") or img.get("aspectRatio"))
    if ratio:
        return ratio

    width = _unwrap_dimension_value(img.get("width"))
    height = _unwrap_dimension_value(img.get("height"))
    ratio_from_dimensions = _calculate_ratio_from_dimensions(width, height)
    if ratio_from_dimensions:
        return ratio_from_dimensions

    return (
        _extract_ratio_from_url(img.get("urlTemplate"))
        or _extract_ratio_from_url(img.get("url"))
        or _ratio_from_image_type(img)
        or ""
    )


def _ratio_from_image_type(img: dict[str, Any]) -> str:
    """Best-effort ratio fallback based on image type/name when dimensions are unavailable."""
    img_type = _normalize_text(img.get("type") or img.get("imageType") or img.get("kind") or img.get("name")).lower()
    if not img_type:
        return ""

    # Common Viaplay conventions.
    if "landscape" in img_type or "showcard" in img_type or "screenshot" in img_type:
        return "16:9"
    if "boxart" in img_type or "poster" in img_type:
        return "2:3"

    return ""


def _normalize_image_type_canonical(value: Any) -> str:
    raw = _normalize_text(value).lower()
    if not raw:
        return ""
    if "screenshot" in raw:
        return "screenshot"
    if any(token in raw for token in ["showcard", "boxart", "poster", "packshot"]):
        return "showcard"
    if any(token in raw for token in ["hero", "backdrop", "landscape"]):
        return "backdrop"
    return raw


def _normalize_image_scope_canonical(value: Any) -> str:
    raw = _normalize_text(value).lower()
    if not raw:
        return ""
    if "series" in raw:
        return "series"
    if "season" in raw:
        return "season"
    if any(token in raw for token in ["program", "content", "movie", "episode"]):
        return "content"
    return ""


def _image_requirement_applies(record: dict[str, Any], requirement: dict[str, Any], type_keys: list[str]) -> bool:
    scope = _normalize_text(requirement.get("scope")).lower()
    if not scope:
        return True

    payload = _primary_payload(record)
    record_type = _guess_type(payload if isinstance(payload, dict) else record, type_keys)
    if scope == "series":
        return record_type == "series" or isinstance(payload.get("series") if isinstance(payload, dict) else None, dict)
    if scope == "season":
        return record_type == "season" or isinstance(payload.get("season") if isinstance(payload, dict) else None, dict)
    if scope == "content":
        return True
    return True


def _image_matches_requirement(img: dict[str, Any], requirement: dict[str, Any]) -> bool:
    req_ratio = _normalize_text(requirement.get("ratio"))
    req_purpose = _normalize_text(requirement.get("purpose")).lower()
    req_scope = _normalize_text(requirement.get("scope")).lower()

    img_ratio = _extract_ratio_canonical(img)
    img_type_raw = img.get("type") or img.get("imageType") or img.get("kind") or img.get("name")
    img_purpose = _normalize_image_type_canonical(img_type_raw)
    img_scope = _normalize_image_scope_canonical(img_type_raw)

    if req_ratio and img_ratio != req_ratio:
        return False
    if req_purpose and img_purpose != req_purpose:
        return False
    if req_scope and img_scope != req_scope:
        return False
    return True


def _required_image_variants_missing(
    record: dict[str, Any],
    images: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    type_keys: list[str],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        if not _image_requirement_applies(record, requirement, type_keys):
            continue
        if not any(_image_matches_requirement(img, requirement) for img in images):
            missing.append(requirement)
    return missing


def _check_original_image_conversion_prerequisites(
    images: list[dict[str, Any]],
    file_ref: str,
    content_id: str | None,
    cfg: dict[str, Any],
) -> list[Finding]:
    findings: list[Finding] = []
    allowed_types = {
        _normalize_text(value).lower()
        for value in cfg.get("provider_required_image_types", ["content", "season", "series"])
        if _normalize_text(value)
    }
    for img in images:
        raw_type = _normalize_text(img.get("type")).lower()
        if raw_type not in allowed_types:
            findings.append(
                Finding(
                    "orig_image_scope_invalid",
                    "high",
                    "Image type must be one of content/season/series.",
                    file_ref,
                    content_id,
                )
            )

        raw_url = _normalize_text(img.get("url") or img.get("urlTemplate") or img.get("value"))
        if not raw_url:
            findings.append(
                Finding(
                    "orig_image_value_missing",
                    "high",
                    "Image value/URL is missing.",
                    file_ref,
                    content_id,
                )
            )

        width = _unwrap_dimension_value(img.get("width"))
        height = _unwrap_dimension_value(img.get("height"))
        width_int = _safe_int(width)
        height_int = _safe_int(height)
        if width_int is None or height_int is None or width_int <= 0 or height_int <= 0:
            findings.append(
                Finding(
                    "orig_image_dimensions_invalid",
                    "high",
                    "Image dimensions are required and must be numeric > 0.",
                    file_ref,
                    content_id,
                )
            )
            continue

    return findings


def _is_tvepisode_packshot_exception(record: dict[str, Any], img: dict[str, Any]) -> bool:
    """Allow packshot image names on TVEpisode content (known accepted Viaplay pattern)."""
    record_type = _normalize_text(record.get("@type") or record.get("type")).lower()
    image_name = _normalize_text(img.get("type") or img.get("imageType") or img.get("kind") or img.get("name")).lower()
    return record_type == "tvepisode" and image_name == "packshot"


def _extract_genres_canonical(
    record: dict[str, Any],
    genre_candidates: list[str] | None = None,
) -> list[str]:
    payload = _primary_payload(record)

    def _collect_parent_containers(node: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(node, dict):
            return out
        for key in ("series", "season", "partOfSeries", "partOfSeason"):
            nested = node.get(key)
            if isinstance(nested, dict):
                out.append(nested)
        return out

    def _append_text_value(out: list[str], value: Any) -> None:
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())

    def _extract_from_container(container: dict[str, Any]) -> list[str]:
        local: list[str] = []

        # Read from configured candidate keys first, then canonical fallbacks used by converted payloads.
        keys = list(genre_candidates or [])
        for fallback_key in ("genres", "genre", "sport", "sportsLeague"):
            if fallback_key not in keys:
                keys.append(fallback_key)
        for key in keys:
            if "." in key:
                raw_genres = _get_by_path(container, key)
            else:
                raw_genres = container.get(key)

            if isinstance(raw_genres, dict):
                _append_text_value(local, raw_genres.get("main"))
                _append_text_value(local, raw_genres.get("sub"))
            elif isinstance(raw_genres, list):
                for item in raw_genres:
                    if isinstance(item, str):
                        local.append(item.strip())
                    elif isinstance(item, dict):
                        name = item.get("name") or item.get("value") or item.get("label")
                        _append_text_value(local, name)
            else:
                _append_text_value(local, raw_genres)

        return [g for g in local if g]

    containers = [payload]
    containers.extend(_collect_parent_containers(payload))
    if payload is not record and isinstance(record, dict):
        containers.append(record)
        containers.extend(_collect_parent_containers(record))

    # De-duplicate containers while preserving order.
    deduped_containers: list[dict[str, Any]] = []
    seen_containers: set[int] = set()
    for container in containers:
        marker = id(container)
        if marker in seen_containers:
            continue
        seen_containers.add(marker)
        deduped_containers.append(container)

    out: list[str] = []
    for container in deduped_containers:
        out.extend(_extract_from_container(container))

    # De-duplicate extracted genres while preserving order.
    seen_genres: set[str] = set()
    normalized_out: list[str] = []
    for genre in out:
        key = _normalize_text(genre).lower()
        if not key or key in seen_genres:
            continue
        seen_genres.add(key)
        normalized_out.append(_normalize_text(genre))

    return normalized_out


def _extract_genres_from_container(
    container: dict[str, Any],
    genre_candidates: list[str] | None = None,
) -> list[str]:
    if not isinstance(container, dict):
        return []

    out: list[str] = []
    keys = list(genre_candidates or [])
    for fallback_key in ("genres", "genre", "sport", "sportsLeague"):
        if fallback_key not in keys:
            keys.append(fallback_key)

    for key in keys:
        raw_genres = _get_by_path(container, key) if "." in key else container.get(key)
        if isinstance(raw_genres, dict):
            for nested in (raw_genres.get("main"), raw_genres.get("sub")):
                if isinstance(nested, str) and nested.strip():
                    out.append(nested.strip())
                elif isinstance(nested, list):
                    out.extend(item.strip() for item in nested if isinstance(item, str) and item.strip())
        elif isinstance(raw_genres, list):
            for item in raw_genres:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("value") or item.get("label")
                    text = _normalize_text(name)
                    if text:
                        out.append(text)
        else:
            text = _normalize_text(raw_genres)
            if text:
                out.append(text)

    seen: set[str] = set()
    normalized_out: list[str] = []
    for genre in out:
        key = _normalize_text(genre).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized_out.append(_normalize_text(genre))
    return normalized_out


def _extract_production_year_canonical(record: dict[str, Any]) -> str:
    payload = _primary_payload(record)
    year = _safe_int(payload.get("productionYear"))
    if year is not None:
        return str(year)

    released = _normalize_text(
        _get_by_path(payload, "releasedEvent.startDate")
        or payload.get("releaseDate")
        or payload.get("startDate")
        or _get_by_path(payload, "publicationEvent.startDate")
        or _get_by_path(payload, "eventSchedule.startDate")
        or _get_by_path(record, "releasedEvent.startDate")
        or record.get("releaseDate")
        or record.get("startDate")
    )
    match = re.search(r"\b(\d{4})\b", released)
    if match:
        return match.group(1)

    # Deep fallback for variant schemas: scan payload then whole record.
    for key in ("productionYear", "releaseDate", "startDate", "airDate", "publishDate"):
        for node in (payload, record):
            values = _find_values_by_key(node, key)
            for value in values:
                text = _normalize_text(value)
                deep_match = re.search(r"\b(\d{4})\b", text)
                if deep_match:
                    return deep_match.group(1)

    # Last-resort schema-agnostic scan: any key that looks like a date/time/year field.
    for node in (payload, record):
        for key, value in _walk(node):
            key_lc = _normalize_text(key).lower()
            if not key_lc:
                continue
            if not any(token in key_lc for token in ("year", "date", "time", "start", "release", "publish", "air")):
                continue
            text = _normalize_text(value)
            deep_match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", text)
            if deep_match:
                return deep_match.group(1)

    return ""


def _extract_duration_canonical(record: dict[str, Any]) -> int | None:
    payload = _primary_payload(record)
    duration = _safe_int(payload.get("duration"))
    if duration is not None:
        return duration

    vods = record.get("vods")
    if isinstance(vods, list):
        for item in vods:
            if isinstance(item, dict):
                value = _safe_int(item.get("durationInSeconds") or item.get("duration"))
                if value is not None:
                    return value

    # Deep fallback for provider schemas that keep duration under nested product/media fields.
    for key in ("duration", "durationInSeconds", "runtime", "runtimeSeconds"):
        for value in _find_values_by_key(record, key):
            parsed = _safe_int(value)
            if parsed is not None:
                return parsed
    return None


def _normalize_rating_value(value: Any) -> str:
    # Reject containers outright instead of falling through to _normalize_text,
    # which would stringify e.g. an empty list into the truthy text "[]".
    if isinstance(value, (list, tuple, set, dict)):
        return ""
    text = _normalize_text(value)
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits or text.lower()


def _extract_age_rating_canonical(record: dict[str, Any]) -> str:
    payload = _primary_payload(record)

    direct = payload.get("ageRating") or _get_by_path(payload, "contentRating.ratingValue")
    if direct is not None:
        return _normalize_rating_value(direct)

    # Event/original schemas can represent contentRating as list, dict, or plain string.
    content_rating = payload.get("contentRating")
    if isinstance(content_rating, list):
        for item in content_rating:
            if not isinstance(item, dict):
                continue
            rating = _normalize_rating_value(
                item.get("ratingValue")
                or item.get("originalRating")
                or item.get("rating")
                or item.get("value")
            )
            if rating:
                return rating
    elif isinstance(content_rating, dict):
        rating = _normalize_rating_value(
            content_rating.get("ratingValue")
            or content_rating.get("originalRating")
            or content_rating.get("rating")
            or content_rating.get("value")
        )
        if rating:
            return rating
    elif isinstance(content_rating, str):
        rating = _normalize_rating_value(content_rating)
        if rating:
            return rating

    age_ratings = payload.get("ageRatings")
    if isinstance(age_ratings, list):
        preferred: dict[str, Any] | None = None
        first: dict[str, Any] | None = None
        for item in age_ratings:
            if not isinstance(item, dict):
                continue
            if first is None:
                first = item
            if _normalize_text(item.get("country")).upper() == "FI":
                preferred = item
                break
        chosen = preferred or first
        if chosen is not None:
            return _normalize_rating_value(
                chosen.get("originalRating")
                or chosen.get("rating")
                or chosen.get("convertedRating")
            )

    # Deep fallback for variant schemas: scan payload then whole record.
    # Note: deliberately excludes "classification" — providers (e.g. TV4) use that
    # key for generic content classification (e.g. "regular"/"kids"), not age ratings.
    for key in (
        "ageRating",
        "ratingValue",
        "originalRating",
        "convertedRating",
        "rating",
        "parentalRating",
    ):
        for node in (payload, record):
            values = _find_values_by_key(node, key)
            for value in values:
                normalized = _normalize_rating_value(value)
                if normalized:
                    return normalized

    # Last-resort schema-agnostic scan: any key that looks rating-related.
    for node in (payload, record):
        for key, value in _walk(node):
            key_lc = _normalize_text(key).lower()
            if not key_lc:
                continue
            # Avoid false positives from keys like "availabilityStarts" and, notably,
            # generic content "classification" fields (e.g. TV4's "regular"/"kids"
            # classification), which are not age ratings despite the naming overlap.
            if not (
                any(token in key_lc for token in ("rating", "parental", "certificate"))
                or key_lc in {"age", "agerating", "age_rating", "minimumage", "minage"}
            ):
                continue
            normalized = _normalize_rating_value(value)
            if normalized:
                return normalized

    return ""


def _extract_deeplink_canonical(record: dict[str, Any]) -> str:
    payload = _primary_payload(record)

    direct = _normalize_text(payload.get("deeplink"))
    if direct:
        return direct

    deeplinks = payload.get("deeplinks")
    if isinstance(deeplinks, list):
        for item in deeplinks:
            if isinstance(item, dict):
                link = _normalize_text(item.get("link") or item.get("url") or item.get("urlTemplate"))
                if link:
                    return link

    vods = record.get("vods")
    if isinstance(vods, list):
        for vod in vods:
            if not isinstance(vod, dict):
                continue
            vod_links = vod.get("deeplinks")
            if isinstance(vod_links, list):
                for item in vod_links:
                    if isinstance(item, dict):
                        link = _normalize_text(item.get("link") or item.get("url") or item.get("urlTemplate"))
                        if link:
                            return link

    # Provider-specific nested deeplink maps, e.g. data.deeplinks.tv4play.episode.
    nested_deeplinks = _get_by_path(record, "data.deeplinks")
    if isinstance(nested_deeplinks, dict):
        for provider_links in nested_deeplinks.values():
            if not isinstance(provider_links, dict):
                continue
            for key in ("episode", "series", "url", "link", "urlTemplate"):
                link = _normalize_text(provider_links.get(key))
                if link:
                    return link

    return _extract_deeplink_from_potential_action(payload) or ""


def _is_episode_like_converted(record: dict[str, Any]) -> bool:
    payload = _primary_payload(record)
    if _safe_int(payload.get("episodeNumber")) is not None:
        return True
    if isinstance(payload.get("season"), dict):
        return True
    return isinstance(payload.get("series"), dict)


def _extract_series_id_from_converted(record: dict[str, Any]) -> str:
    payload = _primary_payload(record)

    series_obj = payload.get("series")
    if isinstance(series_obj, dict):
        series_id = _normalize_text(series_obj.get("seriesId"))
        if series_id:
            return series_id

        original_series_id = _normalize_text(series_obj.get("originalSeriesId"))
        if original_series_id:
            return original_series_id

    fallback_series_id = _normalize_text(
        payload.get("seriesId")
        or payload.get("originalSeriesId")
        or payload.get("showId")
        or payload.get("parentSeriesId")
    )
    return fallback_series_id


def _extract_original_series_id_from_converted(record: dict[str, Any]) -> str:
    payload = _primary_payload(record)
    series_obj = payload.get("series")
    if isinstance(series_obj, dict):
        original_series_id = _normalize_text(series_obj.get("originalSeriesId"))
        if original_series_id:
            return original_series_id
    return _normalize_text(payload.get("originalSeriesId") or payload.get("seriesOriginalId"))


def _extract_converted_content_id(record: dict[str, Any]) -> str:
    payload = _primary_payload(record)
    return _normalize_text(
        payload.get("contentId")
        or record.get("contentId")
        or payload.get("originalContentId")
        or record.get("originalContentId")
        or payload.get("timelineId")
        or record.get("timelineId")
    )


def _build_adapter_fragments(adapter_id: str) -> list[str]:
    """Build adapter ID fragments for matching content across provider variants.
    
    Handles viaplay-deeplink-vod, viaplay-deeplink-event, etc.
    """
    normalized = _normalize_text(adapter_id).lower()
    if not normalized:
        # Default to VOD if not specified
        normalized = "viaplay-deeplink-vod"
    
    base = normalized
    for suffix in ("-vod", "-event", "-epg", "-svod"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break

    fragments = {
        normalized,
        normalized.replace("-", "."),
        base,
        base.replace("-", "."),
        "viaplay-deeplink",
        "viaplay.deeplink",
    }
    return [item for item in fragments if item]


def _matches_adapter_content_id(content_id: str, adapter_id: str) -> bool:
    if not content_id:
        return False

    content_lc = content_id.lower()
    fragments = _build_adapter_fragments(adapter_id)
    return any(fragment in content_lc for fragment in fragments)


def _parse_iso_dt(value: Any) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class MetadataChecker:
    def __init__(
        self,
        cfg: dict[str, Any],
        reference_date: str | date | None = None,
        reference_date_from: str | date | None = None,
        reference_date_to: str | date | None = None,
    ):
        self.cfg = cfg
        self.candidate = cfg["candidate_keys"]

        # content_id -> title, collected while checks run so callers (e.g. the
        # Excel export) can label findings with a human-readable content name.
        self.content_titles: dict[str, str] = {}

        # Parse reference dates if provided
        # Single date (legacy) or date range
        self._parse_reference_dates(reference_date, reference_date_from, reference_date_to)

    def _parse_reference_dates(
        self,
        reference_date: str | date | None,
        reference_date_from: str | date | None,
        reference_date_to: str | date | None,
    ) -> None:
        """Parse and store reference dates for validation."""
        today = date.today()
        
        # If date range is provided, use it
        if reference_date_from or reference_date_to:
            self.reference_date_from = self._parse_date(reference_date_from) if reference_date_from else None
            self.reference_date_to = self._parse_date(reference_date_to) if reference_date_to else None
            # For backwards compatibility, set reference_date to the start of range
            self.reference_date = self.reference_date_from or self.reference_date_to or today
        else:
            # Use single reference date if provided
            self.reference_date = self._parse_date(reference_date) if reference_date else today
            self.reference_date_from = None
            self.reference_date_to = None

    def _parse_date(self, value: str | date | None) -> date | None:
        """Parse a date value from string, date, or datetime."""
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                # Fail fast with a clear message instead of propagating None to later date comparisons.
                raise ValueError(f"Invalid reference_date '{value}'. Expected format YYYY-MM-DD.")
        elif isinstance(value, datetime):
            return value.date()
        elif isinstance(value, date):
            return value
        # Non-date values should fail early for the same reason.
        raise ValueError(f"Invalid reference_date type '{type(value).__name__}'. Expected str or date.")

    def _resolve_field_candidates(self, canonical_field: str) -> list[str]:
        configured = self.candidate.get(canonical_field)
        if isinstance(configured, list) and configured:
            return [str(item) for item in configured if str(item).strip()]
        return [canonical_field]

    def _first_non_empty_for_field(self, data: dict[str, Any], canonical_field: str) -> Any:
        return _first_non_empty(data, self._resolve_field_candidates(canonical_field))

    def _first_value_for_field(self, data: dict[str, Any], canonical_field: str) -> Any:
        return _first_value(data, self._resolve_field_candidates(canonical_field))

    def _extract_list_for_field(self, data: dict[str, Any], canonical_field: str) -> list[dict[str, Any]]:
        value = self._first_non_empty_for_field(data, canonical_field)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
        return []

    def _normalize_required_field_paths(self, field_name: str) -> list[str]:
        configured = self.candidate.get(field_name)
        if isinstance(configured, list) and configured:
            return [str(item) for item in configured if str(item).strip()]
        return [field_name]

    def _detect_metadata_kind(self, record: dict[str, Any]) -> str:
        explicit_metadata_type = _normalize_text(
            _first_non_empty(record, ["metadataType", "metadata_type", "type", "contentType"])
        ).lower()
        if explicit_metadata_type in {"epg", "vod", "event"}:
            return explicit_metadata_type

        if self._first_non_empty_for_field(record, "events") is not None:
            return "event"

        has_contents = self._first_non_empty_for_field(record, "contents") is not None
        has_broadcasts = self._first_non_empty_for_field(record, "broadcasts") is not None
        has_schedules = _first_value(record, ["schedules", "schedule"]) is not None
        # EPG payloads can also arrive flattened from XML, where schedule hints
        # exist without a nested schedules[] node.
        has_epg_hints = any(
            (
                self._first_non_empty_for_field(record, "channel_id") is not None,
                self._first_non_empty_for_field(record, "schedule_from") is not None,
                self._first_non_empty_for_field(record, "schedule_to") is not None,
                self._first_non_empty_for_field(record, "schedule_date") is not None,
                self._first_non_empty_for_field(record, "broadcast_id") is not None,
                self._first_non_empty_for_field(record, "original_broadcast_id") is not None,
            )
        )
        if has_contents or has_broadcasts or has_schedules or has_epg_hints:
            return "epg"

        # Avoid treating arbitrary content-level fragments as VOD envelopes.
        # Only classify as VOD when there are clear VOD-envelope or VOD-domain markers.
        has_version = _first_non_empty(record, self._normalize_required_field_paths("version")) is not None
        has_timestamp = _first_non_empty(record, self._normalize_required_field_paths("timestamp")) is not None
        source_type = _normalize_text(self._first_non_empty_for_field(record, "source_type")).lower()
        has_explicit_vod_id = _first_non_empty(
            record,
            [
                "vodId",
                "vod_id",
                "originalVodId",
                "content.vodId",
                "content.vod_id",
            ],
        ) is not None
        has_vod_markers = any(
            (
                source_type in {"deeplink", "vod", "asset"},
                self._first_non_empty_for_field(record, "publishing_info") is not None,
                has_explicit_vod_id,
                not _is_empty(_extract_deeplink_canonical(record)),
            )
        )
        if (has_version and has_timestamp) or has_vod_markers:
            return "vod"

        return "unknown"

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = _normalize_text(value).lower()
        return text in {"1", "true", "yes", "y"}

    @staticmethod
    def _first_text(value: Any) -> str:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                text = _normalize_text(item)
                if text:
                    return text
            return ""
        return _normalize_text(value)

    @staticmethod
    def _text_values(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            out: list[str] = []
            for item in value:
                text = _normalize_text(item)
                if text:
                    out.append(text)
            return out
        text = _normalize_text(value)
        return [text] if text else []

    def _check_envelope_contract(
        self,
        record: dict[str, Any],
        file_ref: str,
        is_converted: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []
        prefix = "conv" if is_converted else "orig"
        metadata_kind = self._detect_metadata_kind(record)
        if metadata_kind == "unknown":
            return findings

        required_by_kind = self.cfg.get("required_envelope_fields", {})
        required_fields = required_by_kind.get(metadata_kind, []) if isinstance(required_by_kind, dict) else []
        for field_name in required_fields:
            candidates = self._normalize_required_field_paths(str(field_name))
            value = _first_non_empty(record, candidates)
            if _is_empty(value):
                findings.append(
                    Finding(
                        f"{prefix}_envelope_required_field_missing",
                        "high",
                        f"Missing required envelope field '{field_name}' for metadata type '{metadata_kind}'.",
                        file_ref,
                        None,
                    )
                )

        if metadata_kind == "epg":
            schedules = _first_non_empty(record, self._normalize_required_field_paths("schedules"))
            if _is_empty(schedules):
                fallback_required = ["broadcasts", "channelId", "from", "to", "date"]
                for field_name in fallback_required:
                    value = _first_non_empty(record, self._normalize_required_field_paths(field_name))
                    if _is_empty(value):
                        findings.append(
                            Finding(
                                f"{prefix}_envelope_required_field_missing",
                                "high",
                                "EPG envelope must contain 'schedules' or all of "
                                "'broadcasts', 'channelId', 'from', 'to', 'date'. "
                                f"Missing '{field_name}'.",
                                file_ref,
                                None,
                            )
                        )

        return findings

    def _collect_content_ids(self, record: dict[str, Any]) -> set[str]:
        content_ids: set[str] = set()

        contents = self._extract_list_for_field(record, "contents")
        for item in contents:
            content_id = _normalize_text(_first_non_empty(item, self.cfg.get("id_keys", [])))
            if content_id:
                content_ids.add(content_id)

        # If payload is already content-shaped, include top-level identity too.
        top_level_id = _normalize_text(_first_non_empty(record, self.cfg.get("id_keys", [])))
        if top_level_id:
            content_ids.add(top_level_id)

        return content_ids

    def _check_broadcast_contract(
        self,
        record: dict[str, Any],
        file_ref: str,
        is_converted: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []
        prefix = "conv" if is_converted else "orig"
        broadcasts = self._extract_list_for_field(record, "broadcasts")
        broadcast_entries: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

        if broadcasts:
            broadcast_entries.extend((broadcast, None) for broadcast in broadcasts)

        schedules_value = _first_value(record, ["schedules", "schedule"])
        schedules: list[dict[str, Any]] = []
        if isinstance(schedules_value, list):
            schedules = [item for item in schedules_value if isinstance(item, dict)]
        elif isinstance(schedules_value, dict):
            schedules = [schedules_value]

        for schedule in schedules:
            schedule_broadcasts = schedule.get("broadcasts")
            if isinstance(schedule_broadcasts, list):
                for broadcast in schedule_broadcasts:
                    if isinstance(broadcast, dict):
                        broadcast_entries.append((broadcast, schedule))

        if not broadcast_entries:
            return findings

        # Detect duplicate IDs before deduplication; otherwise exact duplicates are masked.
        raw_scope_ids: dict[str, list[str]] = defaultdict(list)
        for broadcast, schedule in broadcast_entries:
            raw_broadcast_id = _normalize_text(
                _first_non_empty(broadcast, self._normalize_required_field_paths("broadcastId"))
                or self._first_non_empty_for_field(broadcast, "broadcast_id")
            )
            if not raw_broadcast_id:
                continue
            raw_channel_id = _normalize_text(
                self._first_non_empty_for_field(broadcast, "channel_id")
                or (self._first_non_empty_for_field(schedule, "channel_id") if isinstance(schedule, dict) else None)
                or self._first_non_empty_for_field(record, "channel_id")
            )
            raw_schedule_date = _normalize_text(
                self._first_non_empty_for_field(broadcast, "schedule_date")
                or (self._first_non_empty_for_field(schedule, "schedule_date") if isinstance(schedule, dict) else None)
                or self._first_non_empty_for_field(record, "schedule_date")
            )
            raw_scope_key = f"{raw_channel_id}|{raw_schedule_date}"
            raw_scope_ids[raw_scope_key].append(raw_broadcast_id)

        for scope_key, ids in raw_scope_ids.items():
            duplicates = sorted({item for item in ids if ids.count(item) > 1})
            for duplicate_id in duplicates:
                findings.append(
                    Finding(
                        f"{prefix}_duplicate_broadcast_id",
                        "high",
                        f"Duplicate broadcastId '{duplicate_id}' detected in scope '{scope_key}'.",
                        file_ref,
                        duplicate_id,
                    )
                )

        deduped_entries: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        seen_entries: set[tuple[str, str, str, str]] = set()
        for broadcast, schedule in broadcast_entries:
            dedupe_key = (
                _normalize_text(_first_non_empty(broadcast, self._normalize_required_field_paths("broadcastId"))),
                _normalize_text(_first_non_empty(broadcast, self._normalize_required_field_paths("contentId"))),
                _normalize_text(self._first_non_empty_for_field(broadcast, "display_start")),
                _normalize_text(self._first_non_empty_for_field(broadcast, "display_end")),
            )
            if dedupe_key in seen_entries:
                continue
            seen_entries.add(dedupe_key)
            deduped_entries.append((broadcast, schedule))
        broadcast_entries = deduped_entries

        required_fields = [
            str(item) for item in self.cfg.get("required_broadcast_fields", []) if str(item).strip()
        ]

        valid_content_ids = self._collect_content_ids(record)

        for idx, (broadcast, schedule) in enumerate(broadcast_entries):
            broadcast_id = _normalize_text(
                _first_non_empty(broadcast, self._normalize_required_field_paths("broadcastId"))
                or self._first_non_empty_for_field(broadcast, "broadcast_id")
            )

            for field_name in required_fields:
                candidates = self._normalize_required_field_paths(field_name)
                value = _first_non_empty(broadcast, candidates)
                if _is_empty(value) and isinstance(schedule, dict):
                    value = _first_non_empty(schedule, candidates)
                if _is_empty(value):
                    missing_check_id = f"{prefix}_broadcast_required_field_missing"
                    if field_name == "broadcastId":
                        missing_check_id = f"{prefix}_broadcast_id_missing"
                    elif field_name == "channel_id":
                        missing_check_id = f"{prefix}_channel_id_missing"
                    findings.append(
                        Finding(
                            missing_check_id,
                            "high",
                            f"Missing required broadcast field '{field_name}'.",
                            file_ref,
                            broadcast_id or None,
                        )
                    )

            broadcast_content_id = _normalize_text(
                _first_non_empty(broadcast, self._normalize_required_field_paths("contentId"))
                or self._first_non_empty_for_field(broadcast, "broadcast_content_id")
            )
            if broadcast_content_id and valid_content_ids and broadcast_content_id not in valid_content_ids:
                findings.append(
                    Finding(
                        f"{prefix}_broadcast_content_reference_invalid",
                        "high",
                        "Broadcast contentId does not resolve to any content in contents[]. "
                        f"broadcast contentId='{broadcast_content_id}'.",
                        file_ref,
                        broadcast_content_id,
                    )
                )

            rerun = self._as_bool(self._first_non_empty_for_field(broadcast, "rerun"))
            live = self._as_bool(self._first_non_empty_for_field(broadcast, "live"))
            premiere = self._as_bool(self._first_non_empty_for_field(broadcast, "premiere"))
            if sum([rerun, live, premiere]) > 1:
                findings.append(
                    Finding(
                        f"{prefix}_broadcast_rerun_live_premiere_conflict",
                        "medium",
                        "Broadcast flags rerun/live/premiere are contradictory.",
                        file_ref,
                        broadcast_id or None,
                    )
                )

            start_text = _normalize_text(self._first_non_empty_for_field(broadcast, "display_start"))
            end_text = _normalize_text(self._first_non_empty_for_field(broadcast, "display_end"))
            start_dt = _parse_iso_dt(start_text)
            end_dt = _parse_iso_dt(end_text)
            if start_dt and end_dt and end_dt <= start_dt:
                findings.append(
                    Finding(
                        f"{prefix}_epg_from_to_dates_invalid",
                        "high",
                        f"Broadcast must have positive duration (start={start_text}, end={end_text}).",
                        file_ref,
                        broadcast_id or None,
                    )
                )

        by_channel: dict[str, list[tuple[datetime, datetime, str]]] = defaultdict(list)
        for broadcast, schedule in broadcast_entries:
            channel_id = _normalize_text(
                self._first_non_empty_for_field(broadcast, "channel_id")
                or (self._first_non_empty_for_field(schedule, "channel_id") if isinstance(schedule, dict) else None)
                or self._first_non_empty_for_field(record, "channel_id")
                or "unknown"
            )
            start_text = _normalize_text(self._first_non_empty_for_field(broadcast, "display_start"))
            end_text = _normalize_text(self._first_non_empty_for_field(broadcast, "display_end"))
            start_dt = _parse_iso_dt(start_text)
            end_dt = _parse_iso_dt(end_text)
            if start_dt and end_dt:
                by_channel[channel_id].append((start_dt, end_dt, start_text))

        for channel_id, slots in by_channel.items():
            slots.sort(key=lambda item: item[0])
            for i in range(1, len(slots)):
                prev_start, prev_end, _ = slots[i - 1]
                cur_start, cur_end, cur_raw = slots[i]
                if cur_start > prev_end:
                    findings.append(
                        Finding(
                            f"{prefix}_broadcast_gap_between",
                            "medium",
                            f"Gap between broadcasts on channel '{channel_id}' before start '{cur_raw}'.",
                            file_ref,
                            None,
                        )
                    )
                elif cur_start < prev_end:
                    findings.append(
                        Finding(
                            f"{prefix}_broadcast_overlap_start",
                            "medium",
                            f"Broadcast overlap start on channel '{channel_id}' at '{cur_raw}'.",
                            file_ref,
                            None,
                        )
                    )
                    findings.append(
                        Finding(
                            f"{prefix}_broadcast_overlap_end",
                            "medium",
                            f"Previous broadcast overlaps end on channel '{channel_id}' at '{prev_end.isoformat()}'.",
                            file_ref,
                            None,
                        )
                    )

        return findings

    def _check_event_contract(
        self,
        record: dict[str, Any],
        file_ref: str,
        is_converted: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []
        prefix = "conv" if is_converted else "orig"
        events = self._extract_list_for_field(record, "events")
        if not events:
            return findings

        required_fields = [
            str(item) for item in self.cfg.get("required_event_fields", []) if str(item).strip()
        ]
        for idx, event in enumerate(events):
            for field_name in required_fields:
                candidates = self._normalize_required_field_paths(field_name)
                if _is_empty(_first_non_empty(event, candidates)):
                    findings.append(
                        Finding(
                            f"{prefix}_event_required_field_missing",
                            "high",
                            f"Missing required event field '{field_name}' in event index {idx}.",
                            file_ref,
                            None,
                        )
                    )
        return findings

    def _check_epg_contents_contract(
        self,
        record: dict[str, Any],
        file_ref: str,
        is_converted: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []
        prefix = "conv" if is_converted else "orig"

        contents = self._extract_list_for_field(record, "contents")
        if not contents:
            return findings

        for idx, item in enumerate(contents):
            content_id = _normalize_text(_first_non_empty(item, self.cfg.get("id_keys", [])))
            if not content_id:
                findings.append(
                    Finding(
                        f"{prefix}_content_id_missing",
                        "high",
                        f"Missing contentId in contents[{idx}].",
                        file_ref,
                        None,
                    )
                )

            record_type = _guess_type(item, self.cfg.get("type_keys", []))
            title = _first_non_empty_shallow(item, self.candidate["title"])
            if record_type in {"movie", "series", "episode"} and _is_empty(title):
                findings.append(
                    Finding(
                        f"{prefix}_title_presence",
                        "high",
                        f"Missing title for {record_type} in contents[{idx}].",
                        file_ref,
                        content_id or None,
                    )
                )

        return findings

    def _check_expected_content_shape(
        self,
        record: dict[str, Any],
        file_ref: str,
    ) -> list[Finding]:
        """Flag files where a channel's content type contradicts a configured expectation.

        E.g. `se.viaplay.classics` is configured as "movies_only" -- if a series/episode
        shows up in a converted EPG file scheduled on that channel, that's worth a finding
        even though every individual per-content check may otherwise pass.
        """
        findings: list[Finding] = []
        shape_config = self.cfg.get("expected_content_shape") or {}
        if not shape_config:
            return findings
        shape_config_norm = {str(k).strip().lower(): str(v).strip().lower() for k, v in shape_config.items()}

        contents = self._extract_list_for_field(record, "contents")
        if not contents:
            return findings

        # Collect every channel id referenced by schedules/broadcasts in this file.
        channel_ids: set[str] = set()
        schedules_value = _first_value(record, ["schedules", "schedule"])
        schedules: list[dict[str, Any]] = []
        if isinstance(schedules_value, list):
            schedules = [item for item in schedules_value if isinstance(item, dict)]
        elif isinstance(schedules_value, dict):
            schedules = [schedules_value]

        for schedule in schedules:
            cid = _normalize_text(self._first_non_empty_for_field(schedule, "channel_id"))
            if cid:
                channel_ids.add(cid.lower())
            schedule_broadcasts = schedule.get("broadcasts")
            if isinstance(schedule_broadcasts, list):
                for broadcast in schedule_broadcasts:
                    if isinstance(broadcast, dict):
                        bcid = _normalize_text(self._first_non_empty_for_field(broadcast, "channel_id"))
                        if bcid:
                            channel_ids.add(bcid.lower())

        record_channel_id = _normalize_text(self._first_non_empty_for_field(record, "channel_id"))
        if record_channel_id:
            channel_ids.add(record_channel_id.lower())

        relevant_channels = {cid: shape_config_norm[cid] for cid in channel_ids if cid in shape_config_norm}
        if not relevant_channels:
            return findings

        for idx, item in enumerate(contents):
            content_id = _normalize_text(_first_non_empty(item, self.cfg.get("id_keys", [])))
            is_series_like = any(key in item for key in ("series", "season", "partOfSeries", "partOfSeason"))
            record_type = _guess_type(item, self.cfg.get("type_keys", []))
            if record_type in {"series", "episode", "season"}:
                is_series_like = True

            for channel_id, expected_shape in relevant_channels.items():
                if expected_shape == "movies_only" and is_series_like:
                    findings.append(
                        Finding(
                            "conv_unexpected_content_shape",
                            "medium",
                            f"Channel '{channel_id}' is configured as movies_only but "
                            f"contents[{idx}] looks like series/episode content.",
                            file_ref,
                            content_id or None,
                        )
                    )
                elif expected_shape == "series_only" and not is_series_like:
                    findings.append(
                        Finding(
                            "conv_unexpected_content_shape",
                            "medium",
                            f"Channel '{channel_id}' is configured as series_only but "
                            f"contents[{idx}] looks like movie/standalone content.",
                            file_ref,
                            content_id or None,
                        )
                    )

        return findings

    def _check_epg_date_contract(
        self,
        record: dict[str, Any],
        file_ref: str,
        is_converted: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []
        if self.cfg.get("skip_epg_date_contract"):
            return findings

        prefix = "conv" if is_converted else "orig"

        from_text = _normalize_text(self._first_non_empty_for_field(record, "schedule_from"))
        to_text = _normalize_text(self._first_non_empty_for_field(record, "schedule_to"))
        from_dt = _parse_iso_dt(from_text)
        to_dt = _parse_iso_dt(to_text)

        if to_text and to_dt is None:
            findings.append(
                Finding(
                    f"{prefix}_epg_to_date_format_invalid",
                    "high",
                    f"Invalid EPG to-date format: '{to_text}'.",
                    file_ref,
                    None,
                )
            )

        if from_text and from_dt is None:
            findings.append(
                Finding(
                    f"{prefix}_epg_from_to_dates_invalid",
                    "high",
                    f"Invalid EPG from-date format: '{from_text}'.",
                    file_ref,
                    None,
                )
            )

        if from_dt and to_dt:
            if to_dt <= from_dt:
                findings.append(
                    Finding(
                        f"{prefix}_epg_from_to_dates_invalid",
                        "high",
                        f"Invalid EPG range: to must be after from (from={from_text}, to={to_text}).",
                        file_ref,
                        None,
                    )
                )

            duration_hours = (to_dt - from_dt).total_seconds() / 3600
            if duration_hours > 24:
                findings.append(
                    Finding(
                        f"{prefix}_schedule_duration_exceeds_24h",
                        "medium",
                        f"Schedule duration exceeds 24 hours ({duration_hours:.2f}h).",
                        file_ref,
                        None,
                    )
                )

        return findings

    def _check_image_structure(
        self,
        images: list[dict[str, Any]],
        file_ref: str,
        content_id: str | None,
        is_converted: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []
        prefix = "conv" if is_converted else "orig"

        for img in images:
            raw_url = _normalize_text(img.get("url") or img.get("urlTemplate"))
            if raw_url:
                parsed = urlparse(raw_url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    findings.append(
                        Finding(
                            f"{prefix}_image_url_invalid",
                            "high",
                            f"Invalid image URL '{raw_url}'.",
                            file_ref,
                            content_id,
                        )
                    )

            width = _unwrap_dimension_value(img.get("width"))
            height = _unwrap_dimension_value(img.get("height"))
            has_width = width is not None and _normalize_text(width) != ""
            has_height = height is not None and _normalize_text(height) != ""
            if has_width or has_height:
                width_int = _safe_int(width)
                height_int = _safe_int(height)
                if width_int is None or height_int is None or width_int <= 0 or height_int <= 0:
                    findings.append(
                        Finding(
                            f"{prefix}_image_dimensions_invalid",
                            "high",
                            "Image dimensions are invalid. Both width and height must be present and > 0.",
                            file_ref,
                            content_id,
                        )
                    )

        return findings

    def _check_iso_codes(
        self,
        record: dict[str, Any],
        file_ref: str,
        content_id: str | None,
        is_converted: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []
        prefix = "conv" if is_converted else "orig"

        language_values: list[str] = []
        language_values.extend(_extract_title_languages(record))
        language_values.extend(list(_lang_map(record, self.candidate.get("descriptions_block", [])).keys()))
        direct_language = self._first_non_empty_for_field(record, "language_code")
        language_values.extend(self._text_values(direct_language))

        for language in {item.lower() for item in language_values if _normalize_text(item)}:
            normalized_language = _normalize_language_code(language)
            if normalized_language not in ISO639_1_CODES:
                findings.append(
                    Finding(
                        f"{prefix}_language_code_invalid",
                        "high",
                        f"Invalid language code '{language}'.",
                        file_ref,
                        content_id,
                    )
                )

        countries: list[str] = []
        for field_name in self._resolve_field_candidates("country_code"):
            values = _find_values_by_key(record, field_name) if "." not in field_name else [_get_by_path(record, field_name)]
            for value in values:
                for text in self._text_values(value):
                    countries.append(_normalize_country_value(text))

        for country in set(countries):
            if country not in ISO3166_1_ALPHA2_CODES:
                findings.append(
                    Finding(
                        f"{prefix}_country_code_invalid",
                        "high",
                        f"Invalid country code '{country}'.",
                        file_ref,
                        content_id,
                    )
                )

        return findings

    def _check_vod_structural_contract(
        self,
        record: dict[str, Any],
        file_ref: str,
        content_id: str | None,
        is_converted: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []
        prefix = "conv" if is_converted else "orig"

        source_type = self._first_text(self._first_non_empty_for_field(record, "source_type"))
        source_types: list[str] = []
        if source_type:
            source_types.append(source_type)

        vods_value = record.get("vods")
        if isinstance(vods_value, list):
            for item in vods_value:
                if isinstance(item, dict):
                    nested_source_type = _normalize_text(item.get("sourceType") or item.get("source_type"))
                    if nested_source_type:
                        source_types.append(nested_source_type)

        is_deeplink_source = any(item.lower() == "deeplink" for item in source_types)

        deeplink_value = _extract_deeplink_canonical(record)
        if is_deeplink_source and _is_empty(deeplink_value):
            findings.append(
                Finding(
                    f"{prefix}_deeplink_missing",
                    "high",
                    "Missing deeplink for sourceType=Deeplink.",
                    file_ref,
                    content_id,
                )
            )
        if (not is_deeplink_source) and (not _is_empty(deeplink_value)):
            findings.append(
                Finding(
                    f"{prefix}_vod_deeplinks_for_non_deeplink",
                    "high",
                    "Deeplinks present for non-Deeplink sourceType.",
                    file_ref,
                    content_id,
                )
            )

        vod_id_value = self._first_text(self._first_non_empty_for_field(record, "vod_id"))
        if not vod_id_value and isinstance(vods_value, list):
            for item in vods_value:
                if isinstance(item, dict):
                    nested_vod_id = _normalize_text(item.get("vodId") or item.get("vod_id"))
                    if nested_vod_id:
                        vod_id_value = nested_vod_id
                        break
        vod_pattern = _normalize_text(self.cfg.get("vod_id_pattern") or r"^[A-Za-z0-9._:-]+$")
        if vod_id_value and vod_pattern:
            try:
                if re.match(vod_pattern, vod_id_value) is None:
                    findings.append(
                        Finding(
                            f"{prefix}_vod_id_invalid",
                            "high",
                            f"Invalid VOD id format '{vod_id_value}'.",
                            file_ref,
                            content_id,
                        )
                    )
            except re.error:
                pass

        publishing_info = self._first_non_empty_for_field(record, "publishing_info")
        if isinstance(publishing_info, dict):
            publishing_entries = [publishing_info]
        elif isinstance(publishing_info, list):
            publishing_entries = [item for item in publishing_info if isinstance(item, dict)]
        else:
            publishing_entries = []

        # Absence of publishingInfo itself is a rights validation failure.
        if not publishing_entries:
            findings.append(
                Finding(
                    f"{prefix}_publishing_rights_missing",
                    "high",
                    "Missing publishingInfo for distributed content.",
                    file_ref,
                    content_id,
                )
            )
            return findings

        if publishing_entries:
            has_valid_target = False
            has_any_rights = False
            for entry in publishing_entries:
                targets = entry.get("targets")
                if isinstance(targets, list) and targets:
                    has_valid_target = True
                    for target in targets:
                        if isinstance(target, dict) and isinstance(target.get("rights"), dict) and target.get("rights"):
                            has_any_rights = True
                rights = entry.get("rights")
                if isinstance(rights, dict) and rights:
                    has_any_rights = True

            if not has_valid_target:
                findings.append(
                    Finding(
                        f"{prefix}_publishing_no_valid_targets",
                        "high",
                        "Publishing info has no valid targets.",
                        file_ref,
                        content_id,
                    )
                )
            if not has_any_rights:
                findings.append(
                    Finding(
                        f"{prefix}_publishing_rights_missing",
                        "high",
                        "Missing publishing or broadcast rights for distributed content.",
                        file_ref,
                        content_id,
                    )
                )

        return findings

    def load_records_from_local(self, folder_path: str, sample_size: int, seed: int) -> list[tuple[str, dict[str, Any]]]:
        path = Path(folder_path)
        files = sorted(path.rglob("*.json"))
        if not files:
            return []

        sample_size = max(1, min(sample_size, len(files)))
        rng = random.Random(seed)
        sampled = rng.sample(files, sample_size)

        records: list[tuple[str, dict[str, Any]]] = []
        for file_path in sampled:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                for idx, item in enumerate(payload):
                    if isinstance(item, dict):
                        records.append((f"{file_path}#{idx}", item))
            elif isinstance(payload, dict):
                records.append((str(file_path), payload))
        return records

    def load_records_from_s3(
        self,
        bucket: str,
        prefix: str,
        sample_size: int,
        seed: int,
        profile_name: str | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        keys = list_sampled_json_keys(
            bucket=bucket,
            prefix=prefix,
            sample_size=sample_size,
            random_seed=seed,
            profile_name=profile_name,
        )
        records = read_json_objects(bucket=bucket, keys=keys, profile_name=profile_name)
        return [(r.key, r.payload) for r in records]

    def load_records_from_s3_by_date_range(
        self,
        bucket: str,
        prefix: str,
        start_date: str,
        end_date: str,
        profile_name: str | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Load records from S3 filtered by LastModified date range."""
        keys = list_json_keys_by_date_range(
            bucket=bucket,
            prefix=prefix,
            start_date=start_date,
            end_date=end_date,
            profile_name=profile_name,
        )
        records = read_json_objects(bucket=bucket, keys=keys, profile_name=profile_name)
        return [(r.key, r.payload) for r in records]

    def find_ungrouped_series_in_converted(
        self,
        converted_records: list[tuple[str, dict[str, Any]]],
        adapter_id: str | None = None,
        page: int = 0,
        size: int = 50,
    ) -> dict[str, Any]:
        # Default to VOD if not specified
        scoped_adapter = _normalize_text(adapter_id) or "viaplay-deeplink-vod"
        safe_page = max(0, int(page))
        safe_size = max(1, int(size))

        by_series_total: Counter[str] = Counter()
        by_series_file_counts: dict[str, Counter[str]] = defaultdict(Counter)
        by_series_latest_ts: dict[str, datetime] = {}
        by_series_original_id: dict[str, str] = {}
        by_series_sample_content_id: dict[str, str] = {}

        for file_ref, conv in converted_records:
            if not _is_episode_like_converted(conv):
                continue

            content_id = _extract_converted_content_id(conv)
            if not _matches_adapter_content_id(content_id, scoped_adapter):
                continue

            series_id = _extract_series_id_from_converted(conv)
            if not series_id:
                continue

            original_series_id = _extract_original_series_id_from_converted(conv)

            by_series_total[series_id] += 1
            by_series_file_counts[series_id][file_ref] += 1

            if original_series_id and series_id not in by_series_original_id:
                by_series_original_id[series_id] = original_series_id
            if content_id and series_id not in by_series_sample_content_id:
                by_series_sample_content_id[series_id] = content_id

            ts = _parse_iso_dt(_first_non_empty(conv, ["timestamp", "content.timestamp"]))
            if ts is not None:
                current = by_series_latest_ts.get(series_id)
                if current is None or ts > current:
                    by_series_latest_ts[series_id] = ts

        rows: list[dict[str, Any]] = []
        for series_id, total_count in by_series_total.items():
            if total_count < 2:
                continue

            latest_dt = by_series_latest_ts.get(series_id)
            latest_ts = latest_dt.isoformat() if latest_dt is not None else None

            for file_ref, file_count in by_series_file_counts[series_id].items():
                rows.append(
                    {
                        "series_id": series_id,
                        "original_series_id": by_series_original_id.get(series_id),
                        "file_ref": file_ref,
                        "sample_content_id": by_series_sample_content_id.get(series_id),
                        "episodes_total": total_count,
                        "episodes_in_file": file_count,
                        "adapter_id": scoped_adapter,
                        "created_at": latest_ts,
                    }
                )

        rows.sort(
            key=lambda item: (
                item.get("created_at") or "",
                item.get("series_id") or "",
                item.get("file_ref") or "",
            ),
            reverse=True,
        )

        start = safe_page * safe_size
        end = start + safe_size
        return {
            "count": len(rows),
            "rows": rows[start:end],
        }

    def _record_identity(self, record: dict[str, Any]) -> str | None:
        def _first_scalar_text(value: Any) -> str:
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    text = _normalize_text(item)
                    if text:
                        return text
                return ""
            return _normalize_text(value)

        value = _first_non_empty_shallow(record, self.cfg["id_keys"])
        if value is None and not self.cfg.get("disable_id_fallback", False):
            # Fallback identity keys common in converted/event payloads. Providers that
            # need a single consistent content_id format (e.g. always-numeric) can set
            # `disable_id_fallback: true` to skip this and rely solely on `id_keys`.
            value = _first_non_empty(
                record,
                [
                    "contentId",
                    "originalContentId",
                    "timelineId",
                    "guid",
                    "id",
                    "content.contentId",
                    "content.originalContentId",
                    "content.timelineId",
                    "content.guid",
                ],
            )
        if value is None:
            return None
        identity = _first_scalar_text(value)
        return identity or None

    def _record_identity_candidates(self, record: dict[str, Any]) -> list[str]:
        """Return all likely IDs for record matching across raw/converted data."""
        candidates: list[str] = []

        # Primary IDs from config (contentId/guid/etc.).
        primary = _first_non_empty(record, self.cfg["id_keys"])
        candidates.extend(_id_aliases(primary))

        # Converted records often carry the source identity separately.
        fallback_id = _first_non_empty(record, ["originalContentId", "timelineId", "originalVodId"])
        candidates.extend(_id_aliases(fallback_id))

        # Manual uploads often use simplified keys; include these as resilient fallbacks.
        manual_fallbacks = _first_non_empty(
            record,
            [
                "id",
                "guid",
                "content.contentId",
                "content.id",
                "content.originalContentId",
            ],
        )
        candidates.extend(_id_aliases(manual_fallbacks))

        # Deduplicate while preserving order.
        seen: set[str] = set()
        out: list[str] = []
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _check_event_validity(
        self,
        record: dict[str, Any],
        file_ref: str,
        content_id: str | None,
        is_converted: bool = False,
    ) -> list[Finding]:
        """Check if event is valid on the reference date.
        
        For original format (Schema.org): checks startDate
        For converted format: checks publishingInfo[].targets[].rights.validFrom/validTo
        
        Auto-detects format if not explicitly specified.
        Returns list of Finding objects for out-of-range dates.
        """
        findings: list[Finding] = []
        
        # Auto-detect converted format (has publishingInfo with rights)
        publishing_infos = _get_by_path(record, "publishingInfo")
        has_converted_format = publishing_infos and isinstance(publishing_infos, list)
        
        if is_converted or has_converted_format:
            # Check converted format validFrom/validTo
            if not publishing_infos or not isinstance(publishing_infos, list):
                return findings
            
            for pub_info in publishing_infos:
                targets = _get_by_path(pub_info, "targets")
                if not targets or not isinstance(targets, list):
                    continue
                
                for target in targets:
                    rights = _get_by_path(target, "rights")
                    if not rights or not isinstance(rights, dict):
                        continue
                    
                    valid_from_str = rights.get("validFrom")
                    valid_to_str = rights.get("validTo")
                    
                    # Parse dates
                    valid_from = None
                    valid_to = None
                    
                    if valid_from_str:
                        dt = _parse_iso_dt(valid_from_str)
                        if dt:
                            valid_from = dt.date()
                    
                    if valid_to_str:
                        dt = _parse_iso_dt(valid_to_str)
                        if dt:
                            valid_to = dt.date()
                    
                    # Check validity based on whether we have a date range or single date
                    if self.reference_date_from or self.reference_date_to:
                        # Date range validation: check if event overlaps the range
                        range_start = self.reference_date_from
                        range_end = self.reference_date_to
                        
                        # Event is outside range if:
                        # - event ends before range starts, or
                        # - event starts after range ends
                        if valid_to and range_start and valid_to < range_start:
                            findings.append(
                                Finding(
                                    "conv_event_expired",
                                    "high",
                                    f"Event is expired before validity range {range_start}–{range_end or 'any'}. Valid until: {valid_to}.",
                                    file_ref,
                                    content_id,
                                )
                            )
                        elif valid_from and range_end and valid_from > range_end:
                            findings.append(
                                Finding(
                                    "conv_event_not_yet_valid",
                                    "high",
                                    f"Event starts after validity range {range_start or 'any'}–{range_end}. Valid from: {valid_from}.",
                                    file_ref,
                                    content_id,
                                )
                            )
                    else:
                        # Single date validation: check if reference_date is within validity window
                        if valid_from and self.reference_date < valid_from:
                            findings.append(
                                Finding(
                                    "conv_event_not_yet_valid",
                                    "high",
                                    f"Event is not yet valid on reference date {self.reference_date}. Valid from: {valid_from}.",
                                    file_ref,
                                    content_id,
                                )
                            )
                        elif valid_to and self.reference_date > valid_to:
                            findings.append(
                                Finding(
                                    "conv_event_expired",
                                    "high",
                                    f"Event is expired on reference date {self.reference_date}. Valid until: {valid_to}.",
                                    file_ref,
                                    content_id,
                                )
                            )
        else:
            # Check original format startDate
            start_date_str = record.get("startDate")
            if not start_date_str:
                # Try alternative paths
                start_date_str = _get_by_path(record, "publicationEvent.startDate") or \
                                _get_by_path(record, "eventSchedule.startDate") or \
                                _get_by_path(record, "releasedEvent.startDate")
            
            if start_date_str:
                dt = _parse_iso_dt(start_date_str)
                if dt:
                    start_date = dt.date()
                    # For original, just inform if start is significantly in the past
                    # (don't flag as error, just informational since we don't have end date)
                    if self.reference_date < start_date:
                        findings.append(
                            Finding(
                                "orig_event_future",
                                "info",
                                f"Event starts after reference date {self.reference_date}. Starts: {start_date}.",
                                file_ref,
                                content_id,
                            )
                        )
        
        return findings

    def _check_schedule_contract(
        self,
        record: dict[str, Any],
        file_ref: str,
        content_id: str | None,
        is_converted: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []

        required_fields = [
            _normalize_text(item)
            for item in self.cfg.get("schedule_required_fields", [])
            if _normalize_text(item)
        ]
        if not required_fields:
            return findings

        # Avoid applying schedule contract checks to non-schedule content payloads.
        # For viaplay-epg datasets that mix content and schedule structures, this
        # prevents false positives like missing adapterId/date/broadcasts on VOD-like records.
        schedule_only_records = bool(self.cfg.get("schedule_only_records", True))
        if schedule_only_records:
            # Require strong schedule anchors to avoid running schedule checks on
            # content-level payloads that happen to contain one overlapping field.
            has_schedule_container = (
                _first_value(record, ["schedules", "schedule"]) is not None
                or self._first_non_empty_for_field(record, "broadcasts") is not None
            )
            has_schedule_window = (
                self._first_non_empty_for_field(record, "schedule_from") is not None
                and self._first_non_empty_for_field(record, "schedule_to") is not None
            )
            if not (has_schedule_container or has_schedule_window):
                return findings

        prefix = "conv" if is_converted else "orig"
        for field_name in required_fields:
            value = _first_non_empty(
                record,
                [
                    field_name,
                    f"schedule.{field_name}",
                    f"content.{field_name}",
                    f"content.schedule.{field_name}",
                ],
            )
            if _is_empty(value):
                findings.append(
                    Finding(
                        f"{prefix}_schedule_required_field_missing",
                        "high",
                        f"Missing required schedule field '{field_name}'.",
                        file_ref,
                        content_id,
                    )
                )

        allowed_original_channel_ids = {
            _normalize_text(item).lower()
            for item in self.cfg.get("schedule_allowed_original_channel_ids", [])
            if _normalize_text(item)
        }
        if allowed_original_channel_ids:
            original_channel_id = _normalize_text(
                _first_non_empty(
                    record,
                    [
                        "originalChannelId",
                        "schedule.originalChannelId",
                        "content.originalChannelId",
                        "content.schedule.originalChannelId",
                    ],
                )
            )
            if original_channel_id and original_channel_id.lower() not in allowed_original_channel_ids:
                findings.append(
                    Finding(
                        f"{prefix}_schedule_original_channel_id_invalid",
                        "high",
                        "Invalid originalChannelId for SE EPG schedule. "
                        f"Expected one of {sorted(allowed_original_channel_ids)}, got '{original_channel_id}'.",
                        file_ref,
                        content_id,
                    )
                )

        expected_adapter_ids = {
            _normalize_text(item).lower()
            for item in self.cfg.get("schedule_expected_adapter_ids", [])
            if _normalize_text(item)
        }
        if expected_adapter_ids:
            adapter_id = _normalize_text(
                _first_non_empty(
                    record,
                    [
                        "adapterId",
                        "schedule.adapterId",
                        "content.adapterId",
                        "content.schedule.adapterId",
                    ],
                )
            )
            if adapter_id and adapter_id.lower() not in expected_adapter_ids:
                findings.append(
                    Finding(
                        f"{prefix}_schedule_adapter_id_invalid",
                        "high",
                        f"Invalid adapterId for schedule. Expected one of {sorted(expected_adapter_ids)}, got '{adapter_id}'.",
                        file_ref,
                        content_id,
                    )
                )

        return findings

    def run_original_checks(self, records: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        findings: list[Finding] = []
        disabled_original_checks = {
            str(item).strip() for item in self.cfg.get("disabled_original_checks", [])
        }

        grouped_genre: dict[str, int] = defaultdict(int)
        grouped_total: dict[str, int] = defaultdict(int)
        series_episode_counts: Counter[str] = Counter()

        for file_ref, record in records:
            # Fresh per-record key index so _find_values_by_key can cache walks
            # across the many field lookups a single record's checks perform.
            _clear_key_index_cache()
            record_type = _guess_type(record, self.cfg["type_keys"])
            metadata_kind = self._detect_metadata_kind(record)
            content_id = self._record_identity(record)

            findings.extend(self._check_envelope_contract(record, file_ref, is_converted=False))
            if metadata_kind == "epg":
                findings.extend(self._check_epg_contents_contract(record, file_ref, is_converted=False))
                findings.extend(self._check_epg_date_contract(record, file_ref, is_converted=False))
                findings.extend(self._check_broadcast_contract(record, file_ref, is_converted=False))
            if metadata_kind == "event":
                findings.extend(self._check_event_contract(record, file_ref, is_converted=False))

            if _is_empty(content_id):
                findings.append(
                    Finding(
                        "orig_content_id_missing",
                        "high",
                        "Missing contentId (no configured id key resolved).",
                        file_ref,
                        None,
                    )
                )

            # Check if metadata is essentially empty (only system/framework fields)
            system_fields = {"@context", "@type", "metadataOriginCountry", "context"}
            meaningful_fields = {k: v for k, v in record.items() if k not in system_fields and not _is_empty(v)}
            if not meaningful_fields:
                findings.append(
                    Finding(
                        "orig_empty_metadata",
                        "high",
                        "Metadata record is empty or contains only system fields.",
                        file_ref,
                        content_id,
                    )
                )
                continue

            # Check event validity for events with date ranges
            if record_type == "event":
                validity_findings = self._check_event_validity(record, file_ref, content_id, is_converted=False)
                findings.extend(validity_findings)

            findings.extend(
                self._check_schedule_contract(record, file_ref, content_id, is_converted=False)
            )

            title = _first_non_empty_shallow(record, self.candidate["title"])
            if content_id and not _is_empty(title):
                self.content_titles[content_id] = str(title)
            if record_type in {"movie", "series", "episode", "event"} and _is_empty(title):
                findings.append(Finding("orig_title_presence", "high", "Missing title.", file_ref, content_id))

            original_title_languages = _extract_title_languages(record, "ORIGINAL")
            if original_title_languages and "fi" not in original_title_languages:
                findings.append(
                    Finding(
                        "orig_title_language_missing_fi",
                        "high",
                        f"Original title languages should include 'fi' for SeriesTitleToSeriesIdGenerator. Found: {sorted(original_title_languages)}.",
                        file_ref,
                        content_id,
                    )
                )

            description = _extract_description_canonical(record)
            placeholder_descriptions = {
                str(item).strip().lower() for item in self.cfg.get("placeholder_descriptions", [])
            }
            description_text = _normalize_text(description)
            is_placeholder_description = bool(
                description_text and description_text.lower() in placeholder_descriptions
            )

            hierarchy_targets: list[tuple[str, dict[str, Any] | None]] = [("content", _primary_payload(record))]
            payload = _primary_payload(record)
            if isinstance(payload, dict):
                hierarchy_targets.append(("series", payload.get("series") if isinstance(payload.get("series"), dict) else None))
                hierarchy_targets.append(("season", payload.get("season") if isinstance(payload.get("season"), dict) else None))

            for level_name, container in hierarchy_targets:
                if not isinstance(container, dict):
                    continue
                level_description = _normalize_text(_extract_description_from_container(container))
                level_is_placeholder = bool(
                    level_description and level_description.lower() in placeholder_descriptions
                )
                if _is_empty(level_description) or level_is_placeholder:
                    findings.append(
                        Finding(
                            "orig_description_presence",
                            "high",
                            f"Missing description on {level_name} level.",
                            file_ref,
                            content_id,
                        )
                    )

            lang_map = _lang_map(record, self.candidate["descriptions_block"])
            if lang_map:
                for req_lang in self.cfg["required_languages"]:
                    if req_lang not in lang_map:
                        findings.append(
                            Finding(
                                "orig_required_languages",
                                "medium",
                                f"Missing required language '{req_lang}'.",
                                file_ref,
                                content_id,
                            )
                        )

            findings.extend(self._check_iso_codes(record, file_ref, content_id, is_converted=False))

            words = [w for w in description_text.split() if w]
            if not is_placeholder_description and words and len(words) < int(self.cfg["min_description_words"]):
                findings.append(
                    Finding("orig_description_too_short", "low", "Description is too short.", file_ref, content_id)
                )

            images = _extract_images_canonical(record)
            if images:
                findings.extend(_check_original_image_conversion_prerequisites(images, file_ref, content_id, self.cfg))
                required_variants = [item for item in self.cfg.get("required_image_variants", []) if isinstance(item, dict)]
                found_ratios = set()
                for img in images:
                    ratio = _extract_ratio_canonical(img)

                    if ratio:
                        found_ratios.add(ratio)

                    img_type = _normalize_text(img.get("type") or img.get("imageType") or img.get("kind") or img.get("name")).lower()
                    if img_type in set(self.cfg["undefined_image_types"]) and not _is_tvepisode_packshot_exception(record, img):
                        findings.append(
                            Finding("orig_image_type_invalid", "medium", "Image type is undefined/staple.", file_ref, content_id)
                        )

                if required_variants:
                    for requirement in _required_image_variants_missing(record, images, required_variants, self.cfg["type_keys"]):
                        req_ratio = _normalize_text(requirement.get("ratio"))
                        req_purpose = _normalize_text(requirement.get("purpose")).lower()
                        req_scope = _normalize_text(requirement.get("scope")).lower()
                        detail = " ".join(part for part in [req_scope, req_ratio, req_purpose] if part).strip()
                        findings.append(
                            Finding("orig_missing_image_ratio", "medium", f"Missing required image variant {detail}.", file_ref, content_id)
                        )
                else:
                    for req_ratio in self.cfg["required_image_ratios"]:
                        if req_ratio not in found_ratios:
                            findings.append(
                                Finding("orig_missing_image_ratio", "medium", f"Missing image ratio {req_ratio}.", file_ref, content_id)
                            )
                findings.extend(self._check_image_structure(images, file_ref, content_id, is_converted=False))
            else:
                findings.append(Finding("orig_images_missing", "medium", "No images found.", file_ref, content_id))

            genres = _extract_genres_canonical(record, self.candidate.get("genre"))
            if not genres:
                findings.append(Finding("orig_genre_presence", "high", "Missing or empty genres.", file_ref, content_id))

            group_name = _first_non_empty_shallow(record, self.cfg["provider_group_keys"])
            group = _normalize_text(group_name) or "ungrouped"
            grouped_total[group] += 1
            if genres:
                grouped_genre[group] += 1

            series_container = payload.get("series") if isinstance(payload, dict) and isinstance(payload.get("series"), dict) else None
            season_container = payload.get("season") if isinstance(payload, dict) and isinstance(payload.get("season"), dict) else None
            if series_container is not None and not _extract_genres_from_container(series_container, self.candidate.get("genre")):
                findings.append(
                    Finding("orig_hierarchy_genre_presence", "medium", "Missing hierarchy-level genres on series level.", file_ref, content_id)
                )
            if season_container is not None and not _extract_genres_from_container(season_container, self.candidate.get("genre")):
                findings.append(
                    Finding("orig_hierarchy_genre_presence", "medium", "Missing hierarchy-level genres on season level.", file_ref, content_id)
                )

            kids_marker = _first_value_shallow(record, self.cfg["kids_flags_keys"])
            kids_detected = _normalize_text(kids_marker).lower() in {"kids", "kid", "children", "true", "yes"}
            if kids_detected and not any("kid" in g.lower() for g in genres):
                findings.append(Finding("orig_kids_genre_missing", "high", "Kids content missing Kids genre.", file_ref, content_id))

            year_value = _extract_production_year_canonical(record)
            if record_type not in {"event"} and _is_empty(year_value):
                findings.append(Finding("orig_production_year_missing", "medium", "Missing production year.", file_ref, content_id))

            duration = _safe_int(_first_non_empty_shallow(record, self.candidate["duration"]))
            max_asset_duration_seconds = int(self.cfg.get("max_asset_duration_seconds", 21600))
            if duration is not None and duration > max_asset_duration_seconds:
                findings.append(
                    Finding(
                        "orig_duration_invalid",
                        "medium",
                        f"Asset duration {duration}s exceeds threshold {max_asset_duration_seconds}s.",
                        file_ref,
                        content_id,
                    )
                )

            age_rating = _extract_age_rating_canonical(record)
            if _is_empty(age_rating):
                findings.append(Finding("orig_age_rating_missing", "high", "Missing age rating.", file_ref, content_id))

            if metadata_kind == "vod":
                findings.extend(
                    self._check_vod_structural_contract(
                        record,
                        file_ref,
                        content_id,
                        is_converted=False,
                    )
                )

            if record_type == "episode":
                series_id = _normalize_text(_first_non_empty_shallow(record, self.candidate["series_id"])) or "unknown_series"
                series_episode_counts[series_id] += 1

        min_ep = int(self.cfg["episode_count_thresholds"]["min"])
        max_ep = int(self.cfg["episode_count_thresholds"]["max"])
        for series_id, count in series_episode_counts.items():
            if count < min_ep or count > max_ep:
                findings.append(
                    Finding(
                        "orig_episode_count_outlier",
                        "low",
                        f"Series {series_id} has episode count {count} outside [{min_ep}, {max_ep}].",
                        "<aggregate>",
                        series_id,
                    )
                )

        for group, total in grouped_total.items():
            if total == 0 or group == "ungrouped":
                continue
            with_genre = grouped_genre.get(group, 0)
            coverage = with_genre / total
            if coverage < 0.8:
                findings.append(
                    Finding(
                        "orig_group_genre_coverage",
                        "medium",
                        f"Group {group} genre coverage is {coverage:.1%}.",
                        "<aggregate>",
                        group,
                    )
                )

        return [f.as_dict() for f in findings if f.check_id not in disabled_original_checks]

    def run_converted_checks(
        self,
        original_records: list[tuple[str, dict[str, Any]]],
        converted_records: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        findings: list[Finding] = []
        disabled_checks = {str(item).strip() for item in self.cfg.get("disabled_converted_checks", [])}

        original_index: dict[str, tuple[str, dict[str, Any]]] = {}
        for file_ref, record in original_records:
            for content_id in self._record_identity_candidates(record):
                original_index[content_id] = (file_ref, record)

        for conv_file, conv in converted_records:
            # Fresh per-record key index so _find_values_by_key can cache walks
            # across the many field lookups a single record's checks perform.
            _clear_key_index_cache()
            content_id = self._record_identity(conv)
            if not content_id:
                fallback_candidates = self._record_identity_candidates(conv)
                content_id = fallback_candidates[0] if fallback_candidates else None
            conv_record_type = _guess_type(conv, self.cfg["type_keys"])
            metadata_kind = self._detect_metadata_kind(conv)
            conv_images = _extract_images_canonical(conv)

            findings.extend(self._check_envelope_contract(conv, conv_file, is_converted=True))
            if metadata_kind == "epg":
                findings.extend(self._check_epg_contents_contract(conv, conv_file, is_converted=True))
                findings.extend(self._check_epg_date_contract(conv, conv_file, is_converted=True))
                findings.extend(self._check_broadcast_contract(conv, conv_file, is_converted=True))
                findings.extend(self._check_expected_content_shape(conv, conv_file))
            if metadata_kind == "event":
                findings.extend(self._check_event_contract(conv, conv_file, is_converted=True))

            if _is_empty(content_id):
                findings.append(
                    Finding(
                        "conv_content_id_missing",
                        "high",
                        "Missing contentId (no configured id key resolved).",
                        conv_file,
                        None,
                    )
                )

            # Check event validity for converted events
            if conv_record_type == "event":
                validity_findings = self._check_event_validity(conv, conv_file, content_id, is_converted=True)
                findings.extend(validity_findings)

            findings.extend(
                self._check_schedule_contract(conv, conv_file, content_id, is_converted=True)
            )

            conv_genres = _extract_genres_canonical(conv, self.candidate.get("genre"))
            conv_genres_norm = [_normalize_contract_genre(g) for g in conv_genres]
            invalid_conv_genres = sorted({g for g in conv_genres_norm if g and g not in INTERNAL_GENRES})

            if invalid_conv_genres:
                findings.append(
                    Finding(
                        "conv_genre_mapping",
                        "medium",
                        f"Converted genres are outside internal contract: {invalid_conv_genres}.",
                        conv_file,
                        content_id,
                    )
                )

            findings.extend(self._check_iso_codes(conv, conv_file, content_id, is_converted=True))
            findings.extend(self._check_image_structure(conv_images, conv_file, content_id, is_converted=True))

            conv_payload = _primary_payload(conv)
            conv_credits = conv_payload.get("credits") if isinstance(conv_payload, dict) else None
            if not conv_credits:
                findings.append(
                    Finding(
                        "conv_credits_missing",
                        "medium",
                        f'Content "{content_id or "unknown"}" has no credits (cast/crew) information.',
                        conv_file,
                        content_id,
                    )
                )

            content_year = _normalize_text(_get_by_path(conv_payload, "productionYear") or "")
            series_year = _normalize_text(_get_by_path(conv_payload, "series.productionYear") or "")
            season_year = _normalize_text(_get_by_path(conv_payload, "season.productionYear") or "")
            if not any([content_year, series_year, season_year]):
                findings.append(
                    Finding(
                        "conv_production_year_missing",
                        "medium",
                        f'Content "{content_id or "unknown"}" is missing a production year at all hierarchy levels (content/season/series).',
                        conv_file,
                        content_id,
                    )
                )

            if metadata_kind == "vod":
                findings.extend(
                    self._check_vod_structural_contract(
                        conv,
                        conv_file,
                        content_id,
                        is_converted=True,
                    )
                )

            orig = None
            for candidate in self._record_identity_candidates(conv):
                maybe = original_index.get(candidate)
                if maybe is not None:
                    orig = maybe[1]
                    break
            if orig is None:
                findings.append(Finding("conv_no_original_match", "high", "Converted record has no original match.", conv_file, content_id))
                continue

            orig_images = _extract_images_canonical(orig)

            conv_title = _extract_title_canonical(conv)
            if content_id and conv_title:
                self.content_titles.setdefault(content_id, str(conv_title))
            # Check title presence only — exact string comparison is not appropriate because
            # original and converted schemas use different title structures (flat XML list vs
            # structured titles[] array with language/type metadata).
            if "conv_title_mapping" not in disabled_checks and not conv_title:
                findings.append(Finding("conv_title_mapping", "high", "Title missing after conversion (no title found in converted record).", conv_file, content_id))

            conv_desc = _extract_description_canonical(conv)
            # Description: flag only when converted has none at all, not on mismatch, for the same
            # structural schema reasons as titles.
            if not conv_desc:
                conv_level = (
                    conv_record_type
                    if conv_record_type in {"movie", "series", "season", "episode"}
                    else "record"
                )
                findings.append(
                    Finding(
                        "conv_description_mapping",
                        "high",
                        f"Missing mapped description on {conv_level} level after conversion.",
                        conv_file,
                        content_id,
                    )
                )
            if False:  # replaced; description mismatch block superseded above
                pass

            conv_lang = _lang_map(conv, self.candidate["descriptions_block"])
            # Only compare language coverage when the converted record uses the same
            # structured descriptions block format as the original.
            orig_lang = _lang_map(orig, self.candidate["descriptions_block"])
            if orig_lang and conv_lang:
                missing_langs = set(orig_lang.keys()) - set(conv_lang.keys())
                if missing_langs:
                    findings.append(
                        Finding(
                            "conv_language_mapping",
                            "medium",
                            f"Missing language blocks in converted record: {sorted(missing_langs)}.",
                            conv_file,
                            content_id,
                        )
                    )

            required_variants = [item for item in self.cfg.get("required_image_variants", []) if isinstance(item, dict)]
            if required_variants:
                for requirement in required_variants:
                    if not _image_requirement_applies(orig, requirement, self.cfg["type_keys"]):
                        continue
                    if any(_image_matches_requirement(img, requirement) for img in orig_images) and not any(
                        _image_matches_requirement(img, requirement) for img in conv_images
                    ):
                        req_ratio = _normalize_text(requirement.get("ratio"))
                        req_purpose = _normalize_text(requirement.get("purpose")).lower()
                        req_scope = _normalize_text(requirement.get("scope")).lower()
                        detail = " ".join(part for part in [req_scope, req_ratio, req_purpose] if part).strip()
                        findings.append(Finding("conv_image_ratio_mapping", "medium", f"Missing mapped image variant {detail}.", conv_file, content_id))
            else:
                conv_ratios = {_extract_ratio_canonical(i) for i in conv_images if _extract_ratio_canonical(i)}
                orig_ratios = {_extract_ratio_canonical(i) for i in orig_images if _extract_ratio_canonical(i)}
                for req_ratio in self.cfg["required_image_ratios"]:
                    if req_ratio in orig_ratios and req_ratio not in conv_ratios:
                        findings.append(Finding("conv_image_ratio_mapping", "medium", f"Missing mapped image ratio {req_ratio}.", conv_file, content_id))

            orig_types = {
                _normalize_image_type_canonical(i.get("type") or i.get("imageType") or i.get("kind") or i.get("name"))
                for i in orig_images
            }
            conv_types = {
                _normalize_image_type_canonical(i.get("type") or i.get("imageType") or i.get("kind") or i.get("name"))
                for i in conv_images
            }

            # Known accepted Viaplay pattern: TVEpisode may carry movie-style "packshot" image names.
            if _normalize_text(orig.get("@type") or orig.get("type")).lower() == "tvepisode":
                orig_types.discard("packshot")
                conv_types.discard("packshot")

            orig_types.discard("")
            conv_types.discard("")
            if orig_types and conv_types and orig_types.isdisjoint(conv_types):
                findings.append(Finding("conv_image_type_mapping", "medium", "Image types are not fully mapped.", conv_file, content_id))

            orig_genres = _extract_genres_canonical(orig, self.candidate.get("genre"))
            if (not invalid_conv_genres) and orig_genres and not conv_genres:
                findings.append(
                    Finding(
                        "conv_genre_mapping",
                        "medium",
                        "Converted record is missing genres after mapping.",
                        conv_file,
                        content_id,
                    )
                )

            conv_type = _guess_type(conv, self.cfg["type_keys"])
            if conv_type in {"series", "season", "episode"} and not conv_genres:
                findings.append(Finding("conv_hierarchy_genre_missing", "medium", "Missing hierarchy-level genres.", conv_file, content_id))

            kids_marker = _first_value(conv, self.cfg["kids_flags_keys"])
            kids_detected = _normalize_text(kids_marker).lower() in {"kids", "kid", "children", "true", "yes"}
            if kids_detected and conv_genres:
                if "kids" not in conv_genres[0].lower():
                    findings.append(Finding("conv_kids_not_first", "low", "Kids genre should be first.", conv_file, content_id))

            conv_year = _extract_production_year_canonical(conv)
            orig_year = _extract_production_year_canonical(orig)
            if "conv_production_year_mapping" not in disabled_checks and conv_year != orig_year:
                findings.append(Finding("conv_production_year_mapping", "medium", "Production year mismatch.", conv_file, content_id))

            conv_duration = _extract_duration_canonical(conv)
            orig_duration = _extract_duration_canonical(orig)
            if "conv_duration_mapping" not in disabled_checks and (conv_duration is None or conv_duration <= 0 or conv_duration != orig_duration):
                findings.append(Finding("conv_duration_mapping", "high", "Duration mismatch or invalid value.", conv_file, content_id))

            conv_rating = _extract_age_rating_canonical(conv)
            if _is_empty(conv_rating):
                findings.append(
                    Finding(
                        "conv_age_rating_missing",
                        "medium",
                        f'Content "{content_id or "unknown"}" is missing an age rating.',
                        conv_file,
                        content_id,
                    )
                )

            conv_link = _extract_deeplink_canonical(conv)
            orig_link = _extract_deeplink_canonical(orig)
            if (orig_link and not conv_link) or (orig_link and conv_link and conv_link != orig_link):
                findings.append(Finding("conv_deeplink_mapping", "medium", "Deeplink mismatch or missing.", conv_file, content_id))

            # Detect likely mojibake / decoding artifacts without flagging ordinary punctuation.
            mojibake_markers = (
                "\ufffd",  # replacement character
                "\u00c3",
                "\u00c2",
                "\u00d0",
                "\u00d1",
                "\u00e2\u20ac",
                "\u00e2\u20ac\u2122",
                "\u00e2\u20ac\u0153",
                "\u00e2\u20ac\x9d",
                "\u00e2\u20ac\u201c",
                "\u00e2\u20ac\u201d",
            )

            # Support explicit latin-1 decoded UTF-8 punctuation artifacts.
            mojibake_markers += (
                "\u00c3\u00a5",  # å
                "\u00c3\u00a4",  # ä
                "\u00c3\u00b6",  # ö
            )

            def _looks_like_encoding_issue(text: str) -> bool:
                t = _normalize_text(text)
                if not t:
                    return False
                return any(marker in t for marker in mojibake_markers)

            if _looks_like_encoding_issue(conv_title):
                findings.append(Finding("conv_special_chars", "medium", "Possible title encoding issue.", conv_file, content_id))
            if _looks_like_encoding_issue(conv_desc or ""):
                findings.append(Finding("conv_special_chars", "medium", "Possible description encoding issue.", conv_file, content_id))

        return [f.as_dict() for f in findings if f.check_id not in disabled_checks]


def summarize_findings(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_check = Counter(item["check_id"] for item in findings)
    by_severity = Counter(item["severity"] for item in findings)
    has_errors = by_severity.get("ERROR", 0) > 0
    return {
        "total_findings": len(findings),
        "by_check": dict(by_check),
        "by_severity": dict(by_severity),
        "has_errors": has_errors,
    }
