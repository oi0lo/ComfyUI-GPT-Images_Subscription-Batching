import base64
import json
import unittest

import helpers  # noqa: F401
import batch_request
from references import Encoded


# Written out on purpose: a typo in batch_request.INSTRUCTIONS must fail this test.
INSTRUCTIONS = (
    "Use the configured image_generation tool. Follow the user prompt. "
    "Pass the user prompt to the tool as written. "
    "The attached images are references, in the order supplied; if any."
)


def png(n):
    return Encoded("image/png", bytes(range(256)) * (n // 256) + bytes(n % 256), 64, 32)


def jpeg(n):
    return Encoded("image/jpeg", b"\xff\xd8" + b"x" * (n - 2), 64, 32)


class BatchRequestTests(unittest.TestCase):
    def full_object(self, request, prompt, size, quality, images, instructions, background="opaque"):
        content = [{"type": "input_text", "text": prompt}]
        for image in images:
            url = f"data:{image.mime};base64," + base64.b64encode(image.data).decode("ascii")
            content.append({"type": "input_image", "image_url": url})
        return {
            "custom_id": request.custom_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": "gpt-6-luna",
                "reasoning": {"effort": "none"},
                "instructions": instructions,
                "input": [{"role": "user", "content": content}],
                "tools": [{
                    "type": "image_generation",
                    "model": "gpt-image-2.5-sunburst",
                    "action": "edit" if images else "generate",
                    "size": size,
                    "quality": quality,
                    "output_format": "png",
                    "background": background,
                    "moderation": "low",
                    "partial_images": 0,
                }],
                "tool_choice": {"type": "image_generation"},
                "max_tool_calls": 1,
                "parallel_tool_calls": False,
                "stream": False,
            },
        }

    def check_golden(self, prompt, images, transparent=False):
        request = batch_request.build(prompt, 3840, 2160, "max", images, transparent)
        background = "transparent" if transparent else "opaque"
        expected = (
            json.dumps(self.full_object(request, prompt, "3840x2160", "max", images, INSTRUCTIONS, background),
                       ensure_ascii=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        actual = request.to_bytes()
        self.assertEqual(actual, expected)
        self.assertEqual(request.size, len(expected))
        self.assertTrue(actual.endswith(b"\n"))
        self.assertEqual(actual.count(b"\n"), 1)
        return request

    def test_golden_with_references_in_order(self):
        images = [png(1000), jpeg(1001), png(1002)]  # covers all three base64 padding cases
        request = self.check_golden('A "quoted" prompt with ümlauts, \\ and\nnew lines', images)
        line = json.loads(request.to_bytes())
        urls = [c["image_url"] for c in line["body"]["input"][0]["content"][1:]]
        self.assertTrue(urls[1].startswith("data:image/jpeg;base64,"))
        self.assertEqual(base64.b64decode(urls[2].split(",", 1)[1]), images[2].data)

    def test_golden_prompt_only_uses_generate(self):
        request = self.check_golden("Prompt only", [])
        line = json.loads(request.to_bytes())
        self.assertEqual(line["body"]["tools"][0]["action"], "generate")

    def test_transparent_background(self):
        request = self.check_golden("Prompt", [png(10)], transparent=True)
        tool = json.loads(request.to_bytes())["body"]["tools"][0]
        self.assertEqual((tool["background"], tool["output_format"]), ("transparent", "png"))

    def test_same_instruction_with_and_without_images(self):
        with_images = json.loads(batch_request.build("x", 1024, 1024, "max", [png(10)]).to_bytes())
        without = json.loads(batch_request.build("x", 1024, 1024, "max", []).to_bytes())
        self.assertEqual(with_images["body"]["instructions"], INSTRUCTIONS)
        self.assertEqual(without["body"]["instructions"], INSTRUCTIONS)
        self.assertNotIn("exactly one", INSTRUCTIONS)

    def test_size_for_counts_the_background_value(self):
        images = [png(1000)]
        for transparent in (False, True):
            request = batch_request.build("x", 1024, 1024, "max", images, transparent)
            self.assertEqual(batch_request.size_for("x", 1024, 1024, "max", images, transparent),
                             len(request.to_bytes()))

    def test_prompt_is_kept_unchanged(self):
        prompt = "  keep  my spacing  \n\n"
        line = json.loads(batch_request.build(prompt, 1024, 1024, "high", []).to_bytes())
        self.assertEqual(line["body"]["input"][0]["content"][0]["text"], prompt)

    def test_prompt_containing_placeholder_like_text(self):
        self.check_golden("@@IMAGE_0_deadbeef@@ is just text", [png(10)])

    def test_unique_custom_ids(self):
        a = batch_request.build("x", 1024, 1024, "max", [])
        b = batch_request.build("x", 1024, 1024, "max", [])
        self.assertNotEqual(a.custom_id, b.custom_id)

    def test_bad_quality(self):
        with self.assertRaises(ValueError):
            batch_request.build("x", 1024, 1024, "ultra", [])

    def test_size_matches_without_building_base64(self):
        images = [png(5_000_000)]
        request = batch_request.build("x", 1024, 1024, "max", images)
        self.assertEqual(request.size, len(request.to_bytes()))


if __name__ == "__main__":
    unittest.main()
