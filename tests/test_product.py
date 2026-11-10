import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_config import fixture

from llm_service_manager import presentation, status, tui
from llm_service_manager.commands import commands, recipes


class ProductTests(unittest.TestCase):
    def tearDown(self):
        presentation.LANGUAGE = "zh"

    def test_apply_is_single_action_and_checks_before_start(self):
        c = fixture()
        ui = Mock()
        ui.choose.side_effect = ["strata", "s"]
        ui.external.side_effect = lambda fn, *args: fn()
        with (
            patch.object(tui, "available_backends", return_value=["strata"]),
            patch.object(tui, "model_present", return_value=True),
            patch.object(tui, "edit", return_value=c),
            patch.object(tui, "preflight", return_value=([], [])) as pre,
            patch.object(tui, "switch") as switch,
        ):
            tui.selection_flow(ui, c, "revision")
            self.assertEqual(ui.choose.call_count, 2)
            pre.assert_called_once()
            switch.assert_called_once_with(c, "strata", "revision")
            ui.text.assert_not_called()

    def test_preflight_failure_returns_to_editor_without_start(self):
        c = fixture()
        ui = Mock()
        ui.choose.side_effect = ["strata", "s"]
        with (
            patch.object(tui, "available_backends", return_value=["strata"]),
            patch.object(tui, "model_present", return_value=True),
            patch.object(tui, "edit", side_effect=[c, tui.Cancelled]),
            patch.object(tui, "preflight", return_value=(["missing"], [])),
            patch.object(tui, "switch") as switch,
        ):
            with self.assertRaises(tui.Cancelled):
                tui.selection_flow(ui, c, "revision")
            switch.assert_not_called()
            ui.text.assert_called_once()

    def test_form_has_fixed_bottom_actions_and_dependency_labels(self):
        c = fixture()
        c["settings"]["mtp"]["value"] = False
        rows, disabled = tui.setting_rows(c, "llama.cpp")
        self.assertEqual([k for k, _ in rows[-2:]], ["review", "back"])
        for key in ["reserve_mib", "mtp_candidates", "mtp_kv", "gpu_only", "ple_io"]:
            self.assertIn(key, disabled)
        self.assertIn("不适用", str(dict(rows)["ple_io"]))

    def test_release_cancel_last_but_default(self):
        ui = Mock()
        ui.choose.return_value = False
        tui.release_resources(ui)
        rows = ui.choose.call_args.args[1]
        self.assertEqual([key for key, _ in rows], [True, False])
        self.assertEqual(ui.choose.call_args.kwargs["focus"], 1)
        ui.external.assert_not_called()

    def test_release_menu_calls_unload_not_service_stop(self):
        ui = Mock()
        ui.choose.return_value = True
        ui.external.side_effect = lambda fn, title: fn()
        with patch.object(tui, "release_model") as release:
            tui.release_resources(ui)
            release.assert_called_once_with()
        self.assertIn("release", dict(tui.main_menu()))
        self.assertNotIn("stop", dict(tui.main_menu()))

    def test_options_only_language_version_back(self):
        ui = Mock()
        ui.choose.return_value = "back"
        tui.options(ui)
        self.assertEqual(
            [k for k, _ in ui.choose.call_args.args[1]], ["language", "version", "back"]
        )

    def test_language_persists_without_model_changes(self):
        from llm_service_manager.execution import Operations as Ops

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(presentation, "PREFERENCES", Path(tmp) / "ui.json"),
            patch.object(Ops, "event"),
        ):
            presentation.set_language("en")
            presentation.LANGUAGE = "zh"
            presentation.load_language()
            self.assertEqual(presentation.LANGUAGE, "en")
            self.assertEqual(tui.main_menu()[0][1], "Switch backend/model")
            self.assertEqual(presentation.label("context"), "Context per request")

    def test_install_guide_return_never_executes(self):
        ui = Mock()
        ui.blocks.return_value = len(recipes()) + 1
        rows = [{"entry": entry, "installed": True} for entry in recipes()]
        with (
            patch.object(tui, "inventory", return_value=rows),
            patch.object(tui, "execute") as execute,
        ):
            tui.installation_list(ui, fixture())
            execute.assert_not_called()
            ui.external.assert_not_called()

    def test_recipes_display_exact_commands_and_shell_syntax(self):
        for backend in ["ollama", "llama.cpp", "strata"]:
            text = commands(backend)
            for _, _, cmd in recipes(backend):
                self.assertIn(cmd, text)
                subprocess.run(
                    ["bash", "-n", "-c", cmd], check=True
                )  # Parse only; never install/download.

    def test_status_reads_only_and_no_model_loaded_from_selection(self):
        c = fixture()
        c["target"] = {"backend": "ollama", "model": "o"}
        ops = Mock()

        def run(argv, *args, **kwargs):
            output = (
                "LoadState=loaded\nActiveState="
                + ("active" if "ollama.service" in argv else "inactive")
                + "\n"
            )
            return subprocess.CompletedProcess(
                argv, 0, output if argv[0] == "systemctl" else ""
            )

        ops.run.side_effect = run
        ops.http.return_value = {"models": []}
        with patch.object(
            status, "available_backends", return_value=["ollama", "llama.cpp", "strata"]
        ):
            snap = status.collect(c, ops)
        ops.http.assert_called_once_with("http://127.0.0.1:55555/api/ps", timeout=3)
        out = io.StringIO()
        with patch("sys.stdout", out):
            status.show(c, snap)
        self.assertIn("未加载；服务空闲等待请求", out.getvalue())
        self.assertIn("不是全部参数的实时回读", out.getvalue())

    def test_unknown_residency_not_claimed_loaded(self):
        snap = {
            "services": {},
            "runtime": {"backend": "llama.cpp", "response": {"status": "ok"}},
            "memory": {},
            "gpu": [],
            "errors": [],
        }
        out = io.StringIO()
        with patch("sys.stdout", out):
            status.show(fixture(), snap)
        self.assertIn("未取得官方 llama.cpp Router 模型状态", out.getvalue())

    def test_output_boundaries_and_no_color(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            print("old output")
            presentation.boundary("Status")
            print("new output")
        self.assertIn("old output\n\n", out.getvalue())
        self.assertIn("Status\n\nnew output", out.getvalue())
        self.assertNotIn("\x1b", out.getvalue())

    def test_executor_runs_only_displayed_commands_and_stops_on_failure(self):
        # Stub all subprocesses: no actual installer or download is executed.
        from types import SimpleNamespace

        from llm_service_manager.commands import execute
        from llm_service_manager.config import Error

        for code in (0, 7):
            with (
                self.subTest(exit=code),
                patch(
                    "pwd.getpwnam",
                    return_value=SimpleNamespace(
                        pw_uid=1000, pw_name="user", pw_dir="/home/user"
                    ),
                ),
                patch("llm_service_manager.execution.Operations") as ops,
                patch("subprocess.Popen") as popen,
                patch("os.read", return_value=b""),
                patch("sys.stdout", io.StringIO()),
            ):
                proc = popen.return_value
                proc.wait.return_value = code
                proc.poll.return_value = code
                if code:
                    with self.assertRaises(Error):
                        execute("ollama", fixture())
                else:
                    execute("ollama", fixture())
                expected = [cmd for _, _, cmd in recipes("ollama")][
                    : 1 if code else None
                ]
                self.assertEqual(
                    [call.args[0][-1] for call in popen.call_args_list], expected
                )
                for call in popen.call_args_list:
                    self.assertEqual(
                        call.args[0][:-1],
                        [
                            "sudo",
                            "-H",
                            "-u",
                            "user",
                            "--",
                            "bash",
                            "-o",
                            "pipefail",
                            "-c",
                        ],
                    )
                self.assertTrue(
                    all(call[0] == "event" for call in ops.return_value.method_calls)
                )
