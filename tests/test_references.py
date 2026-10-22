import io
import os
import tempfile
import unittest

import numpy as np
from PIL import Image

import helpers  # noqa: F401
import references

try:
    import torch
except ImportError:  # the pure numpy path is still tested
    torch = None


def batch(count, height=24, width=32, value=0.5, channels=3):
    data = np.full((count, height, width, channels), value, dtype=np.float32)
    return torch.from_numpy(data) if torch is not None else data


class CollectTests(unittest.TestCase):
    def test_numeric_socket_order_and_batch_flattening(self):
        images = {"image_10": batch(1, value=0.1), "image_2": batch(2, value=0.2), "image_1": batch(1, value=0.3)}
        ordered = references.collect(images)
        self.assertEqual([socket for socket, _ in ordered], ["image_1", "image_2", "image_2", "image_10"])

    def test_list_inputs_are_flattened_in_order(self):
        images = {"image_1": [batch(1, value=0.1), batch(1, value=0.9)], "image_3": [None]}
        ordered = references.collect(images)
        self.assertEqual(len(ordered), 2)
        first = references.to_uint8(ordered[0][1])
        self.assertEqual(int(first[0, 0, 0]), round(0.1 * 255))

    def test_empty(self):
        self.assertEqual(references.collect(None), [])
        self.assertEqual(references.collect({}), [])

    def test_rejects_rgba_and_bad_shapes(self):
        with self.assertRaises(references.ReferenceError):
            references.collect({"image_1": batch(1, channels=4)})
        bad = np.zeros((24, 32, 3), dtype=np.float32)
        with self.assertRaises(references.ReferenceError):
            references.collect({"image_1": bad})

    def test_rejects_non_finite(self):
        data = batch(1)
        data[0, 0, 0, 0] = float("nan")
        (socket, item), = references.collect({"image_1": data})
        with self.assertRaises(references.ReferenceError):
            references.encode(item, "png", socket)


class EncodeTests(unittest.TestCase):
    def test_8bit_round_trip_is_exact(self):
        # Same conversion ComfyUI's LoadImage uses: uint8 / 255 as float32.
        values = np.arange(256, dtype=np.uint8).reshape(16, 16)
        rgb = np.stack([values, values[::-1], values.T], axis=-1)
        as_float = (rgb.astype(np.float32) / 255.0)[None]
        tensor = torch.from_numpy(as_float) if torch is not None else as_float
        (socket, item), = references.collect({"image_1": tensor})
        encoded = references.encode(item, "png", socket)
        decoded = np.asarray(Image.open(io.BytesIO(encoded.data)))
        self.assertTrue(np.array_equal(decoded, rgb))
        self.assertEqual(encoded.mime, "image/png")
        self.assertEqual((encoded.width, encoded.height), (16, 16))

    def test_jpeg_fallback_only_when_png_does_not_fit(self):
        refs = references.collect({"image_1": batch(1)})
        encoded, kind = references.encode_with_fallback(refs, lambda items: True)
        self.assertEqual(kind, "png")
        calls = []

        def png_too_big(items):
            calls.append(items[0].mime)
            return items[0].mime == "image/jpeg"

        encoded, kind = references.encode_with_fallback(refs, png_too_big)
        self.assertEqual(kind, "jpeg")
        self.assertEqual(calls, ["image/png", "image/jpeg"])
        self.assertTrue(encoded[0].data.startswith(b"\xff\xd8"))

    def test_jpeg_survives_incompressible_4k_image(self):
        # Regression: Pillow's optimize=True buffer overflowed on noisy 4K images.
        noise = np.random.default_rng(0).random((1, 2160, 3840, 3), dtype=np.float32)
        (socket, item), = references.collect({"image_1": noise})
        encoded = references.encode(item, "jpeg", socket)
        self.assertGreater(len(encoded.data), 1_000_000)
        self.assertEqual(Image.open(io.BytesIO(encoded.data)).size, (3840, 2160))

    def test_real_fallback_with_a_size_limit(self):
        rng = np.random.default_rng(1)
        y, x = np.mgrid[0:512, 0:768]
        image = np.stack([x / 767, y / 511, (x + y) / 1278], -1) * 0.8 + rng.random((512, 768, 3)) * 0.08
        refs = references.collect({"image_1": image[None].astype(np.float32)})
        png_size = len(references.encode_all(refs, "png")[0].data)
        jpeg_size = len(references.encode_all(refs, "jpeg")[0].data)
        self.assertLess(jpeg_size, png_size)
        limit = (png_size + jpeg_size) // 2
        encoded, kind = references.encode_with_fallback(refs, lambda items: len(items[0].data) <= limit)
        self.assertEqual(kind, "jpeg")
        self.assertLessEqual(len(encoded[0].data), limit)

    def test_png_pass_stops_early_once_too_big(self):
        refs = references.collect({"image_1": batch(5)})
        seen = []

        def fits(items):
            seen.append((items[-1].mime, len(items)))
            return items[-1].mime == "image/jpeg" or len(items) < 2

        encoded, kind = references.encode_with_fallback(refs, fits)
        self.assertEqual(kind, "jpeg")
        self.assertEqual(len(encoded), 5)
        png_calls = [n for mime, n in seen if mime == "image/png"]
        self.assertEqual(png_calls, [1, 2])  # stopped after the second PNG, not all five

    def test_error_when_jpeg_still_too_big(self):
        refs = references.collect({"image_1": batch(1)})
        with self.assertRaises(references.ReferenceError):
            references.encode_with_fallback(refs, lambda items: False)

    def test_write_files_only_creates_new_files(self):
        refs = references.collect({"image_1": batch(2)})
        encoded = references.encode_all(refs, "png")
        with tempfile.TemporaryDirectory() as folder:
            paths = references.write_files(encoded, folder)
            self.assertEqual([os.path.basename(p) for p in paths], ["reference_01.png", "reference_02.png"])
            with self.assertRaises(FileExistsError):
                references.write_files(encoded, folder)


if __name__ == "__main__":
    unittest.main()
