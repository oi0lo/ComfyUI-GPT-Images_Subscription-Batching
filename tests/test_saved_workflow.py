import unittest

import helpers  # noqa: F401
import saved_workflow as sw

SWITCH = "mode.transparent_background"


def pnginfo(value, node_id=7, with_named=True):
    node = {"id": node_id, "type": "GPTImagesSubscriptionSunburstBatch", "widgets_values": []}
    if with_named:
        node["widgets_values_named"] = {"mode": "Sunburst Batch", SWITCH: value}
    return {"workflow": {"nodes": [{"id": 1, "type": "LoadImage"}, node]}}


class SavedValueTests(unittest.TestCase):
    def test_real_switch_values(self):
        for value in (True, False):
            self.assertIs(sw.saved_value(pnginfo(value), "7", SWITCH), value)
            self.assertIsNone(sw.leftover_text(sw.saved_value(pnginfo(value), "7", SWITCH)))

    def test_leftover_prompt_text_is_reported(self):
        prompt = "Make the image photorealistic. As in the source image, the nose bridge is straight"
        value = sw.saved_value(pnginfo(prompt), "7", SWITCH)
        self.assertEqual(value, prompt)
        quoted = sw.leftover_text(value)
        self.assertTrue(quoted.startswith("Make the image photorealistic."))
        self.assertLessEqual(len(quoted), 60)

    def test_unavailable_is_missing_not_an_error(self):
        self.assertIs(sw.saved_value(None, "7", SWITCH), sw.MISSING)              # queued through the API
        self.assertIs(sw.saved_value(pnginfo(True), "8", SWITCH), sw.MISSING)     # other node id
        self.assertIs(sw.saved_value(pnginfo(True), "5:7", SWITCH), sw.MISSING)   # inside a subgraph
        self.assertIs(sw.saved_value(pnginfo(True, with_named=False), "7", SWITCH), sw.MISSING)
        self.assertIs(sw.saved_value({"workflow": {"nodes": "bad"}}, "7", SWITCH), sw.MISSING)
        self.assertIsNone(sw.leftover_text(sw.MISSING))


if __name__ == "__main__":
    unittest.main()
