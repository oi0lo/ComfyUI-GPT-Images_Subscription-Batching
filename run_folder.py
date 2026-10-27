"""A new private folder for each subscription run.

Codex, Grok Build and Antigravity treat their working folder as a workspace they may
look into. So a run never works in Windows' own folder (an administrator PowerShell
starts in C:\\Windows\\System32, which is where Antigravity asked for access in the
user's test) or in ComfyUI's folder. Each run gets a new, empty folder in the user's
temporary folder; the node writes its reference copies there and deletes the folder
when the run ends.
"""

from __future__ import annotations

import os
import sys
import tempfile


class RunFolderError(RuntimeError):
    pass


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def inside(path: str, root: str) -> bool:
    path, root = _norm(path), _norm(root)
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def windows_folder() -> str | None:
    if sys.platform != "win32":
        return None
    return os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"


def create(prefix: str, avoid: list[str] | tuple = ()) -> str:
    """Make the folder; `avoid` are further folders it must not be inside (ComfyUI's)."""
    forbidden = [root for root in [windows_folder(), *avoid] if root]
    bases = [tempfile.gettempdir()]
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        bases.append(os.path.join(os.environ["LOCALAPPDATA"], "Temp"))
    for base in bases:
        if os.path.isdir(base) and not any(inside(base, root) for root in forbidden):
            return tempfile.mkdtemp(prefix=prefix, dir=base)
    raise RunFolderError(
        f"No safe temporary folder: {bases[0]} is inside Windows' folder or ComfyUI's folder, where "
        "this node never runs an agent. Start ComfyUI as your own Windows user (not as a service), "
        "or point the TEMP variable to a folder in your user profile."
    )
