import os
import shutil
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401
import run_folder


class RunFolderTests(unittest.TestCase):
    def test_new_empty_folder_in_temp(self):
        path = run_folder.create("comfyui-test-")
        try:
            self.assertTrue(os.path.isdir(path))
            self.assertEqual(os.listdir(path), [])
            self.assertTrue(run_folder.inside(path, tempfile.gettempdir()))
            self.assertTrue(os.path.basename(path).startswith("comfyui-test-"))
        finally:
            shutil.rmtree(path)

    def test_refuses_a_temp_folder_inside_an_avoided_folder(self):
        with tempfile.TemporaryDirectory() as comfy:
            temp = os.path.join(comfy, "temp")
            os.makedirs(temp)
            with mock.patch.object(tempfile, "gettempdir", return_value=temp):
                with self.assertRaises(run_folder.RunFolderError) as caught:
                    run_folder.create("comfyui-test-", avoid=[comfy])
            self.assertIn("never runs an agent", str(caught.exception))
            self.assertEqual(os.listdir(temp), [])

    def test_windows_folder_is_avoided(self):
        with tempfile.TemporaryDirectory() as windows:
            system32 = os.path.join(windows, "System32")
            os.makedirs(system32)
            with mock.patch.object(run_folder, "windows_folder", return_value=windows), \
                    mock.patch.object(tempfile, "gettempdir", return_value=system32):
                with self.assertRaises(run_folder.RunFolderError):
                    run_folder.create("comfyui-test-")

    def test_inside(self):
        self.assertTrue(run_folder.inside("/a/b/c", "/a/b"))
        self.assertTrue(run_folder.inside("/a/b", "/a/b"))
        self.assertFalse(run_folder.inside("/a/bc", "/a/b"))
        self.assertFalse(run_folder.inside("/a", "/a/b"))


if __name__ == "__main__":
    unittest.main()
