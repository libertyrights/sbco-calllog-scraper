import unittest
from datetime import datetime

import arrest_index_builder as builder


class ExtractCodeVariantsTests(unittest.TestCase):
    def test_extracts_primary_code_from_descriptive_charge(self):
        variants = builder.extract_code_variants("12500(A); - Drive W/O License")
        self.assertIn("12500A", variants)

    def test_extracts_multiple_codes_from_multi_charge_text(self):
        variants = builder.extract_code_variants(
            "11377(A) - Possession of Controlled Substance 11364 - Possess Paraphernalia 459.5 - Shoplifting"
        )
        self.assertIn("11377A", variants)
        self.assertIn("11364", variants)
        self.assertIn("4595", variants)

    def test_extracts_parenthetical_variants(self):
        variants = builder.extract_code_variants("243(E)(1); - Battery On Spouse / Cohabitant / Former Spouse")
        self.assertIn("243E1", variants)

    def test_root_only_match_is_allowed_when_one_side_is_truncated(self):
        score, reason = builder.score_code_match(
            builder.extract_code_tokens("11395"),
            builder.extract_code_tokens("11395(B)(1); - Possession of hard drugs with prior"),
        )
        self.assertEqual((score, reason), (2, "charge_code_root_match"))

    def test_conflicting_specific_subsections_do_not_root_match(self):
        score, reason = builder.score_code_match(
            builder.extract_code_tokens("243(E)(1)"),
            builder.extract_code_tokens("243(D); - Battery w/Serious Bodily Injury"),
        )
        self.assertEqual((score, reason), (0, None))


class ArrestLikeTests(unittest.TestCase):
    def test_wararr_is_treated_as_arrest_like(self):
        row = {"call type": "WARARR", "disposition": "*"}
        self.assertTrue(builder.is_arrest_like(row))

    def test_warser_is_not_treated_as_arrest_like_without_arrest_disposition(self):
        row = {"call type": "WARSER", "disposition": "NAT"}
        self.assertFalse(builder.is_arrest_like(row))


def make_call(base="BA261400001", town="daggett", call_type="SUSPER"):
    location = "35300 Santa Fe St, DAG" if town == "daggett" else "Main St, BAR"
    return {
        "base_call_number": base,
        "call_number": base,
        "agency": "SBSO",
        "station": "Barstow",
        "date_time": "05/20/2026 01:23:00 PM",
        "report_number": "",
        "call_type": call_type,
        "disposition": "ARR",
        "location": location,
        "call_dt": datetime(2026, 5, 20, 13, 23),
        "call_date_key": "2026-05-20",
        "call_town": town,
        "call_suffix": "DAG" if town == "daggett" else "BAR",
        "call_prefix": "BA",
        "location_tokens": builder.extract_location_tokens(location),
        "has_specific_location": True,
        "call_code_variants": sorted(builder.extract_code_variants(call_type)),
    }


def make_candidate(arrest_id, town="daggett", charge="484(A) - Petty Theft", arrest_location="Not Available"):
    return {
        "arrest_id": arrest_id,
        "arrest_name": f"Person {arrest_id}",
        "detail_url": f"https://example.test/{arrest_id}",
        "arrest_date": "May 20, 2026",
        "arrest_date_dt": datetime(2026, 5, 20),
        "arrest_date_key": "2026-05-20",
        "charge": charge,
        "map_charge_codes": sorted(builder.extract_code_variants(charge)),
        "detail_charge_codes": sorted(builder.extract_code_variants(charge)),
        "resident_city_state": town.title() + ", CA",
        "resident_tags": [town],
        "area_tags": [],
        "is_local_resident": True,
        "arrest_location": arrest_location,
        "has_explicit_location": builder.has_specific_location(arrest_location),
        "map_county": "San Bernardino",
        "map_source_agency": "San Bernardino County Sheriff",
        "details": {"source_agency": "San Bernardino County Sheriff", "county_of_arrest": "San Bernardino"},
        "linked_call_bases": [],
        "linked_report_numbers": [],
    }


class SmallTownDateMatchTests(unittest.TestCase):
    def test_single_daggett_arrest_same_day_is_confident_without_charge_or_address(self):
        call = make_call(call_type="SUSPER")
        candidate = make_candidate("1", charge="484(A) - Petty Theft")

        matches = builder.build_matches([call], {"1": candidate})

        self.assertEqual(matches[call["base_call_number"]][0]["arrest_id"], "1")
        self.assertIn("unique_small_town_resident_same_day", matches[call["base_call_number"]][0]["reasons"])
        self.assertEqual(matches[call["base_call_number"]][0]["confidence"], "lower")

    def test_arrest_location_town_is_stronger_than_resident_town(self):
        call = make_call(town="barstow", call_type="SUSPER")
        candidate = make_candidate(
            "1",
            town="ontario",
            charge="484(A) - Petty Theft",
            arrest_location="215 E Main St Barstow Ca",
        )

        matches = builder.build_matches([call], {"1": candidate})

        self.assertEqual(matches[call["base_call_number"]][0]["arrest_id"], "1")
        self.assertIn("unique_town_same_day", matches[call["base_call_number"]][0]["reasons"])
        self.assertEqual(matches[call["base_call_number"]][0]["confidence"], "medium")

    def test_multiple_daggett_arrests_same_day_do_not_match_without_tie_breaker(self):
        call = make_call(call_type="SUSPER")
        candidates = {
            "1": make_candidate("1", charge="484(A) - Petty Theft"),
            "2": make_candidate("2", charge="496(A) - Receiving Stolen Property"),
        }

        matches = builder.build_matches([call], candidates)

        self.assertNotIn(call["base_call_number"], matches)

    def test_barstow_same_day_unique_can_match_but_ambiguous_needs_charge_or_address(self):
        unique_call = make_call(town="barstow", call_type="SUSPER")
        unique_candidate = make_candidate("1", town="barstow", charge="484(A) - Petty Theft")
        self.assertIn(unique_call["base_call_number"], builder.build_matches([unique_call], {"1": unique_candidate}))

        charge_call = make_call(base="BA261400002", town="barstow", call_type="484(A)")
        candidates = {
            "1": make_candidate("1", town="barstow", charge="484(A) - Petty Theft"),
            "2": make_candidate("2", town="barstow", charge="496(A) - Receiving Stolen Property"),
        }
        self.assertIn(charge_call["base_call_number"], builder.build_matches([charge_call], candidates))


if __name__ == "__main__":
    unittest.main()
