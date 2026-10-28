import unittest

import helpers  # noqa: F401
import grok_video
from grok_video import VideoRequest


class ChoicesTests(unittest.TestCase):
    def test_choices_are_the_ones_grok_build_accepts(self):
        # video_gen/mod.rs (synced 2026-09-29)
        self.assertEqual(grok_video.ASPECT_RATIOS, ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"))
        self.assertEqual(grok_video.RESOLUTIONS, ("480p", "720p"))
        self.assertEqual(grok_video.DURATIONS, tuple(range(1, 16)))
        self.assertEqual((grok_video.MAX_REFERENCES, grok_video.MAX_VOICES), (14, 3))

    def test_voices_are_the_28_text_to_speech_voices_and_none(self):
        self.assertEqual(len(grok_video.VOICE_IDS), 28)
        self.assertEqual(len(set(grok_video.VOICE_IDS)), 28)
        self.assertEqual(grok_video.VOICE_CHOICES[0], "none")
        for voice in ("ara", "eve", "leo", "rex", "sal", "zenith", "carina"):
            self.assertIn(voice, grok_video.VOICE_IDS)

    def test_middle_frame_is_halfway(self):
        self.assertEqual(grok_video.middle_timestamp(6), 3.0)
        self.assertEqual(grok_video.middle_timestamp(15), 7.5)
        self.assertEqual(grok_video.middle_timestamp(1), 0.5)
        for duration in grok_video.DURATIONS:  # Grok needs 0 < t < duration
            self.assertTrue(0 < grok_video.middle_timestamp(duration) < duration)


class ValidateTests(unittest.TestCase):
    def check(self, message_part, **kwargs):
        with self.assertRaises(grok_video.VideoRequestError) as caught:
            VideoRequest(**dict({"prompt": "p"}, **kwargs)).validate()
        self.assertIn(message_part, str(caught.exception))

    def test_valid_requests(self):
        VideoRequest(prompt="p").validate()
        VideoRequest(prompt="p", references=tuple(f"r{i}.png" for i in range(14)), voices=("ara", "eve", "leo")).validate()
        VideoRequest(prompt="p", first_frame="a.png", middle_frame="b.png", last_frame="c.png").validate()

    def test_refusals(self):
        self.check("The prompt is empty", prompt="  ")
        self.check("Unknown aspect ratio '21:9'", aspect_ratio="21:9")
        self.check("Unknown resolution '1080p'", resolution="1080p")
        self.check("Duration must be a whole number", duration=16)
        self.check("Duration must be a whole number", duration=0)
        self.check("Duration must be a whole number", duration=True)
        self.check("at most 14 reference images; 15", references=tuple(f"r{i}.png" for i in range(15)))
        self.check("at most 3 voices", voices=("ara", "eve", "leo", "rex"))
        self.check("Unknown voice: nobody", voices=("nobody",))
        self.check("cannot be used in the same run", references=("r.png",), first_frame="a.png")

    def test_first_frame_is_made_only_when_nothing_else_is_given(self):
        self.assertTrue(VideoRequest(prompt="p").make_first_frame)
        for other in ({"first_frame": "a"}, {"middle_frame": "a"}, {"last_frame": "a"}, {"references": ("a",)},
                      {"voices": ("ara",)}):
            self.assertFalse(VideoRequest(prompt="p", **other).make_first_frame, other)
        self.assertEqual(VideoRequest(prompt="p").tools, ("image_gen", "reference_to_video"))
        self.assertEqual(VideoRequest(prompt="p", voices=("ara",)).tools, ("reference_to_video",))


class MentionTests(unittest.TestCase):
    def convert(self, text, images=None, voices=None):
        return grok_video.convert_mentions(text, images, voices or {})

    def test_images_become_grok_tags(self):
        result = self.convert("Image 1 hugs image 2, then IMAGE_3 waves. See image #2 and image1.",
                              {1: [0], 2: [1], 3: [2]})
        self.assertEqual(result.text, "<IMAGE_0> hugs <IMAGE_1>, then <IMAGE_2> waves. See <IMAGE_1> and <IMAGE_0>.")
        self.assertEqual(result.unmatched, [])
        self.assertEqual(result.replaced[0], ("Image 1", "<IMAGE_0>"))

    def test_tag_numbers_follow_the_connected_images_not_the_socket_numbers(self):
        # image_1 and image_3 are connected: they are Grok's images 0 and 1
        result = self.convert("image 3 meets image 1", {1: [0], 3: [1]})
        self.assertEqual(result.text, "<IMAGE_1> meets <IMAGE_0>")

    def test_a_socket_with_a_batch_becomes_all_its_tags(self):
        self.assertEqual(self.convert("image 2 dance", {1: [0], 2: [1, 2, 3]}).text, "<IMAGE_1> <IMAGE_2> <IMAGE_3> dance")

    def test_mentions_of_missing_images_are_left_alone_and_reported(self):
        result = self.convert("image 1 and image 4 and image 0", {1: [0]})
        self.assertEqual(result.text, "<IMAGE_0> and image 4 and image 0")
        self.assertEqual(result.unmatched, ["image 4", "image 0"])

    def test_not_a_mention(self):
        for text in ("images 1 and 2", "the subimage 2", "image 123", "a <IMAGE_1> tag", "image one", "an imagery 2",
                     "image 2x", "myimage1"):
            result = self.convert(text, {1: [0], 2: [1]})
            self.assertEqual(result.text, text, text)
            self.assertEqual(result.replaced, [], text)

    def test_frames_mode_leaves_image_mentions_alone(self):
        self.assertEqual(self.convert("image 1 walks", None).text, "image 1 walks")

    def test_voices(self):
        result = self.convert("Voice 1 says hi, voice_2 answers, voice #3 is quiet.", None, {1: 0, 2: 1})
        self.assertEqual(result.text, "<AUDIO_0> says hi, <AUDIO_1> answers, voice #3 is quiet.")
        self.assertEqual(result.unmatched, ["voice #3"])

    def test_voice_numbers_follow_the_chosen_voices(self):
        # voice_1 is none, voice_2 is the first voice Grok gets
        self.assertEqual(self.convert("voice 2 sings", None, {2: 0}).text, "<AUDIO_0> sings")

    def test_existing_tags_are_untouched(self):
        text = "<IMAGE_0> and <AUDIO_1> and <IMAGE_9>"
        self.assertEqual(self.convert(text, {1: [0]}, {1: 0}).text, text)


class InstructionTests(unittest.TestCase):
    def test_no_input_makes_the_first_frame_like_the_users_test(self):
        text = grok_video.build_video_instruction(VideoRequest(prompt="a pale woman shoving the viewer", duration=5))
        self.assertEqual(text, (
            "Make one short video in two steps. Call each tool exactly once, do not use any other tool, and do not "
            "make another attempt if a tool fails.\n"
            "1. Call image_gen with the video prompt below, unchanged, as its prompt, and aspect_ratio \"16:9\". "
            "This image becomes the first frame of the video.\n"
            "2. Call reference_to_video with exactly these arguments and no others:\n"
            "   prompt: the video prompt below, unchanged\n"
            "   first_frame: the absolute path of the image from step 1\n"
            "   aspect_ratio: \"16:9\"\n"
            "   duration: 5\n"
            "   resolution_name: \"720p\"\n"
            "The prompt may contain tags such as <IMAGE_0> or <AUDIO_0>; leave them exactly as they are.\n"
            "When both tools have finished, reply with one short sentence.\n"
            "\n"
            "VIDEO PROMPT:\n"
            "a pale woman shoving the viewer\n"))

    def test_frames(self):
        text = grok_video.build_video_instruction(VideoRequest(
            prompt="she turns", aspect_ratio="9:16", duration=7, resolution="480p",
            first_frame="/run/first_frame.png", middle_frame="/run/middle_frame.png", last_frame="/run/last_frame.png"))
        self.assertEqual(text, (
            "Make one short video. Call the reference_to_video tool exactly once, do not use any other tool, and do "
            "not make another attempt if the tool fails. Pass exactly these arguments and no others:\n"
            "   prompt: the video prompt below, unchanged\n"
            "   first_frame: /run/first_frame.png\n"
            "   keyframes: a list with one entry: image /run/middle_frame.png, timestamp_s 3.5\n"
            "   last_frame: /run/last_frame.png\n"
            "   aspect_ratio: \"9:16\"\n"
            "   duration: 7\n"
            "   resolution_name: \"480p\"\n"
            "The prompt may contain tags such as <IMAGE_0> or <AUDIO_0>; leave them exactly as they are.\n"
            "When the tool has finished, reply with one short sentence.\n"
            "\n"
            "VIDEO PROMPT:\n"
            "she turns\n"))

    def test_only_the_connected_frames_are_named(self):
        text = grok_video.build_video_instruction(VideoRequest(prompt="p", last_frame="/run/last_frame.png"))
        self.assertIn("   last_frame: /run/last_frame.png\n", text)
        self.assertNotIn("first_frame", text)
        self.assertNotIn("keyframes", text)
        self.assertNotIn("image_gen", text)

    def test_references_and_voices(self):
        text = grok_video.build_video_instruction(VideoRequest(
            prompt="<IMAGE_0> speaks as <AUDIO_0>", references=("/run/reference_01.png", "C:\\run\\reference_02.png"),
            voices=("ara", "rex")))
        self.assertIn("   images: these image files, in this order:\n      /run/reference_01.png\n"
                      "      C:\\run\\reference_02.png\n   voices: [\"ara\", \"rex\"]\n   aspect_ratio:", text)
        self.assertNotIn("image_gen", text)
        self.assertTrue(text.endswith("VIDEO PROMPT:\n<IMAGE_0> speaks as <AUDIO_0>\n"))

    def test_voice_only_names_no_image(self):
        text = grok_video.build_video_instruction(VideoRequest(prompt="she sings", voices=("eve",)))
        self.assertIn("   voices: [\"eve\"]\n", text)
        for word in ("first_frame", "images", "keyframes", "image_gen"):
            self.assertNotIn(word, text)

    def test_describe(self):
        self.assertEqual(grok_video.describe(VideoRequest(prompt="p")),
                         "16:9, 6 s, 720p, no input: the prompt makes the first frame")
        self.assertIn("middle frame connected (at 3.0 s)", grok_video.describe(VideoRequest(prompt="p", middle_frame="m")))


if __name__ == "__main__":
    unittest.main()
