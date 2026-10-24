import http.client
import http.server
import json
import os
import ssl
import threading
import unittest

import helpers  # noqa: F401
import batch_http
import batch_request
from references import Encoded

KEY = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789"


class Recorder(http.server.BaseHTTPRequestHandler):
    calls = []
    replies = []

    def do_GET(self):
        Recorder.calls.append((self.path, dict(self.headers), b""))
        self._reply()

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        Recorder.calls.append((self.path, dict(self.headers), body))
        self._reply()

    def _reply(self):
        status, payload = Recorder.replies.pop(0)
        raw = isinstance(payload, bytes)  # plain-text body, as a proxy in front of OpenAI sends
        data = payload if raw else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain" if raw else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class LocalServer:
    def __enter__(self):
        Recorder.calls = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.connect = lambda: http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def sample_request():
    images = [Encoded("image/png", b"\x89PNG" + bytes(range(256)) * 40, 8, 8)]
    return batch_request.build("forest", 3840, 2160, "max", images)


class SubmitTests(unittest.TestCase):
    def test_upload_and_create(self):
        request = sample_request()
        Recorder.replies = [(200, {"id": "file-abc"}), (200, {"id": "batch_123", "status": "validating"})]
        with LocalServer() as server:
            batch_id = batch_http.submit(request, KEY, connect=server.connect)
        self.assertEqual(batch_id, "batch_123")
        (path1, headers1, body1), (path2, headers2, body2) = Recorder.calls
        self.assertEqual(path1, "/v1/files")
        self.assertEqual(headers1["Authorization"], f"Bearer {KEY}")
        boundary = headers1["Content-Type"].split("boundary=", 1)[1]
        parts = body1.split(f"--{boundary}".encode())
        self.assertIn(b'name="purpose"\r\n\r\nbatch\r\n', parts[1])
        file_part = parts[2]
        header_end = file_part.index(b"\r\n\r\n") + 4
        self.assertIn(f'filename="{request.custom_id}.jsonl"'.encode(), file_part[:header_end])
        self.assertEqual(file_part[header_end:-2], request.to_bytes())  # exact JSONL bytes
        self.assertEqual(parts[3], b"--\r\n")
        self.assertEqual(path2, "/v1/batches")
        self.assertEqual(
            json.loads(body2),
            {"input_file_id": "file-abc", "endpoint": "/v1/responses", "completion_window": "24h"},
        )

    def test_auth_error_is_clear_and_redacted(self):
        Recorder.replies = [(401, {"error": {"message": f"Incorrect API key provided: {KEY}"}})]
        with LocalServer() as server:
            with self.assertRaises(batch_http.BatchError) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        message = str(caught.exception)
        self.assertIn("401", message)
        self.assertNotIn(KEY, message)
        self.assertEqual(len(Recorder.calls), 1)  # no batch creation, no retry

    def test_create_error_is_not_retried(self):
        Recorder.replies = [(200, {"id": "file-abc"}), (500, {"error": {"message": "boom"}})]
        with LocalServer() as server:
            with self.assertRaises(batch_http.BatchError) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertEqual(len(Recorder.calls), 2)
        self.assertTrue(caught.exception.ambiguous)

    def test_upload_503_quotes_openai_and_says_no_batch(self):
        # The exact answer from the user's failed run on 2026-10-24.
        envoy = b"upstream connect error or disconnect/reset before headers. reset reason: connection termination"
        Recorder.replies = [(503, envoy)]
        with LocalServer() as server:
            with self.assertRaises(batch_http.BatchError) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertEqual(
            str(caught.exception),
            "OpenAI returned an error while the file was uploading (HTTP 503): upstream connect error or "
            "disconnect/reset before headers. reset reason: connection termination. No batch was created, "
            "so queueing this run again cannot make a duplicate.",
        )
        self.assertFalse(caught.exception.ambiguous)
        self.assertEqual(len(Recorder.calls), 1)  # no retry, no batch creation

    def test_upload_json_error_quotes_openai_message(self):
        Recorder.replies = [(429, {"error": {"message": "You exceeded your current quota.", "code": "insufficient_quota"}})]
        with LocalServer() as server:
            with self.assertRaises(batch_http.BatchError) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertEqual(
            str(caught.exception),
            "OpenAI returned an error while the file was uploading (HTTP 429): You exceeded your current quota. "
            "No batch was created, so queueing this run again cannot make a duplicate.",
        )

    def test_create_server_error_may_have_created_a_batch(self):
        Recorder.replies = [(200, {"id": "file-abc"}), (503, b"upstream connect error or disconnect/reset before headers.")]
        with LocalServer() as server:
            with self.assertRaises(batch_http.BatchError) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertEqual(
            str(caught.exception),
            "OpenAI returned a server error while creating the batch (HTTP 503): upstream connect error or "
            "disconnect/reset before headers. A batch may or may not have been created. Check "
            "https://platform.openai.com/batches before queueing again.",
        )
        self.assertTrue(caught.exception.ambiguous)

    def test_create_refused_means_no_batch(self):
        Recorder.replies = [(200, {"id": "file-abc"}), (400, {"error": {"message": "Invalid input file"}})]
        with LocalServer() as server:
            with self.assertRaises(batch_http.BatchError) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertFalse(caught.exception.ambiguous)
        self.assertEqual(
            str(caught.exception),
            "OpenAI returned an error while creating the batch (HTTP 400): Invalid input file. "
            "No batch was created, so queueing this run again cannot make a duplicate.",
        )

    def test_upload_401_is_key_rejected(self):
        Recorder.replies = [(401, {"error": {"message": "Incorrect API key provided"}})]
        with LocalServer() as server:
            with self.assertRaises(batch_http.KeyRejected) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertFalse(caught.exception.ambiguous)
        self.assertEqual(len(Recorder.calls), 1)

    def test_create_401_is_not_treated_as_a_fresh_key_problem(self):
        Recorder.replies = [(200, {"id": "file-abc"}), (401, {"error": {"message": "nope"}})]
        with LocalServer() as server:
            with self.assertRaises(batch_http.BatchError) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertNotIsInstance(caught.exception, batch_http.KeyRejected)

    def test_redirect_is_not_followed(self):
        Recorder.replies = [(307, {"location": "elsewhere"})]
        with LocalServer() as server:
            with self.assertRaises(batch_http.BatchError) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertIn("redirect", str(caught.exception))
        self.assertEqual(len(Recorder.calls), 1)


