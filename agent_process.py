"""Run one agent CLI (Codex, Grok Build, Antigravity) as a child process.

Shared by the three subscription runners: no console window on Windows, output
read on background threads while the ComfyUI run stays responsive, Stop in
ComfyUI and a time limit both end the whole process tree. Nothing is retried.
Standard library only.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PIPE_GRACE_SECONDS = 5.0  # after the program ends, time to read what it wrote


class ProcessInterrupted(Exception):
    """Stop was pressed in ComfyUI; the process tree was ended."""


class ProcessTimedOut(Exception):
    """The time limit passed; the process tree was ended."""


def kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True, timeout=15, creationflags=NO_WINDOW,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except Exception:
        pass
    if process.poll() is None:
        process.kill()
        process.wait(timeout=10)


def _drain(stream, handler) -> None:
    try:
        handler(stream)
    except Exception:
        # A reader must never die silently while the child still writes: keep emptying the pipe.
        try:
            for _ in stream:
                pass
        except Exception:
            pass


async def run(
    command: list[str],
    *,
    cwd: str,
    env: dict,
    stdin_data: bytes | None,
    on_stdout,
    on_stderr,
    timeout: float,
    is_interrupted=lambda: False,
    poll_interval: float = 0.25,
) -> int:
    """Start `command`, feed `stdin_data` (or nothing), hand stdout/stderr to the
    two reader callbacks (each gets the binary stream) and wait.

    Returns the exit code. Raises OSError if the program cannot start,
    ProcessInterrupted on Stop and ProcessTimedOut after `timeout` seconds.
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        creationflags=NO_WINDOW,
        start_new_session=(sys.platform != "win32"),
    )

    def write_stdin():
        try:
            process.stdin.write(stdin_data)
            process.stdin.close()
        except OSError:
            pass

    # (thread, the pipe it uses)
    workers = [
        (threading.Thread(target=_drain, args=(process.stdout, on_stdout), daemon=True), process.stdout),
        (threading.Thread(target=_drain, args=(process.stderr, on_stderr), daemon=True), process.stderr),
    ]
    if stdin_data is not None:
        workers.insert(0, (threading.Thread(target=write_stdin, daemon=True), process.stdin))
    for thread, _ in workers:
        thread.start()

    clock = time.monotonic()
    try:
        while process.poll() is None:
            if is_interrupted():
                kill_tree(process)
                raise ProcessInterrupted()
            if time.monotonic() - clock > timeout:
                kill_tree(process)
                raise ProcessTimedOut()
            await asyncio.sleep(poll_interval)
    finally:
        deadline = time.monotonic() + PIPE_GRACE_SECONDS
        for thread, _ in workers:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        for thread, pipe in workers:
            # A pipe still in use belongs to something the program left running (it keeps the
            # pipe open). Closing it would wait for that, so it stays with its daemon thread.
            if pipe is None or thread.is_alive():
                continue
            try:
                pipe.close()
            except OSError:
                pass
    return process.returncode
