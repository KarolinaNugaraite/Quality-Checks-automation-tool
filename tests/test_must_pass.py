import json
import unittest
from pathlib import Path

from metadata_checker.checker import MetadataChecker
from metadata_checker.config import load_config


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "sample_data" / "new_channel_integration"


class MustPassStructuralChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(str(ROOT / "configs" / "rules.example.yaml"))
        self.checker = MetadataChecker(self.cfg)

    def _run_original_for_fixture(self, file_name: str) -> list[dict]:
        payload = json.loads((FIXTURES / file_name).read_text(encoding="utf-8"))
        records = [(str(FIXTURES / file_name), payload)]
        return self.checker.run_original_checks(records)

    def _assert_has_error_code(self, findings: list[dict], error_code: str) -> None:
        codes = {item.get("errorCode") for item in findings}
        self.assertIn(error_code, codes)

    def test_missing_envelope_fields(self) -> None:
        findings = self._run_original_for_fixture("missing_envelope_fields.json")
        self._assert_has_error_code(findings, "MISSING_MANDATORY_ATTRIBUTES")

    def test_duplicate_broadcast_id(self) -> None:
        findings = self._run_original_for_fixture("duplicate_broadcast_id.json")
        self._assert_has_error_code(findings, "DUPLICATE_BROADCAST_ID")

    def test_dangling_broadcast_content_reference(self) -> None:
        findings = self._run_original_for_fixture("dangling_broadcast_reference.json")
        self._assert_has_error_code(findings, "INVALID_BROADCAST_CONTENT_REFERENCE")

    def test_invalid_image_url_and_dimensions(self) -> None:
        findings = self._run_original_for_fixture("invalid_image_url.json")
        self._assert_has_error_code(findings, "INVALID_IMAGE_URL")
        self._assert_has_error_code(findings, "INVALID_IMAGE_DIMENSIONS")

    def test_invalid_epg_from_to_dates(self) -> None:
        findings = self._run_original_for_fixture("invalid_image_and_dates.json")
        self._assert_has_error_code(findings, "INVALID_EPG_FROM_TO_DATES")

    def test_non_deeplink_source_with_deeplink_present(self) -> None:
        findings = self._run_original_for_fixture("non_deeplink_with_deeplink.json")
        self._assert_has_error_code(findings, "VOD_DEEPLINKS_FOR_NON_DEEPLINK_SOURCE_TYPE")


if __name__ == "__main__":
    unittest.main()
