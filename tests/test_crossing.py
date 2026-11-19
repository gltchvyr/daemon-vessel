import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from daemon_vessel import crossing


class CrossingTests(unittest.TestCase):
    def event(self, name="one", *, fidelity="exact_bytes_unverified"):
        raw = f"payload-{name}".encode("utf-8")
        return {
            "schema": "gltch.house-event/0.1.0",
            "event_id": f"event:{name}",
            "occurred_at": "2026-11-18T12:00:00Z",
            "ingested_at": "2026-11-18T12:00:01Z",
            "source": {
                "surface_id": "surface:test",
                "room_id": "room:test",
                "thread_id": "thread:test",
                "native_event_id": f"native:{name}",
            },
            "actor": {
                "actor_id": "actor:synthetic-human",
                "speaker_position": "user",
                "runtime_id": "runtime:test",
                "runtime_evidence": "reported",
            },
            "content": {
                "media_type": "text/plain",
                "fidelity": fidelity,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "inline": raw.decode("utf-8"),
                "ref": None,
                "language": "en",
            },
            "privacy_class": "relational_private",
            "authority_class": "historical_evidence",
            "lineage": {
                "reply_to": [],
                "corrects": [],
                "supersedes": [],
                "derived_from": [],
            },
            "attachments": [],
            "continuity": {"candidate_delta_ref": None, "mutation_authorized": False},
            "transport": {
                "mode": "synthetic",
                "receipt_id": None,
                "raw_bytes_available": fidelity == "exact_bytes_verified",
                "rendered_content_available": fidelity == "rendered_content",
            },
        }

    def packet(self):
        return {
            "schema": "gltch.context-packet/0.1.0",
            "packet_id": "packet:test",
            "orientation": "Synthetic wake material.",
            "purpose": "synthetic validation",
            "created_at": "2026-11-18T12:00:00Z",
            "sha256": "1" * 64,
            "access_index_nodes": ["entry:test"],
            "registry_resources": ["resource:test"],
            "sources": [{
                "event_id": "event:one",
                "actor_id": "actor:synthetic-human",
                "speaker_position": "user",
                "source_locator": "house://surface-test/room-test/event-one",
                "authority_class": "historical_evidence",
                "provenance": "synthetic",
                "fidelity": "exact_bytes_unverified",
            }],
            "corrections": [],
            "open_commitments": [],
            "unresolved": [],
            "runtime_seam": [{"runtime_id": "runtime:test", "evidence": "reported"}],
            "budgets": {"hops": 1, "resources": 2, "tokens": 100},
            "omissions": [],
            "compaction_ledger": [{
                "operation": "none",
                "source_refs": ["event:one"],
                "output_ref": None,
                "fidelity_loss": "none",
            }],
            "mutation_authorized": False,
            "construction_receipt_id": "receipt:test",
        }

    def run_cli(self, *args, env=None):
        command = [sys.executable, "-m", "daemon_vessel", *args]
        return subprocess.run(
            command,
            cwd=pathlib.Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_all_fidelity_states_and_exact_rendered_invariants(self):
        states = [
            "exact_bytes_verified",
            "exact_bytes_unverified",
            "rendered_content",
            "user_relayed",
            "derived_summary",
            "unavailable",
        ]
        for state in states:
            with self.subTest(state=state):
                self.assertEqual(crossing.validate_event(self.event(fidelity=state))["content"]["fidelity"], state)
        invalid = self.event(fidelity="exact_bytes_verified")
        invalid["transport"]["raw_bytes_available"] = False
        with self.assertRaisesRegex(crossing.CrossingError, "exact bytes"):
            crossing.validate_event(invalid)
        invalid = self.event(fidelity="rendered_content")
        invalid["transport"]["raw_bytes_available"] = True
        with self.assertRaisesRegex(crossing.CrossingError, "rendered content"):
            crossing.validate_event(invalid)
        invalid = self.event(fidelity="rendered_content")
        invalid["transport"]["rendered_content_available"] = False
        with self.assertRaisesRegex(crossing.CrossingError, "rendered content"):
            crossing.validate_event(invalid)

    def test_schemas_reject_untyped_nested_values_and_invalid_datetime(self):
        invalid = self.event()
        invalid["actor"]["actor_id"] = {"private": "SENTINEL"}
        with self.assertRaises(crossing.CrossingError) as caught:
            crossing.validate_event(invalid)
        self.assertNotIn("SENTINEL", str(caught.exception))
        invalid = self.event()
        invalid["occurred_at"] = "not-a-date"
        with self.assertRaises(crossing.CrossingError):
            crossing.validate_event(invalid)

    def test_external_schema_reference_refuses_without_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            schema_dir = pathlib.Path(directory)
            (schema_dir / "house-event.schema.json").write_text(json.dumps({
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "gltch.house-event/0.1.0",
                "$ref": "https://example.invalid/private-schema.json",
            }), encoding="utf-8")
            with mock.patch.object(crossing, "SCHEMAS", schema_dir):
                with self.assertRaisesRegex(crossing.CrossingError, "external reference"):
                    crossing.validate_event(self.event())

    def test_mutation_authority_inline_ref_and_lineage_refusals(self):
        invalid = self.event()
        invalid["continuity"]["mutation_authorized"] = True
        with self.assertRaisesRegex(crossing.CrossingError, "canonical-mutation"):
            crossing.validate_event(invalid)
        invalid = self.event()
        invalid["content"]["ref"] = "blob:test"
        with self.assertRaisesRegex(crossing.CrossingError, "exactly one"):
            crossing.validate_event(invalid)
        invalid = self.event()
        invalid["lineage"]["reply_to"] = [invalid["event_id"]]
        with self.assertRaisesRegex(crossing.CrossingError, "itself"):
            crossing.validate_event(invalid)
        invalid = self.event()
        invalid["lineage"]["derived_from"] = ["event:other", "event:other"]
        with self.assertRaises(crossing.CrossingError):
            crossing.validate_event(invalid)

    def test_actor_runtime_separation_and_packet_validation(self):
        value = self.event()
        value["actor"]["actor_id"] = "actor:synthetic-daemon"
        value["actor"]["runtime_id"] = "runtime:unknown"
        accepted = crossing.validate_event(value)
        self.assertEqual(accepted["actor"]["actor_id"], "actor:synthetic-daemon")
        self.assertEqual(accepted["actor"]["runtime_id"], "runtime:unknown")
        self.assertEqual(crossing.validate_packet(self.packet())["packet_id"], "packet:test")
        invalid = self.packet()
        invalid["mutation_authorized"] = True
        with self.assertRaisesRegex(crossing.CrossingError, "canonical-mutation"):
            crossing.validate_packet(invalid)
        invalid = self.packet()
        invalid["sources"][0]["source_locator"] = "https://user:secret@example.invalid/private"
        with self.assertRaisesRegex(crossing.CrossingError, "unsafe packet source"):
            crossing.validate_packet(invalid)

    def test_local_and_credential_bearing_locator_refusals(self):
        for locator in ("../secret", "..\\secret", "C:\\secret", "\\\\server\\share"):
            invalid = self.event()
            invalid["attachments"] = [{
                "asset_id": "asset:test",
                "media_type": "image/png",
                "sha256": None,
                "locator_state": "local",
                "locator": locator,
            }]
            with self.subTest(locator=locator), self.assertRaises(crossing.CrossingError):
                crossing.validate_event(invalid)
        invalid = self.event()
        invalid["attachments"] = [{
            "asset_id": "asset:test",
            "media_type": "image/png",
            "sha256": None,
            "locator_state": "remote",
            "locator": "https://user:password@example.invalid/asset",
        }]
        with self.assertRaises(crossing.CrossingError):
            crossing.validate_event(invalid)
        invalid = self.event()
        invalid["attachments"] = [{
            "asset_id": "asset:test",
            "media_type": "image/png",
            "sha256": None,
            "locator_state": "remote",
            "locator": "https://example.invalid/asset?X-Amz-Signature=synthetic",
        }]
        with self.assertRaises(crossing.CrossingError):
            crossing.validate_event(invalid)
        invalid = self.event()
        invalid["attachments"] = [{
            "asset_id": "asset:test",
            "media_type": "image/png",
            "sha256": None,
            "locator_state": "remote",
            "locator": None,
        }]
        with self.assertRaisesRegex(crossing.CrossingError, "requires a locator"):
            crossing.validate_event(invalid)
        invalid = self.event()
        invalid["content"]["inline"] = None
        invalid["content"]["ref"] = "file:///synthetic/private"
        with self.assertRaisesRegex(crossing.CrossingError, "unsafe content"):
            crossing.validate_event(invalid)

    def test_read_snapshot_once_and_digest_text(self):
        with mock.patch.object(pathlib.Path, "read_bytes", return_value=b"{}") as read:
            raw, value = crossing.read_json_snapshot("synthetic.json", "synthetic")
        self.assertEqual(raw, b"{}")
        self.assertEqual(value, {})
        self.assertEqual(read.call_count, 1)
        with self.assertRaisesRegex(crossing.CrossingError, "lowercase SHA-256"):
            crossing.validate_expected_sha256("ABC")

    def test_append_duplicate_and_integrity_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            first = crossing.ingest(self.event(), home=directory)
            duplicate = crossing.ingest(self.event(), home=directory)
            result = crossing.verify(home=directory)
        self.assertEqual(first["outcome"], "success")
        self.assertEqual(duplicate["outcome"], "duplicate")
        self.assertEqual(duplicate["original_receipt_id"], first["receipt_id"])
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual((result["events"], result["receipts"]), (1, 2))

    def test_divergent_event_id_and_source_key_collisions_dead_letter(self):
        with tempfile.TemporaryDirectory() as directory:
            crossing.ingest(self.event(), home=directory)
            changed = self.event()
            changed["source"]["native_event_id"] = "native:other"
            changed["actor"]["actor_id"] = "actor:other"
            with self.assertRaisesRegex(crossing.CrossingError, "event-ID"):
                crossing.ingest(changed, home=directory)
            changed = self.event("other")
            changed["source"]["native_event_id"] = "native:one"
            with self.assertRaisesRegex(crossing.CrossingError, "source-key"):
                crossing.ingest(changed, home=directory)
            result = crossing.verify(home=directory)
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["dead_letters"], 2)

    def test_changed_actor_is_not_an_idempotent_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            crossing.ingest(self.event(), home=directory)
            changed = self.event()
            changed["actor"]["actor_id"] = "actor:other"
            with self.assertRaises(crossing.CrossingError):
                crossing.ingest(changed, home=directory)

    def test_correction_and_supersession_preserve_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            crossing.ingest(self.event("original"), home=directory)
            correction = self.event("correction")
            correction["lineage"]["corrects"] = ["event:original"]
            crossing.ingest(correction, home=directory)
            replacement = self.event("replacement")
            replacement["lineage"]["supersedes"] = ["event:correction"]
            crossing.ingest(replacement, home=directory)
            ledgers = list((pathlib.Path(directory) / "crossings" / "events").glob("*/*/*.jsonl"))
            records = [json.loads(line) for line in ledgers[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["event_id"] for record in records], [
            "event:original", "event:correction", "event:replacement",
        ])

    def test_injected_receipt_failure_rolls_back_and_dead_letters(self):
        with tempfile.TemporaryDirectory() as directory:
            original = crossing._publish_staged
            calls = 0
            def fail_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected")
                return original(source, destination)
            with mock.patch.object(crossing, "_publish_staged", side_effect=fail_second):
                with self.assertRaisesRegex(crossing.CrossingError, "transaction failed"):
                    crossing.ingest(self.event(), home=directory)
            result = crossing.verify(home=directory)
        self.assertEqual((result["events"], result["receipts"], result["dead_letters"]), (0, 0, 1))

    def test_crash_between_publications_is_recovered_as_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            original = crossing._publish_staged
            calls = 0
            def crash_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise KeyboardInterrupt()
                return original(source, destination)
            with mock.patch.object(crossing, "_publish_staged", side_effect=crash_second):
                with self.assertRaises(KeyboardInterrupt):
                    crossing.ingest(self.event(), home=directory)
            result = crossing.verify(home=directory)
        self.assertEqual((result["events"], result["receipts"]), (0, 0))
        self.assertEqual(result["transactions_recovered"]["rolled_back"], 1)

    def test_crash_after_both_publications_is_recovered_as_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            original = crossing._cleanup_transaction
            calls = 0
            def crash_cleanup(journal, members):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise KeyboardInterrupt()
                return original(journal, members)
            with mock.patch.object(crossing, "_cleanup_transaction", side_effect=crash_cleanup):
                with self.assertRaises(KeyboardInterrupt):
                    crossing.ingest(self.event(), home=directory)
            result = crossing.verify(home=directory)
        self.assertEqual((result["events"], result["receipts"]), (1, 1))
        self.assertEqual(result["transactions_recovered"]["committed"], 1)

    def test_truncated_tail_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = pathlib.Path(directory) / "crossings/events/2026/11/events.jsonl"
            ledger.parent.mkdir(parents=True)
            ledger.write_bytes(b'{"schema":')
            with self.assertRaisesRegex(crossing.CrossingError, "truncated tail"):
                crossing.verify(home=directory)

    def test_stale_lock_recovers_and_live_lock_refuses(self):
        invalid_pid = OSError("invalid process identifier")
        invalid_pid.winerror = 87
        with mock.patch.object(crossing.os, "name", "nt"), mock.patch.object(
            crossing.os, "kill", side_effect=invalid_pid
        ):
            self.assertFalse(crossing._pid_alive(99999999))
        with tempfile.TemporaryDirectory() as directory:
            lock = pathlib.Path(directory) / "crossings/.ledger.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({
                "pid": 99999999,
                "created_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat(),
                "token": "stale",
            }), encoding="utf-8")
            self.assertEqual(crossing.ingest(self.event(), home=directory)["outcome"], "success")
            lock.write_text(json.dumps({
                "pid": os.getpid(),
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "token": "live",
            }), encoding="utf-8")
            with self.assertRaisesRegex(crossing.CrossingError, "busy"):
                crossing.verify(home=directory)

    def test_repository_worktree_and_symlink_escape_refuse(self):
        with self.assertRaisesRegex(crossing.CrossingError, "filesystem root"):
            crossing.resolve_home(pathlib.Path(pathlib.Path.cwd().anchor))
        inside = pathlib.Path(crossing.REPOSITORY_ROOT) / "synthetic-continuity-home"
        with self.assertRaisesRegex(crossing.CrossingError, "inside the repository"):
            crossing.resolve_home(inside)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / ".git").mkdir()
            with self.assertRaisesRegex(crossing.CrossingError, "Git worktree"):
                crossing.resolve_home(root / "continuity")
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            home = pathlib.Path(directory)
            try:
                (home / "crossings").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(crossing.CrossingError, "escapes continuity home"):
                crossing.ingest(self.event(), home=home)
            self.assertFalse((pathlib.Path(outside) / ".ledger.lock").exists())

    def test_receipt_tampering_and_missing_artifact_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            crossing.ingest(self.event(), home=directory)
            receipt = next((pathlib.Path(directory) / "crossings/receipts").glob("*/*/*.json"))
            value = json.loads(receipt.read_text(encoding="utf-8"))
            value["content_sha256"] = "f" * 64
            receipt.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(crossing.CrossingError, "digest disagree"):
                crossing.verify(home=directory)
        with tempfile.TemporaryDirectory() as directory:
            crossing.ingest(self.event(), home=directory)
            ledger = next((pathlib.Path(directory) / "crossings/events").glob("*/*/*.jsonl"))
            ledger.unlink()
            with self.assertRaisesRegex(crossing.CrossingError, "missing artifact"):
                crossing.verify(home=directory)

    def test_duplicate_and_replay_receipt_relationships_are_attributable(self):
        with tempfile.TemporaryDirectory() as directory:
            first = crossing.ingest(self.event(), home=directory)
            crossing.ingest(self.event(), home=directory)
            receipts = sorted((pathlib.Path(directory) / "crossings/receipts").glob("*/*/*.json"))
            duplicate_path = next(
                path for path in receipts
                if json.loads(path.read_text(encoding="utf-8"))["outcome"] == "duplicate"
            )
            duplicate = json.loads(duplicate_path.read_text(encoding="utf-8"))
            duplicate["original_receipt_id"] = "receipt:missing"
            duplicate_path.write_text(json.dumps(duplicate) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(crossing.CrossingError, "unknown relation"):
                crossing.verify(home=directory)
        replay = crossing._receipt(
            when=dt.datetime.now(dt.timezone.utc),
            outcome="success",
            operation="replay",
            event=self.event(),
        )
        replay["artifact_paths"] = ["crossings/receipts/synthetic.json"]
        with self.assertRaisesRegex(crossing.CrossingError, "replay receipt"):
            crossing.validate_receipt(replay)
        replay["replay_of"] = first["receipt_id"]
        crossing.validate_receipt(replay)
        with tempfile.TemporaryDirectory() as directory:
            event = self.event()
            first = crossing.ingest(event, home=directory)
            paths = crossing.resolve_home(directory)
            replay = crossing._receipt(
                when=dt.datetime.now(dt.timezone.utc),
                outcome="success",
                operation="replay",
                event=event,
                replay_of=first["receipt_id"],
            )
            crossing._write_standalone_receipt(paths, dt.datetime.now(dt.timezone.utc), replay)
            result = crossing.verify(home=directory)
        self.assertEqual((result["events"], result["receipts"]), (1, 2))

    def test_cli_help_append_and_private_nonleak(self):
        help_result = self.run_cli("crossing", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            event_path = root / "event.json"
            event_path.write_text(json.dumps(self.event()), encoding="utf-8")
            digest = hashlib.sha256(event_path.read_bytes()).hexdigest()
            append = self.run_cli(
                "crossing", "append", "--source", str(event_path),
                "--expect-sha256", digest, "--home", str(root / "home"),
            )
            self.assertEqual(append.returncode, 0, append.stderr)
            invalid = self.event()
            invalid["actor"]["actor_id"] = {"secret": "PRIVATE_SENTINEL"}
            event_path.write_text(json.dumps(invalid), encoding="utf-8")
            refused = self.run_cli("crossing", "validate", "--source", str(event_path))
            self.assertNotEqual(refused.returncode, 0)
            self.assertNotIn("PRIVATE_SENTINEL", refused.stderr + refused.stdout)

    def test_cli_malformed_inputs_and_digest_refuse_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "event.json"
            source.write_text("{", encoding="utf-8")
            result = self.run_cli("crossing", "validate", "--source", str(source))
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            source.write_text(json.dumps(self.event()), encoding="utf-8")
            result = self.run_cli(
                "crossing", "append", "--source", str(source),
                "--expect-sha256", "NOT-A-DIGEST", "--home", str(pathlib.Path(directory) / "home"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)

    def test_schema_files_are_packaged_utf8_lf_and_valid(self):
        for name in (
            "house-event.schema.json",
            "crossing-receipt.schema.json",
            "context-packet.schema.json",
        ):
            path = pathlib.Path(crossing.SCHEMAS) / name
            content = path.read_bytes()
            if os.name != "nt":
                self.assertNotIn(b"\r\n", content)
                self.assertTrue(content.endswith(b"\n"))
            json.loads(content.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
