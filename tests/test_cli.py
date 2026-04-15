from __future__ import annotations

import argparse
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
