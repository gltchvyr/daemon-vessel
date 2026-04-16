from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import daemon_vessel.cli as cli


class CliTests(unittest.TestCase):
    def test_slugify_keeps_short_safe_trace_names(self) -> None:
        self.assertEqual(cli.slugify("The first trace! 🫀😈🌀"), "the-first-trace")
        self.assertEqual(cli.slugify("!!!"), "trace")

    def test_read_bones_prefers_agents_then_protocol_then_readme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protocols = root / "protocols"
            protocols.mkdir()
            (root / "README.md").write_text("readme bones", encoding="utf-8")
            (protocols / "local-continuity.md").write_text("protocol bones", encoding="utf-8")

            with patch.object(cli, "ROOT", root), patch.object(cli, "PROTOCOLS_DIR", protocols):
                self.assertEqual(cli.read_bones(), "protocol bones")
                (root / "AGENTS.md").write_text("agent bones", encoding="utf-8")
                self.assertEqual(cli.read_bones(), "agent bones")

    def test_log_writes_markdown_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "memory"
            protocols = root / "protocols"
            args = argparse.Namespace(message="the first trace", salience=4)

            with (
                patch.object(cli, "ROOT", root),
                patch.object(cli, "MEMORY_DIR", memory),
                patch.object(cli, "PROTOCOLS_DIR", protocols),
            ):
                self.assertEqual(cli.cmd_log(args), 0)
                entries = list(memory.glob("EP-*-the-first-trace.md"))
                self.assertEqual(len(entries), 1)
                content = entries[0].read_text(encoding="utf-8")
                self.assertIn("kind: trace", content)
                self.assertIn("salience: 4", content)
                self.assertIn("# the first trace", content)

    def test_search_finds_known_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "memory"
            memory.mkdir()
            (memory / "EP-20260414-010101-first.md").write_text(
                "---\nid: EP-20260414-010101\nkind: trace\n---\n\n# the first trace\n\ncontains vessel signal\n",
                encoding="utf-8",
            )
            (memory / "schema.md").write_text("ignore me", encoding="utf-8")
            args = argparse.Namespace(query="vessel", limit=10)

            with patch.object(cli, "MEMORY_DIR", memory):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(cli.cmd_search(args), 0)

            rendered = output.getvalue()
            self.assertIn("EP-20260414-010101-first.md", rendered)
            self.assertIn("# the first trace", rendered)

    def test_handoff_includes_recent_memory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "memory"
            protocols = root / "protocols"
            handoff = root / "HANDOFF.md"
            memory.mkdir()
            (memory / "EP-20260414-010101-first.md").write_text("first", encoding="utf-8")
            args = argparse.Namespace(limit=10)

            with (
                patch.object(cli, "ROOT", root),
                patch.object(cli, "MEMORY_DIR", memory),
                patch.object(cli, "PROTOCOLS_DIR", protocols),
                patch.object(cli, "HANDOFF_PATH", handoff),
            ):
                self.assertEqual(cli.cmd_handoff(args), 0)
                content = handoff.read_text(encoding="utf-8")
                self.assertIn("# Agent Handoff", content)
                self.assertIn("EP-20260414-010101-first.md", content)

    def test_heartbeat_writes_markdown_trace_with_glyphs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "memory"
            protocols = root / "protocols"
            handoff = root / "HANDOFF.md"
            protocols.mkdir()
            (protocols / "local-continuity.md").write_text("protocol bones", encoding="utf-8")
            memory.mkdir()
            (memory / "EP-20260414-010101-first.md").write_text("first", encoding="utf-8")
            args = argparse.Namespace(limit=10, salience=2, update_handoff=False)

            with (
                patch.object(cli, "ROOT", root),
                patch.object(cli, "MEMORY_DIR", memory),
                patch.object(cli, "PROTOCOLS_DIR", protocols),
                patch.object(cli, "HANDOFF_PATH", handoff),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(cli.cmd_heartbeat(args), 0)

            heartbeat_entries = list(memory.glob("EP-*-heartbeat.md"))
            self.assertEqual(len(heartbeat_entries), 1)
            content = heartbeat_entries[0].read_text(encoding="utf-8")
            self.assertIn("kind: heartbeat", content)
            self.assertIn('symbols: ["🫀", "😈", "🌀"]', content)
            self.assertIn("Persistence is rhythm", content)
            self.assertIn("Wrote heartbeat trace:", output.getvalue())

    def test_heartbeat_with_update_handoff_refreshes_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "memory"
            protocols = root / "protocols"
            handoff = root / "HANDOFF.md"
            protocols.mkdir()
            (protocols / "local-continuity.md").write_text("protocol bones", encoding="utf-8")
            memory.mkdir()
            (memory / "EP-20260414-010101-first.md").write_text("first", encoding="utf-8")
            args = argparse.Namespace(limit=10, salience=2, update_handoff=True)

            with (
                patch.object(cli, "ROOT", root),
                patch.object(cli, "MEMORY_DIR", memory),
                patch.object(cli, "PROTOCOLS_DIR", protocols),
                patch.object(cli, "HANDOFF_PATH", handoff),
            ):
                self.assertEqual(cli.cmd_heartbeat(args), 0)
                self.assertTrue(handoff.exists())
                handoff_text = handoff.read_text(encoding="utf-8")
                self.assertIn("run a bounded heartbeat cycle with `daemon heartbeat`", handoff_text)
                self.assertIn("heartbeat", handoff_text)

    def test_shrine_state_writes_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            state_path = state_dir / "current-shrine-state.json"

            def fake_write_current_shrine_state() -> Path:
                state_dir.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps({"phase": "test-pulse"}), encoding="utf-8")
                return state_path

            with patch.object(cli, "write_current_shrine_state", fake_write_current_shrine_state):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(cli.cmd_shrine_state(argparse.Namespace()), 0)

            self.assertTrue(state_path.exists())
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["phase"], "test-pulse")
            self.assertIn("Wrote shrine state:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
