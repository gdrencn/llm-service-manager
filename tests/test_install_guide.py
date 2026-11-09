import copy
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_config import fixture
from test_inline import interact

from llm_service_manager import tui
from llm_service_manager.artifacts import embedded_mtp, gguf_set
from llm_service_manager.commands import recipes
from llm_service_manager.install_guide import list_blocks
from llm_service_manager.inventory import ollama_model


def sample_gguf(path, mtp=True):
    key = b"qwen38.nextn_predict_layers"
    path.write_bytes(
        b"GGUF"
        + struct.pack("<IQQ", 3, 0, 1)
        + struct.pack("<Q", len(key))
        + key
        + struct.pack("<II", 4, int(mtp))
    )


class InstallationTests(unittest.TestCase):
    def test_execute_one_row_hide_then_refresh(self):
        rows = [{"entry": entry, "installed": False} for entry in recipes()]
        updated = copy.deepcopy(rows)
        updated[1]["installed"] = True
        ui = Mock()
        ui.blocks.side_effect = [1, len(rows) + 1]
        order = []
        ui.clear.side_effect = lambda: order.append("clear")
        with (
            patch.object(tui, "inventory", side_effect=[rows, updated]) as inv,
            patch.object(
                tui, "execute", side_effect=lambda *a, **kw: order.append("execute")
            ) as execute,
        ):
            tui.installation_list(ui, fixture())
        self.assertEqual(order, ["clear", "execute"])
        self.assertEqual(inv.call_count, 2)
        execute.assert_called_once_with("all", fixture(), items=[recipes()[1]])
        self.assertIn(1, ui.blocks.call_args.kwargs["disabled"])
        ui.external.assert_not_called()
        ui.text.assert_not_called()

    def test_failure_keeps_table_and_does_not_execute_next_row(self):
        rows = [{"entry": entry, "installed": False} for entry in recipes()]
        ui = Mock()
        ui.blocks.side_effect = [0, 8]
        with (
            patch.object(tui, "inventory", return_value=rows),
            patch.object(tui, "execute", side_effect=OSError("failed")) as execute,
        ):
            tui.installation_list(ui, fixture())
        execute.assert_called_once()
        ui.text.assert_called_once()

    def test_commands_fully_preserved_when_wrapped(self):
        rows = [{"entry": entry, "installed": False} for entry in recipes()]
        for width in (60, 80, 160):
            blocks = list_blocks(rows, width, 6)
            for row, block in zip(rows, blocks):
                # Value-coloured parts contain the command text; remove layout padding only.
                parts = [
                    line.parts[0][0][2:]
                    for line in block
                    if hasattr(line, "parts") and line.parts[0][1] == "command"
                ]
                self.assertIn(
                    row["entry"][2].replace(" ", ""), "".join(parts).replace(" ", "")
                )

    def test_local_blob_size_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root = home / ".ollama/models"
            manifest = root / "manifests/registry.ollama.ai/library/qwen3.8/latest"
            manifest.parent.mkdir(parents=True)
            (root / "blobs").mkdir()
            digest = "a" * 64
            blob = root / "blobs" / ("sha256-" + digest)
            blob.write_bytes(b"abc")
            item = {"digest": "sha256:" + digest, "size": 3}
            manifest.write_text(json.dumps({"config": item, "layers": [item]}))
            self.assertTrue(ollama_model(home, roots=[root]))
            blob.write_bytes(b"ab")
            self.assertFalse(ollama_model(home, roots=[root]))

    def test_mtp_metadata_and_missing_shards(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "model-00001-of-00002.gguf"
            sample_gguf(p)
            self.assertTrue(embedded_mtp(p))
            self.assertFalse(gguf_set(p))
            sample_gguf(p.with_name("model-00002-of-00002.gguf"))
            self.assertTrue(gguf_set(p))
            sample_gguf(p, False)
            self.assertFalse(embedded_mtp(p))

    def test_terminal_table_selects_only_missing_row(self):
        script = """from llm_service_manager.tui import UI
ui=UI()
rows=[{'entry':('ollama','Install','true'),'installed':True},{'entry':('llama.cpp','Download','true'),'installed':False}]
from llm_service_manager.install_guide import choose_installation
choice=choose_installation(ui, rows)
ui.finish()
assert choice==1,choice
print('SELECTED_MISSING')
"""
        code, out = interact(
            [sys.executable, "-c", script], [("✓ 已安装".encode(), b"\r\x1b[B\r")]
        )
        self.assertEqual(code, 0)
        self.assertIn(b"SELECTED_MISSING", out)


class ListTests(unittest.TestCase):
    def test_bulk_installs_only_missing_in_display_order_and_refreshes(self):
        rows = [
            {"entry": entry, "installed": n in (0, 2, 4)}
            for n, entry in enumerate(recipes())
        ]
        done = [dict(row, installed=True) for row in rows]
        ui = Mock()
        ui.blocks.side_effect = [len(rows), len(rows) + 1]
        with (
            patch.object(tui, "inventory", side_effect=[rows, done]),
            patch.object(tui, "execute") as execute,
        ):
            tui.installation_list(ui, fixture())
        execute.assert_called_once_with(
            "all", fixture(), items=[recipes()[i] for i in (1, 3, 5, 6)]
        )
        ui.clear.assert_called_once()
        ui.text.assert_not_called()
        self.assertEqual(
            ui.blocks.call_args.kwargs["disabled"], list(range(len(rows) + 1))
        )

    def test_list_failure_returns_without_retry(self):
        rows = [{"entry": entry, "installed": False} for entry in recipes()]
        ui = Mock()
        ui.blocks.side_effect = [7, 8]
        with (
            patch.object(tui, "inventory", return_value=rows),
            patch.object(tui, "execute", side_effect=OSError("failed")) as execute,
        ):
            tui.installation_list(ui, fixture())
        execute.assert_called_once()
        ui.text.assert_called_once()

    def test_list_width_preserves_command_and_cancel_last(self):
        from llm_service_manager.install_guide import list_blocks

        rows = [{"entry": entry, "installed": False} for entry in recipes()]
        blocks = list_blocks(rows, 60, 0)
        for row, block in zip(rows, blocks):
            text = "".join(
                part[0][2:]
                for line in block
                if hasattr(line, "parts")
                for part in [line.parts[0]]
                if part[1] == "command"
            )
            self.assertEqual(text, row["entry"][2])
        self.assertIn("自动安装全部项", str(blocks[-2][0]))
        self.assertIn("返回", str(blocks[-1][0]))

    def test_list_keyboard_bulk_action(self):
        script = """from llm_service_manager.tui import UI
ui=UI();rows=[{'entry':('ollama','Install','true'),'installed':False}]
from llm_service_manager.install_guide import choose_installation
choice=choose_installation(ui, rows)
ui.finish()
assert choice==1,choice
print('BULK_SELECTED')
"""
        code, out = interact(
            [sys.executable, "-c", script], [("Bash pipefail".encode(), b"\x1b[B\r")]
        )
        self.assertEqual(code, 0)
        self.assertIn(b"BULK_SELECTED", out)
