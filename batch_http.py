"""Upload the JSONL file and create one Batch job, using only the standard library.

Rules from the plan: fixed origin (api.openai.com), TLS verification on, no
redirects, finite timeouts, and no automatic retry of either POST.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import uuid
from urllib.parse import urlsplit

API_HOST = "api.openai.com"
API_PORT = 443
DASHBOARD_URL = "https://platform.openai.com/batches/{batch_id}"
BATCHES_PAGE = "https://platform.openai.com/batches"
SOCKET_TIMEOUT = 120  # seconds per network operation
USER_AGENT = "comfyui-gpt-images/1.0"
MAX_RESPONSE_BYTES = 1_000_000

NO_BATCH = "No batch was created, so queueing this run again cannot make a duplicate."
MAYBE_BATCH = f"A batch may or may not have been created. Check {BATCHES_PAGE} before queueing again."


class BatchError(RuntimeError):
    """Submission failed. `ambiguous` means a batch may exist despite the error.

    When OpenAI answered with an HTTP error, `status` is its status code and `detail`
    is OpenAI's own error text, as returned (key redacted).
    """

    def __init__(self, message: str, ambiguous: bool = False, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.ambiguous = ambiguous
        self.status = status
        self.detail = detail


class KeyRejected(BatchError):
    """OpenAI answered 401: the key is wrong, expired or revoked. Nothing was created."""


class KeyNotAllowed(BatchError):
    """OpenAI answered 403 to the key check: the key may not use the Batch API."""


def _proxy_from_env() -> tuple[str, int] | None:
    for name in ("HTTPS_PROXY", "https_proxy"):
        value = os.environ.get(name)
        if value:
            parts = urlsplit(value if "://" in value else "http://" + value)
            if parts.hostname:
                return parts.hostname, parts.port or 8080
    return None


def _no_proxy_matches(host: str) -> bool:
    raw = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    for entry in (e.strip().lower() for e in raw.split(",")):
        if entry and (entry == "*" or host == entry.lstrip(".") or host.endswith("." + entry.lstrip("."))):
            return True
    return False


def default_connection() -> http.client.HTTPSConnection:
    context = ssl.create_default_context()
    proxy = None if _no_proxy_matches(API_HOST) else _proxy_from_env()
    if proxy:
        connection = http.client.HTTPSConnection(proxy[0], proxy[1], timeout=SOCKET_TIMEOUT, context=context)
        connection.set_tunnel(API_HOST, API_PORT)
        return connection
    return http.client.HTTPSConnection(API_HOST, API_PORT, timeout=SOCKET_TIMEOUT, context=context)


def redact(text: str, api_key: str) -> str:
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return re.sub(r"sk-[A-Za-z0-9_\-*]{6,}", "sk-[redacted]", text)


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _exception_text(exc: BaseException) -> str:
    return str(exc).strip() or type(exc).__name__


def _openai_error_detail(body: bytes) -> str:
    """OpenAI's own error text: error.message of a JSON error body, otherwise the body itself."""
    text = body.decode("utf-8", "replace")
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("code")
        if isinstance(message, str) and message.strip():
            return " ".join(message.split())
    elif isinstance(error, str) and error.strip():
        return " ".join(error.split())
    return " ".join(text.split())[:300]


def _describe(exc: BatchError, during: str, server_word: bool = False) -> str:
    """'OpenAI returned an error while <during> (HTTP 503): <OpenAI's text>.'"""
    if exc.status is None:
        return _sentence(str(exc))
    kind = "a server error" if server_word and exc.status >= 500 else "an error"
    return _sentence(f"OpenAI returned {kind} while {during} (HTTP {exc.status})" + (f": {exc.detail}" if exc.detail else ""))


