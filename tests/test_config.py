import json
import os
import pathlib
import pwd
import tempfile
import unittest
from unittest.mock import patch

from llm_service_manager import manager, services, tui
from llm_service_manager.adapters import native
from llm_service_manager.config import Error, normalized, validate, value, warnings
from llm_service_manager.paths import UNITS


def fixture():
    c = normalized({})
    c["catalog"] = [
        {
            "id": "o",
            "backend": "ollama",
            "name": "o",
            "source": "qwen3.8:latest",
            "mtp_supported": True,
        },
        {
            "id": "l",
            "backend": "llama.cpp",
            "name": "l",
            "path": "/models/model.gguf",
            "vision_path": "/models/mm.gguf",
            "mtp_supported": True,
        },
        {
            "id": "s",
            "backend": "strata",
            "name": "s",
            "config_path": "/models/strata.json",
            "mtp_supported": True,
        },
    ]
    c["model_selection"] = {"ollama": "o", "llama.cpp": "l", "strata": "s"}
    return c


STRATA = {
    "exe": "component:strata-engine",
    "cwd": "/opt/strata",
    "args": [
        "--pack",
        "/models/pack",
        "--max-context",
        "8192",
        "--kv",
        "q4_0",
        "--spec",
        "4",
        "--mtp",
        "/models/mtp",
        "--vision",
    ],
    "vision": {"gpu": False, "mmproj": "/models/mm.gguf", "exe": "/opt/strata-vision"},
}


