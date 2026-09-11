from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any


def parse_xml_payload_as_record(xml_text: str) -> dict[str, Any]:
    """Parse XML payload into a normalized dict used by metadata checks."""

    def strip_ns(tag: str) -> str:
        if "}" in tag:
            return tag.rsplit("}", 1)[-1]
        return tag

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"Failed to parse XML payload: {str(e)}") from e

    record: dict[str, Any] = {}

    def _append_list_value(key: str, value: Any) -> None:
        existing = record.get(key)
        if existing is None:
            record[key] = [value]
        elif isinstance(existing, list):
            existing.append(value)
        else:
            record[key] = [existing, value]

    def _append_container_description(container: dict[str, Any], entry: dict[str, Any]) -> None:
        existing = container.get("descriptions")
        if existing is None:
            container["descriptions"] = [entry]
        elif isinstance(existing, list):
            existing.append(entry)
        else:
            container["descriptions"] = [existing, entry]

    def _normalize_content_description_lists(node: Any) -> None:
        if not isinstance(node, dict):
            return

        raw_list = node.get("descriptionList")
        if not isinstance(raw_list, dict):
            return

        raw_descriptions = raw_list.get("description")
        if isinstance(raw_descriptions, dict):
            description_entries = [raw_descriptions]
        elif isinstance(raw_descriptions, list):
            description_entries = [item for item in raw_descriptions if isinstance(item, dict)]
        else:
            description_entries = []

        if not description_entries:
            return

        content_entries: list[dict[str, Any]] = []
        series_entries: list[dict[str, Any]] = []
        season_entries: list[dict[str, Any]] = []

        for item in description_entries:
            desc_type = str(item.get("type") or "").strip().lower()
            normalized_entry = dict(item)
            if desc_type == "series":
                series_entries.append(normalized_entry)
            elif desc_type == "season":
                season_entries.append(normalized_entry)
            else:
                content_entries.append(normalized_entry)

        for entry in content_entries:
            _append_container_description(node, entry)

        if series_entries:
            series_container = node.get("series")
            if not isinstance(series_container, dict):
                series_container = {}
                node["series"] = series_container
            for entry in series_entries:
                _append_container_description(series_container, entry)

        if season_entries:
            season_container = node.get("season")
            if not isinstance(season_container, dict):
                season_container = {}
                node["season"] = season_container
            for entry in season_entries:
                _append_container_description(season_container, entry)

    def _element_to_object(elem: ET.Element) -> dict[str, Any]:
        item: dict[str, Any] = {}
        for attr_name, attr_value in elem.attrib.items():
            item[strip_ns(attr_name)] = attr_value
        for child in list(elem):
            child_key = strip_ns(child.tag)
            child_text = (child.text or "").strip()
            child_obj: Any
            if list(child) or child.attrib:
                child_obj = _element_to_object(child)
                if child_text and "value" not in child_obj:
                    child_obj["value"] = child_text
            else:
                child_obj = child_text

            existing = item.get(child_key)
            if existing is None:
                item[child_key] = child_obj
            elif isinstance(existing, list):
                existing.append(child_obj)
            else:
                item[child_key] = [existing, child_obj]
        return item

    for elem in root.iter():
        key = strip_ns(elem.tag)
        lower_key = key.lower()
        text = (elem.text or "").strip()

        for attr_name, attr_value in elem.attrib.items():
            normalized_attr = strip_ns(attr_name)
            dotted_attr = f"{key}.{normalized_attr}"

            existing_attr = record.get(normalized_attr)
            if existing_attr is None:
                record[normalized_attr] = attr_value
            elif isinstance(existing_attr, list):
                existing_attr.append(attr_value)
            else:
                record[normalized_attr] = [existing_attr, attr_value]

            existing_dotted = record.get(dotted_attr)
            if existing_dotted is None:
                record[dotted_attr] = attr_value
            elif isinstance(existing_dotted, list):
                existing_dotted.append(attr_value)
            else:
                record[dotted_attr] = [existing_dotted, attr_value]

        if lower_key in {"broadcast", "broadcastitem", "programme", "program"}:
            _append_list_value("broadcasts", _element_to_object(elem))
        if lower_key in {"content", "asset", "program", "programme", "event"}:
            _append_list_value("contents", _element_to_object(elem))

        if lower_key == "image":
            obj = _element_to_object(elem)
            img_type = obj.get("type") or elem.attrib.get("type") or ""
            for child in list(elem):
                child_tag = strip_ns(child.tag).lower()
                if child_tag == "imageref":
                    img_url = (child.text or "").strip()
                    if img_url:
                        img_obj: dict[str, Any] = {"url": img_url}
                        if img_type:
                            img_obj["type"] = img_type
                        for attr_k, attr_v in child.attrib.items():
                            img_obj[strip_ns(attr_k)] = attr_v
                        w = img_obj.get("width") or obj.get("width")
                        h = img_obj.get("height") or obj.get("height")
                        if w and h:
                            try:
                                from math import gcd as _gcd

                                wi, hi = int(w), int(h)
                                if wi > 0 and hi > 0:
                                    d = _gcd(wi, hi)
                                    img_obj["ratio"] = f"{wi // d}:{hi // d}"
                            except (ValueError, TypeError):
                                pass
                        _append_list_value("images", img_obj)

        if not text:
            continue

        existing = record.get(key)
        if existing is None:
            record[key] = text
        elif isinstance(existing, list):
            existing.append(text)
        else:
            record[key] = [existing, text]

    def _alias(source_key: str, target_key: str) -> None:
        if source_key in record and target_key not in record:
            record[target_key] = record[source_key]

    def _norm_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    def _alias_case_insensitive(source_keys: list[str], target_key: str) -> None:
        if target_key in record:
            return
        normalized_sources = {_norm_key(key) for key in source_keys}
        for key, value in record.items():
            if _norm_key(key) in normalized_sources:
                record[target_key] = value
                return

    _alias("Version", "version")
    _alias("Timestamp", "timestamp")
    _alias("ChannelId", "channelId")
    _alias("AdapterId", "adapterId")
    _alias("OriginalChannelId", "originalChannelId")
    _alias("From", "from")
    _alias("To", "to")
    _alias("Date", "date")
    _alias("Guid", "guid")
    _alias("ContentId", "contentId")
    _alias("SourceType", "sourceType")
    _alias("Country", "country")

    _alias_case_insensitive(["version", "schemaVersion", "metadataVersion"], "version")
    _alias_case_insensitive(["timestamp"], "timestamp")
    _alias_case_insensitive(["channelid", "channel_id"], "channelId")
    _alias_case_insensitive(["adapterid", "adapter_id", "adapter", "adaptername"], "adapterId")
    _alias_case_insensitive(["originalchannelid", "original_channel_id"], "originalChannelId")
    _alias_case_insensitive(["from", "start"], "from")
    _alias_case_insensitive(["to", "end"], "to")
    _alias_case_insensitive(["date", "scheduleDate", "broadcastDate", "airDate"], "date")
    _alias_case_insensitive(["broadcastid", "broadcast_id"], "broadcastId")
    _alias_case_insensitive(["originalbroadcastid", "original_broadcast_id"], "originalBroadcastId")
    _alias_case_insensitive(["contentid", "content_id"], "contentId")
    _alias_case_insensitive(["sourcetype", "source_type"], "sourceType")
    _alias_case_insensitive(["country", "countrycode", "country_code"], "country")
    _alias_case_insensitive(["starttime", "start_time", "from", "start"], "from")
    _alias_case_insensitive(["endtime", "end_time", "to", "end"], "to")
    _alias_case_insensitive(["parentalrating", "parental_rating", "agerating", "age_rating"], "parentalRating")
    _alias_case_insensitive(["seriesid", "series_id"], "seriesId")
    _alias_case_insensitive(["seasonnumber", "season_number"], "seasonNumber")
    _alias_case_insensitive(["episodenumber", "episode_number"], "episodeNumber")
    _alias_case_insensitive(["contentidref", "content_id_ref"], "contentId")

    if "broadcasts" not in record and (
        "BroadcastId" in record or "broadcastId" in record or "originalBroadcastId" in record
    ):
        record["broadcasts"] = (
            record.get("BroadcastId") or record.get("broadcastId") or record.get("originalBroadcastId")
        )

    if "contents" not in record and ("contentId" in record or "ContentId" in record):
        content_values = record.get("contentId") or record.get("ContentId")
        if isinstance(content_values, list):
            record["contents"] = [{"contentId": value} for value in content_values if str(value).strip()]
        elif str(content_values).strip():
            record["contents"] = [{"contentId": content_values}]

    contents = record.get("contents")
    if isinstance(contents, list):
        for item in contents:
            _normalize_content_description_lists(item)
    elif isinstance(contents, dict):
        _normalize_content_description_lists(contents)

    if "ProgramId" in record and "program_id" not in record:
        record["program_id"] = record["ProgramId"]
    if "AssetType" in record and "asset_type" not in record:
        record["asset_type"] = record["AssetType"]
    if "Duration" in record and "duration_iso8601" not in record:
        record["duration_iso8601"] = record["Duration"]
    if "Category" in record and "category" not in record:
        record["category"] = record["Category"]
    if "ProductionYear" in record and "production_year" not in record:
        record["production_year"] = record["ProductionYear"]

    return record
