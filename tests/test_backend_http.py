"""Real loopback HTTP responses exercise the shared backend transport."""

import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from llm_service_manager.execution import Operations


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(409 if self.path == "/busy" else 200)
        self.end_headers()
        self.wfile.write(
            {"/invalid": b"not json", "/array": b"[]"}.get(
                self.path, b'{"status":"ok"}'
            )
        )

    def log_message(self, *_):
        pass


class BackendHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ops = Operations(self.directory.name, quiet=True)

    def events(self):
        return [
            json.loads(line)
            for line in (Path(self.directory.name) / "audit.jsonl")
            .read_text()
            .splitlines()
        ]

    def test_backend_does_not_use_environment_proxy(self):
        with patch.dict(
            os.environ,
            {
                "http_proxy": "http://127.0.0.1:1",
                "HTTP_PROXY": "http://127.0.0.1:1",
                "no_proxy": "",
                "NO_PROXY": "",
            },
        ):
            self.assertEqual(self.ops.http(self.url, timeout=2), {"status": "ok"})
        self.assertEqual([r["kind"] for r in self.events()], ["http", "http-result"])

    def test_error_status_is_preserved_closed_and_audited(self):
        with self.assertRaises(HTTPError) as result:
            self.ops.http(self.url + "/busy")
        self.assertEqual(result.exception.code, 409)
        self.assertTrue(result.exception.closed)
        self.assertEqual(self.events()[-1]["kind"], "http-failed")
        self.assertIn("409", self.events()[-1]["error"])

    def test_invalid_json_and_nonobject_responses_are_audited(self):
        for endpoint in ("/invalid", "/array"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.ops.http(self.url + endpoint)
            self.assertEqual(self.events()[-1]["kind"], "http-failed")
            self.assertEqual(self.events()[-1]["url"], self.url + endpoint)

    def test_failure_audit_error_does_not_hide_original_http_error(self):
        original = self.ops.event

        def event(kind, **data):
            if kind == "http-failed":
                raise OSError("disk full")
            original(kind, **data)

        with (
            patch.object(self.ops, "event", side_effect=event),
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(HTTPError) as result:
                self.ops.http(self.url + "/busy")
        self.assertEqual(result.exception.code, 409)