def _read_json(response: http.client.HTTPResponse, api_key: str) -> dict:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    status = response.status
    if 300 <= status < 400:
        raise BatchError(f"OpenAI answered with a redirect (HTTP {status}), which the node does not follow.")
    if status != 200:
        detail = redact(_openai_error_detail(body), api_key)
        raise BatchError(f"HTTP {status}" + (f": {detail}" if detail else ""), status=status, detail=detail)
    if len(body) > MAX_RESPONSE_BYTES:
        raise BatchError("OpenAI response was unexpectedly large.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise BatchError(f"OpenAI returned a response that is not JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise BatchError("OpenAI returned an unexpected response.")
    return payload


def _common_headers(api_key: str) -> list[tuple[str, str]]:
    return [
        ("Authorization", f"Bearer {api_key}"),
        ("User-Agent", USER_AGENT),
        ("Accept", "application/json"),
    ]


def upload_jsonl(request, api_key: str, connect=default_connection) -> str:
    """POST /v1/files (purpose=batch). Streams the JSONL chunks; returns the file id."""
    boundary = "gptimages-" + uuid.uuid4().hex
    filename = f"{request.custom_id}.jsonl"
    preamble = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="purpose"\r\n\r\n'
        "batch\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("ascii")
    epilogue = f"\r\n--{boundary}--\r\n".encode("ascii")
    length = len(preamble) + request.size + len(epilogue)

    connection = connect()
    sent_anything = False
    try:
        try:
            connection.putrequest("POST", "/v1/files", skip_accept_encoding=True)
            for name, value in _common_headers(api_key):
                connection.putheader(name, value)
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(length))
            connection.endheaders()
            sent_anything = True
            connection.send(preamble)
            for chunk in request.iter_chunks():
                connection.send(chunk)
            connection.send(epilogue)
            response = connection.getresponse()
            payload = _read_json(response, api_key)
        except (OSError, http.client.HTTPException) as exc:
            where = "while the file was uploading" if sent_anything else "before anything was sent"
            raise BatchError(
                f"The connection to OpenAI failed {where}: {_sentence(_exception_text(exc))} {NO_BATCH}"
            ) from None
        except BatchError as exc:
            message = f"{_describe(exc, 'the file was uploading')} {NO_BATCH}"
            if exc.status == 401:  # refused before anything was stored: safe to try another key
                raise KeyRejected(message, status=401, detail=exc.detail) from None
            raise BatchError(message, status=exc.status, detail=exc.detail) from None
    finally:
        connection.close()
    file_id = payload.get("id")
    if not isinstance(file_id, str) or not file_id:
        raise BatchError(f"OpenAI accepted the upload but returned no file id. {NO_BATCH}")
    return file_id


def create_batch(file_id: str, api_key: str, connect=default_connection) -> str:
    """POST /v1/batches. Returns the batch id. Never retried."""
    body = json.dumps(
        {"input_file_id": file_id, "endpoint": "/v1/responses", "completion_window": "24h"}
    ).encode("ascii")
    connection = connect()
    sent = False
    try:
        try:
            connection.putrequest("POST", "/v1/batches", skip_accept_encoding=True)
            for name, value in _common_headers(api_key):
                connection.putheader(name, value)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(body)))
            connection.endheaders()
            sent = True
            connection.send(body)
            response = connection.getresponse()
            payload = _read_json(response, api_key)
        except (OSError, http.client.HTTPException) as exc:
            text = _sentence(_exception_text(exc))
            if not sent:
                raise BatchError(
                    f"The connection to OpenAI failed before the batch request was sent: {text} "
                    f"The file {file_id} was uploaded, but no batch was created, so queueing this run "
                    "again cannot make a duplicate."
                ) from None
            raise BatchError(
                f"The connection to OpenAI failed after the batch request was sent: {text} {MAYBE_BATCH}",
                ambiguous=True,
            ) from None
        except BatchError as exc:
            described = _describe(exc, "creating the batch", server_word=True)
            if exc.status is not None and exc.status < 500:
                # OpenAI refused the request (4xx), so no batch exists.
                raise BatchError(f"{described} {NO_BATCH}", status=exc.status, detail=exc.detail) from None
            # A server error, a redirect or an unreadable answer after the request was sent:
            # OpenAI may have created the batch before the answer was lost.
            raise BatchError(f"{described} {MAYBE_BATCH}", ambiguous=True, status=exc.status,
                             detail=exc.detail) from None
    finally:
        connection.close()
    batch_id = payload.get("id")
    if not isinstance(batch_id, str) or not batch_id:
        raise BatchError(f"OpenAI answered without a batch id. {MAYBE_BATCH}", ambiguous=True)
    return batch_id


def check_key(api_key: str, connect=default_connection) -> None:
    """One free request: list at most one batch. Uses the same permission Batch mode needs.

    Returns normally if OpenAI accepts the key. Raises KeyRejected (401), KeyNotAllowed (403)
    or BatchError (anything else, including no network). Never retried.
    """
    connection = connect()
    try:
        try:
            connection.putrequest("GET", "/v1/batches?limit=1", skip_accept_encoding=True)
            for name, value in _common_headers(api_key):
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            _read_json(response, api_key)
        except (OSError, http.client.HTTPException) as exc:
            raise BatchError(
                f"The connection to OpenAI failed while checking the key: {_sentence(_exception_text(exc))} "
                "The key was not saved."
            ) from None
        except BatchError as exc:
            if exc.status == 401:
                raise KeyRejected("OpenAI rejected this key.", status=401, detail=exc.detail) from None
            if exc.status == 403:
                raise KeyNotAllowed(
                    "OpenAI accepted this key but it may not use the Batch API. In your OpenAI key "
                    "settings, allow Batches and Files for it, or use another key.",
                    status=403,
                    detail=exc.detail,
                ) from None
            raise BatchError(f"{_describe(exc, 'checking the key')} The key was not saved.",
                             status=exc.status, detail=exc.detail) from None
    finally:
        connection.close()


def submit(request, api_key: str, connect=default_connection) -> str:
    file_id = upload_jsonl(request, api_key, connect)
    return create_batch(file_id, api_key, connect)


def dashboard_url(batch_id: str) -> str:
    return DASHBOARD_URL.format(batch_id=batch_id)
