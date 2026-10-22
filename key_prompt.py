"""Windows' own credential window, used to paste the OpenAI API key (Windows only).

Calls CredUIPromptForCredentialsW from credui.dll through ctypes: a masked field,
no console, no frontend code. The window is owned by a hidden top-most helper
window so it opens in front of ComfyUI. The key travels from the window to the
caller in memory only; this module never writes it anywhere.

Flags and limits follow Microsoft's documentation of CredUIPromptForCredentialsW:
CREDUI_FLAGS_DO_NOT_PERSIST is required to get the typed text back as plain text.
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import threading
import time
from ctypes import POINTER, Structure, byref, c_int, c_uint32, c_void_p, c_wchar_p, sizeof

CAPTION = "ComfyUI GPT Images: OpenAI API key"  # at most 128 characters
TARGET = "ComfyUI GPT Images"
USER_LABEL = "OpenAI API key"
MAX_KEY_CHARS = 1280  # CREDUI_MAX_PASSWORD_LENGTH (CRED_MAX_CREDENTIAL_BLOB_SIZE / 2)
USER_BUFFER = 514  # CREDUI_MAX_USERNAME_LENGTH (513) + 1

_GENERIC_CREDENTIALS = 0x00040000
_ALWAYS_SHOW_UI = 0x00000080
_DO_NOT_PERSIST = 0x00000002
_KEEP_USERNAME = 0x00100000
_EXCLUDE_CERTIFICATES = 0x00000008
FLAGS = _GENERIC_CREDENTIALS | _ALWAYS_SHOW_UI | _DO_NOT_PERSIST | _KEEP_USERNAME | _EXCLUDE_CERTIFICATES
FLAGS_SIMPLE = _GENERIC_CREDENTIALS | _ALWAYS_SHOW_UI | _DO_NOT_PERSIST

_NO_ERROR = 0
_ERROR_INVALID_FLAGS = 1004
_ERROR_CANCELLED = 1223
_WM_CLOSE = 0x0010
_WS_POPUP = 0x80000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_CLOSE_GRACE_SECONDS = 10


class CREDUI_INFOW(Structure):
    _fields_ = [
        ("cbSize", c_uint32),
        ("hwndParent", c_void_p),
        ("pszMessageText", c_wchar_p),
        ("pszCaptionText", c_wchar_p),
        ("hbmBanner", c_void_p),
    ]


class KeyPromptError(RuntimeError):
    pass


class KeyPromptInterrupted(Exception):
    pass


def available() -> bool:
    return sys.platform == "win32"


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.argtypes = [c_uint32, c_wchar_p, c_wchar_p, c_uint32, c_int, c_int, c_int, c_int,
                                       c_void_p, c_void_p, c_void_p, c_void_p]
    user32.CreateWindowExW.restype = c_void_p
    user32.DestroyWindow.argtypes = [c_void_p]
    user32.DestroyWindow.restype = c_int
    user32.IsWindow.argtypes = [c_void_p]
    user32.IsWindow.restype = c_int
    user32.SetForegroundWindow.argtypes = [c_void_p]
    user32.SetForegroundWindow.restype = c_int
    user32.GetSystemMetrics.argtypes = [c_int]
    user32.GetSystemMetrics.restype = c_int
    user32.PostMessageW.argtypes = [c_void_p, c_uint32, c_void_p, c_void_p]
    user32.PostMessageW.restype = c_int
    return user32


def _prompt_blocking(message: str, state: dict) -> str | None:
    """Show the window and wait. Returns the typed key, or None if cancelled or closed."""
    credui = ctypes.WinDLL("credui", use_last_error=True)
    prompt = credui.CredUIPromptForCredentialsW
    prompt.argtypes = [POINTER(CREDUI_INFOW), c_wchar_p, c_void_p, c_uint32, c_wchar_p, c_uint32,
                       c_wchar_p, c_uint32, POINTER(c_int), c_uint32]
    prompt.restype = c_uint32
    user32 = _user32()

    # Hidden top-most owner at the screen centre: the window opens in front of ComfyUI.
    x, y = user32.GetSystemMetrics(0) // 2, user32.GetSystemMetrics(1) // 2
    owner = user32.CreateWindowExW(_WS_EX_TOPMOST | _WS_EX_TOOLWINDOW, "STATIC", CAPTION, _WS_POPUP,
                                   x, y, 0, 0, None, None, None, None)
    state["owner"] = owner
    if owner:
        user32.SetForegroundWindow(owner)
    info = CREDUI_INFOW(sizeof(CREDUI_INFOW), owner, message, CAPTION, None)
    user = ctypes.create_unicode_buffer(USER_LABEL, USER_BUFFER)
    password = ctypes.create_unicode_buffer(MAX_KEY_CHARS + 1)
    save = c_int(0)
    try:
        code = prompt(byref(info), TARGET, None, 0, user, USER_BUFFER, password, MAX_KEY_CHARS + 1, byref(save), FLAGS)
        if code == _ERROR_INVALID_FLAGS:  # fall back to the minimum documented flag set
            code = prompt(byref(info), TARGET, None, 0, user, USER_BUFFER, password, MAX_KEY_CHARS + 1,
                          byref(save), FLAGS_SIMPLE)
        if code == _NO_ERROR and not state.get("closing"):
            return password.value
        if code in (_NO_ERROR, _ERROR_CANCELLED) or state.get("closing"):
            return None
        raise KeyPromptError(f"Windows could not show the key window (error {code}).")
    finally:
        ctypes.memset(password, 0, sizeof(password))
        state["owner"] = None
        if owner and user32.IsWindow(owner):
            user32.DestroyWindow(owner)


def _close(state: dict) -> None:
    state["closing"] = True
    owner = state.get("owner")
    if owner and available():
        _user32().PostMessageW(owner, _WM_CLOSE, None, None)  # closing the owner closes the key window


async def prompt_for_key(message: str, *, timeout: float, is_interrupted=lambda: False,
                         poll: float = 0.2, blocking=None, closer=None) -> str | None:
    """Show the key window without blocking ComfyUI's loop.

    Returns the key, or None if the user pressed Cancel. Raises KeyPromptInterrupted
    when ComfyUI's Stop is pressed, KeyPromptError on timeout or a Windows failure.
    `blocking` and `closer` exist for tests.
    """
    if blocking is None and not available():
        raise KeyPromptError("The key window is only available on Windows.")
    blocking = blocking or _prompt_blocking
    closer = closer or _close
    state: dict = {}
    result: dict = {}

    def run():
        try:
            result["key"] = blocking(message, state)
        except BaseException as exc:  # handed back to the async side
            result["error"] = exc

    thread = threading.Thread(target=run, name="gpt-images-key-window", daemon=True)
    thread.start()
    started = time.monotonic()
    reason = None
    closed_at = None
    while thread.is_alive():
        if reason is None:
            if is_interrupted():
                reason = "interrupted"
            elif time.monotonic() - started > timeout:
                reason = "timeout"
            if reason:
                closer(state)
                closed_at = time.monotonic()
        elif time.monotonic() - closed_at > _CLOSE_GRACE_SECONDS:
            break  # the window did not close; stop waiting for it
        await asyncio.sleep(poll)
    if reason == "interrupted":
        raise KeyPromptInterrupted()
    if reason == "timeout":
        raise KeyPromptError(
            f"No key was entered within {max(1, int(timeout // 60))} minutes, so the key window was closed. "
            "Nothing was sent to OpenAI."
        )
    if "error" in result:
        raise result["error"]
    return result.get("key")