class ConfigTests(unittest.TestCase):
    def test_shared_context_and_native_conversions(self):
        for context in [131072, 262144]:
            c = fixture()
            c["settings"]["context"]["value"] = context
            c["settings"]["reserve_mib"]["value"] = 4096
            files = native(c, lambda p: json.dumps(STRATA))
            s = json.loads(files["/etc/strata/strata-local.json"])
            args = s["args"]
            self.assertEqual(args[args.index("--max-context") + 1], str(context))
            self.assertEqual(args[args.index("--kv") + 1], "int8")
            self.assertIn(
                f"num_ctx {context}", files["/etc/ollama/ollama-local.Modelfile"]
            )
            self.assertIn(
                f"ctx-size = {context}\n",
                files["/etc/llama.cpp/llama-local.ini"],
            )
            self.assertIn(
                "OLLAMA_GPU_OVERHEAD=4294967296",
                files["/etc/systemd/system/ollama.service.d/ollama-local.conf"],
            )
            self.assertEqual(s["idle_unload_s"], 300)
            self.assertTrue(s["vision"]["gpu"])
            self.assertEqual(s["parallel"], 1)

    def test_strata_old_overrides_cannot_shadow_shared_values(self):
        c = fixture()
        c["settings"]["reserve_mib"]["value"] = 4096
        old = dict(STRATA, user_args=["--vram-reserve-mib", "0"])
        f = native(c, lambda p: json.dumps(old))
        s = json.loads(f["/etc/strata/strata-local.json"])
        self.assertEqual(s["user_args"], [])
        self.assertEqual(s["args"][s["args"].index("--vram-reserve-mib") + 1], "4096")

    def test_disable_idle_correct_semantics(self):
        c = fixture()
        c["settings"]["idle_seconds"]["value"] = 0
        f = native(c, lambda p: json.dumps(STRATA))
        self.assertIn(
            "OLLAMA_KEEP_ALIVE=-1",
            f["/etc/systemd/system/ollama.service.d/ollama-local.conf"],
        )
        self.assertIn(
            "sleep-idle-seconds = -1\n",
            f["/etc/llama.cpp/llama-local.ini"],
        )
        self.assertEqual(
            json.loads(f["/etc/strata/strata-local.json"])["idle_unload_s"], 0
        )

    def test_applicability_and_fixed_gpu(self):
        c = fixture()
        c["settings"]["gpu_only"]["value"] = False
        with self.assertRaises(Error):
            validate(c)
        c = fixture()
        c["settings"]["ple_io"]["applies_to"].append("ollama")
        with self.assertRaises(Error):
            validate(c)

    def test_reserve_not_falsely_effective(self):
        c = fixture()
        c["settings"]["reserve_mib"]["value"] = 4096
        self.assertTrue(warnings(c, "llama.cpp"))
        self.assertFalse(warnings(c, "strata"))

    def test_strata_gpu_vision_headroom_warning(self):
        self.assertTrue(any("回退到CPU" in x for x in warnings(fixture(), "strata")))

    def test_disabled_mtp_and_vision_removed(self):
        c = fixture()
        c["settings"]["mtp"]["value"] = False
        c["settings"]["vision"]["value"] = False
        f = native(c, lambda p: json.dumps(STRATA))
        s = json.loads(f["/etc/strata/strata-local.json"])
        self.assertNotIn("vision", s)
        self.assertNotIn("--mtp", s["args"])
        self.assertNotIn(
            "--mmproj", f["/etc/systemd/system/llama-server-local.service"]
        )

    def test_only_current_configuration_format_is_supported(self):
        for raw in ({"schema": 1}, {"models": {"ollama": {"source": "x"}}}):
            with self.assertRaises(Error):
                normalized(raw)

    def test_working_directory_systemd_syntax(self):
        c = fixture()
        c["runtime"]["home"] = "/srv/model-user"
        f = native(c, lambda p: json.dumps(STRATA))
        self.assertIn(
            "WorkingDirectory=/srv/model-user\n",
            f["/etc/systemd/system/llama-server-local.service"],
        )
        self.assertNotIn(
            'WorkingDirectory="', f["/etc/systemd/system/llama-server-local.service"]
        )

    def test_rollback_revalidates_previous_model(self):
        c = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with (
                patch.object(services, "read_config", return_value=c),
                patch.object(services.Path, "exists", return_value=False),
                patch.object(ops, "wait_ready"),
                patch.object(ops, "ready") as ready,
                patch.object(ops, "run"),
            ):
                ops.restored(
                    {
                        "active": ["ollama.service"],
                        "runtime": {"backend": "ollama", "loaded": True},
                    }
                )
                ready.assert_called_once_with(
                    c,
                    "ollama",
                    unit="ollama.service",
                    configure=False,
                )

    def test_boot_target_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with (
                patch.object(ops, "available_units", return_value=list(UNITS.values())),
                patch.object(ops, "run") as run,
            ):
                ops.boot_target("strata")
                self.assertIn(
                    unittest.mock.call(["systemctl", "enable", "strata-local.service"]),
                    run.call_args_list,
                )
                self.assertIn(
                    unittest.mock.call(["systemctl", "disable", "ollama.service"]),
                    run.call_args_list,
                )
                run.reset_mock()
                ops.restore_boot(
                    {
                        "enabled": {
                            "ollama.service": "enabled",
                            "llama-server-local.service": "disabled",
                        }
                    }
                )
                self.assertIn(
                    unittest.mock.call(["systemctl", "enable", "ollama.service"]),
                    run.call_args_list,
                )
                self.assertIn(
                    unittest.mock.call(
                        ["systemctl", "disable", "strata-local.service"]
                    ),
                    run.call_args_list,
                )

    def test_stop_ignores_inactive_bad_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with (
                patch.object(ops, "active", side_effect=[["ollama.service"], []]),
                patch.object(ops, "run") as run,
            ):
                ops.stop()
                run.assert_called_once_with(
                    ["systemctl", "stop", "ollama.service"], timeout=120
                )

    def test_systemd_escaping(self):
        c = fixture()
        c["catalog"][1]["path"] = "/models/space $x %n.gguf"
        f = native(c, lambda p: json.dumps(STRATA))
        self.assertIn("space $x %n.gguf", f["/etc/llama.cpp/llama-local.ini"])

    def test_injection_rejected(self):
        c = fixture()
        c["settings"]["host"]["value"] = "127.0.0.1\nExecStart=evil"
        with self.assertRaises(Error):
            validate(c)

    def test_cancel_edit_does_not_write(self):
        class UI:
            n = 0

            def choose(self, *args, **kwargs):
                self.n += 1
                if self.n == 1:
                    return "context"
                return "back"

            def input(self, *args):
                return "256k"

        c = fixture()
        with self.assertRaises(tui.Cancelled):
            tui.edit(UI(), c, "strata")
        self.assertEqual(value(c, "context"), 131072)

    def test_commands_are_text_only(self):
        from llm_service_manager.commands import commands

        with patch("subprocess.run", side_effect=AssertionError("must not execute")):
            s = commands()
            self.assertIn("--mmproj-auto --mtp", s)
            self.assertIn("update --yes", s)

    def test_strata_recipe_completes_engine_vision_and_mtp_in_order(self):
        from llm_service_manager.commands import commands

        text = commands("strata")
        self.assertLess(text.index("update --yes"), text.index("install -- --family"))
        for flag in [
            "--vision gpu",
            "--mtp on",
            "--drafter mtp",
            "--no-start",
            "--yes",
        ]:
            self.assertIn(flag, text)
        self.assertIn('"$HOME/.local/bin/strata"', text)

    def test_missing_model_before_service_stop(self):
        c = fixture()
        with patch.object(manager, "Ops") as ops:
            errors, _ = manager.preflight(c, "llama.cpp")
            self.assertTrue(errors)
            ops.assert_not_called()


