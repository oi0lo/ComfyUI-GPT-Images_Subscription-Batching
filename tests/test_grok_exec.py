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
import grok_video

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_grok.py")


@unittest.skipIf(sys.platform == "win32", "uses a POSIX script as the stand-in Grok Build")
class FakeGrokCase(unittest.TestCase):
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
        self.old_env = {k: os.environ.get(k) for k in list(self.env) + ["FAKE_GROK_MODE", "FAKE_GROK_STALE",
                                                                         "FAKE_REF_COPY_DIR"]}
        os.environ.update(self.env)

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def logged(self):
        with open(self.log) as handle:
            return json.load(handle)

    def reference(self, name="reference_01.png"):
        from PIL import Image
        path = os.path.join(self.run_dir, name)
        Image.new("RGBA", (8, 8), (1, 2, 3, 0)).save(path)
        os.utime(path, (time.time() - 60, time.time() - 60))
        return path


class GenerateTests(FakeGrokCase):
    def run_mode(self, mode, references=(), **kwargs):
        os.environ["FAKE_GROK_MODE"] = mode
        return asyncio.run(grok_exec.generate(self.grok, "a cat on Saturn's rings", list(references),
                                              self.run_dir, **kwargs))

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


class GenerateVideoTests(FakeGrokCase):
    PROMPT = "a pale young blonde woman is playfully angry at the viewer"

    def request(self, **kwargs):
        return grok_video.VideoRequest(**dict({"prompt": self.PROMPT, "duration": 5}, **kwargs))

    def run_video(self, mode, request=None, **kwargs):
        os.environ["FAKE_GROK_MODE"] = mode
        return asyncio.run(grok_exec.generate_video(self.grok, request or self.request(), self.run_dir, **kwargs))

    def calls(self):
        return self.logged()["tool_calls"]

    def test_no_input_makes_the_first_frame_then_the_video(self):
        result = self.run_video("success")
        self.assertTrue(result.video.path.endswith(".mp4") and os.path.isfile(result.video.path))
        self.assertEqual((result.video.tool, result.video.prompt, result.video.aspect_ratio, result.video.duration,
                          result.video.resolution), ("reference_to_video", self.PROMPT, "16:9", 5, "720p"))
        self.assertEqual((result.first_frame.tool, result.first_frame.prompt), ("image_gen", self.PROMPT))
        self.assertTrue(os.path.isfile(result.first_frame.path))
        self.assertEqual(result.extra_videos, [])
        self.assertAlmostEqual(result.cost_usd, 0.31)
        seen = self.logged()
        args = seen["args"]
        self.assertEqual(args[args.index("--tools") + 1], "image_gen,reference_to_video")
        self.assertEqual(args[args.index("--max-turns") + 1], "6")
        self.assertNotIn("--effort", args)
        self.assertFalse(seen["has_api_key"])
        self.assertEqual(seen["disable_api_key_auth"], "1")
        first, second = self.calls()
        self.assertEqual(first, {"tool": "image_gen", "input": {"prompt": self.PROMPT, "aspect_ratio": "16:9"}})
        self.assertEqual(second["tool"], "reference_to_video")
        self.assertEqual(second["input"], {"prompt": self.PROMPT, "first_frame": result.first_frame.path,
                                           "aspect_ratio": "16:9", "duration": 5, "resolution_name": "720p"})

    def test_frames_are_pinned_first_middle_last_and_no_first_frame_is_made(self):
        first, middle, last = (self.reference(n) for n in ("first_frame.png", "middle_frame.png", "last_frame.png"))
        result = self.run_video("success", self.request(first_frame=first, middle_frame=middle, last_frame=last,
                                                        duration=7, aspect_ratio="9:16", resolution="480p"))
        self.assertIsNone(result.first_frame)
        args = self.logged()["args"]
        self.assertEqual(args[args.index("--tools") + 1], "reference_to_video")
        self.assertEqual(args[args.index("--max-turns") + 1], "4")
        (call,) = self.calls()
        self.assertEqual(call["input"], {
            "prompt": self.PROMPT, "first_frame": first, "keyframes": [{"image": middle, "timestamp_s": 3.5}],
            "last_frame": last, "aspect_ratio": "9:16", "duration": 7, "resolution_name": "480p"})

    def test_references_and_voices_are_passed_in_order(self):
        refs = [self.reference(f"reference_0{n}.png") for n in (1, 2, 3)]
        result = self.run_video("success", self.request(prompt="<IMAGE_0> and <IMAGE_1> talk, <AUDIO_0>",
                                                        references=tuple(refs), voices=("ara", "rex")))
        (call,) = self.calls()
        self.assertEqual(call["input"]["images"], refs)
        self.assertEqual(call["input"]["voices"], ["ara", "rex"])
        self.assertEqual(call["input"]["prompt"], "<IMAGE_0> and <IMAGE_1> talk, <AUDIO_0>")
        self.assertNotIn("first_frame", call["input"])
        self.assertEqual(result.video.prompt, "<IMAGE_0> and <IMAGE_1> talk, <AUDIO_0>")

    def test_voice_only_has_no_first_frame_and_no_image_tool(self):
        result = self.run_video("success", self.request(voices=("eve",)))
        self.assertIsNone(result.first_frame)
        self.assertEqual([c["tool"] for c in self.calls()], ["reference_to_video"])
        self.assertEqual(self.calls()[0]["input"]["voices"], ["eve"])
        args = self.logged()["args"]
        self.assertEqual(args[args.index("--tools") + 1], "reference_to_video")

    def test_prompt_output_is_the_tool_input_not_ours(self):
        result = self.run_video("rewrite")
        self.assertEqual(result.video.prompt, "REWRITTEN BY AGENT: " + self.PROMPT)

    def test_privacy_mode_error_says_how_to_turn_it_off_and_keeps_the_first_frame(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_video("zdr")
        message = str(caught.exception)
        self.assertTrue(message.startswith(
            "Grok Build refused to make a video because its privacy mode (zero data retention) is on. Turn it off in "
            "Grok Build: type /privacy and set \"Coding data, retention, and training\" to Opt in, then run again. "
            "No video was made. The first frame Grok made is saved at: "), message)
        self.assertIn("Grok said: Video generation tools are unavailable under zero data retention (ZDR).", message)
        self.assertIn(os.path.join("sessions"), message)

    def test_free_plan_message(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_video("upsell", self.request(voices=("eve",)))
        self.assertIn("does not include video generation", str(caught.exception))
        self.assertIn("SuperGrok", str(caught.exception))

    def test_403_and_429_and_timeout_and_unknown_voice_quote_the_tool(self):
        cases = {
            "video_http403": ["Grok's video tool failed: Video generation failed with HTTP 403 Forbidden",
                              "refused the request for this account", "includes video generation"],
            "video_429": ["HTTP 429 Too Many Requests: Too many requests for team", "limit on xAI's side",
                          "'grok logout' and then 'grok login'"],
            "video_timeout": ["Video generation did not complete within 300s (request_id=req-abc)",
                              "waits at most 300 seconds for a video. Nothing was retried."],
            "bad_voice": ["unknown voice. Available voices: ara, eve"],
        }
        for mode, parts in cases.items():
            with self.assertRaises(grok_exec.GrokError, msg=mode) as caught:
                self.run_video(mode, self.request(voices=("eve",)))
            for part in parts:
                self.assertIn(part, str(caught.exception), mode)

    def test_first_frame_failure_is_reported_as_an_image_error(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_video("still_fails")
        self.assertTrue(str(caught.exception).startswith("Grok's image tool failed: Image generation failed with HTTP 403"))
        self.assertIn("includes image generation", str(caught.exception))

    def test_agent_stops_after_the_still(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_video("still_only")
        message = str(caught.exception)
        self.assertIn("Grok's agent did not call the video tool. It made the first frame.", message)
        self.assertIn("The first frame Grok made is saved at: ", message)

    def test_no_tool_call(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_video("no_tool", self.request(voices=("eve",)))
        self.assertIn("did not call the video tool", str(caught.exception))

    def test_video_uploaded_to_a_bucket_is_explained(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_video("uploaded", self.request(voices=("eve",)))
        self.assertIn("uploaded the video to the storage bucket", str(caught.exception))
        self.assertIn("https://bucket.example/grok-videos/abc.mp4", str(caught.exception))

    def test_path_missing_from_stream_falls_back_to_the_session_videos_folder(self):
        result = self.run_video("no_path", self.request(voices=("eve",)))
        self.assertIn(result.session_id, result.video.path)
        self.assertTrue(result.video.path.endswith("1.mp4"))

    def test_a_second_video_is_reported_but_the_first_is_the_result(self):
        result = self.run_video("two_videos", self.request(voices=("eve",)))
        self.assertTrue(result.video.path.endswith("1.mp4"))
        self.assertEqual(len(result.extra_videos), 1)
        self.assertTrue(result.extra_videos[0].endswith("2.mp4"))

    def test_an_input_or_an_old_file_is_never_the_video(self):
        ref = self.reference("first_frame.png")
        with self.assertRaises(grok_exec.GrokError):
            self.run_video("lie", self.request(first_frame=ref))
        old = os.path.join(self.run_dir, "old.mp4")
        with open(old, "wb") as handle:
            handle.write(b"old video")
        os.utime(old, (time.time() - 600, time.time() - 600))
        os.environ["FAKE_GROK_STALE"] = old
        with self.assertRaises(grok_exec.GrokError):
            self.run_video("stale", self.request(voices=("eve",)))

    def test_refused_before_running_when_the_request_is_wrong(self):
        for request, part in ((self.request(duration=99), "Duration must be"),
                              (self.request(aspect_ratio="21:9"), "Unknown aspect ratio"),
                              (self.request(prompt=" "), "The prompt is empty")):
            with self.assertRaises(grok_exec.GrokError) as caught:
                self.run_video("success", request)
            self.assertIn(part, str(caught.exception))
            self.assertTrue(str(caught.exception).endswith("Nothing was sent."))
        self.assertFalse(os.path.exists(self.log))  # Grok Build never started

    def test_timeout_and_stop_end_the_process(self):
        with self.assertRaises(grok_exec.GrokError) as caught:
            self.run_video("hang", timeout=1.0, poll_interval=0.05)
        self.assertIn("did not finish", str(caught.exception))
        started = time.monotonic()
        with self.assertRaises(grok_exec.GrokInterrupted):
            self.run_video("hang", is_interrupted=lambda: time.monotonic() - started > 0.5, poll_interval=0.05)

    def test_image_errors_still_get_the_429_note(self):
        os.environ["FAKE_GROK_MODE"] = "http429"
        with self.assertRaises(grok_exec.GrokError) as caught:
            asyncio.run(grok_exec.generate(self.grok, "a cat", [], self.run_dir))
        self.assertIn("HTTP 429 Too Many Requests", str(caught.exception))
        self.assertIn("limit on xAI's side", str(caught.exception))


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
