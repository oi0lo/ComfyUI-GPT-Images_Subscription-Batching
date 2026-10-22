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
SOCKET_TIMEOUT = 120  # seconds per network operation
USER_AGENT = "comfyui-gpt-images/1.0"
MAX_RESPONSE_BYTES = 1_000_000


class BatchError(RuntimeError):
    """Submission failed. `ambiguous` means a batch may exist despite the error."""

    def __init__(self, message: str, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous


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


def _api_error_text(status: int, body: bytes) -> str:
    detail = ""
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            detail = error.get("message") or error.get("code") or ""
        elif isinstance(error, str):
            detail = error
    except ValueError:
        detail = body[:300].decode("utf-8", "replace")
    hints = {
        401: "OpenAI rejected the API key. Check the key (the setup helper's status action shows which key is used).",
        403: "This API key or project is not allowed to do this.",
        429: "Rate limit or quota reached on your OpenAI account.",
    }
    hint = hints.get(status, "OpenAI returned an error." if status < 500 else "OpenAI had a server error.")
    return f"{hint} HTTP {status}" + (f": {detail}" if detail else "")


def _read_json(response: http.client.HTTPResponse, api_key: str) -> dict:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    status = response.status
    if 300 <= status < 400:
        raise BatchError(f"Unexpected redirect from OpenAI (HTTP {status}); not followed.")
    if status != 200:
        raise BatchError(redact(_api_error_text(status, body), api_key))
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
            where = "while uploading" if sent_anything else "before anything was sent"
            raise BatchError(
                f"Network error {where}: {exc}. No batch was created; it is safe to queue again."
            ) from None
    finally:
        connection.close()
    file_id = payload.get("id")
    if not isinstance(file_id, str) or not file_id:
        raise BatchError("OpenAI accepted the upload but returned no file id.")
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
            if not sent:
                raise BatchError(
                    f"Network error before the batch request was sent: {exc}. "
                    f"The file {file_id} was uploaded but no batch was created; it is safe to queue again."
                ) from None
            raise BatchError(
                f"Network error after the batch request was sent: {exc}. A batch may or may not have "
                "been created. Check https://platform.openai.com/batches before queueing again.",
                ambiguous=True,
            ) from None
    finally:
        connection.close()
    batch_id = payload.get("id")
    if not isinstance(batch_id, str) or not batch_id:
        raise BatchError(
            "OpenAI answered without a batch id. Check https://platform.openai.com/batches before queueing again.",
            ambiguous=True,
        )
    return batch_id


def submit(request, api_key: str, connect=default_connection) -> str:
    file_id = upload_jsonl(request, api_key, connect)
    return create_batch(file_id, api_key, connect)


def dashboard_url(batch_id: str) -> str:
    return DASHBOARD_URL.format(batch_id=batch_id)
