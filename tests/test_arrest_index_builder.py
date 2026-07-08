import unittest

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


if __name__ == "__main__":
    unittest.main()
