import asyncio
import json
import os
import stat
import sys
import tempfile
import time
import unittest

import helpers  # noqa: F401
import agy_exec

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_agy.py")


@unittest.skipIf(sys.platform == "win32", "uses a POSIX script as the stand-in Antigravity CLI")
class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.agy = os.path.join(root, "agy")
        with open(self.agy, "w") as handle:
            handle.write(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
        os.chmod(self.agy, os.stat(self.agy).st_mode | stat.S_IEXEC)
        self.home = os.path.join(root, "home")
        os.makedirs(self.home)
        self.log = os.path.join(root, "log.json")
        self.run_dir = os.path.join(root, "run")
        os.makedirs(self.run_dir)
        self.env = {"HOME": self.home, "FAKE_AGY_LOG": self.log, "GEMINI_API_KEY": "must-not-reach-agy",
                    "AGY_ADC_AUTH": "true", "GOOGLE_GEMINI_BASE_URL": "https://example.invalid"}
        self.old_env = {k: os.environ.get(k) for k in list(self.env) + ["FAKE_AGY_MODE"]}
        os.environ.update(self.env)

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def run_mode(self, mode, references=(), prompt="a painting with visible craquelure", **kwargs):
        os.environ["FAKE_AGY_MODE"] = mode
        return asyncio.run(agy_exec.generate(self.agy, prompt, list(references), self.run_dir, **kwargs))

    def logged(self):
        with open(self.log) as handle:
            return json.load(handle)

    def reference(self, name="reference_01.png"):
        from PIL import Image
        path = os.path.join(self.run_dir, name)
        Image.new("RGBA", (12, 12), (9, 8, 7, 0)).save(path)
        return path

    def test_success_command_and_environment(self):
        result = self.run_mode("success")
        (image,) = result.images
        self.assertTrue(image.startswith(os.path.join(self.home, ".gemini", "antigravity-cli", "brain")))
        self.assertIn(result.conversation_id, image)
        seen = self.logged()
        args = seen["args"]
        self.assertEqual(args[args.index("--model") + 1], "gemini-3.8-flash-low")
        self.assertEqual(args[args.index("--output-format") + 1], "json")
        self.assertEqual(args[args.index("--print-timeout") + 1], "15m")
        self.assertTrue(os.path.isfile(args[args.index("--json-schema") + 1]))
        self.assertEqual(seen["cwd"], os.path.realpath(self.run_dir))
        self.assertEqual(seen["env"], {"GEMINI_API_KEY": None, "AGY_ADC_AUTH": None, "GOOGLE_GEMINI_BASE_URL": None})
        self.assertIn("There are no reference images.", seen["request"])
        self.assertTrue(seen["request"].endswith("IMAGE PROMPT:\na painting with visible craquelure"))

    def test_reference_is_passed_and_its_copies_are_never_results(self):
        ref = self.reference()
        result = self.run_mode("success", [ref])
        self.assertEqual(len(result.images), 1)
        self.assertTrue(result.images[0].endswith(".jpg"))
        self.assertIn(f"Reference image 1: {ref}", self.logged()["request"])

    def test_three_saved_images_are_all_returned_in_order(self):
        result = self.run_mode("three")
        self.assertEqual(len(result.images), 3)
        times = [os.path.getmtime(p) for p in result.images]
        self.assertEqual(times, sorted(times))

    def test_unreported_image_is_found_in_the_conversation_folder(self):
        self.assertEqual(len(self.run_mode("unreported").images), 1)

    def test_reported_reference_or_outside_file_is_rejected(self):
        ref = self.reference()
        with self.assertRaises(agy_exec.AgyError) as caught:
            self.run_mode("lie", [ref])
        message = str(caught.exception)
        self.assertTrue(message.startswith(
            "Antigravity finished without a new image. None of the paths it reported is a new image "
            f"saved by this run: {ref}, /etc/hostname. Antigravity's files for this run: "
            f"{os.path.join(self.home, '.gemini', 'antigravity-cli', 'brain')}{os.sep}"), message)

    def test_identical_copy_of_a_reference_is_rejected(self):
        ref = self.reference()
        with self.assertRaises(agy_exec.AgyError):
            self.run_mode("copy_in_folder", [ref])

    def test_denied_actions_are_named(self):
        with self.assertRaises(agy_exec.AgyError) as caught:
            self.run_mode("denied")
        self.assertIn("read_file", str(caught.exception))

    def test_quota_quotes_the_exact_message(self):
        with self.assertRaises(agy_exec.AgyError) as caught:
            self.run_mode("quota")
        message = str(caught.exception)
        self.assertIn("quota appears to be used up", message)
        self.assertIn("Your AI credits balance is too low to continue.", message)
        self.assertIn("exit code 3", message)

    def test_google_server_error_is_named_and_quoted(self):
        with self.assertRaises(agy_exec.AgyError) as caught:
            self.run_mode("unavailable")
        self.assertTrue(str(caught.exception).startswith(
            "A Google server did not finish the request in time (a server-side error). Nothing was "
            "retried; the same run may work later. Antigravity said: 503 Service Unavailable "
            "(\"Deadline expired before operation could complete.\", status: UNAVAILABLE) Antigravity's files "
            "for this run: "), str(caught.exception))

    def test_not_signed_in(self):
        with self.assertRaises(agy_exec.AgyError) as caught:
            self.run_mode("auth")
        self.assertIn("not signed in", str(caught.exception))

    def test_unknown_model(self):
        with self.assertRaises(agy_exec.AgyError) as caught:
            self.run_mode("bad_model")
        self.assertIn("does not offer gemini-3.8-flash-low", str(caught.exception))

    def test_api_key_mode_is_refused_before_running(self):
        settings = os.path.join(self.home, ".gemini", "antigravity-cli")
        os.makedirs(settings)
        with open(os.path.join(settings, "settings.json"), "w") as handle:
            json.dump({"modelProvider": "gemini"}, handle)
        with self.assertRaises(agy_exec.AgyError) as caught:
            self.run_mode("success")
        self.assertIn("API-key mode", str(caught.exception))
        self.assertFalse(os.path.exists(self.log))  # Antigravity never started

    def test_too_long_prompt_is_refused_before_running(self):
        with self.assertRaises(agy_exec.AgyError) as caught:
            self.run_mode("success", prompt="x" * 31_000)
        self.assertIn("too long", str(caught.exception))
        self.assertFalse(os.path.exists(self.log))

    def test_timeout_and_stop_end_the_process(self):
        with self.assertRaises(agy_exec.AgyError) as caught:
            self.run_mode("hang", timeout=1.0, poll_interval=0.05)
        self.assertIn("did not finish", str(caught.exception))
        started = time.monotonic()
        with self.assertRaises(agy_exec.AgyInterrupted):
            self.run_mode("hang", is_interrupted=lambda: time.monotonic() - started > 0.5, poll_interval=0.05)


class StructuredTests(unittest.TestCase):
    def test_reads_structured_output_or_the_json_in_the_response(self):
        real = {"conversation_id": "c", "status": "SUCCESS",
                "response": "I have dispatched the task.\n{\"error\":\"\",\"generated\":true,\"image_paths\":[\"C:\\\\a.jpg\"]}\n"}
        self.assertEqual(agy_exec.structured(real)["image_paths"], ["C:\\a.jpg"])
        real["structured_output"] = {"generated": False, "image_paths": [], "error": "x"}
        self.assertEqual(agy_exec.structured(real)["error"], "x")


if __name__ == "__main__":
    unittest.main()
