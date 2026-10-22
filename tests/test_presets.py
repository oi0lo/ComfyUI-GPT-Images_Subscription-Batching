import unittest

import helpers  # noqa: F401
import presets


class PresetTests(unittest.TestCase):
    def test_dropdown_has_square_once_and_both_orientations(self):
        self.assertEqual(len(presets.ASPECTS), 17)
        self.assertEqual(presets.ASPECTS.count("1:1 Square"), 1)
        self.assertIn("16:9 Landscape", presets.ASPECTS)
        self.assertIn("9:16 Portrait", presets.ASPECTS)
        self.assertIn("1:3 Portrait", presets.ASPECTS)

    def test_every_preset_meets_documented_rules(self):
        count = 0
        for aspect, entries in presets.PRESETS.items():
            self.assertEqual(len(entries), 4, aspect)
            for label, (w, h) in entries.items():
                self.assertIsNone(presets.size_problem(w, h), f"{aspect} {label}")
                self.assertIn(f"{w} x {h}", label)
                count += 1
        self.assertEqual(count, 68)

    def test_portrait_is_transposed_landscape(self):
        landscape = list(presets.PRESETS["16:9 Landscape"].values())
        portrait = list(presets.PRESETS["9:16 Portrait"].values())
        self.assertEqual(portrait, [(h, w) for w, h in landscape])

    def test_defaults(self):
        self.assertEqual(presets.DEFAULT_ASPECT, "16:9 Landscape")
        self.assertEqual(presets.ASPECTS[0], "16:9 Landscape")  # dynamic dropdowns start on entry one
        self.assertEqual(sorted(presets.ASPECTS), sorted(presets.PRESETS))
        label = presets.default_resolution("16:9 Landscape")
        self.assertEqual(presets.resolve("16:9 Landscape", label), (3840, 2160))
        self.assertEqual(presets.resolve("1:1 Square", presets.default_resolution("1:1 Square")), (2880, 2880))

    def test_experimental_marking(self):
        self.assertTrue(presets.default_resolution("16:9 Landscape").endswith("(experimental)"))
        small = [label for label in presets.PRESETS["16:9 Landscape"] if label.startswith("1k:")][0]
        self.assertNotIn("experimental", small)

    def test_stale_resolution_rejected(self):
        square_max = presets.default_resolution("1:1 Square")
        with self.assertRaises(ValueError):
            presets.resolve("16:9 Landscape", square_max)
        with self.assertRaises(ValueError):
            presets.resolve("7:5 Landscape", square_max)

    def test_rule_checker(self):
        self.assertIsNotNone(presets.size_problem(1000, 1000))  # not divisible by 16
        self.assertIsNotNone(presets.size_problem(4096, 2048))  # edge above 3840
        self.assertIsNotNone(presets.size_problem(3840, 1264))  # wider than 3:1
        self.assertIsNotNone(presets.size_problem(768, 768))  # too few pixels


if __name__ == "__main__":
    unittest.main()
