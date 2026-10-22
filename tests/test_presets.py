import unittest

import helpers  # noqa: F401
import presets

EXPECTED_ORDER = [
    "1:1 Square", "16:9 Landscape", "9:16 Portrait", "4:5 Portrait",
    "5:4 Landscape", "3:2 Landscape", "2:3 Portrait",
]


class PresetTests(unittest.TestCase):
    def test_order_and_default_follow_the_table(self):
        self.assertEqual(presets.ASPECTS, EXPECTED_ORDER)
        self.assertEqual(presets.DEFAULT_ASPECT, "1:1 Square")
        self.assertEqual([len(presets.PRESETS[a]) for a in EXPECTED_ORDER], [6, 10, 10, 9, 9, 8, 8])

    def test_every_size_meets_documented_rules(self):
        for aspect in presets.ASPECTS:
            for width, height, _, _ in presets.table_rows(aspect):
                self.assertIsNone(presets.size_problem(width, height), f"{aspect} {width}x{height}")

    def test_megapixels_in_table_are_correct(self):
        for aspect in presets.ASPECTS:
            for width, height, megapixels, _ in presets.table_rows(aspect):
                self.assertEqual(f"{width * height / 1e6:.2f}", megapixels, f"{width}x{height}")

    def test_experimental_marks_match_documented_threshold(self):
        for aspect in presets.ASPECTS:
            for width, height, _, classification in presets.table_rows(aspect):
                marked = "Experimental" in classification
                self.assertEqual(marked, width * height > presets.EXPERIMENTAL_ABOVE_PIXELS, f"{width}x{height}")

    def test_portraits_mirror_landscapes(self):
        for landscape, portrait in [("16:9 Landscape", "9:16 Portrait"), ("5:4 Landscape", "4:5 Portrait"),
                                    ("3:2 Landscape", "2:3 Portrait")]:
            wide = [(w, h) for w, h, _, _ in presets.table_rows(landscape)]
            tall = [(w, h) for w, h, _, _ in presets.table_rows(portrait)]
            self.assertEqual(tall, [(h, w) for w, h in wide])

    def test_labels_and_defaults(self):
        self.assertEqual(presets.default_resolution("1:1 Square"), "2880 × 2880 (8.29 MP) Supported maximum square + Experimental")
        self.assertEqual(presets.resolve("1:1 Square", presets.default_resolution("1:1 Square")), (2880, 2880))
        self.assertEqual(presets.resolve("16:9 Landscape", presets.default_resolution("16:9 Landscape")), (3840, 2160))
        self.assertIn("1024 × 1024 (1.05 MP) Recommended", presets.PRESETS["1:1 Square"])

    def test_stale_resolution_rejected(self):
        with self.assertRaises(ValueError):
            presets.resolve("16:9 Landscape", presets.default_resolution("1:1 Square"))
        with self.assertRaises(ValueError):
            presets.resolve("21:9 Landscape", presets.default_resolution("1:1 Square"))

    def test_rule_checker(self):
        self.assertIsNotNone(presets.size_problem(1000, 1000))
        self.assertIsNotNone(presets.size_problem(4096, 2048))
        self.assertIsNotNone(presets.size_problem(3840, 1264))
        self.assertIsNotNone(presets.size_problem(768, 768))


if __name__ == "__main__":
    unittest.main()
