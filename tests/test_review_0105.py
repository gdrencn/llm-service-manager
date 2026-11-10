"""Regressions for real input, shared context semantics and recoverable files."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_config import STRATA, FakeOps, fixture
from test_inline import interact

from inline_ui.style import wrap
from llm_service_manager import config, manager, transaction
from llm_service_manager.adapters import native
from llm_service_manager.paths import UNITS


class InputTests(unittest.TestCase):
    def test_input_has_no_selection_background_and_edit_keys_do_not_leak(self):
        script = """from inline_ui import UI
with UI() as ui:
 text=ui.input('EDIT','128k')
 assert text=='256k',repr(text)
print('EDIT_OK')
"""
        code, out = interact(
            [sys.executable, "-c", script],
            [(b"128k", b"\x1b[1~\x1b[3~\x1b[3~\x1b[3~256\x1b[4~\r")],
        )
        self.assertEqual(code, 0, out)
        self.assertIn(b"EDIT_OK", out)
        self.assertNotIn(b"\x1b[1;30;46m", out)
        self.assertIn(b"\x1b[?25h", out)
        self.assertNotIn("▏".encode(), out)
        self.assertIn(b"\x1b[1A\r\x1b[5G\x1b[?25h", out)
        self.assertIn(b"\x1b[?25l\x1b[1B\r", out)

    def test_unknown_escape_sequence_is_consumed_and_unicode_can_be_edited(self):
        script = """from inline_ui import UI
with UI() as ui:
 text=ui.input('UNICODE','中文')
 assert text=='中A文',repr(text)
