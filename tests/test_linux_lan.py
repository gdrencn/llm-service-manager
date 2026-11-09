"""Fresh-host and existing firewall policies; never install or alter host rules."""

import json
import unittest
from unittest.mock import Mock, patch

from llm_service_manager import linux_lan as lan
from llm_service_manager.config import Error


def result(output="", code=0):
    return Mock(stdout=output, returncode=code)


class LinuxLanTests(unittest.TestCase):
    def tools(self, names):
        return patch.object(
            lan.shutil,
            "which",
            side_effect=lambda name: (
                "/usr/bin/" + name if name in names.split() else None
            ),
        )

    def test_fresh_apt_only_provisions_standard_inspection_packages(self):
        with self.tools("apt-get ip ss"):
            plan = lan.inspect(55555, Mock())
        self.assertFalse(plan.ready)
        self.assertEqual(
            plan.commands,
            (
                ("apt-get", "update"),
                ("apt-get", "install", "-y", "nftables", "iptables"),
            ),
        )

    def test_missing_network_tools_use_distro_package(self):
        for manager, package in [("apt-get", "iproute2"), ("dnf", "iproute")]:
            with self.tools(manager):
                plan = lan.inspect(55555)
            self.assertIn(package, plan.commands[-1])

    def test_unknown_package_manager_is_not_guessed(self):
        with self.tools("pacman"):
            self.assertEqual(lan.inspect(55555).commands, ())

    def test_empty_kernel_rules_require_no_new_firewall(self):
        ops = Mock()
        ops.run.side_effect = [
            result(
                json.dumps(
                    {"nftables": [{"metainfo": {}}, {"chain": {"policy": "accept"}}]}
                )
            ),
            result("*filter\n:INPUT ACCEPT [0:0]\nCOMMIT\n"),
            result(""),
        ]
        with self.tools("ip ss nft iptables-save iptables-legacy-save"):
            plan = lan.inspect(55555, ops)
        self.assertTrue(plan.ready)
        self.assertEqual(plan.commands, ())
        self.assertTrue(
            all(
                "list" in call.args[0] or call.args[0][0].endswith("-save")
                for call in ops.run.call_args_list
            )
        )

    def test_legacy_rules_are_not_missed_when_nft_is_empty(self):
        ops = Mock()
        ops.run.side_effect = [
            result('{"nftables":[]}'),
            result(""),
            result("*filter\n:INPUT DROP [0:0]\nCOMMIT"),
        ]
        with self.tools("ip ss nft iptables-save iptables-legacy-save"):
            plan = lan.inspect(55555, ops)
        self.assertFalse(plan.ready)
        self.assertFalse(plan.commands)

    def test_custom_nft_policy_is_never_flushed(self):
        ops = Mock()
        ops.run.side_effect = [
            result('{"nftables":[{"chain":{"policy":"drop"}}]}'),
            result(""),
        ]
        with self.tools("ip ss nft iptables-save"):
            plan = lan.inspect(55555, ops)
        self.assertFalse(plan.ready)
        self.assertFalse(plan.commands)

    def test_query_failure_is_not_treated_as_no_firewall(self):
        ops = Mock()
        ops.run.side_effect = Error("permission denied")
        with self.tools("ip ss nft iptables-save"):
            plan = lan.inspect(55555, ops)
        self.assertFalse(plan.ready)
        self.assertFalse(plan.commands)
        self.assertIn("permission denied", plan.reason)

    def test_ufw_active_inactive_allow_and_deny(self):
        for output, ready, commands in [
            ("Status: active\n", False, (("ufw", "allow", "55555/tcp"),)),
            ("Status: active\n55555/tcp ALLOW Anywhere\n", True, ()),
            ("Status: active\n55555/tcp DENY Anywhere\n", False, ()),
            (
                "Status: inactive\n",
                False,
                (
                    ("apt-get", "update"),
                    ("apt-get", "install", "-y", "nftables", "iptables"),
                ),
            ),
        ]:
            ops = Mock()
            ops.run.return_value = result(output)
            with self.tools("ip ss ufw apt-get"):
                plan = lan.inspect(55555, ops)
            self.assertEqual((plan.ready, plan.commands), (ready, commands))

    def test_firewalld_runtime_and_permanent_are_independent(self):
        for runtime, persistent in [(0, 0), (1, 0), (0, 1), (1, 1)]:
            ops = Mock()
            ops.run.side_effect = [
                result("running"),
                result('[{"dev":"eth0"}]'),
                result("public"),
                result(code=runtime),
                result(code=persistent),
            ]
            with self.tools("ip ss firewall-cmd"):
                plan = lan.inspect(55555, ops)
            self.assertEqual(plan.ready, runtime == persistent == 0)
            self.assertEqual(len(plan.commands), runtime + persistent)
            self.assertTrue(all("--add-port=55555/tcp" in c for c in plan.commands))
            self.assertFalse(any("--reload" in c for c in plan.commands))

    def test_firewalld_default_zone_and_malformed_zone(self):
        ops = Mock()
        ops.run.side_effect = [
            result("running"),
            result('[{"dev":"eth0"}]'),
            result("no zone"),
            result("bad zone"),
        ]
        with self.tools("ip ss firewall-cmd"):
            plan = lan.inspect(55555, ops)
        self.assertFalse(plan.commands)
        self.assertIn("zone", plan.reason)

    def test_provision_replans_then_verifies(self):
        ops = Mock()
        ops.run.return_value = result()
        plans = [
            lan.Plan(False, "install", (("apt-get", "install", "-y", "nftables"),)),
            lan.Plan(True, "ready"),
        ]
        with patch.object(lan, "inspect", side_effect=plans), patch("builtins.print"):
            lan.configure(55555, ops)
        ops.run.assert_called_once_with(
            ["apt-get", "install", "-y", "nftables"], timeout=1200
        )

    def test_install_failure_stops_without_firewall_changes(self):
        ops = Mock()
        ops.run.side_effect = Error("repository unavailable")
        with (
            patch.object(
                lan,
                "inspect",
                return_value=lan.Plan(
                    False,
                    "install",
                    (("apt-get", "update"), ("apt-get", "install", "-y", "nftables")),
                ),
            ),
            patch("builtins.print"),
        ):
            with self.assertRaises(Error):
                lan.configure(55555, ops)
        self.assertEqual(ops.run.call_count, 1)
