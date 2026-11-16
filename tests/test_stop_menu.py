"""Dynamic stop actions use live service identity, never saved model selection."""

import tempfile
import unittest
from unittest.mock import Mock, patch

from test_config import fixture

from llm_service_manager import manager, services, tui
from llm_service_manager.config import Error
from llm_service_manager.paths import UNITS


class StopMenuTests(unittest.TestCase):
    def test_stop_actions_follow_release_and_only_include_running_backends(self):
        for active in ([], ["ollama"], ["llama.cpp"], ["strata"], list(UNITS)):
            keys = [key for key, _ in tui.main_menu(active)]
            index = keys.index("release") + 1
            self.assertEqual(
                keys[index : index + len(active)], ["stop:" + b for b in active]
            )
            self.assertEqual(
                [key for key in keys if key.startswith("stop:")],
                ["stop:" + b for b in active],
            )
            for backend in active:
                self.assertEqual(
                    dict(tui.main_menu(active))["stop:" + backend], "停止 " + backend
                )

    def test_inventory_uses_services_even_without_model_configuration(self):
        ops = Mock()
        ops.active.return_value = [UNITS["strata"]]
        self.assertEqual(manager.running_backends(ops), ["strata"])

    def test_stop_only_named_backend_and_preserve_other_running_services(self):
        ops = services.Services()
        with (
            patch.object(
                ops,
                "active",
                side_effect=[list(UNITS.values()), [UNITS["ollama"], UNITS["strata"]]],
            ),
            patch.object(ops, "run") as run,
        ):
            ops.stop(backend="llama.cpp")
        run.assert_called_once_with(
            ["systemctl", "stop", UNITS["llama.cpp"]], timeout=120
        )

    def test_stale_menu_never_stops_a_newly_selected_backend(self):
        ops = services.Services()
        with (
            patch.object(ops, "active", return_value=[UNITS["ollama"]]),
            patch.object(ops, "run") as run,
        ):
            ops.stop(backend="strata")
        run.assert_not_called()

    def test_failed_stop_is_not_reported_as_success(self):
        ops = services.Services()
        with (
            patch.object(ops, "active", return_value=[UNITS["strata"]]),
            patch.object(ops, "run"),
            self.assertRaises(Error),
        ):
            ops.stop(backend="strata")

    def test_scoped_stop_uses_shared_lock_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = services.Services(tmp, quiet=True)
            with patch.object(ops, "stop") as stop:
                manager.stop(ops, backend="strata")
            stop.assert_called_once_with(backend="strata")

    def test_menu_refreshes_after_stop_without_loading_or_changing_config(self):
        ui = Mock()
        ui.choose.side_effect = ["stop:strata", "exit"]
        ui.external.side_effect = lambda fn, title: fn()
        with (
            patch.object(tui, "read_snapshot", return_value=(fixture(), "rev")),
            patch.object(tui, "discover", side_effect=lambda c: c),
            patch.object(tui, "running_backends", side_effect=[["strata"], []]),
            patch.object(tui, "stop") as stop,
            patch("builtins.print"),
        ):
            tui.run_menu(ui)
        stop.assert_called_once_with(backend="strata")
        self.assertIn("stop:strata", dict(ui.choose.call_args_list[0].args[1]))
        self.assertNotIn("stop:strata", dict(ui.choose.call_args_list[1].args[1]))

    def test_unavailable_systemd_still_allows_menu_exit(self):
        ui = Mock()
        ui.choose.return_value = "exit"
        with (
            patch.object(tui, "read_snapshot", return_value=(fixture(), "rev")),
            patch.object(tui, "discover", side_effect=lambda c: c),
            patch.object(
                tui, "running_backends", side_effect=Error("systemd unavailable")
            ),
        ):
            tui.run_menu(ui)
        self.assertIn("systemd unavailable", str(ui.choose.call_args.args[2]))
        self.assertFalse(
            any(key.startswith("stop:") for key, _ in ui.choose.call_args.args[1])
        )
