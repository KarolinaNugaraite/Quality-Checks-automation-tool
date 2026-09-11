"""
Viaplay FI grouping checker — framework-agnostic core logic.

Three public entry points:
    parse_episode_files(file_objects)                   → list[EpisodeRecord]
    GroupingApiClient(refresh_token=... | access_token=...) → client with .check_guids(guids)
    build_report(episodes, results)                     → dict
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

TOKEN_EXCHANGE_URL = (
    "https://sso.tvm.telia.com/realms/tvm/protocol/openid-connect/token"
)
CLIENT_ID = "inspector-gadget"
TOKEN_TTL_SECONDS = 240  # re-exchange after 4 minutes
IG_ENVIRONMENT = os.getenv("IG_ENVIRONMENT", "pilot").strip().lower() or "pilot"
GROUPING_SEARCH_WORKERS = max(1, int(os.getenv("GROUPING_SEARCH_WORKERS", "24")))
GROUPING_SEARCH_PAGE_SIZE = max(1, int(os.getenv("GROUPING_SEARCH_PAGE_SIZE", "10")))

# Process-wide cap on concurrent outbound search requests. GROUPING_SEARCH_WORKERS
# only bounds concurrency *within a single check_guids() call* — but the web app
# can run several chunks concurrently (once per adaptive-split request), each
# spinning up its own thread pool. Without a shared cap across all of them, total
# outbound concurrency could multiply (chunks x workers) and overwhelm the pilot
# adminportal host, which is a lower-capacity/shared environment and starts
# returning read timeouts under heavy concurrent load.
GROUPING_MAX_CONCURRENT_REQUESTS = max(
    1, int(os.getenv("GROUPING_MAX_CONCURRENT_REQUESTS", "8"))
)
_SEARCH_SEMAPHORE = threading.Semaphore(GROUPING_MAX_CONCURRENT_REQUESTS)

# The real "search by original content id" lookup lives on the authenticated
# inspectorgadgetservice (via adminportal), NOT the public inspector-gadget-api
# host previously used here — confirmed by inspecting real Inspector Gadget UI
# network traffic. Each environment has its own adminportal subdomain.
ADMINPORTAL_HOSTS = {
    "pilot": "adminportal-pilot.tvm.telia.com",
    "staging": "adminportal-staging.tvm.telia.com",
    "production": "adminportal.tvm.telia.com",
}
SEARCH_PATH = "/adminportal/rest/inspectorgadgetservice/rest/v1/search"
MAPPED_METADATA_PATH = "/adminportal/rest/inspectorgadgetservice/rest/v1/metadata/mapped-metadata"

# Maps the canonical "W:H" ratio text used in checker.py findings (e.g. "2:3")
# to the enum values IG's mapped-metadata response uses for merged images
# (e.g. "RATIO_2X3"). Anything not listed here falls back to the same
# W:H -> RATIO_WxH pattern (see _ratio_label_for()).
IMAGE_RATIO_LABELS = {
    "16:9": "RATIO_16X9",
    "2:3": "RATIO_2X3",
    "3:2": "RATIO_3X2",
    "1:1": "RATIO_1X1",
    "4:3": "RATIO_4X3",
}


def _ratio_label_for(ratio: str) -> str:
    normalized = str(ratio or "").strip()
    if normalized in IMAGE_RATIO_LABELS:
        return IMAGE_RATIO_LABELS[normalized]
    return f"RATIO_{normalized.replace(':', 'X')}"


# --------------------------------------------------------------------------- #
# Data classes                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class EpisodeRecord:
    guid: str
    series_guid: str
    series_name: str
    episode_number: int | None
    season_number: int | None
    source_file: str


@dataclass
class GuidResult:
    guid: str
    found: bool
    grouped: bool
    media_id: str | None = None
    series_id: str | None = None
    season_id: str | None = None
    error: str | None = None
    # True when this result came from the mapped-metadata fallback rather than
    # /v1/search. Grouping data is confirmed, but the item may still be missing
    # from IG's search index / channel listings (a separate visibility gap) —
    # keep this distinct so reports don't imply full IG visibility.
    via_fallback: bool = False


# --------------------------------------------------------------------------- #
# File parser                                                                  #
# --------------------------------------------------------------------------- #

def parse_episode_files(
    file_objects: list[Any],
) -> list[EpisodeRecord]:
    """
    Accept a list of file-like objects (or paths). Each may contain a single
    JSON object or an array. Returns deduplicated episode-like records that can
    be linked to a series.
    """
    episodes, _ = parse_episode_files_with_debug(file_objects)
    return episodes


def parse_episode_files_with_debug(
    file_objects: list[Any],
) -> tuple[list[EpisodeRecord], dict[str, Any]]:
    """Parse episodes and return compact diagnostics for zero-result troubleshooting."""
    seen_guids: set[str] = set()
    episodes: list[EpisodeRecord] = []
    debug: dict[str, Any] = {
        "files_total": len(file_objects),
        "files_json_ok": 0,
        "files_json_failed": 0,
        "candidate_records_total": 0,
        "episode_like_records": 0,
        "missing_series_link": 0,
        "missing_episode_id": 0,
        "duplicate_episode_id": 0,
        "xml_like_inputs": 0,
        "sample_reasons": [],
    }

    def _push_reason(name: str, reason: str) -> None:
        # Keep the payload small but actionable for API/UI errors.
        if len(debug["sample_reasons"]) < 8:
            debug["sample_reasons"].append({"file": name, "reason": reason})

    def _to_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except (ValueError, TypeError):
            return None

    def _first_non_empty_string(value: Any) -> str:
        if isinstance(value, str):
            text = value.strip()
            return text if text else ""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            text = str(value).strip()
            return text if text else ""
        if isinstance(value, dict):
            for key in ("guid", "id", "seriesId", "series_id", "value", "contentId", "content_id"):
                candidate = _first_non_empty_string(value.get(key))
                if candidate:
                    return candidate
        if isinstance(value, list):
            for item in value:
                candidate = _first_non_empty_string(item)
                if candidate:
                    return candidate
        return ""

    def _is_episode_record(record: dict[str, Any]) -> bool:
        # Viaplay EPG may use different type fields/labels than literal "TVEpisode".
        type_candidates = [
            record.get("@type"),
            record.get("type"),
            record.get("contentType"),
            record.get("assetType"),
        ]
        normalized = {
            str(value).strip().lower().replace("_", "").replace("-", "").replace(" ", "")
            for value in type_candidates
            if value is not None and str(value).strip()
        }
        if any(
            value in {
                "tvepisode",
                "episode",
                "episodic",
                "programepisode",
                "tvprogramepisode",
            }
            for value in normalized
        ):
            return True

        # Some Viaplay EPG records omit explicit type but still expose episode/series structure.
        has_episode_number = any(
            record.get(key) is not None
            for key in ("episodeNumber", "episode_number", "episodeNo", "episode_no", "number")
        )
        has_series_link = any(
            record.get(key) is not None
            for key in (
                "partOfSeries",
                "partOf",
                "series",
                "seriesId",
                "series_id",
                "seriesGuid",
                "series_guid",
                "originalSeriesId",
            )
        )
        return bool(has_episode_number and has_series_link)

    def _extract_episode_id(record: dict[str, Any]) -> str:
        # Converted metadata is commonly keyed by contentId; keep other id flavors as fallback.
        for key in (
            "contentId",
            "content_id",
            "originalContentId",
            "guid",
            "id",
            "episodeId",
            "episode_id",
        ):
            value = _first_non_empty_string(record.get(key))
            if value:
                return value
        return ""

    def _extract_title_from_titles(block: dict[str, Any]) -> str:
        # Converted Viaplay schema has no flat "name" field on series/episode blocks —
        # names live in a "titles" array of {language, value, type}. Prefer "FULL"
        # type titles, favoring Swedish, falling back to the first usable value.
        titles = block.get("titles")
        if not isinstance(titles, list):
            return ""
        preferred = None
        swedish_full = None
        fallback = None
        for item in titles:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            if not value:
                continue
            title_type = str(item.get("type") or "").strip().upper()
            language = str(item.get("language") or "").strip().lower()
            if title_type == "FULL" and language == "sv":
                swedish_full = value
                break
            if title_type == "FULL" and preferred is None:
                preferred = value
            if fallback is None:
                fallback = value
        return swedish_full or preferred or fallback or ""

    def _extract_series_data(record: dict[str, Any]) -> tuple[str, str]:
        # Viaplay EPG can model series relation via partOfSeries, series, or flat seriesId.
        def _from_series_block(series_block: dict[str, Any]) -> tuple[str, str]:
            series_guid = _first_non_empty_string(
                series_block.get("guid")
                or series_block.get("id")
                or series_block.get("seriesId")
                or series_block.get("series_id")
                or series_block.get("originalSeriesId")
            )
            series_name = _first_non_empty_string(
                series_block.get("name")
                or _extract_title_from_titles(series_block)
                or series_guid
            )
            if series_guid:
                return series_guid, series_name
            return "", ""

        part_of_series = record.get("partOfSeries")
        if isinstance(part_of_series, dict):
            resolved_guid, resolved_name = _from_series_block(part_of_series)
            if resolved_guid:
                return resolved_guid, resolved_name

        # Common hierarchy: episode.partOf -> season, season.partOf -> series.
        part_of = record.get("partOf")
        if isinstance(part_of, dict):
            resolved_guid, resolved_name = _from_series_block(part_of)
            if resolved_guid:
                return resolved_guid, resolved_name

            nested_parent = part_of.get("partOf")
            if isinstance(nested_parent, dict):
                resolved_guid, resolved_name = _from_series_block(nested_parent)
                if resolved_guid:
                    return resolved_guid, resolved_name
        elif isinstance(part_of, list):
            for parent in part_of:
                if not isinstance(parent, dict):
                    continue
                resolved_guid, resolved_name = _from_series_block(parent)
                if resolved_guid:
                    return resolved_guid, resolved_name
                nested_parent = parent.get("partOf")
                if isinstance(nested_parent, dict):
                    resolved_guid, resolved_name = _from_series_block(nested_parent)
                    if resolved_guid:
                        return resolved_guid, resolved_name

        series_block = record.get("series")
        if isinstance(series_block, dict):
            resolved_guid, resolved_name = _from_series_block(series_block)
            if resolved_guid:
                return resolved_guid, resolved_name

        series_guid = _first_non_empty_string(
            record.get("seriesId") or record.get("series_id") or record.get("originalSeriesId")
        )
        if series_guid:
            series_name = _first_non_empty_string(record.get("seriesName") or record.get("series_name") or series_guid)
            return series_guid, series_name

        series_guid = _first_non_empty_string(record.get("seriesGuid") or record.get("series_guid"))
        if series_guid:
            series_name = _first_non_empty_string(record.get("seriesName") or record.get("series_name") or series_guid)
            return series_guid, series_name

        return "", ""

    def _candidate_records(payload: Any) -> list[dict[str, Any]]:
        def _expand_dict(item: dict[str, Any]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = [item]
            contents = item.get("contents")
            if isinstance(contents, list):
                for nested in contents:
                    if isinstance(nested, dict):
                        # Preserve parent envelope fields for nested content rows.
                        out.append({**item, **nested})
            content = item.get("content")
            if isinstance(content, dict):
                # Same preservation for singular content payloads.
                out.append({**item, **content})
            return out

        if isinstance(payload, list):
            out: list[dict[str, Any]] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                out.extend(_expand_dict(item))
            return out
        if isinstance(payload, dict):
            return _expand_dict(payload)
        return []

    for file_obj in file_objects:
        name = getattr(file_obj, "filename", getattr(file_obj, "name", "<unknown>"))
        try:
            if hasattr(file_obj, "read"):
                raw = file_obj.read()
            else:
                # Close files promptly; avoid leaking descriptors on large batches.
                with open(file_obj, "rb") as f:
                    raw = f.read()
                name = str(file_obj)

            if isinstance(raw, bytes) and raw.lstrip().startswith(b"<"):
                debug["xml_like_inputs"] += 1
                _push_reason(str(name), "Input looks like XML, expected converted JSON.")

            payload = json.loads(raw)
            debug["files_json_ok"] += 1
        except Exception as exc:
            debug["files_json_failed"] += 1
            _push_reason(str(name), f"JSON parse failed: {exc}")
            logger.warning("Skipping file %s: %s", getattr(file_obj, "filename", "?"), exc)
            continue

        records = _candidate_records(payload)
        debug["candidate_records_total"] += len(records)

        matched_any_in_file = False
        for record in records:
            if not isinstance(record, dict):
                continue
            if not _is_episode_record(record):
                continue

            debug["episode_like_records"] += 1
            series_guid, series_name = _extract_series_data(record)
            if not series_guid:
                debug["missing_series_link"] += 1
                continue

            guid = _extract_episode_id(record)
            if not guid:
                debug["missing_episode_id"] += 1
                continue
            if guid in seen_guids:
                debug["duplicate_episode_id"] += 1
                continue

            ep_num: int | None = None
            ep_num = (
                _to_int(record.get("episodeNumber"))
                or _to_int(record.get("episode_number"))
                or _to_int(record.get("episodeNo"))
                or _to_int(record.get("episode_no"))
            )

            season_num: int | None = None
            season_block = record.get("season") or record.get("partOfSeason") or {}
            if isinstance(season_block, dict):
                season_num = (
                    _to_int(season_block.get("seasonNumber"))
                    or _to_int(season_block.get("season_number"))
                    or _to_int(season_block.get("number"))
                )
            if season_num is None:
                season_num = (
                    _to_int(record.get("seasonNumber"))
                    or _to_int(record.get("season_number"))
                )

            # Common hierarchy fallback: episode.partOf.seasonNumber or episode.partOf.partOf.seasonNumber
            if season_num is None and isinstance(record.get("partOf"), dict):
                part_of_block = record.get("partOf") or {}
                season_num = (
                    _to_int(part_of_block.get("seasonNumber"))
                    or _to_int(part_of_block.get("season_number"))
                    or _to_int((part_of_block.get("partOf") or {}).get("seasonNumber"))
                    if isinstance(part_of_block.get("partOf"), dict)
                    else None
                )

            seen_guids.add(guid)
            matched_any_in_file = True
            episodes.append(
                EpisodeRecord(
                    guid=guid,
                    series_guid=series_guid,
                    series_name=series_name,
                    episode_number=ep_num,
                    season_number=season_num,
                    source_file=str(name),
                )
            )

        if not matched_any_in_file:
            _push_reason(str(name), "No episode rows matched after parsing.")

    debug["episodes_found"] = len(episodes)
    return episodes, debug


# --------------------------------------------------------------------------- #
# API client                                                                   #
# --------------------------------------------------------------------------- #

class GroupingApiClient:
    def __init__(
        self,
        refresh_token: str | None = None,
        access_token: str | None = None,
        country_code: str = "FI",
    ) -> None:
        self._refresh_token = self._normalize_token_text(refresh_token)
        self._access_token: str | None = self._normalize_token_text(access_token)
        self._token_issued_at: float = time.time() if self._access_token else 0.0
        # Keep lookup country configurable so SE/NO payloads don't query FI cache.
        self._country_code = str(country_code or "FI").strip().upper() or "FI"
        # The real IG search API takes country as an uppercase query param
        # (confirmed via real UI network capture: ?country=SE), unlike the old
        # (wrong) asset-by-id-type host which used a lowercase path segment.
        self._country_query = self._country_code
        self._http = requests.Session()
        # Default pool size (10) is smaller than GROUPING_SEARCH_WORKERS, so
        # concurrent threads were serializing on connection checkout instead of
        # actually running in parallel. Size the pool to match the worker count.
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=GROUPING_SEARCH_WORKERS,
            pool_maxsize=GROUPING_SEARCH_WORKERS,
        )
        self._http.mount("https://", adapter)
        self._http.mount("http://", adapter)

    def _search_base_url(self) -> str:
        """Base URL for the real Inspector Gadget search endpoint (adminportal host)."""
        host = ADMINPORTAL_HOSTS.get(IG_ENVIRONMENT, ADMINPORTAL_HOSTS["pilot"])
        return f"https://{host}{SEARCH_PATH}"

    def _mapped_metadata_base_url(self) -> str:
        """Base URL for the mapped-metadata fallback endpoint (adminportal host)."""
        host = ADMINPORTAL_HOSTS.get(IG_ENVIRONMENT, ADMINPORTAL_HOSTS["pilot"])
        return f"https://{host}{MAPPED_METADATA_PATH}"

    @staticmethod
    def _normalize_lookup_id(value: str) -> str:
        """Normalize lookup ids for tolerant matching across response shapes/casing."""
        text = str(value or "").strip()
        if not text:
            return ""
        return text.replace("%2F", "/").replace("%2f", "/").lower()

    @staticmethod
    def _expand_lookup_ids(guid: str) -> list[str]:
        """Generate alias IDs for IG lookup (EPG content ids and canonical original-content ids)."""
        base = str(guid or "").strip()
        if not base:
            return []

        aliases: list[str] = [base]

        # Convert se.viaplay.epg.*.content.105240XXXXXXXX... to
        # se.viaplay.content.10.5240/XXXX-XXXX-XXXX-XXXX-XXXX-X when possible.
        if ".content." in base and base.startswith("se.viaplay.epg."):
            compact = base.split(".content.", 1)[1].strip()
            if compact.startswith("105240") and len(compact) >= 27:
                suffix = compact[6:]
                groups = [suffix[0:4], suffix[4:8], suffix[8:12], suffix[12:16], suffix[16:20], suffix[20:]]
                if all(groups):
                    canonical = f"se.viaplay.content.10.5240/{'-'.join(groups)}"
                    if canonical not in aliases:
                        aliases.append(canonical)

        # Also include compact alias for canonical ids in case an endpoint expects no separators.
        if base.startswith("se.viaplay.content.10.5240/"):
            token = base.split("/", 1)[1].replace("-", "")
            if token:
                compact_epg = f"se.viaplay.epg.crime.content.105240{token}"
                if compact_epg not in aliases:
                    aliases.append(compact_epg)

        return aliases

    @staticmethod
    def _normalize_token_text(raw_token: str | None) -> str:
        """Accept plain token, Bearer-prefixed or pasted JSON payload with token fields."""
        text = str(raw_token or "").strip()
        if not text:
            return ""

        if text.lower().startswith("bearer "):
            text = text[7:].strip()

        # Handle copied JSON snippets from browser/network tools.
        if text.startswith("{") and text.endswith("}"):
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    for key in (
                        "refresh_token",
                        "refreshToken",
                        "access_token",
                        "accessToken",
                        "token",
                    ):
                        candidate = payload.get(key)
                        if isinstance(candidate, str) and candidate.strip():
                            return candidate.strip()
            except json.JSONDecodeError:
                pass

        return text.strip().strip('"').strip("'")

    @staticmethod
    def _token_exchange_error_message(resp: requests.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            body = {}

        error = str(body.get("error") or "").strip()
        description = str(body.get("error_description") or "").strip()
        if error and description:
            return f"{error}: {description}"
        if error:
            return error
        if description:
            return description
        return f"HTTP {resp.status_code}"

    # ------------------------------------------------------------------ #
    # Token management                                                     #
    # ------------------------------------------------------------------ #

    def exchange_token(self) -> str:
        """Exchange refresh token for a new access token. Raises ValueError on failure."""
        if not self._refresh_token:
            raise ValueError("Missing refresh token.")

        resp = self._http.post(
            TOKEN_EXCHANGE_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": self._refresh_token,
            },
            timeout=15,
        )

        if resp.status_code >= 400:
            raise ValueError(self._token_exchange_error_message(resp))

        body = resp.json()
        self._access_token = body["access_token"]
        self._token_issued_at = time.time()
        return self._access_token

    def _ensure_token(self) -> str:
        if (
            self._access_token is None
            or (time.time() - self._token_issued_at) > TOKEN_TTL_SECONDS
        ):
            self.exchange_token()
        return self._access_token  # type: ignore[return-value]

    def validate_current_access_token(self) -> bool:
        """Best-effort check whether current access token is accepted by Inspector Gadget."""
        token = self._normalize_token_text(self._access_token)
        if not token:
            return False

        try:
            # We only care if auth is accepted; a 400/empty-result business
            # response still proves token validity for this API.
            resp = self._http.get(
                self._search_base_url(),
                params={
                    "query": "__auth_probe__",
                    "country": self._country_query,
                    "category": "ANY",
                    "page": 1,
                    "size": 1,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException:
            return False

        if resp.status_code in {401, 403}:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Asset lookup                                                         #
    # ------------------------------------------------------------------ #

    def _search_media(self, token: str, query: str) -> list[dict[str, Any]]:
        """
        Search Inspector Gadget for media matching a query string (typically an
        original content id or one of its canonical aliases).

        This calls the real, authenticated search endpoint used by the Inspector
        Gadget UI itself (confirmed via browser DevTools network capture):
            GET {adminportal-host}/adminportal/rest/inspectorgadgetservice/rest/v1/search
                ?query=...&country=...&category=ANY&page=1&size=50

        Returns only entries whose "type" is "media" (search can also return
        stores/channels).

        Uses a process-wide semaphore (GROUPING_MAX_CONCURRENT_REQUESTS) so total
        outbound concurrency stays bounded even when several chunk requests are
        being handled at once by the web app — the pilot adminportal host is a
        shared/limited environment and starts returning read timeouts if too many
        requests hit it simultaneously. A single retry is attempted for transient
        read timeouts/connection errors before giving up.
        """
        last_exc: Exception | None = None
        for attempt in range(2):
            with _SEARCH_SEMAPHORE:
                try:
                    resp = self._http.get(
                        self._search_base_url(),
                        params={
                            "query": query,
                            "country": self._country_query,
                            "category": "ANY",
                            "page": 1,
                            "size": GROUPING_SEARCH_PAGE_SIZE,
                        },
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=20,
                    )
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                    last_exc = exc
                    if attempt == 0:
                        time.sleep(0.5)
                        continue
                    raise
            resp.raise_for_status()
            break
        else:
            if last_exc is not None:
                raise last_exc

        try:
            payload = resp.json()
        except ValueError as exc:
            content_type = (resp.headers.get("content-type") or "").lower()
            snippet = (resp.text or "").strip().replace("\n", " ")[:180]
            raise ValueError(
                "Inspector Gadget search API returned non-JSON response. "
                f"status={resp.status_code}, content-type={content_type or 'unknown'}, body={snippet}"
            ) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []

        return [
            item
            for item in data
            if isinstance(item, dict) and item.get("type") == "media"
        ]

    def _lookup_mapped_metadata(self, token: str, guid: str) -> dict[str, Any] | None:
        """
        Fallback lookup used when /v1/search has no match for a guid.

        /v1/search runs against a separate search index that can lag behind the
        actual grouping/mapping data — confirmed live: several episodes that
        /v1/search returned zero results for were nonetheless fully grouped
        (had valid seriesId/seasonId) when queried here instead. This endpoint
        reads mapped metadata directly by original content id, bypassing the
        search index entirely. It expects the compact (non-canonical-alias) id.
        """
        with _SEARCH_SEMAPHORE:
            try:
                resp = self._http.get(
                    f"{self._mapped_metadata_base_url()}",
                    params={
                        "country": self._country_query,
                        "idType": "CONTENT",
                        "id": guid,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=20,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                return None

        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        try:
            payload = resp.json()
        except ValueError:
            return None

        if not isinstance(payload, dict) or not payload.get("success"):
            return None

        media = (payload.get("data") or {}).get("media") or {}
        references = media.get("references") or {}
        series_refs = references.get("SERIES") or []
        season_refs = references.get("SEASON") or []

        series_id = series_refs[0].get("id") if series_refs else None
        season_id = season_refs[0].get("id") if season_refs else None

        if not series_id and not season_id:
            return None

        return {"series_id": series_id, "season_id": season_id}

    def _fetch_merged_metadata(self, token: str, content_id: str) -> tuple[dict[str, Any] | None, str | None]:
        """
        Shared fetch for cross-source checks: calls the mapped-metadata
        endpoint (idType=CONTENT) and returns the merged `media.metadata`
        dict, which aggregates fields (images/title/synopsis/genre/...)
        across every provider source IG's content-matcher has grouped under
        this content's universal media (EPG + SVOD + deeplink-VOD, etc.).

        Returns (metadata_dict, None) on success or (None, error_message) on
        failure — callers attach the error to their own response shape.
        """
        with _SEARCH_SEMAPHORE:
            resp = self._http.get(
                self._mapped_metadata_base_url(),
                params={
                    "country": self._country_query,
                    "idType": "CONTENT",
                    "id": content_id,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )

        if resp.status_code == 404:
            return None, "Content id not found in Inspector Gadget."
        resp.raise_for_status()

        try:
            payload = resp.json()
        except ValueError:
            return None, "Unexpected (non-JSON) response from Inspector Gadget."

        if not isinstance(payload, dict) or not payload.get("success"):
            return None, "Inspector Gadget returned an unsuccessful response."

        media = (payload.get("data") or {}).get("media") or {}
        metadata = media.get("metadata") or {}
        return metadata, None

    def check_image_ratio(self, token: str, content_id: str, ratio: str) -> dict[str, Any]:
        """
        Live "is this missing-image finding a real gap?" check for
        MISSING_16X9_IMAGE / MISSING_2X3_IMAGE findings.

        Background: IG's content-matcher groups every provider source for the
        same title (EPG broadcast, SVOD/VOD, deeplink-VOD, ...) under one
        universal media, and the media/GraphQL layer serves a *merged* image
        pool across all of them. So a broadcast can genuinely be missing an
        image ratio at the EPG-source level (a real finding, worth reporting
        to Viaplay) while GraphQL still shows that ratio because a different
        matched source (e.g. the SVOD asset) supplied it.

        This calls the same mapped-metadata endpoint used by the grouping
        fallback (idType=CONTENT), but here we inspect the merged
        `media.metadata.images` pool instead of series/season references, to
        see whether the missing ratio is available via *any* matched source.
        """
        target_label = _ratio_label_for(ratio)

        metadata, error = self._fetch_merged_metadata(token, content_id)
        if error:
            return {"found": False, "matched_adapters": [], "ratio_label": target_label, "error": error}

        images_by_type = (metadata or {}).get("images") or {}

        found = False
        for image_list in images_by_type.values():
            if not isinstance(image_list, list):
                continue
            for entry in image_list:
                value = (entry or {}).get("value") or {}
                if value.get("imageRatio") == target_label:
                    found = True
                    break
            if found:
                break

        matched_adapters = sorted(set((metadata or {}).get("providerContentAdapterId") or []))

        return {
            "found": found,
            "matched_adapters": matched_adapters,
            "ratio_label": target_label,
            "error": None,
        }

    def check_field_presence(self, token: str, content_id: str, field: str) -> dict[str, Any]:
        """
        Same cross-source idea as check_image_ratio() but for text fields:
        MISSING_TITLE / MISSING_DESCRIPTION / MISSING_GENRE / UNMAPPED_GENRE
        findings. Checks whether the merged mapped-metadata (any matched
        provider source) has a non-empty value for the field, even though
        this specific EPG/converted source doesn't.

        `field` is one of "title", "description", "genre".
        """
        metadata, error = self._fetch_merged_metadata(token, content_id)
        if error:
            return {"found": False, "matched_adapters": [], "field": field, "value_preview": None, "error": error}

        found = False
        value_preview: str | None = None

        if field == "title":
            title = (metadata or {}).get("title") or {}
            for block_name in ("full", "original", "sub", "epg"):
                block = title.get(block_name) or {}
                if not isinstance(block, dict):
                    continue
                for lang_entry in block.values():
                    text = (lang_entry or {}).get("value") if isinstance(lang_entry, dict) else None
                    if text and str(text).strip():
                        found = True
                        value_preview = str(text).strip()
                        break
                if found:
                    break

        elif field == "description":
            synopsis = (metadata or {}).get("synopsis") or {}
            for block_name in ("briefSynopsis", "shortSynopsis", "mediumSynopsis", "longSynopsis", "extendedSynopsis"):
                block = synopsis.get(block_name) or {}
                if not isinstance(block, dict):
                    continue
                for lang_entry in block.values():
                    text = (lang_entry or {}).get("value") if isinstance(lang_entry, dict) else None
                    if text and str(text).strip():
                        found = True
                        value_preview = str(text).strip()[:120]
                        break
                if found:
                    break

        elif field == "genre":
            main_genre = (metadata or {}).get("mainGenre") or {}
            genre_value = main_genre.get("value") if isinstance(main_genre, dict) else None
            genre_name = genre_value.get("value") if isinstance(genre_value, dict) else None
            if genre_name and str(genre_name).strip():
                found = True
                value_preview = str(genre_name).strip()

        matched_adapters = sorted(set((metadata or {}).get("providerContentAdapterId") or []))

        return {
            "found": found,
            "matched_adapters": matched_adapters,
            "field": field,
            "value_preview": value_preview,
            "error": None,
        }

    def check_guids(self, guids: list[str]) -> dict[str, GuidResult]:
        """
        Query Inspector Gadget's mapped metadata for each guid and return a
        dict keyed by the original guid.

        Mapped metadata is the authoritative grouping source and avoids the
        slower search-index and alias lookups for normal grouped episodes.
        Search is retained as a fallback for entries with no direct mapping.
        """
        results: dict[str, GuidResult] = {}
        token = self._ensure_token()

        def _check_guid(guid: str) -> tuple[str, GuidResult]:
            # This endpoint exposes the actual series/season references used
            # for grouping. Query it first instead of paying for one or more
            # search-index queries that may be stale or require alias retries.
            mapped = self._lookup_mapped_metadata(token, guid)
            if mapped is not None:
                return guid, GuidResult(
                    guid=guid,
                    found=True,
                    grouped=bool(mapped.get("series_id")),
                    media_id=None,
                    series_id=mapped.get("series_id"),
                    season_id=mapped.get("season_id"),
                )

            matched_item: dict[str, Any] | None = None
            last_exc: Exception | None = None
            for query_id in self._expand_lookup_ids(guid):
                try:
                    matches = self._search_media(token, query_id)
                except requests.HTTPError as exc:
                    response = getattr(exc, "response", None)
                    status = response.status_code if response is not None else "?"
                    logger.warning(
                        "Grouping search failed guid=%s query=%s status=%s: %s",
                        guid, query_id, status, exc,
                    )
                    last_exc = exc
                    continue
                except Exception as exc:
                    logger.warning(
                        "Grouping search error guid=%s query=%s: %s", guid, query_id, exc
                    )
                    last_exc = exc
                    continue

                if matches:
                    matched_item = matches[0]
                    break

            if matched_item is None:
                return guid, GuidResult(
                    guid=guid,
                    found=False,
                    grouped=False,
                    error=str(last_exc) if last_exc else None,
                )

            attributes: dict[str, Any] = matched_item.get("attributes") or {}
            media_id = matched_item.get("id") or attributes.get("mediaId")
            series_id = attributes.get("seriesId")
            # /v1/media?mediaId=... response — so it stays unset here.
            season_id = attributes.get("seasonId")

            return guid, GuidResult(
                guid=guid,
                found=True,
                grouped=bool(series_id),
                media_id=media_id,
                series_id=series_id,
                season_id=season_id,
            )

        max_workers = min(GROUPING_SEARCH_WORKERS, max(1, len(guids)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for guid, result in executor.map(_check_guid, guids):
                results[guid] = result

        return results

# --------------------------------------------------------------------------- #
# Report builder                                                               #
# --------------------------------------------------------------------------- #

def build_report(
    episodes: list[EpisodeRecord],
    results: dict[str, GuidResult],
    checked_at: str | None = None,
) -> dict[str, Any]:
    import datetime

    if checked_at is None:
        checked_at = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    total = len(episodes)
    grouped_count = 0
    ungrouped_count = 0
    not_found_count = 0
    error_count = 0
    pending_visibility_count = 0

    ungrouped_by_series: dict[str, list[dict[str, Any]]] = {}
    pending_visibility: list[dict[str, Any]] = []
    all_results: list[dict[str, Any]] = []

    for ep in episodes:
        result = results.get(ep.guid)
        if result is None:
            status = "not_found"
            not_found_count += 1
        elif result.error:
            status = "error"
            error_count += 1
        elif not result.found:
            status = "not_found"
            not_found_count += 1
        elif result.grouped:
            status = "grouped"
            grouped_count += 1
        else:
            status = "ungrouped"
            ungrouped_count += 1

        via_fallback = bool(result and result.via_fallback)
        if via_fallback and status == "grouped":
            pending_visibility_count += 1

        row: dict[str, Any] = {
            "guid": ep.guid,
            "series_name": ep.series_name,
            "series_guid": ep.series_guid,
            "episode_number": ep.episode_number,
            "season_number": ep.season_number,
            "source_file": ep.source_file,
            "status": status,
            "media_id": result.media_id if result else None,
            "series_id": result.series_id if result else None,
            "season_id": result.season_id if result else None,
            "error": result.error if result else None,
            # Grouping confirmed via the mapped-metadata fallback rather than
            # /v1/search — the item is properly grouped in metadata, but has
            # NOT been confirmed visible in IG's search index or channel
            # listings. Treat as "grouped, but verify IG visibility separately".
            "via_fallback": via_fallback,
        }
        all_results.append(row)

        if status == "ungrouped":
            series_key = ep.series_name or ep.series_guid
            ungrouped_by_series.setdefault(series_key, []).append(row)

        if via_fallback and status == "grouped":
            pending_visibility.append(row)

    # Sort ungrouped rows per series: season → episode
    for rows in ungrouped_by_series.values():
        rows.sort(key=lambda r: (r["season_number"] or 0, r["episode_number"] or 0))

    pending_visibility.sort(key=lambda r: (
        r["series_name"] or "",
        r["season_number"] or 0,
        r["episode_number"] or 0,
    ))

    # Sort all_results for the full table
    all_results.sort(key=lambda r: (
        r["series_name"] or "",
        r["season_number"] or 0,
        r["episode_number"] or 0,
    ))

    def pct(n: int) -> str:
        if total == 0:
            return "0.0%"
        return f"{n / total * 100:.1f}%"

    return {
        "checked_at": checked_at,
        "total": total,
        "grouped": grouped_count,
        "ungrouped": ungrouped_count,
        "not_found": not_found_count,
        "errors": error_count,
        "pending_visibility": pending_visibility_count,
        "grouped_pct": pct(grouped_count),
        "ungrouped_pct": pct(ungrouped_count),
        "not_found_pct": pct(not_found_count),
        "errors_pct": pct(error_count),
        "pending_visibility_pct": pct(pending_visibility_count),
        "ungrouped_by_series": ungrouped_by_series,
        "pending_visibility_rows": pending_visibility,
        "all_results": all_results,
    }
