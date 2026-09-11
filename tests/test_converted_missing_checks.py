import unittest
from pathlib import Path

from metadata_checker.checker import MetadataChecker
from metadata_checker.config import load_config


ROOT = Path(__file__).resolve().parents[1]


class ConvertedMissingChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        cfg_path = ROOT / "configs" / "providers" / "viaplay" / "viaplay-epg.rules.yaml"
        self.cfg = load_config(str(cfg_path))
        self.checker = MetadataChecker(self.cfg)

    def _run(self, orig: dict, conv: dict) -> list[dict]:
        return self.checker.run_converted_checks([("orig.json", orig)], [("conv.json", conv)])

    def _assert_error_code(self, findings: list[dict], error_code: str) -> None:
        codes = {item.get("errorCode") for item in findings}
        self.assertIn(error_code, codes)

    def test_missing_age_rating_and_credits(self) -> None:
        orig = {
            "contentId": "c1",
            "descriptions": [{"language": "swe", "value": "desc"}],
            "titles": [{"language": "swe", "value": "title"}],
            "genre": ["Crime"],
            "parentalRating": "15",
        }
        conv = {
            "contents": [
                {
                    "contentId": "c1",
                    "descriptions": [{"language": "sv", "value": "desc"}],
                    "titles": [{"language": "sv", "value": "title"}],
                    "genres": {"main": "CRIME", "sub": []},
                }
            ]
        }

        findings = self._run(orig, conv)
        self._assert_error_code(findings, "MISSING_AGE_RATING")
        self._assert_error_code(findings, "MISSING_CREDITS")

    def test_missing_production_year_all_levels(self) -> None:
        orig = {
            "contentId": "c2",
            "descriptions": [{"language": "swe", "value": "desc"}],
            "titles": [{"language": "swe", "value": "title"}],
            "genre": ["Action"],
            "productionYear": "2020",
            "parentalRating": "12",
        }
        conv = {
            "contents": [
                {
                    "contentId": "c2",
                    "descriptions": [{"language": "sv", "value": "desc"}],
                    "titles": [{"language": "sv", "value": "title"}],
                    "genres": {"main": "ACTION", "sub": []},
                    "series": {},
                    "season": {},
                    "ageRatings": [{"rating": "12"}],
                    "credits": [{"name": "A", "type": "ACTOR"}],
                }
            ]
        }

        findings = self._run(orig, conv)
        self._assert_error_code(findings, "MISSING_PRODUCTION_YEAR")

if __name__ == "__main__":
    unittest.main()
