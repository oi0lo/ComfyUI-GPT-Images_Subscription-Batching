import asyncio
import os
import signal
import sys
import tempfile
import time
import unittest

import helpers  # noqa: F401
import agent_process
import agy_exec

# Prints its answer, leaves a helper running that keeps stdout and stderr open, and exits.
LEAVES_A_HELPER = """
import json, subprocess, sys
helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(25)"])
open(sys.argv[1], "w").write(str(helper.pid))
print(json.dumps({"conversation_id": "c", "status": "SUCCESS"}))
sys.stdout.flush()
"""


@unittest.skipIf(sys.platform == "win32", "POSIX process handling")
class LeftOverHelperTests(unittest.TestCase):
    def test_returns_promptly_with_the_full_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            pid_file = os.path.join(folder, "helper.pid")
            output = agy_exec.AgyOutput()
            started = time.monotonic()
            code = asyncio.run(agent_process.run(
                [sys.executable, "-c", LEAVES_A_HELPER, pid_file], cwd=folder, env=dict(os.environ),
                stdin_data=None, on_stdout=output.feed_stdout, on_stderr=output.feed_stderr,
                timeout=60, poll_interval=0.05,
            ))
            took = time.monotonic() - started
            try:
                os.kill(int(open(pid_file).read()), signal.SIGTERM)
            except (OSError, ValueError):
                pass
        self.assertEqual(code, 0)
        self.assertLess(took, agent_process.PIPE_GRACE_SECONDS + 4)
        self.assertEqual(output.answer(), {"conversation_id": "c", "status": "SUCCESS"})

    def test_stdin_is_delivered_and_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            seen = []
            code = asyncio.run(agent_process.run(
                [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], cwd=folder,
                env=dict(os.environ), stdin_data=b"instruction", on_stdout=lambda s: seen.append(s.read()),
                on_stderr=lambda s: s.read(), timeout=30, poll_interval=0.05,
            ))
        self.assertEqual((code, seen), (0, [b"INSTRUCTION\n"]))


if __name__ == "__main__":
    unittest.main()
