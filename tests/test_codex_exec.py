import asyncio
import json
import os
import stat
import sys
import tempfile
import time
import unittest

import helpers  # noqa: F401
import codex_exec

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_codex.py")


@unittest.skipIf(sys.platform == "win32", "uses a POSIX script as the stand-in Codex")
class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.codex = os.path.join(root, "codex")
        with open(self.codex, "w") as handle:
            handle.write(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
        os.chmod(self.codex, os.stat(self.codex).st_mode | stat.S_IEXEC)
        self.home = os.path.join(root, "codex_home")
        self.log = os.path.join(root, "log.json")
        self.run_dir = os.path.join(root, "run")
        os.makedirs(self.run_dir)
        self.env = {
            "CODEX_HOME": self.home, "FAKE_CODEX_LOG": self.log,
            "OPENAI_API_KEY": "sk-should-not-reach-codex", "CODEX_API_KEY": "also-not",
        }
        self.old_env = {k: os.environ.get(k) for k in list(self.env) + ["FAKE_CODEX_MODE", "FAKE_CODEX_STALE"]}
        os.environ.update(self.env)
        codex_exec.forget_login()

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def run_mode(self, mode, refs=(), **kwargs):
        os.environ["FAKE_CODEX_MODE"] = mode
        return asyncio.run(codex_exec.generate(self.codex, "a cat", list(refs), self.run_dir, **kwargs))

    def make_ref(self, name="reference_01.png"):
        path = os.path.join(self.run_dir, name)
        with open(path, "wb") as handle:
            handle.write(b"fake")
        return path

    def test_success_uses_image_from_this_runs_folder(self):
        ref = self.make_ref()
        result = self.run_mode("success", [ref])
        self.assertTrue(result.image_path.startswith(os.path.join(self.home, "generated_images", result.thread_id)))
        self.assertTrue(os.path.isfile(result.image_path))
        self.assertEqual(result.usage["output_tokens"], 258)
        with open(self.log) as handle:
            seen = json.load(handle)
        self.assertFalse(seen["has_api_key"])
        self.assertFalse(seen["has_codex_key"])
        self.assertEqual(seen["cwd"], os.path.realpath(self.run_dir))
        self.assertIn("a cat", seen["stdin"])
        self.assertIn(ref, seen["stdin"])
        args = seen["args"]
        for flag in ["--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check"]:
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(args[args.index("--enable") + 1], "image_generation")
        self.assertEqual(args[-2:], ["--image", ref])
        self.assertEqual(args[args.index("--image") - 1], "-")

    def test_folder_wins_over_a_wrong_reported_path(self):
        result = self.run_mode("success_wrong_path")
        self.assertIn(os.path.join("generated_images", result.thread_id), result.image_path)

    def test_moderation_block_is_reported(self):
        with self.assertRaises(codex_exec.CodexError) as caught:
            self.run_mode("blocked")
        self.assertIn("moderation", str(caught.exception))

    def test_reference_is_never_returned_as_result(self):
        ref = self.make_ref()
        with self.assertRaises(codex_exec.CodexError):
            self.run_mode("lie_reference", [ref])

    def test_stale_file_is_rejected(self):
        stale = os.path.join(self.tmp.name, "old.png")
        with open(stale, "wb") as handle:
            handle.write(b"old")
        old = time.time() - 3600
        os.utime(stale, (old, old))
        os.environ["FAKE_CODEX_STALE"] = stale
        with self.assertRaises(codex_exec.CodexError):
            self.run_mode("stale")

    def test_crash_reports_exit_code(self):
        with self.assertRaises(codex_exec.CodexError) as caught:
            self.run_mode("crash")
        self.assertIn("exit code 3", str(caught.exception))

    def test_timeout_stops_codex(self):
        start = time.monotonic()
        with self.assertRaises(codex_exec.CodexError) as caught:
            self.run_mode("hang", timeout=1.5, poll_interval=0.05)
        self.assertLess(time.monotonic() - start, 20)
        self.assertIn("did not finish", str(caught.exception))

    def test_interrupt_stops_codex(self):
        start = time.monotonic()
        deadline = start + 1.0
        with self.assertRaises(codex_exec.CodexInterrupted):
            self.run_mode("hang", is_interrupted=lambda: time.monotonic() > deadline, poll_interval=0.05)
        self.assertLess(time.monotonic() - start, 20)

    def test_login_checks(self):
        os.environ["FAKE_CODEX_MODE"] = "login_ok"
        codex_exec.check_login(self.codex)
        codex_exec.forget_login()
        os.environ["FAKE_CODEX_MODE"] = "login_none"
        with self.assertRaises(codex_exec.CodexError) as caught:
            codex_exec.check_login(self.codex)
        self.assertIn("codex login", str(caught.exception))
        os.environ["FAKE_CODEX_MODE"] = "login_apikey"
        with self.assertRaises(codex_exec.CodexError) as caught:
            codex_exec.check_login(self.codex)
        self.assertIn("API key", str(caught.exception))


class InstructionTests(unittest.TestCase):
    def test_prompt_only_has_no_reference_sentence(self):
        text = codex_exec.build_instruction("a cat", [])
        self.assertNotIn("attached images", text)
        self.assertIn('"""\na cat\n"""', text)

    def test_references_listed_in_order(self):
        text = codex_exec.build_instruction("x", ["/a/reference_01.png", "/a/reference_02.png"])
        self.assertLess(text.index("reference_01"), text.index("reference_02"))


if __name__ == "__main__":
    unittest.main()
