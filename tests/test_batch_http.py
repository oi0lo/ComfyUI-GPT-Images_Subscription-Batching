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

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        Recorder.calls.append((self.path, dict(self.headers), body))
        status, payload = Recorder.replies.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
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
            with self.assertRaises(batch_http.BatchError):
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertEqual(len(Recorder.calls), 2)

    def test_redirect_is_not_followed(self):
        Recorder.replies = [(307, {"location": "elsewhere"})]
        with LocalServer() as server:
            with self.assertRaises(batch_http.BatchError) as caught:
                batch_http.submit(sample_request(), KEY, connect=server.connect)
        self.assertIn("redirect", str(caught.exception))
        self.assertEqual(len(Recorder.calls), 1)


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
        self.assertIn("No batch was created", str(caught.exception))

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
