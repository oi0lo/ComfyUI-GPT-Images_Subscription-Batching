"""Get a working OpenAI key from the user: ask, check it with OpenAI, save it, return it.

No imports from the rest of the package: nodes.py passes in the window, the check,
the store and the syntax check, so this logic can be tested anywhere.
"""

from __future__ import annotations

KEY_FACTS = (
    "It is checked once with OpenAI (free), then stored by Windows Credential Manager for your "
    "Windows user. It is never saved in the node, the workflow, or ComfyUI's files and logs."
)
MESSAGE_FIRST = (
    "Sunburst Batch mode needs your OpenAI API key. Paste it into the Password field and press OK. "
    + KEY_FACTS
)
MESSAGE_REJECTED = (
    "OpenAI rejected the saved API key (it may have expired or been revoked). Paste a new key into the "
    "Password field and press OK. " + KEY_FACTS
)
MESSAGE_WRONG = "OpenAI rejected that key. Check that you copied all of it, then paste it again."


class KeySetupCancelled(RuntimeError):
    pass


async def obtain_key(prompt, validate, save, check_syntax, *, rejected: bool) -> str:
    """Loop until the user enters a key OpenAI accepts, or cancels.

    prompt(message) -> key or None (async); validate(key) raises an exception with
    .status 401 or 403 for a refused key, anything else for other failures (async);
    save(key) stores it; check_syntax(key) raises for an obviously malformed key.
    Nothing is saved unless OpenAI accepted the key.
    """
    message = MESSAGE_REJECTED if rejected else MESSAGE_FIRST
    while True:
        key = await prompt(message)
        if key is None:
            raise KeySetupCancelled("No API key was entered, so nothing was sent to OpenAI.")
        key = key.strip()
        try:
            check_syntax(key)
        except Exception as exc:
            message = f"That does not look like a key: {exc}"
            continue
        try:
            await validate(key)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status == 401:
                message = MESSAGE_WRONG
                continue
            if status == 403:
                message = f"{exc} Paste another key, or press Cancel."
                continue
            raise  # no network or another problem: nothing is saved
        save(key)
        return key
