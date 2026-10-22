"""Store, check or remove the OpenAI API key used by Sunburst Batch mode.

Run it with ComfyUI's own Python, from a terminal (see README):

    python setup_credentials.py set      asks for the key with hidden input
    python setup_credentials.py status   shows which key source the node will use
    python setup_credentials.py delete   removes this package's stored key

The key is never accepted as a command argument and never printed.
"""

from __future__ import annotations

import getpass
import os
import sys
import warnings

import credentials as cred


def _set() -> int:
    if not cred.store_available():
        print("Windows Credential Manager is only available on Windows. Use the OPENAI_API_KEY environment variable.")
        return 2
    if not sys.stdin.isatty():
        print("Run this in a terminal window so the key can be typed with hidden input. Nothing was saved.")
        return 2
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = getpass.getpass("Paste your OpenAI API key (input stays hidden), then press Enter: ")
        except getpass.GetPassWarning:
            print("This window cannot hide typed input. Nothing was saved.")
            return 2
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled. Nothing was saved.")
            return 1
    try:
        cred.write_store(key.strip())
    except cred.CredentialError as exc:
        print(f"Not saved: {exc}")
        return 1
    print(f"Saved in Windows Credential Manager as '{cred.TARGET}'.")
    if os.environ.get(cred.ENV_VAR, "").strip():
        print("Note: OPENAI_API_KEY is also set here. The node uses the environment variable first.")
    return 0


def _status() -> int:
    env_set = bool(os.environ.get(cred.ENV_VAR, "").strip())
    stored = None
    store_note = "not available on this system"
    if cred.store_available():
        try:
            stored = cred.read_store()
            store_note = "present" if stored else "not present"
        except cred.CredentialError as exc:
            store_note = f"could not be read ({exc})"
    print(f"OPENAI_API_KEY environment variable (as seen by this terminal): {'set' if env_set else 'not set'}")
    print(f"Credential Manager entry '{cred.TARGET}': {store_note}")
    if env_set:
        used = "the OPENAI_API_KEY environment variable"
    elif stored:
        used = "the Credential Manager entry"
    else:
        used = "nothing, so Sunburst Batch mode will stop and show setup instructions"
    print(f"Sunburst Batch mode will use: {used}.")
    print("ComfyUI reads environment variables when it starts; restart it after changing them.")
    return 0


def _delete() -> int:
    try:
        removed = cred.delete_store()
    except cred.CredentialError as exc:
        print(f"Not deleted: {exc}")
        return 1
    print("Removed this package's Credential Manager entry." if removed else "There was no entry to remove.")
    return 0


def main(argv: list[str]) -> int:
    actions = {"set": _set, "status": _status, "delete": _delete}
    if len(argv) != 2 or argv[1] not in actions:
        print(__doc__.strip())
        return 2
    return actions[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
