import asyncio
import json
import os
import stat
import sys
import tempfile
import time
import unittest

import helpers  # noqa: F401
import grok_exec

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_grok.py")


@unittest.skipIf(sys.platform == "win32", "uses a POSIX script as the stand-in Grok Build")
class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.grok = os.path.join(root, "grok")
        with open(self.grok, "w") as handle:
            handle.write(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
        os.chmod(self.grok, os.stat(self.grok).st_mode | stat.S_IEXEC)
        self.home = os.path.join(root, "grok_home")
        self.log = os.path.join(root, "log.json")
        self.run_dir = os.path.join(root, "run")
        os.makedirs(self.run_dir)
        self.env = {"GROK_HOME": self.home, "FAKE_GROK_LOG": self.log, "XAI_API_KEY": "xai-must-not-reach-grok"}
        self.old_env = {k: os.environ.get(k) for k in list(self.env) + ["FAKE_GROK_MODE", "FAKE_GROK_STALE"]}
        os.environ.update(self.env)

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def run_mode(self, mode, references=(), **kwargs):
        os.environ["FAKE_GROK_MODE"] = mode
        return asyncio.run(grok_exec.generate(self.grok, "a cat on Saturn's rings", list(references),
                                              self.run_dir, **kwargs))

    def logged(self):
        with open(self.log) as handle:
            return json.load(handle)

    def reference(self):
        from PIL import Image
        path = os.path.join(self.run_dir, "reference_01.png")
        Image.new("RGBA", (8, 8), (1, 2, 3, 0)).save(path)
        os.utime(path, (time.time() - 60, time.time() - 60))
        return path

    def test_text_only_success_and_command(self):
        result = self.run_mode("success")
        (image,) = result.images
        self.assertTrue(os.path.isfile(image.path))
        self.assertEqual((image.tool, image.prompt, image.aspect_ratio), ("image_gen", "a cat on Saturn's rings", "16:9"))
        self.assertAlmostEqual(result.cost_usd, 0.0139)
        seen = self.logged()
        args = seen["args"]
        for flag in ("--no-auto-update", "--verbatim", "--always-approve"):
            self.assertIn(flag, args)
        self.assertNotIn("--effort", args)  # stopped the user's runs before the agent started
        self.assertEqual(args[args.index("--max-turns") + 1], "4")
        self.assertEqual(args[args.index("--tools") + 1], "image_gen,image_edit")
        self.assertEqual(args[args.index("--output-format") + 1], "streaming-json")
        self.assertEqual(args[args.index("--cwd") + 1], self.run_dir)
        self.assertEqual(seen["cwd"], os.path.realpath(self.run_dir))
        self.assertEqual(args[args.index("-s") + 1], result.session_id)
        self.assertFalse(seen["has_api_key"])
        self.assertEqual(seen["disable_api_key_auth"], "1")
        self.assertIn("image_gen tool exactly once", seen["instruction"])
        self.assertTrue(seen["instruction"].endswith("IMAGE PROMPT:\na cat on Saturn's rings\n"))

    def test_references_use_image_edit_with_paths(self):
        ref = self.reference()
        result = self.run_mode("success", [ref])
        self.assertEqual(result.images[0].tool, "image_edit")
        self.assertIn(ref, self.logged()["instruction"])

    def test_prompt_output_is_the_tool_input_not_ours(self):
        result = self.run_mode("rewrite")
        self.assertEqual(result.images[0].prompt, "REWRITTEN BY AGENT: a cat on Saturn's rings")

    def test_two_tool_calls_give_two_images(self):
        result = self.run_mode("two")
        self.assertEqual(len(result.images), 2)
        self.assertEqual(result.images[1].prompt, "a cat on Saturn's rings (second)")

    def test_path_missing_from_stream_falls_back_to_session_folder(self):
        result = self.run_mode("no_path")
        self.assertEqual(len(result.images), 1)
        self.assertIn(result.session_id, result.images[0].path)

    def test_upgrade_message_becomes_a_clear_error(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_mode("upsell")
        self.assertIn("does not include image generation", str(caught.exception))
        self.assertIn("SuperGrok", str(caught.exception))

    def test_403_quotes_the_exact_tool_error(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_mode("http403")
        message = str(caught.exception)
        self.assertIn("Image generation failed with HTTP 403 Forbidden: credits are exhausted", message)
        self.assertIn("refused the request for this account", message)

    def test_not_signed_in(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_mode("not_signed_in")
        self.assertIn("Grok Build is not signed in", str(caught.exception))

    def test_unknown_flag_points_to_a_version_difference(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_mode("bad_flag")
        self.assertIn("different version", str(caught.exception))

    def test_error_before_the_agent_runs(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_mode("setup_error")
        self.assertEqual(str(caught.exception),
                         "Grok Build stopped with an error before its agent called the image tool. Errors: Couldn't "
                         "apply reasoning effort: Invalid params: \"unknown model id\" (exit code 1)")

    def test_no_tool_call(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_mode("no_tool")
        self.assertIn("did not call the image tool", str(caught.exception))

    def test_reference_or_old_file_is_never_returned(self):
        ref = self.reference()
        with self.assertRaises(grok_exec.GrokError):
            self.run_mode("lie", [ref])
        old = os.path.join(self.run_dir, "old.jpg")
        with open(old, "wb") as handle:
            handle.write(b"\xff\xd8\xff old")
        os.utime(old, (time.time() - 600, time.time() - 600))
        os.environ["FAKE_GROK_STALE"] = old
        with self.assertRaises(grok_exec.GrokError):
            self.run_mode("stale")

    def test_timeout_and_stop_end_the_process(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_mode("hang", timeout=1.0, poll_interval=0.05)
        self.assertIn("did not finish", str(caught.exception))
        started = time.monotonic()
        with self.assertRaises(grok_exec.GrokInterrupted):
            self.run_mode("hang", is_interrupted=lambda: time.monotonic() - started > 0.5, poll_interval=0.05)

    def test_missing_program(self):
        old = os.environ.get("PATH")
        os.environ["PATH"] = ""
        try:
            with self.assertRaises(grok_exec.GrokError):
                grok_exec.find_grok()
        finally:
            os.environ["PATH"] = old or ""


class ReferenceSizeTests(unittest.TestCase):
    def test_follows_grok_build_limits(self):
        from collections import namedtuple
        Item = namedtuple("Item", "data width height")
        big = 400 * 1024 + 1
        grok_exec.check_reference_sizes(["image_1"], [Item(b"x" * big, 4000, 3000)])  # exactly 12 MP: accepted
        grok_exec.check_reference_sizes(["image_1"], [Item(b"x" * (400 * 1024), 8000, 8000)])  # small file: used as is
        with self.assertRaises(grok_exec.GrokError) as caught:
            grok_exec.check_reference_sizes(["image_1", "image_2"],
                                            [Item(b"x" * big, 1000, 1000), Item(b"x" * big, 4096, 4096)])
        self.assertEqual(str(caught.exception),
                         "Grok's image tool refuses references over 12,000,000 pixels (unless the file is at most "
                         "400 KB): image_2 (4096 x 4096). Reduce them before this node. Nothing was sent.")


class AspectNoteTests(unittest.TestCase):
    def test_matches_the_tools(self):
        image = grok_exec.GrokImage("x.jpg", "p", "16:9", "image_gen")
        self.assertEqual(grok_exec.aspect_note(image, 0), "16:9")
        self.assertIn("auto", grok_exec.aspect_note(grok_exec.GrokImage("x.jpg", "p", None, "image_gen"), 0))
        edit = grok_exec.GrokImage("x.jpg", "p", "16:9", "image_edit")
        self.assertIn("taken from the reference", grok_exec.aspect_note(edit, 1))
        self.assertEqual(grok_exec.aspect_note(edit, 2), "16:9")
        self.assertEqual(grok_exec.aspect_note(grok_exec.GrokImage("x.jpg", "", None, "unknown"), 0), "not reported")


class FindGrokTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "POSIX file name")
    def test_installer_location_first(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "bin"))
            path = os.path.join(home, "bin", "grok")
            open(path, "w").close()
            old = os.environ.get("GROK_HOME")
            os.environ["GROK_HOME"] = home
            try:
                self.assertEqual(grok_exec.find_grok(), path)
            finally:
                if old is None:
                    os.environ.pop("GROK_HOME", None)
                else:
                    os.environ["GROK_HOME"] = old


if __name__ == "__main__":
    unittest.main()