class FakeOps:
    def snapshot_runtime(self, *args):
        return None

    def snapshot_ollama(self, c):
        return {
            "name": "qwen3.8:latest",
            "modelfile": "FROM /models/blob",
            "host": "127.0.0.1",
            "port": 55555,
        }

    def restore_ollama(self, record):
        self.events.append(("restore-ollama", record))

    def clear_new_boot_links(self, txn):
        self.events.append(("clear-new-boot-links", txn))

    def check_router(self, c):
        pass

    def __init__(self, state, fail=False):
        self.state = state
        self.running = ["ollama.service"]
        self.fail = fail
        self.events = []

    def enabled(self):
        return {"ollama.service": "enabled"}

    def boot_target(self, *args):
        pass

    def restore_boot(self, *args):
        pass

    def restored(self, *args):
        pass

    def check_bind(self, *args):
        pass

    def prepare_runtime(self, *args):
        pass

    def event(self, kind, **kw):
        self.events.append((kind, kw))

    def active(self):
        return self.running[:]

    def stop(self):
        self.running = []

    def run(self, args, *a, **kw):
        if args[:2] == ["systemctl", "start"]:
            self.running.append(args[2])

    def write(self, path, text):
        manager.atomic(path, text)

    def ready(self, c, b):
        if self.fail:
            raise Error("fixture startup failure")


class Transactions(unittest.TestCase):
    def perform(self, fail=False, interrupt=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            config = root / "config.json"
            unit = root / "unit"
            unit.write_text("old-unit")
            config.write_text(json.dumps(fixture()))
            before = config.read_bytes()
            c = fixture()
            c["runtime"] = {"home": tmp, "user": pwd.getpwuid(os.getuid()).pw_name}
            c["settings"]["context"]["value"] = 262144
            ops = FakeOps(root / "state", fail)
            if interrupt:
                ops.ready = lambda *x: (_ for _ in ()).throw(KeyboardInterrupt())
            with (
                patch.object(manager, "preflight", return_value=([], [])),
                patch.object(manager, "native", return_value={str(unit): "new-unit"}),
            ):
                if fail or interrupt:
                    with self.assertRaises((Error, KeyboardInterrupt)):
                        manager.switch(
                            c, "llama.cpp", manager.revision(config), ops, config
                        )
                    self.assertEqual(config.read_bytes(), before)
                    self.assertEqual(unit.read_text(), "old-unit")
                    self.assertEqual(ops.running, ["ollama.service"])
                else:
                    manager.switch(
                        c, "llama.cpp", manager.revision(config), ops, config
                    )
                    self.assertEqual(
                        value(json.loads(config.read_text()), "context"), 262144
                    )
                    self.assertEqual(ops.running, ["llama-server-local.service"])
                    self.assertEqual(unit.read_text(), "new-unit")
            self.assertFalse((ops.state / "pending.json").exists())

    def test_success(self):
        self.perform()

    def test_failure_restores_all(self):
        self.perform(True)

    def test_interrupt_restores_all(self):
        self.perform(interrupt=True)

    def test_stale_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "config"
            p.write_text("{}")
            ops = FakeOps(pathlib.Path(tmp) / "state")
            with self.assertRaises(Error):
                manager.switch(fixture(), "ollama", "old-hash", ops, p)
            self.assertEqual(ops.running, ["ollama.service"])


if __name__ == "__main__":
    unittest.main()
