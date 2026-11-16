"""Self-update transactions use temporary products; never touch real services."""

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from llm_service_manager import tui, updates
from llm_service_manager.config import Error
from llm_service_manager.storage import locked


def bundle(entries=None):
    entries = entries or {
        "product/llm_service_manager/__main__.py": b"",
        "product/llm_service_manager/__init__.py": b'__version__ = "0.10.7"\n',
        "product/inline_ui/__init__.py": b"",
        "product/README.md": b"readme",
    }
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "product"
        self.root.mkdir()
        self.old = self.root / "old"
        self.old.mkdir()
        (self.old / "marker").write_text("old")
        (self.root / "current").symlink_to(self.old)
        self.config = self.base / "config.json"
        self.config.write_text("preserve exactly")
        self.state = self.base / "state"
        self.archive = bundle()
        self.name = "llm-service-manager-0.10.7-test.tar.gz"
        self.release = {
            "tag_name": "stable/0.10.7",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": name,
                    "browser_download_url": f"https://github.com/{updates.REPOSITORY}/releases/download/stable/0.10.7/{name}",
                }
                for name in (self.name, "SHA256SUMS")
            ],
        }
        self.validator = Mock()

    def fetch(self, url, path, limit):
        if url == updates.LATEST:
            data = json.dumps(self.release).encode()
        elif url.endswith("/SHA256SUMS"):
            data = (
                hashlib.sha256(self.archive).hexdigest() + "  " + self.name + "\n"
            ).encode()
        else:
            data = self.archive
        self.assertLessEqual(len(data), limit)
        path.write_bytes(data)

    def install(self, archive, number, **kwargs):
        updates.activate(archive, number, validate=self.validator, **kwargs)

    def run_update(self, **overrides):
        args = dict(
            current="0.10.6",
            state=self.state,
            root=self.root,
            config=self.config,
            fetch=self.fetch,
            install=self.install,
        )
        args.update(overrides)
        with patch("builtins.print"):
            return updates.check_and_update(**args)

    def assert_preserved(self):
        self.assertEqual((self.root / "current").resolve(), self.old)
        self.assertEqual(self.config.read_text(), "preserve exactly")
        self.assertEqual((self.old / "marker").read_text(), "old")

    def test_new_stable_activates_without_touching_config_or_old_code(self):
        self.assertTrue(self.run_update())
        self.assertNotEqual((self.root / "current").resolve(), self.old)
        self.assertEqual(self.config.read_text(), "preserve exactly")
        self.assertTrue((self.old / "marker").is_file())
        self.validator.assert_called_once()
        self.assertEqual((self.root / "current").stat().st_mode & 0o777, 0o755)
        events = [
            json.loads(x) for x in (self.state / "audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-1]["kind"], "tool-update-complete")

    def test_equal_or_newer_local_version_does_not_download_or_downgrade(self):
        for current in ("0.10.7", "0.11.0"):
            fetch = Mock(side_effect=self.fetch)
            self.assertFalse(self.run_update(current=current, fetch=fetch))
            self.assertEqual(fetch.call_count, 1)
            self.assert_preserved()

    def test_restrictive_umask_keeps_updated_launcher_imports_readable(self):
        previous = os.umask(0o077)
        try:
            self.assertTrue(self.run_update())
        finally:
            os.umask(previous)
        candidate = (self.root / "current").resolve()
        for path in [self.root / "releases", candidate, *candidate.rglob("*")]:
            self.assertEqual(
                path.stat().st_mode & 0o777, 0o755 if path.is_dir() else 0o644
            )

    def test_bad_checksum_leaves_old_version(self):
        def bad(url, path, limit):
            self.fetch(url, path, limit)
            if url.endswith(".tar.gz"):
                path.write_bytes(b"damaged")

        with self.assertRaises(Error):
            self.run_update(fetch=bad)
        self.assert_preserved()
        self.validator.assert_not_called()

    def test_failed_candidate_or_activation_keeps_old_version(self):
        for failure in ("validation", "activation"):
            with self.subTest(failure=failure):
                self.validator.side_effect = (
                    ValueError("incompatible") if failure == "validation" else None
                )
                with patch.object(
                    updates.os, "replace", side_effect=OSError("cannot activate")
                ):
                    with self.assertRaises((ValueError, OSError)):
                        self.run_update()
                self.assert_preserved()
                self.assertEqual(list((self.root / "releases").iterdir()), [])

    def test_rejects_prerelease_draft_and_unexpected_tag(self):
        for key, val in (
            ("prerelease", True),
            ("draft", True),
            ("tag_name", "v0.10.7"),
        ):
            with self.subTest(key=key), patch.dict(self.release, {key: val}):
                with self.assertRaises(Error):
                    self.run_update()
                self.assert_preserved()

    def test_rejects_untrusted_assets_and_duplicates(self):
        for url in ("https://evil.example/a", "http://github.com/a"):
            with patch.dict(self.release["assets"][0], {"browser_download_url": url}):
                with self.assertRaises(Error):
                    self.run_update()
        self.release["assets"].append(self.release["assets"][0])
        with self.assertRaises(Error):
            self.run_update()
        self.assert_preserved()

    def test_archive_links_traversal_and_invalid_tar_do_not_activate(self):
        for entries in (
            {"product/../../oops": b"x"},
            {"product/llm_service_manager/x": None},
        ):
            self.archive = bundle(entries)
            with self.assertRaises(Error):
                self.run_update()
            self.assert_preserved()
        self.archive = b"not a tar file"
        with self.assertRaises(Error):
            self.run_update()
        self.assert_preserved()

    def test_pending_operation_or_lock_prevents_network(self):
        self.state.mkdir()
        fetch = Mock()
        with locked(self.state), self.assertRaises(Error):
            self.run_update(fetch=fetch)
        (self.state / "pending.json").write_text("{}")
        with self.assertRaises(Error):
            self.run_update(fetch=fetch)
        fetch.assert_not_called()

    def test_connection_failure_preserves_installation(self):
        with self.assertRaises(OSError):
            self.run_update(fetch=Mock(side_effect=OSError("offline")))
        self.assert_preserved()

    def test_interrupt_after_commit_keeps_new_product_and_reports_success(self):
        replace = updates.os.replace

        def committed_then_interrupted(source, target):
            replace(source, target)
            raise KeyboardInterrupt

        with patch.object(
            updates.os, "replace", side_effect=committed_then_interrupted
        ):
            self.assertTrue(self.run_update())
        self.assertTrue(
            (self.root / "current/llm_service_manager/__main__.py").is_file()
        )
        self.assertTrue((self.old / "marker").is_file())

    def test_menu_exits_after_successful_update_only(self):
        for result in (False, True):
            ui = Mock()
            ui.choose.side_effect = ["options", "exit"]
            with (
                patch.object(tui, "read_snapshot", return_value=({}, "revision")),
                patch.object(tui, "discover", side_effect=lambda c: c),
                patch.object(tui, "options", return_value=result),
                patch.object(tui, "running_backends", return_value=[]),
            ):
                tui.run_menu(ui)
            self.assertEqual(ui.choose.call_count, 1 if result else 2)

    def test_missing_stable_is_not_an_install_attempt(self):
        from urllib.error import HTTPError

        with patch.object(updates, "latest", return_value=None):
            self.assertFalse(self.run_update())
        self.assert_preserved()
        error = HTTPError(updates.LATEST, 404, "missing", {}, io.BytesIO())
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(updates.latest(Path(tmp), Mock(side_effect=error)))
        self.assertTrue(error.closed)

    def test_options_invokes_update_without_confirmation_and_signals_exit(self):
        for result in (False, True):
            ui = Mock()
            ui.choose.return_value = "update"
            ui.external.side_effect = lambda fn, title: fn()
            with patch.object(
                updates, "check_and_update", return_value=result
            ) as update:
                self.assertEqual(tui.options(ui), result)
            update.assert_called_once_with()
            self.assertEqual(ui.choose.call_count, 1)

    def test_candidate_validation_checks_real_imports_and_version(self):
        # Uses this checkout as a read-only candidate, never runs an installer.
        root = Path(__file__).resolve().parents[1]
        from llm_service_manager import __version__

        updates.validate_candidate(root, __version__, self.base / "absent.json")
        with self.assertRaises(Error):
            updates.validate_candidate(root, "999.0.0", self.base / "absent.json")
