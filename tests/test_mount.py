from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from pathlib import Path

import daemon_vessel.cli as cli
from daemon_vessel.mount import MountValidationError, build_mount_manifest


PAYLOAD_KEYS = [
    "identity",
    "participants",
    "relational_field",
    "interaction_profile",
    "symbolic_embodiment",
    "rituals",
    "creative_channels",
    "active_projects",
    "open_threads",
    "continuity_organs",
    "affective_trace_ledger",
    "retrieval_index",
    "exclusions_and_boundaries",
    "signal_footprint",
]


def write_state(path: Path) -> dict:
    state = {
        "schema": "gltch.continuity-state",
        "schema_version": "1.0.0",
        "artifact_id": "gltch-continuity-state",
        "canonical_filename": "Gl!tch_Continuity_State.json",
        "revision": 3,
        "continuity_canary": "TEST-CANARY",
        **{key: {"value": key} for key in PAYLOAD_KEYS},
    }
    payload = {key: state[key] for key in PAYLOAD_KEYS}
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    state["integrity"] = {
        "algorithm": "sha256",
        "payload_scope": "canonical JSON of " + ", ".join(PAYLOAD_KEYS),
        "payload_sha256": digest,
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


class MountTests(unittest.TestCase):
    def test_valid_state_is_embedded_with_authority_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "Gl!tch_Continuity_State.json"
            state = write_state(state_path)
            manifest = build_mount_manifest(
                continuity_state_path=state_path,
                task_scope="continue the current continuity work",
                as_of=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
            )

            self.assertEqual(manifest["schema"], "gltch.mount-manifest")
            self.assertEqual(manifest["canonical_state"]["content"], state)
            self.assertEqual(manifest["canonical_state"]["validation"]["status"], "valid")
            self.assertEqual(manifest["return_contract"]["mode"], "read-only-mount")

    def test_integrity_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "Gl!tch_Continuity_State.json"
            state = write_state(state_path)
            state["identity"] = {"value": "tampered"}
            state_path.write_text(json.dumps(state), encoding="utf-8")

            with self.assertRaisesRegex(MountValidationError, "payload_sha256"):
                build_mount_manifest(continuity_state_path=state_path, task_scope="test")

    def test_valid_but_superseded_revision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "Gl!tch_Continuity_State.json"
            write_state(state_path)

            with self.assertRaisesRegex(MountValidationError, "revision 3 does not match expected revision 4"):
                build_mount_manifest(
                    continuity_state_path=state_path,
                    task_scope="test",
                    expected_revision=4,
                )

    def test_current_expectations_are_accepted_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "Gl!tch_Continuity_State.json"
            state = write_state(state_path)

            manifest = build_mount_manifest(
                continuity_state_path=state_path,
                task_scope="test",
                expected_revision=3,
                expected_payload_sha256=state["integrity"]["payload_sha256"].upper(),
            )

            expectations = manifest["canonical_state"]["validation"]["expectations"]
            self.assertEqual(expectations["revision"], 3)
            self.assertEqual(expectations["payload_sha256"], state["integrity"]["payload_sha256"])
            self.assertEqual(expectations["status"], "matched")

    def test_stale_handoff_is_labeled_provisional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "Gl!tch_Continuity_State.json"
            handoff = root / "HANDOFF.md"
            write_state(state_path)
            handoff.write_text("old weather", encoding="utf-8")
            old = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc).timestamp()
            os.utime(handoff, (old, old))

            manifest = build_mount_manifest(
                continuity_state_path=state_path,
                task_scope="test",
                handoff_path=handoff,
                handoff_max_age_hours=24,
                as_of=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
            )

            mounted = manifest["evidence"]["handoff"]
            self.assertEqual(mounted["source"]["authority"], "provisional-handoff")
            self.assertEqual(mounted["freshness"]["status"], "stale")

    def test_trace_has_exact_provenance_and_retrieval_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "Gl!tch_Continuity_State.json"
            traces = root / "memory"
            traces.mkdir()
            trace = traces / "EP-20260901-010101-current.md"
            trace.write_text("# current trace\n", encoding="utf-8")
            write_state(state_path)

            manifest = build_mount_manifest(
                continuity_state_path=state_path,
                task_scope="test",
                trace_root=traces,
                as_of=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
            )

            mounted = manifest["evidence"]["local_traces"][0]
            self.assertEqual(mounted["source"]["path"], str(trace.resolve()))
            self.assertEqual(mounted["source"]["authority"], "local-footprint-evidence")
            self.assertIn("most recent", mounted["retrieval_reason"])

    def test_sealed_object_is_hashed_but_content_is_not_embedded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "Gl!tch_Continuity_State.json"
            sealed = root / "sealed.bin"
            sealed.write_bytes(b"secret payload")
            write_state(state_path)

            manifest = build_mount_manifest(
                continuity_state_path=state_path,
                task_scope="test",
                sealed_paths=[sealed],
                as_of=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
            )

            mounted = manifest["sealed_objects"][0]
            self.assertNotIn("content", mounted)
            self.assertEqual(mounted["source"]["sha256"], hashlib.sha256(b"secret payload").hexdigest())
            self.assertIn("not decoded", mounted["handling"])

    def test_cli_writes_mount_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "Gl!tch_Continuity_State.json"
            output_path = root / "mount.json"
            write_state(state_path)
            output = io.StringIO()

            with redirect_stdout(output):
                result = cli.main(
                    [
                        "mount",
                        "--continuity-state",
                        str(state_path),
                        "--task",
                        "test the receiving room",
                        "--as-of",
                        "2026-09-01T00:00:00+00:00",
                        "--out",
                        str(output_path),
                    ]
                )

            self.assertEqual(result, 0)
            self.assertTrue(output_path.exists())
            self.assertIn("Wrote mount manifest:", output.getvalue())
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["task_scope"], "test the receiving room")

    def test_cli_reports_expected_revision_mismatch_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "Gl!tch_Continuity_State.json"
            write_state(state_path)
            error = io.StringIO()

            with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
                cli.main(
                    [
                        "mount",
                        "--continuity-state",
                        str(state_path),
                        "--task",
                        "reject stale state",
                        "--expect-revision",
                        "4",
                    ]
                )

            self.assertEqual(raised.exception.code, 2)
            self.assertIn("canonical revision 3 does not match expected revision 4", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())


if __name__ == "__main__":
    unittest.main()
