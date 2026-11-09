"""Port selection, truthful network observations, and no-execution UI contracts."""

import errno
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_config import fixture

from llm_service_manager import config, lan, lan_ui, linux_lan, network, storage, tui


def snapshot(wsl=True, windows=None):
    return {
        "wsl": wsl,
        "distro": "llm",
        "ip": "172.28.10.2",
        "listening": [],
        "windows": windows,
        "errors": [],
    }


class PortTests(unittest.TestCase):
    def test_new_defaults(self):
        c = config.normalized({})
        self.assertEqual(config.value(c, "host"), "0.0.0.0")
        self.assertEqual(config.value(c, "port"), 55555)

    def test_real_occupied_port_is_skipped_and_probe_is_released(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            start = busy.getsockname()[1]
            if start == 65535:
                self.skipTest("ephemeral port at end of range")
            port = network.free_port("127.0.0.1", start, min(start + 100, 65535))
            self.assertGreater(port, start)
            with socket.socket() as next_owner:
                next_owner.bind(("127.0.0.1", port))
            with self.assertRaises(config.Error):
                network.free_port("127.0.0.1", start, start)

    def test_permission_error_is_not_treated_as_port_collision(self):
        with patch.object(
            network, "check_bind", side_effect=PermissionError(errno.EACCES, "denied")
        ) as check:
            with self.assertRaises(config.Error):
                network.free_port()
        self.assertEqual(check.call_count, 1)

    def test_collision_note_survives_save_and_reload_without_reprobing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            with patch.object(
                network,
                "check_bind",
                side_effect=[
                    OSError(errno.EADDRINUSE, "busy"),
                    OSError(errno.EADDRINUSE, "busy"),
                    None,
                ],
            ):
                c, _ = storage.read_snapshot(path)
            self.assertEqual(config.value(c, "port"), 55557)
            self.assertFalse(path.exists())
            storage.atomic(path, json.dumps(c))
            with patch.object(network, "free_port") as scan:
                loaded = storage.read_config(path)
            scan.assert_not_called()
            self.assertEqual(network.port_adjustment(loaded), 55557)
            loaded["settings"]["port"]["value"] = 60000
            self.assertIsNone(network.port_adjustment(loaded))

    def test_existing_address_and_port_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            raw = {
                "settings": {"host": {"value": "127.0.0.1"}, "port": {"value": 11434}}
            }
            path.write_text(json.dumps(raw))
            before = path.read_bytes()
            with patch.object(network, "free_port") as scan:
                c = storage.read_config(path)
            scan.assert_not_called()
            self.assertEqual(config.value(c, "port"), 11434)
            self.assertEqual(config.value(c, "host"), "127.0.0.1")
            self.assertEqual(before, path.read_bytes())

    def test_adjustment_is_visible_beside_setting(self):
        c = fixture()
        c["settings"]["port"]["value"] = 55556
        c["initial_port"] = {"default": 55555, "selected": 55556}
        rows, _ = tui.setting_rows(c, "ollama")
        self.assertIn("默认55555被占用", str(dict(rows)["port"]))


class LanTests(unittest.TestCase):
    def test_unknown_windows_is_not_marked_configured(self):
        rows = lan.steps(fixture(), snapshot())
        windows = [row for row in rows if row.title.startswith("Windows")]
        self.assertTrue(windows)
        self.assertTrue(all(row.state == "manual" for row in windows))
        self.assertEqual(rows[-1].state, "manual")

    def test_forwarding_wrong_port_or_stale_ip_needs_configuration(self):
        for proxy in [
            "0.0.0.0 55555 172.28.10.3 55555",
            "0.0.0.0 55554 172.28.10.2 55555",
            "",
        ]:
            rows = lan.steps(
                fixture(), snapshot(windows={"portproxy": proxy, "rules": []})
            )
            row = next(row for row in rows if row.title == "Windows：TCP 转发")
            self.assertEqual(row.state, "manual")
        rows = lan.steps(
            fixture(),
            snapshot(
                windows={"portproxy": "0.0.0.0 55555 172.28.10.2 55555", "rules": []}
            ),
        )
        self.assertEqual(
            next(row for row in rows if row.title == "Windows：TCP 转发").state,
            "effective",
        )
        self.assertEqual(rows[-1].state, "manual")

    def test_firewall_rule_requires_exact_port_scope_and_enablement(self):
        rule = {
            "enabled": "True",
            "direction": "Inbound",
            "action": "Allow",
            "profile": "Private",
            "protocol": "TCP",
            "ports": ["55555"],
            "remote": ["LocalSubnet"],
        }
        self.assertTrue(lan._firewall_matches(rule, 55555))
        for field, value in [
            ("enabled", "False"),
            ("ports", ["11434"]),
            ("remote", ["Any"]),
            ("profile", "Public"),
            ("action", "Block"),
        ]:
            self.assertFalse(lan._firewall_matches(dict(rule, **{field: value}), 55555))

    def test_native_linux_omits_windows_commands(self):
        rows = lan.steps(fixture(), snapshot(wsl=False))
        self.assertFalse(
            any(
                "netsh " in row.command or "NetFirewallRule" in row.command
                for row in rows
            )
        )
        self.assertIn("172.28.10.2:55555", rows[-1].command)

    def test_loopback_listener_is_not_lan_ready(self):
        data = snapshot()
        data["listening"] = ["127.0.0.1"]
        self.assertEqual(lan.steps(fixture(), data)[1].state, "manual")
        data["listening"] = ["0.0.0.0"]
        self.assertEqual(lan.steps(fixture(), data)[1].state, "effective")

    def test_lan_menu_does_not_probe_until_an_action_is_selected(self):
        ui = Mock()
        ui.choose.return_value = "back"
        with (
            patch.object(lan_ui, "is_wsl", return_value=True),
            patch.object(lan_ui, "complete") as complete,
        ):
            lan_ui.show(ui)
        self.assertEqual(
            [key for key, _ in ui.choose.call_args.args[1]],
            ["server", "windows", "back"],
        )
        complete.assert_not_called()
        ui.loading.assert_not_called()

    def test_native_linux_hides_windows_option(self):
        ui = Mock()
        ui.choose.return_value = "back"
        with patch.object(lan_ui, "is_wsl", return_value=False):
            lan_ui.show(ui)
        self.assertEqual(
            [key for key, _ in ui.choose.call_args.args[1]], ["server", "back"]
        )

    def test_windows_page_has_no_executable_action(self):
        ui = Mock()
        ui.choose.side_effect = ["windows", "back"]
        with (
            patch.object(lan_ui, "is_wsl", return_value=True),
            patch.object(lan_ui, "read_snapshot", return_value=(fixture(), "rev")),
            patch(
                "subprocess.Popen", side_effect=AssertionError("must not run commands")
            ),
        ):
            lan_ui.show(ui)
        self.assertEqual(ui.document.call_args.args[2], [("back", "返回")])
        self.assertIn(
            "v0.10.2/scripts/windows-bootstrap.ps1", str(ui.document.call_args.args[1])
        )
        ui.external.assert_not_called()

    def test_bad_network_query_is_unknown_not_false_success(self):
        ops = Mock()
        ops.run.side_effect = config.Error("permission denied")
        with (
            patch.object(lan.os, "uname", return_value=Mock(release="Linux")),
            patch.object(lan.shutil, "which", return_value="/usr/bin/ip"),
        ):
            data = lan.probe(fixture(), ops)
        self.assertIsNone(data["ip"])
        self.assertTrue(data["errors"])

    def test_windows_probe_only_uses_read_commands(self):
        ops = Mock()
        ops.run.side_effect = [
            Mock(stdout='[{"prefsrc":"172.28.10.2"}]'),
            Mock(stdout="nat"),
            Mock(stdout=json.dumps({"portproxy": "", "rules": [], "listeners": []})),
        ]
        with (
            patch.object(
                linux_lan, "inspect", return_value=linux_lan.Plan(True, "clear")
            ),
            patch.object(
                lan.os, "uname", return_value=Mock(release="microsoft-standard-WSL2")
            ),
            patch.object(
                lan.shutil,
                "which",
                side_effect=lambda name: None if name == "ufw" else "/bin/" + name,
            ),
        ):
            result = lan.probe(fixture(), ops)
        self.assertEqual(result["windows"]["listeners"], [])
        script = ops.run.call_args.args[0][-1]
        self.assertIn("portproxy show v4tov4", script)
        self.assertIn("Get-NetFirewallRule", script)
        self.assertNotIn("New-NetFirewallRule", script)
        self.assertNotIn("Remove-NetFirewallRule", script)
        self.assertNotIn("portproxy add", script)
        self.assertNotIn("PORT_VALUE", script)

    def test_windows_permission_failure_leaves_states_unknown(self):
        ops = Mock()
        ops.run.side_effect = config.Error("access denied")
        with (
            patch.object(lan.os, "uname", return_value=Mock(release="microsoft-WSL2")),
            patch.object(lan.shutil, "which", return_value="/bin/tool"),
        ):
            result = lan.probe(fixture(), ops)
        self.assertIsNone(result["windows"])
        self.assertTrue(result["errors"])
