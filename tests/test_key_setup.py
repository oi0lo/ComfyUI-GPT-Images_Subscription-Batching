import asyncio
import ctypes
import time
import unittest

import helpers  # noqa: F401
import credentials
import key_prompt
import key_setup


class Refused(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


def run(coro):
    return asyncio.run(coro)


class ObtainKeyTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.saved = []
        self.answers = []
        self.valid = {"sk-good-123456"}

    async def prompt(self, message):
        self.messages.append(message)
        return self.answers.pop(0)

    async def validate(self, key):
        if key == "sk-offline-1234":
            raise ConnectionError("no network")
        if key == "sk-batchless-12":
            raise Refused(403)
        if key not in self.valid:
            raise Refused(401)

    def obtain(self, rejected=False):
        return run(key_setup.obtain_key(self.prompt, self.validate, self.saved.append,
                                        credentials.check_syntax, rejected=rejected))

    def test_good_key_is_checked_then_saved(self):
        self.answers = [" sk-good-123456 "]
        self.assertEqual(self.obtain(), "sk-good-123456")
        self.assertEqual(self.saved, ["sk-good-123456"])
        self.assertEqual(self.messages, [key_setup.MESSAGE_FIRST])

    def test_cancel_saves_nothing(self):
        self.answers = [None]
        with self.assertRaises(key_setup.KeySetupCancelled):
            self.obtain()
        self.assertEqual(self.saved, [])

    def test_wrong_key_asks_again_and_saves_only_the_good_one(self):
        self.answers = ["sk-wrong-123456", "not a key", "sk-good-123456"]
        self.assertEqual(self.obtain(), "sk-good-123456")
        self.assertEqual(self.saved, ["sk-good-123456"])
        self.assertEqual(self.messages[1], key_setup.MESSAGE_WRONG)
        self.assertIn("does not look like a key", self.messages[2])

    def test_key_without_batch_permission_is_explained(self):
        self.answers = ["sk-batchless-12", None]
        with self.assertRaises(key_setup.KeySetupCancelled):
            self.obtain()
        self.assertIn("Paste another key", self.messages[1])
        self.assertEqual(self.saved, [])

    def test_no_network_saves_nothing(self):
        self.answers = ["sk-offline-1234"]
        with self.assertRaises(ConnectionError):
            self.obtain()
        self.assertEqual(self.saved, [])

    def test_rejected_message_for_replacing_a_stored_key(self):
        self.answers = ["sk-good-123456"]
        self.obtain(rejected=True)
        self.assertEqual(self.messages, [key_setup.MESSAGE_REJECTED])

    def test_messages_fit_the_windows_limit(self):
        for message in (key_setup.MESSAGE_FIRST, key_setup.MESSAGE_REJECTED):
            self.assertLess(len(message), 1024)


class KeyWindowWrapperTests(unittest.TestCase):
    @unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p) == 8, "64-bit layout check")
    def test_credui_info_layout(self):
        self.assertEqual(ctypes.sizeof(key_prompt.CREDUI_INFOW), 40)
        self.assertEqual(key_prompt.CREDUI_INFOW.hwndParent.offset, 8)
        self.assertLessEqual(len(key_prompt.CAPTION), 128)

    def test_returns_typed_key(self):
        key = run(key_prompt.prompt_for_key("m", timeout=5, blocking=lambda msg, state: "sk-typed", poll=0.01))
        self.assertEqual(key, "sk-typed")

    def test_cancel_returns_none(self):
        self.assertIsNone(run(key_prompt.prompt_for_key("m", timeout=5, blocking=lambda m, s: None, poll=0.01)))

    def slow_window(self, message, state):
        state["owner"] = "fake-window"
        while not state.get("closing"):
            time.sleep(0.01)
        return None

    def test_timeout_closes_the_window(self):
        closed = []

        def closer(state):
            closed.append(state.get("owner"))
            state["closing"] = True

        with self.assertRaises(key_prompt.KeyPromptError) as caught:
            run(key_prompt.prompt_for_key("m", timeout=0.2, blocking=self.slow_window, closer=closer, poll=0.01))
        self.assertEqual(closed, ["fake-window"])
        self.assertIn("Nothing was sent", str(caught.exception))

    def test_stop_closes_the_window(self):
        deadline = time.monotonic() + 0.2

        def closer(state):
            state["closing"] = True

        with self.assertRaises(key_prompt.KeyPromptInterrupted):
            run(key_prompt.prompt_for_key("m", timeout=60, blocking=self.slow_window, closer=closer,
                                          is_interrupted=lambda: time.monotonic() > deadline, poll=0.01))

    def test_window_errors_are_passed_on(self):
        def broken(message, state):
            raise key_prompt.KeyPromptError("Windows could not show the key window (error 5).")

        with self.assertRaises(key_prompt.KeyPromptError):
            run(key_prompt.prompt_for_key("m", timeout=5, blocking=broken, poll=0.01))

    @unittest.skipIf(key_prompt.available(), "only meaningful off Windows")
    def test_not_available_off_windows(self):
        with self.assertRaises(key_prompt.KeyPromptError):
            run(key_prompt.prompt_for_key("m", timeout=5))


if __name__ == "__main__":
    unittest.main()