class CheckKeyTests(unittest.TestCase):
    def check(self, status, payload):
        Recorder.replies = [(status, payload)]
        with LocalServer() as server:
            batch_http.check_key(KEY, connect=server.connect)

    def test_accepted_key(self):
        self.check(200, {"object": "list", "data": []})
        (path, headers, _), = Recorder.calls
        self.assertEqual(path, "/v1/batches?limit=1")
        self.assertEqual(headers["Authorization"], f"Bearer {KEY}")

    def test_rejected_key(self):
        with self.assertRaises(batch_http.KeyRejected):
            self.check(401, {"error": {"message": "Incorrect API key"}})

    def test_key_without_batch_permission(self):
        with self.assertRaises(batch_http.KeyNotAllowed) as caught:
            self.check(403, {"error": {"message": "insufficient permissions"}})
        self.assertIn("Batches and Files", str(caught.exception))

    def test_server_error_is_neither(self):
        with self.assertRaises(batch_http.BatchError) as caught:
            self.check(500, {"error": {"message": "boom"}})
        self.assertNotIsInstance(caught.exception, (batch_http.KeyRejected, batch_http.KeyNotAllowed))
        self.assertIn("not saved", str(caught.exception))

    def test_no_network(self):
        with self.assertRaises(batch_http.BatchError) as caught:
            batch_http.check_key(KEY, connect=lambda: FakeConnection("connect"))
        self.assertIn("not saved", str(caught.exception))


class FakeConnection:
    """Fails at a chosen step, to check how ambiguity is reported."""

    def __init__(self, fail_on):
        self.fail_on = fail_on

    def putrequest(self, *a, **k):
        if self.fail_on == "connect":
            raise ConnectionRefusedError("refused")

    def putheader(self, *a):
        pass

    def endheaders(self):
        pass

    def send(self, data):
        pass

    def getresponse(self):
        raise TimeoutError("timed out")

    def close(self):
        pass


class AmbiguityTests(unittest.TestCase):
    def test_upload_failure_is_safe(self):
        with self.assertRaises(batch_http.BatchError) as caught:
            batch_http.upload_jsonl(sample_request(), KEY, connect=lambda: FakeConnection("response"))
        self.assertFalse(caught.exception.ambiguous)
        self.assertEqual(
            str(caught.exception),
            "The connection to OpenAI failed while the file was uploading: timed out. "
            "No batch was created, so queueing this run again cannot make a duplicate.",
        )

    def test_create_timeout_is_ambiguous(self):
        with self.assertRaises(batch_http.BatchError) as caught:
            batch_http.create_batch("file-1", KEY, connect=lambda: FakeConnection("response"))
        self.assertTrue(caught.exception.ambiguous)
        self.assertIn("may or may not", str(caught.exception))

    def test_create_not_sent_is_safe(self):
        with self.assertRaises(batch_http.BatchError) as caught:
            batch_http.create_batch("file-1", KEY, connect=lambda: FakeConnection("connect"))
        self.assertFalse(caught.exception.ambiguous)

    def test_dashboard_url(self):
        self.assertEqual(batch_http.dashboard_url("batch_1"), "https://platform.openai.com/batches/batch_1")


class ConnectionTests(unittest.TestCase):
    PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")

    def setUp(self):
        self.saved = {name: os.environ.pop(name, None) for name in self.PROXY_VARS}

    def tearDown(self):
        for name, value in self.saved.items():
            if value is not None:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)

    def test_default_connection_is_verified_tls_to_openai(self):
        connection = batch_http.default_connection()
        self.assertEqual((connection.host, connection.port), ("api.openai.com", 443))
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)

    def test_proxy_tunnels_to_openai(self):
        os.environ["HTTPS_PROXY"] = "http://proxy.local:3128"
        connection = batch_http.default_connection()
        self.assertEqual((connection.host, connection.port), ("proxy.local", 3128))
        self.assertEqual((connection._tunnel_host, connection._tunnel_port), ("api.openai.com", 443))
        self.assertTrue(connection._context.check_hostname)

    def test_no_proxy_respected(self):
        os.environ["HTTPS_PROXY"] = "http://proxy.local:3128"
        os.environ["NO_PROXY"] = ".openai.com"
        self.assertEqual(batch_http.default_connection().host, "api.openai.com")


if __name__ == "__main__":
    unittest.main()
