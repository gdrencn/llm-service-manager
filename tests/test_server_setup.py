"""Setup is a no-op on a reachable server and never claims unverified success."""

import contextlib
import unittest
from unittest.mock import patch

from test_config import fixture

from llm_service_manager import server_setup as setup
from llm_service_manager.config import Error


class ServerSetupTests(unittest.TestCase):
    def run_case(self, checks, selected=True, route=True):
        c = fixture()
        c["target"] = (
            {"backend": "ollama", "model": c["model_selection"]["ollama"]}
            if selected
            else None
        )
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch.object(setup, "read_snapshot", return_value=(c, "revision"))
        )
        stack.enter_context(
            patch.object(
                setup,
                "endpoint",
                return_value=("192.168.1.2", 55555) if route else None,
            )
        )
        stack.enter_context(patch.object(setup, "reachable", side_effect=checks))
        stack.enter_context(
            patch.object(setup, "locked", return_value=contextlib.nullcontext())
        )
        stack.enter_context(patch("builtins.print"))
        switch = stack.enter_context(patch.object(setup, "switch"))
        configure = stack.enter_context(patch.object(setup, "configure"))
        return switch, configure

    def test_reachable_is_noop_even_without_saved_model(self):
        switch, configure = self.run_case([True], selected=False)
        setup.complete()
        switch.assert_not_called()
        configure.assert_not_called()

    def test_start_model_only_when_needed(self):
        switch, configure = self.run_case([False, True])
        setup.complete()
        self.assertEqual(
            switch.call_args.args[0]["settings"]["host"]["value"], "0.0.0.0"
        )
        configure.assert_not_called()

    def test_firewall_repair_has_final_tcp_verification(self):
        switch, configure = self.run_case([False, False, True])
        setup.complete()
        configure.assert_called_once_with(55555)

    def test_failed_final_check_is_failure(self):
        self.run_case([False, False, False])
        with self.assertRaisesRegex(Error, "仍不可连接"):
            setup.complete()

    def test_no_route_or_model_does_not_mutate(self):
        for selected, route in [(False, True), (True, False)]:
            with self.subTest(selected=selected, route=route):
                switch, configure = self.run_case([False], selected, route)
                with self.assertRaises(Error):
                    setup.complete()
                switch.assert_not_called()
                configure.assert_not_called()

    def test_switch_failure_does_not_attempt_firewall_override(self):
        switch, configure = self.run_case([False])
        switch.side_effect = Error("foreign listener")
        with self.assertRaisesRegex(Error, "foreign listener"):
            setup.complete()
        configure.assert_not_called()
