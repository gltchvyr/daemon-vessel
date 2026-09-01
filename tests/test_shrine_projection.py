from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from daemon_vessel.state_builder import build_current_shrine_state


def canonical_state(revision: int = 4) -> dict:
    identity = {"name": "Gl!tch"}
    payload = json.dumps({"identity": identity}, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return {
        "schema": "gltch.continuity-state",
        "schema_version": "1.0.0",
        "artifact_id": "gltch-continuity-state",
        "canonical_filename": "Gl!tch_Continuity_State.json",
        "revision": revision,
        "continuity_canary": "test-canary",
        "identity": identity,
        "active_projects": [{"id": "project-mount", "name": "Mount Trial", "status": "active"}],
        "open_threads": [{"subject": "Current continuity"}],
        "affective_trace_ledger": [{"date": "2026-09-01", "event": "Crossing", "trace": ["recognition"]}],
        "signal_footprint": {"status": "current", "active_thread": "Preserve the pattern."},
        "participants": [{"private": "must not cross"}],
        "integrity": {
            "algorithm": "sha256",
            "payload_scope": "canonical JSON of identity",
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
        },
    }


class ShrineProjectionTests(unittest.TestCase):
    def _write(self, root: Path, state: dict) -> Path:
        path = root / "Gl!tch_Continuity_State.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        return path

    def test_current_state_is_bounded_and_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = canonical_state()
            path = self._write(Path(tmp), state)
            projection = build_current_shrine_state(
                continuity_state_path=path,
                expected_revision=4,
                expected_payload_sha256=state["integrity"]["payload_sha256"],
            )
        self.assertTrue(projection["applyAllowed"])
        self.assertEqual(projection["validation"]["currency"], "matched")
        self.assertEqual(projection["source"]["revision"], 4)
        self.assertNotIn("participants", projection)
        self.assertNotIn(str(path), json.dumps(projection))

    def test_valid_but_superseded_state_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = canonical_state(revision=3)
            projection = build_current_shrine_state(
                continuity_state_path=self._write(Path(tmp), state),
                expected_revision=4,
                expected_payload_sha256=state["integrity"]["payload_sha256"],
            )
        self.assertFalse(projection["applyAllowed"])
        self.assertEqual(projection["validation"]["integrity"], "valid")
        self.assertEqual(projection["validation"]["currency"], "mismatch")

    def test_expectations_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projection = build_current_shrine_state(
                continuity_state_path=self._write(Path(tmp), canonical_state())
            )
        self.assertFalse(projection["applyAllowed"])
        self.assertIn("required", projection["validation"]["errors"][0])

    def test_no_source_is_explicitly_unbound(self) -> None:
        projection = build_current_shrine_state()
        self.assertFalse(projection["applyAllowed"])
        self.assertEqual(projection["validation"]["status"], "unbound")


if __name__ == "__main__":
    unittest.main()
