from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "sample_size": 25,
    "random_seed": 42,
    "required_languages": ["en"],
    "min_description_words": 3,
    "placeholder_descriptions": [
        "n/a",
        "na",
        "tbd",
        "todo",
        "description",
        "test",
    ],
    "required_image_ratios": ["16:9", "2:3"],
    "undefined_image_types": ["", "undefined", "staple", "unknown", "null"],
    "episode_count_thresholds": {
        "min": 1,
        "max": 500,
    },
    "required_envelope_fields": {
        "epg": ["version", "timestamp", "contents"],
        "vod": ["version", "timestamp"],
        "event": ["version", "timestamp", "content", "events", "publishingInfo"],
    },
    "required_broadcast_fields": [
        "broadcastId",
        "originalBroadcastId",
        "contentId",
        "displayTime.start",
        "displayTime.end",
    ],
    "required_event_fields": [
        "start",
        "eventId",
        "originalEventId",
        "contentId",
        "sourceType",
        "references",
    ],
    "vod_id_pattern": r"^[A-Za-z0-9._:-]+$",
    # Maps a channel identifier (matched case-insensitively against originalChannelId/channelId
    # found on schedules/broadcasts) to the expected content shape for that channel:
    #   "movies_only"  -> flag if any series/episode content is found on the channel
    #   "series_only"  -> flag if any non-series (movie) content is found on the channel
    # Leave empty (default) so no channel is constrained unless explicitly configured.
    "expected_content_shape": {},
    "id_keys": [
        "id",
        "contentId",
        "content_id",
        "externalId",
        "external_id",
        "programId",
        "program_id",
    ],
    "type_keys": ["type", "contentType", "content_type", "level", "entityType"],
    "provider_group_keys": ["group", "package", "providerGroup", "provider_group"],
    "kids_flags_keys": ["isKids", "kids", "audience", "targetAudience", "category"],
    "candidate_keys": {
        "title": ["title", "name", "titles.main", "localized.title"],
        "description": ["description", "synopsis", "plot", "descriptions.main"],
        "descriptions_block": ["descriptions", "localized.descriptions", "i18n.descriptions"],
        "images": ["images", "imageAssets", "artworks"],
        "genre": ["genres", "genre", "categories"],
        "production_year": ["productionYear", "year", "releaseYear"],
        "duration": ["duration", "durationSeconds", "runtime", "runtimeSeconds"],
        "age_rating": ["ageRating", "rating", "parentalRating"],
        "deeplink": ["deeplink", "deepLink", "links.app", "links.web"],
        "source_type": ["sourceType", "source_type", "content.sourceType"],
        "vod_id": ["vodId", "vod_id", "contentId", "id"],
        "publishing_info": ["publishingInfo", "publishing", "rights", "broadcastRights"],
        "targets": ["targets", "publishingInfo.targets"],
        "rights": ["rights", "broadcastRights", "publishingInfo.targets.rights"],
        "country_code": ["metadataOriginCountry", "country", "countryCode"],
        "language_code": ["lang", "language", "locale", "languageCode"],
        "contents": ["contents", "content"],
        "broadcasts": ["broadcasts", "schedules.broadcasts"],
        "events": ["events"],
        "broadcast_id": ["broadcastId", "id"],
        "original_broadcast_id": ["originalBroadcastId"],
        "broadcast_content_id": ["contentId"],
        "display_start": ["displayTime.start", "start", "from"],
        "display_end": ["displayTime.end", "end", "to"],
        "channel_id": ["channelId", "originalChannelId"],
        "schedule_from": ["from", "start"],
        "schedule_to": ["to", "end"],
        "schedule_date": ["date"],
        "rerun": ["rerun", "isRerun"],
        "live": ["live", "isLive"],
        "premiere": ["premiere", "isPremiere"],
        "event_start": ["start", "startDate"],
        "event_id": ["eventId", "id"],
        "event_original_id": ["originalEventId"],
        "event_content_id": ["contentId"],
        "event_source_type": ["sourceType"],
        "event_references": ["references"],
        "series_id": ["seriesId", "series_id", "parentSeriesId", "showId"],
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: str | None) -> dict[str, Any]:
    if not config_path:
        return deepcopy(DEFAULT_CONFIG)

    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_file}")

    with cfg_file.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    if not isinstance(loaded, dict):
        raise ValueError("Config file must parse to a dictionary-like object.")

    return _deep_merge(DEFAULT_CONFIG, loaded)
