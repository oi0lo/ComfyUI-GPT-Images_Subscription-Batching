"""OpenAI API key for Sunburst Batch mode.

Order: OPENAI_API_KEY environment variable first, then (Windows only) this
package's own Windows Credential Manager entry. No fallback from the variable
if it turns out to be wrong. Standard library only (ctypes).
"""

from __future__ import annotations

import os
import sys

ENV_VAR = "OPENAI_API_KEY"
TARGET = "ComfyUI.GPTImages.OPENAI_API_KEY"
MAX_BLOB_BYTES = 2560  # CRED_MAX_CREDENTIAL_BLOB_SIZE
ENV_SOURCE = "OPENAI_API_KEY environment variable"
STORE_SOURCE = "Windows Credential Manager"
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def helper_command(action: str = "set") -> str:
    """The exact command for this install: ComfyUI's own Python and this package's helper."""
    script = os.path.join(PACKAGE_DIR, "setup_credentials.py")
    return f'"{sys.executable}" "{script}" {action}'


def setup_hint() -> str:
    if sys.platform != "win32":
        return (
            "No OpenAI API key found for Sunburst Batch mode. Set the OPENAI_API_KEY environment "
            "variable, then restart ComfyUI."
        )
    return (
        "No OpenAI API key found for Sunburst Batch mode. Store it once by running this in a terminal: "
        f"{helper_command('set')}  Or set the OPENAI_API_KEY environment variable and restart ComfyUI."
    )


class CredentialError(RuntimeError):
    pass


class NoKeyError(CredentialError):
    """Neither the environment variable nor the stored entry exists."""


class StoredKeyInvalid(CredentialError):
    """The Credential Manager entry exists but cannot be used."""


def decode_blob(blob: bytes) -> str:
    """The helper stores UTF-8. An entry added by hand in Windows' own Credential
    Manager window is stored as UTF-16, so accept that too."""
    if len(blob) % 2 == 0 and blob[1::2] and set(blob[1::2]) == {0}:
        return blob.decode("utf-16-le")
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialError(
            "The stored key cannot be read. Store it again with setup_credentials.py set."
        ) from None


def check_syntax(key: str) -> str:
    """Basic local checks only. Does not assume a key prefix or length."""
    if not key:
        raise CredentialError("The API key is empty.")
    if len(key.encode("utf-8")) > MAX_BLOB_BYTES:
        raise CredentialError("The API key is too long to be a valid key.")
    if any(c.isspace() for c in key) or not key.isascii() or not key.isprintable():
        raise CredentialError("The API key contains spaces or unexpected characters. Paste it again.")
    return key


# ---------------------------------------------------------------- Windows store

import ctypes
from ctypes import POINTER, Structure, byref, c_ubyte, c_uint32, c_void_p, c_wchar_p


class _FILETIME(Structure):
    _fields_ = [("dwLowDateTime", c_uint32), ("dwHighDateTime", c_uint32)]


class _CREDENTIALW(Structure):
    """Layout of the Win32 CREDENTIALW structure (wincred.h)."""

    _fields_ = [
        ("Flags", c_uint32),
        ("Type", c_uint32),
        ("TargetName", c_wchar_p),
        ("Comment", c_wchar_p),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", c_uint32),
        ("CredentialBlob", POINTER(c_ubyte)),
        ("Persist", c_uint32),
        ("AttributeCount", c_uint32),
        ("Attributes", c_void_p),
        ("TargetAlias", c_wchar_p),
        ("UserName", c_wchar_p),
    ]


if sys.platform == "win32":
    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168
    _api = None

    def _advapi32():
        global _api
        if _api is None:
            lib = ctypes.WinDLL("advapi32", use_last_error=True)
            lib.CredReadW.argtypes = [c_wchar_p, c_uint32, c_uint32, POINTER(POINTER(_CREDENTIALW))]
            lib.CredReadW.restype = ctypes.c_int
            lib.CredWriteW.argtypes = [POINTER(_CREDENTIALW), c_uint32]
            lib.CredWriteW.restype = ctypes.c_int
            lib.CredDeleteW.argtypes = [c_wchar_p, c_uint32, c_uint32]
            lib.CredDeleteW.restype = ctypes.c_int
            lib.CredFree.argtypes = [c_void_p]
            lib.CredFree.restype = None
            _api = lib
        return _api

    def _store_read() -> str | None:
        api = _advapi32()
        pointer = POINTER(_CREDENTIALW)()
        if not api.CredReadW(TARGET, _CRED_TYPE_GENERIC, 0, byref(pointer)):
            error = ctypes.get_last_error()
            if error == _ERROR_NOT_FOUND:
                return None
            raise CredentialError(f"Could not read Windows Credential Manager (error {error}).")
        try:
            credential = pointer.contents
            size = credential.CredentialBlobSize
            blob = ctypes.string_at(credential.CredentialBlob, size) if size else b""
        finally:
            api.CredFree(pointer)
        return decode_blob(blob)

    def _store_write(key: str) -> None:
        blob = key.encode("utf-8")
        if len(blob) > MAX_BLOB_BYTES:
            raise CredentialError("The API key is too long for Windows Credential Manager.")
        buffer = (c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = _CREDENTIALW()
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = TARGET
        credential.Comment = "OpenAI API key used by the ComfyUI GPT Images node"
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, POINTER(c_ubyte))
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = ENV_VAR
        if not _advapi32().CredWriteW(byref(credential), 0):
            raise CredentialError(f"Could not write to Windows Credential Manager (error {ctypes.get_last_error()}).")

    def _store_delete() -> bool:
        if _advapi32().CredDeleteW(TARGET, _CRED_TYPE_GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return False
        raise CredentialError(f"Could not delete the Windows Credential Manager entry (error {error}).")

else:

    def _store_read() -> str | None:
        return None

    def _store_write(key: str) -> None:
        raise CredentialError("Windows Credential Manager is only available on Windows. Use OPENAI_API_KEY.")

    def _store_delete() -> bool:
        return False


def store_available() -> bool:
    return sys.platform == "win32"


def read_store() -> str | None:
    return _store_read()


def write_store(key: str) -> None:
    _store_write(check_syntax(key))


def delete_store() -> bool:
    return _store_delete()


def resolve_api_key(environ=None, store_reader=None) -> tuple[str, str]:
    """Return (key, source).

    Raises NoKeyError if there is no key at all, StoredKeyInvalid if the stored entry
    is unusable, and CredentialError if the environment variable is malformed (that
    one is the user's to fix; the node never falls back from it).
    """
    environ = os.environ if environ is None else environ
    value = environ.get(ENV_VAR, "").strip()
    if value:
        try:
            return check_syntax(value), ENV_SOURCE
        except CredentialError as exc:
            raise CredentialError(
                f"The OPENAI_API_KEY environment variable is not a usable key: {exc} "
                "Fix or remove the variable, then restart ComfyUI."
            ) from None
    reader = _store_read if store_reader is None else store_reader
    try:
        stored = reader()
    except CredentialError as exc:
        raise StoredKeyInvalid(str(exc)) from None
    if stored:
        try:
            return check_syntax(stored.strip()), STORE_SOURCE
        except CredentialError as exc:
            raise StoredKeyInvalid(f"The stored key is not usable: {exc}") from None
    raise NoKeyError(setup_hint())
