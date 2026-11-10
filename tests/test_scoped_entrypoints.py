"""Exercise real document reads through entrypoints, with native operations stubbed."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_backend_settings import config

from llm_service_manager import __main__ as cli
from llm_service_manager import server_setup, tui
from llm_service_manager.config import Error, value
from llm_service_manager.config_document import encode
from llm_service_manager.paths import BACKENDS
from llm_service_manager.storage import read_snapshot


class ScopedEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.path.write_text(json.dumps(encode(config())))
        self.before = self.path.read_bytes()

    def invoke(self, arguments, *, confirm="yes"):
        output = io.StringIO()
        with (
            patch("sys.argv", ["llmsm", *arguments]),
            patch.object(cli.os, "geteuid", return_value=0),
            patch.object(cli, "load_language"),
            patch.object(
                cli, "read_snapshot", side_effect=lambda: read_snapshot(self.path)
            ),
            patch.object(cli, "discover", side_effect=lambda c: c),
            patch.object(cli, "preflight", return_value=([], [])),
            patch.object(cli, "switch") as switch,
            patch("builtins.input", return_value=confirm),
            contextlib.redirect_stdout(output),
        ):
            cli.main()
        self.assertEqual(self.path.read_bytes(), self.before)
        return output.getvalue(), switch

    def test_preview_reads_requested_backend_not_saved_target(self):
        for backend, context in (
            ("ollama", 4096),
            ("llama.cpp", 8192),
            ("strata", 262144),
        ):
            with self.subTest(backend=backend):
                text, switch = self.invoke(["preview", backend])
                result = json.loads(text)
                self.assertEqual(result["model"]["backend"], backend)
                self.assertEqual(result["settings"]["context"]["value"], context)
                switch.assert_not_called()

    def test_cli_edits_only_requested_scope_and_shared_endpoint(self):
        original = json.loads(self.before)
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                _, switch = self.invoke(
                    [
                        "switch",
                        backend,
                        "--set",
                        "context=32k",
                        "--set",
                        "port=55557",
                        "--set",
                        "autostart=false",
                        "--yes",
                    ]
                )
                switch.assert_called_once()
                candidate, selected, revision = switch.call_args.args
                self.assertEqual(selected, backend)
                self.assertEqual(revision, read_snapshot(self.path)[1])
                document = encode(candidate)
                self.assertEqual(
                    document[backend]["settings"]["context"]["value"], 32768
                )
                self.assertEqual(document["global"]["settings"]["port"]["value"], 55557)
                self.assertFalse(document["global"]["settings"]["autostart"]["value"])
                for other in BACKENDS:
                    if other != backend:
                        self.assertEqual(document[other], original[other])

    def test_cli_cancel_and_inapplicable_setting_do_not_apply(self):
        _, switch = self.invoke(
            ["switch", "llama.cpp", "--set", "context=32k"], confirm="no"
        )
        switch.assert_not_called()
        with self.assertRaisesRegex(Error, "不适用于当前底座"):
            self.invoke(["switch", "strata", "--set", "fit=true", "--yes"])
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_menu_selection_loads_backend_scope_and_cancel_does_not_leak(self):
        original, revision = read_snapshot(self.path)
        ui = Mock()
        ui.choose.side_effect = ["llama.cpp", "l", "context", "back"]
        ui.input.return_value = "32k"
        with (
            patch.object(tui, "available_backends", return_value=["llama.cpp"]),
            patch.object(tui, "model_present", return_value=True),
            patch.object(tui, "switch") as switch,
            self.assertRaises(tui.Cancelled),
        ):
            tui.selection_flow(ui, original, revision)
        ui.input.assert_called_once_with(tui.label("context"), 8192)
        switch.assert_not_called()
        self.assertEqual(encode(original), json.loads(self.before))
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_lan_setup_keeps_selected_backend_parameters(self):
        for backend, ident, context in (
            ("ollama", "o", 4096),
            ("llama.cpp", "l", 8192),
            ("strata", "s", 262144),
        ):
            with self.subTest(backend=backend):
                document = json.loads(self.before)
                document["target"] = {"backend": backend, "model": ident}
                self.path.write_text(json.dumps(document))
                with (
                    patch.object(
                        server_setup,
                        "read_snapshot",
                        side_effect=lambda: read_snapshot(self.path),
                    ),
                    patch.object(
                        server_setup, "endpoint", return_value=("192.168.1.2", 55555)
                    ),
                    patch.object(server_setup, "reachable", side_effect=[False, True]),
                    patch.object(server_setup, "switch") as switch,
                    patch.object(server_setup, "configure") as firewall,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    server_setup.complete()
                candidate, selected, _ = switch.call_args.args
                self.assertEqual(selected, backend)
                self.assertEqual(value(candidate, "context"), context)
                self.assertEqual(value(candidate, "host"), "0.0.0.0")
                for other in BACKENDS:
                    self.assertEqual(encode(candidate)[other], document[other])
                firewall.assert_not_called()
                self.assertEqual(json.loads(self.path.read_text()), document)
