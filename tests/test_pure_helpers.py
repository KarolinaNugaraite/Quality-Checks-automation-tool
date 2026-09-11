"""Unit tests for pure helper functions in checker.py and grouping_check.py.

These don't require any fixture files or network access -- they just exercise
small, deterministic functions directly with hand-built inputs.
"""

import unittest

from metadata_checker.checker import (
    _guess_type,
    _is_empty,
    _normalize_contract_genre,
    _normalize_text,
    MetadataChecker,
)
from metadata_checker.config import load_config
from metadata_checker.grouping_check import _ratio_label_for


class IsEmptyTest(unittest.TestCase):
    def test_none_is_empty(self) -> None:
        self.assertTrue(_is_empty(None))

    def test_blank_string_is_empty(self) -> None:
        self.assertTrue(_is_empty("   "))

    def test_empty_collections_are_empty(self) -> None:
        self.assertTrue(_is_empty([]))
        self.assertTrue(_is_empty({}))
        self.assertTrue(_is_empty(()))
        self.assertTrue(_is_empty(set()))

    def test_non_empty_values_are_not_empty(self) -> None:
        self.assertFalse(_is_empty("hello"))
        self.assertFalse(_is_empty([1]))
        self.assertFalse(_is_empty({"a": 1}))
        self.assertFalse(_is_empty(0))
        self.assertFalse(_is_empty(False))


class NormalizeTextTest(unittest.TestCase):
    def test_none_becomes_empty_string(self) -> None:
        self.assertEqual(_normalize_text(None), "")

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(_normalize_text("  a   b\n\tc  "), "a b c")

    def test_non_string_is_stringified(self) -> None:
        self.assertEqual(_normalize_text(42), "42")


class NormalizeContractGenreTest(unittest.TestCase):
    def test_uppercases_and_underscores(self) -> None:
        self.assertEqual(_normalize_contract_genre("sci-fi"), "SCI_FI")

    def test_strips_leading_trailing_junk(self) -> None:
        self.assertEqual(_normalize_contract_genre("  action!! "), "ACTION")

    def test_empty_input(self) -> None:
        self.assertEqual(_normalize_contract_genre(None), "")
        self.assertEqual(_normalize_contract_genre(""), "")


class GuessTypeTest(unittest.TestCase):
    def test_detects_movie(self) -> None:
        self.assertEqual(_guess_type({"type": "Movie"}, ["type"]), "movie")

    def test_detects_series(self) -> None:
        self.assertEqual(_guess_type({"contentType": "tv-show"}, ["contentType"]), "series")

    def test_detects_episode(self) -> None:
        self.assertEqual(_guess_type({"type": "Episode"}, ["type"]), "episode")

    def test_unrecognized_type_returns_raw_value(self) -> None:
        self.assertEqual(_guess_type({"type": "banana"}, ["type"]), "banana")

    def test_missing_type_returns_unknown(self) -> None:
        self.assertEqual(_guess_type({}, ["type"]), "unknown")


class RatioLabelForTest(unittest.TestCase):
    def test_known_ratios(self) -> None:
        self.assertEqual(_ratio_label_for("16:9"), "RATIO_16X9")
        self.assertEqual(_ratio_label_for("2:3"), "RATIO_2X3")

    def test_handles_spacing_and_case(self) -> None:
        self.assertEqual(_ratio_label_for(" 16:9 "), "RATIO_16X9")

    def test_unlisted_ratio_falls_back_to_generated_label(self) -> None:
        self.assertEqual(_ratio_label_for("9:16"), "RATIO_9X16")


class ExpectedContentShapeRuleTest(unittest.TestCase):
    """Tests the config-driven movies_only/series_only channel-shape rule."""

    def setUp(self) -> None:
        self.cfg = load_config(None)
        self.cfg["expected_content_shape"] = {"se.viaplay.classics": "movies_only"}
        self.checker = MetadataChecker(self.cfg)

    def _record(self, contents, channel_id="se.viaplay.classics"):
        return {
            "contents": contents,
            "schedules": [
                {
                    "channelId": channel_id,
                    "broadcasts": [{"contentId": contents[0].get("contentId")}] if contents else [],
                }
            ],
        }

    def test_no_findings_when_all_movies(self) -> None:
        record = self._record([{"contentId": "c1", "type": "movie", "title": "A Movie"}])
        findings = self.checker._check_expected_content_shape(record, "file.json")
        self.assertEqual(findings, [])

    def test_flags_series_on_movies_only_channel(self) -> None:
        record = self._record(
            [{"contentId": "c1", "type": "movie", "title": "A Movie", "series": {"name": "Oops"}}]
        )
        findings = self.checker._check_expected_content_shape(record, "file.json")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check_id, "conv_unexpected_content_shape")

    def test_unconfigured_channel_is_ignored(self) -> None:
        record = self._record(
            [{"contentId": "c1", "series": {"name": "Some Show"}}],
            channel_id="se.viaplay.some-other-channel",
        )
        findings = self.checker._check_expected_content_shape(record, "file.json")
        self.assertEqual(findings, [])

    def test_no_config_means_no_findings(self) -> None:
        self.cfg["expected_content_shape"] = {}
        checker = MetadataChecker(self.cfg)
        record = self._record([{"contentId": "c1", "series": {"name": "Some Show"}}])
        findings = checker._check_expected_content_shape(record, "file.json")
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
