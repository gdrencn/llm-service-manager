"""One state machine for every setup row, independent of platform."""

import unittest
from unittest.mock import patch

from test_config import fixture
from test_lan import snapshot

from llm_service_manager import lan
from llm_service_manager.checklist import State, state


class ChecklistTests(unittest.TestCase):
    def test_complete_state_matrix(self):
        expected = {
            (True, True): State.EFFECTIVE,
            (True, False): State.EFFECTIVE,
            (False, True): State.EXECUTE,
            (False, False): State.MANUAL,
            (None, True): State.MANUAL,
            (None, False): State.MANUAL,
        }
        for args, result in expected.items():
            with self.subTest(args=args):
                self.assertEqual(state(*args), result)

    def test_windows_setup_is_a_copyable_standalone_entry(self):
        for admin in (True, False):
            data = snapshot(
                windows={"portproxy": "", "rules": [], "listeners": [], "admin": admin}
            )
            data.update(network_mode="nat", powershell="/bin/powershell.exe")
            rows = [
                r for r in lan.steps(fixture(), data) if r.title.startswith("Windows")
            ]
            self.assertTrue(all(row.action is None for row in rows))
            setup = next(r for r in rows if r.title == "Windows：一键配置")
            self.assertIn("windows-bootstrap.ps1", setup.command)
            self.assertNotIn("-Distro", setup.command)
            self.assertNotIn("-Port", setup.command)
            self.assertEqual(setup.state, State.MANUAL)

    def test_linux_plan_uses_same_states(self):
        for ready, commands, expected in [
            (False, [("ufw", "allow", "55555/tcp")], State.EXECUTE),
            (True, [], State.EFFECTIVE),
            (False, [], State.MANUAL),
        ]:
            data = snapshot(wsl=False)
            data["linux_plan"] = {
                "ready": ready,
                "commands": commands,
                "reason": "test",
            }
            with patch.object(lan.os, "geteuid", return_value=0):
                row = lan.steps(fixture(), data)[3]
            self.assertEqual(row.state, expected)

    def test_no_privilege_does_not_hide_verified_configuration(self):
        data = snapshot(
            windows={
                "portproxy": "0.0.0.0 55555 172.28.10.2 55555",
                "rules": [],
                "admin": False,
            }
        )
        row = next(
            r for r in lan.steps(fixture(), data) if r.title == "Windows：TCP 转发"
        )
        self.assertEqual(row.state, State.EFFECTIVE)
        self.assertIsNone(row.action)