print('EDIT_OK')
"""
        code, out = interact(
            [sys.executable, "-c", script], [("中文".encode(), b"\x1b[15~\x1b[DA\r")]
        )
        self.assertEqual(code, 0, out)

        self.assertIn(b"\x1b[1A\r\x1b[5G\x1b[?25h", out)

    def test_wrap_cleans_before_measuring_and_slicing(self):
        self.assertEqual(wrap("a\x00bc", 1), ["a", "b", "c"])


class ConfigurationReviewTests(unittest.TestCase):
    def test_parallel_context_is_per_request(self):
        c = fixture()
        c["settings"]["parallel"]["value"] = 2
        c["settings"]["context"]["value"] = 4096
        files = native(c, lambda _: json.dumps(STRATA))
        llama = files["/etc/llama.cpp/llama-local.ini"]
        self.assertIn("ctx-size = 8192", llama)
        self.assertIn("parallel = 2", llama)
        self.assertIn(
            "PARAMETER num_ctx 4096", files["/etc/ollama/ollama-local.Modelfile"]
        )
        strata = json.loads(files["/etc/strata/strata-local.json"])
        self.assertEqual(
            strata["args"][strata["args"].index("--max-context") + 1], "4096"
        )

    def test_invalid_listen_addresses_fail_before_service_changes(self):
        for value in (
            "0.0.0.0:55555",
            ":::",
            "[127.0.0.1]",
            "999.0.0.1",
            "a..b",
            "-bad",
        ):
            with self.subTest(value=value):
                c = fixture()
                c["settings"]["host"]["value"] = value
                with self.assertRaises(config.Error):
                    config.validate(c)
        for value in ("0.0.0.0", "::", "[::1]", "localhost", "my-server.local"):
            c = fixture()
            c["settings"]["host"]["value"] = value
            config.validate(c)
        self.assertEqual(config.parse_setting("context", " 128K "), 131072)

    def test_recovery_restores_removed_file_permissions_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private.conf"
            path.write_text("old")
            path.chmod(0o600)
            before = path.stat()
            contents, attributes = transaction.snapshot({str(path): None})
            path.unlink()
            journal = Path(tmp) / "pending.json"
            journal.write_text(
                json.dumps({"files": contents, "metadata": attributes, "active": []})
            )
            txn, restored = transaction.read_recovery(journal)
            real_replace = os.replace

            def replace(source, destination):
                self.assertEqual(Path(source).stat().st_mode & 0o777, 0o600)
                real_replace(source, destination)

            with patch("llm_service_manager.storage.os.replace", side_effect=replace):
                transaction.restore_file(
                    path, restored[str(path)], txn["metadata"][str(path)]
                )
            self.assertEqual(path.read_text(), "old")
            self.assertEqual(path.stat().st_uid, before.st_uid)
            self.assertEqual(path.stat().st_gid, before.st_gid)

    def test_bad_recovery_attributes_do_not_stop_running_service(self):
        for attrs in (
            {"mode": -1, "uid": 0, "gid": 0},
            {"mode": True, "uid": 0, "gid": 0},
            {"mode": 0o600},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                ops = FakeOps(root)
                (root / "pending.json").write_text(
                    json.dumps(
                        {
                            "active": ["ollama.service"],
                            "files": {str(root / "file"): ""},
                            "metadata": {str(root / "file"): attrs},
                        }
                    )
                )
                with patch.object(ops, "stop") as stop:
                    with self.assertRaises(config.Error):
                        manager.recover(ops)
                stop.assert_not_called()


class StatusReviewTests(unittest.TestCase):
    def test_new_machine_missing_units_are_normal_not_query_errors(self):
        import subprocess
        from unittest.mock import Mock

        from llm_service_manager import status

        ops = Mock()
        ops.run.return_value = subprocess.CompletedProcess(
            [], 1, "LoadState=not-found\nActiveState=inactive\n"
        )
        with patch.object(status, "available_backends", return_value=[]):
            data = status.collect(fixture(), ops)
        self.assertEqual(data["services"], {})
        self.assertEqual(data["errors"], [])
        ops.http.assert_not_called()

    def test_running_service_is_visible_even_when_program_has_been_moved(self):
        import subprocess
        from unittest.mock import Mock

        from llm_service_manager import status

        ops = Mock()

        def run(args, *a, **kw):
            if UNITS["llama.cpp"] in args:
                return subprocess.CompletedProcess(
                    args, 0, "LoadState=loaded\nActiveState=active\n"
                )
            return subprocess.CompletedProcess(args, 1, "LoadState=not-found\n")

        ops.run.side_effect = run
        ops.http.return_value = {
            "data": [{"id": "model", "status": {"value": "loaded"}}]
        }
        with patch.object(status, "available_backends", return_value=[]):
            data = status.collect(fixture(), ops)
        self.assertEqual(data["services"]["llama.cpp"]["ActiveState"], "active")
        self.assertFalse(data["services"]["llama.cpp"]["ProgramAvailable"])
        self.assertEqual(data["runtime"]["backend"], "llama.cpp")


class ErrorBoundaryTests(unittest.TestCase):
    def test_malformed_backend_is_a_configuration_error_not_type_error(self):
        for backend in ([], {}, None, 42):
            c = fixture()
            c["catalog"][0]["backend"] = backend
            with self.assertRaises(config.Error):
                config.validate(config.normalized(c))

    def test_cli_eof_cancels_without_traceback(self):
        import contextlib
        import io

        from llm_service_manager import __main__ as cli

        with (
            patch.object(cli, "main", side_effect=EOFError),
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            with self.assertRaises(SystemExit) as exit:
                cli.entry()
        self.assertEqual(exit.exception.code, 130)
        self.assertNotIn("Traceback", output.getvalue())


class SmallInputTests(unittest.TestCase):
    def test_hidden_input_cannot_be_confirmed(self):
        script = """import os
from inline_ui import UI,Cancelled
os.environ['LINES']='3'
with UI(translate=lambda zh,en:en) as ui:
 try: ui.input('INPUT','128k')
 except Cancelled: pass
 else: raise AssertionError('confirmed invisible input')
print('CANCELLED')
"""
        code, out = interact([sys.executable, "-c", script], [(b"Enlarge", b"\r\x1b")])
        self.assertEqual(code, 0, out)
        self.assertIn(b"CANCELLED", out)
