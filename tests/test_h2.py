import hashlib
import json
import tempfile
import concurrent.futures
import unittest
from pathlib import Path
from unittest import mock

from daemon_vessel import h2


class H2Tests(unittest.TestCase):
    def admission(self, command="pilot", challenge=None, message="synthetic-message"):
        return {
            "schema": "gltch.hermes-admission/0.1.0", "captured_at": h2._now() if command == "ack" else "2026-01-01T00:00:00Z",
            "platform": "discord", "command": command, "session_key_sha256": "a" * 64,
            "message_id": message + "-" + command,
            "source": {"chat_id": "synthetic-chat", "thread_id": "synthetic-thread", "chat_type": "dm", "actor_id": "actor:julie", "actor_mapping_evidence": "local_exact_user_id_match"},
            "body": "synthetic private body" if command == "pilot" else None,
            "challenge_id": challenge,
            "transport": {"raw_discord_bytes_available": False, "normalized_gateway_fields_available": True, "gateway_authorization_completed": True},
        }

    def invoke(self, value, home):
        source = Path(home).parent / "admission.json"
        raw = json.dumps(value).encode("utf-8")
        source.write_bytes(raw)
        return h2.process(source, hashlib.sha256(raw).hexdigest(), home)

    def files(self, home, kind):
        return list((Path(home) / "crossings" / "hermes" / kind).glob("*/*/*.json"))

    def test_full_chain_is_dated_immutable_and_verifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            first = self.invoke(self.admission(), home)
            challenge = first["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
            self.assertEqual(self.invoke(self.admission("ack", challenge), home)["status"], "handled")
            report = h2.verify(home)
            self.assertEqual(report["integrity"], "ok")
            self.assertEqual(report["h2_challenges"], 1)
            self.assertEqual(report["h2_observations"], 1)
            self.assertTrue(self.files(home, "packets"))
            self.assertTrue(self.files(home, "receipts"))
            self.assertFalse((home / "crossings" / "hermes" / "index.json").exists())

    def test_provenance_preserves_surface_native_thread_and_rendered_fidelity(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            reply = self.invoke(self.admission(), home)
            events = list((home / "crossings" / "events").glob("*/*/*.jsonl"))
            source = json.loads(events[0].read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(source["source"]["surface_id"], "surface:discord-hermes")
            self.assertEqual(source["source"]["native_event_id"], "synthetic-message-pilot")
            self.assertEqual(source["source"]["thread_id"], "synthetic-thread")
            self.assertTrue(source["source"]["room_id"].startswith("room:discord-local-"))
            self.assertEqual(source["content"]["fidelity"], "rendered_content")
            self.assertFalse(source["transport"]["raw_bytes_available"])

    def test_retry_and_every_publication_boundary_preserve_chain_ids(self):
        for phase in ("source-persisted", "packet-published", "return-persisted", "delivery-published"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "external-home"
                original = h2._advance
                def interrupt(paths, transaction, actual):
                    original(paths, transaction, actual)
                    if actual == phase:
                        raise RuntimeError("synthetic interruption")
                with mock.patch.object(h2, "_advance", side_effect=interrupt):
                    with self.assertRaises(RuntimeError):
                        self.invoke(self.admission(), home)
                resumed = self.invoke(self.admission(), home)
                self.assertIn("HOUSE-PILOT-ACK challenge:", resumed["reply"])
                self.assertEqual(h2.verify(home)["integrity"], "ok")

    def test_ack_observation_boundary_recovers_without_second_challenge(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            first = self.invoke(self.admission(), home)
            challenge = first["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
            original = h2._advance
            def interrupt(paths, transaction, phase):
                original(paths, transaction, phase)
                if phase == "observation-published":
                    raise RuntimeError("synthetic interruption")
            with mock.patch.object(h2, "_advance", side_effect=interrupt):
                with self.assertRaises(RuntimeError):
                    self.invoke(self.admission("ack", challenge), home)
            replay = self.invoke(self.admission("ack", challenge), home)
            self.assertEqual(replay["status"], "handled")
            self.assertEqual(h2.verify(home)["h2_challenges"], 1)

    def test_refuses_digest_schema_collision_cross_session_and_expired_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            source = Path(tmp) / "bad.json"; source.write_text("{}", encoding="utf-8")
            with self.assertRaises(h2.H2Error): h2.process(source, "0" * 64, home)
            reply = self.invoke(self.admission(), home)
            divergent = self.admission(); divergent["body"] = "different"
            with self.assertRaises(h2.H2Error): self.invoke(divergent, home)
            challenge = reply["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
            cross = self.admission("ack", challenge); cross["session_key_sha256"] = "b" * 64
            with self.assertRaises(h2.H2Error): self.invoke(cross, home)
            expired = self.admission("ack", challenge); expired["captured_at"] = "2099-01-01T00:16:00Z"
            with self.assertRaises(h2.H2Error): self.invoke(expired, home)

    def test_admission_schema_and_oversized_pilot_refuse_without_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            for field in ("captured_at", "session_key_sha256", "message_id", "source", "transport"):
                with self.subTest(field=field):
                    value = self.admission(); value.pop(field)
                    with self.assertRaises(h2.H2Error): self.invoke(value, home)
            oversized = self.admission(); oversized["body"] = "x" * 2_001
            with self.assertRaises(h2.H2Error): self.invoke(oversized, home)
            self.assertFalse(home.exists())

    def test_verifier_refuses_missing_delivery_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; self.invoke(self.admission(), home)
            delivery = next(path for path in self.files(home, "deliveries") if '"kind":"delivery"' in path.read_text(encoding="utf-8"))
            delivery.unlink()
            with self.assertRaises(h2.H2Error): h2.verify(home)

    def test_verifier_refuses_tampered_delivery_observation_and_phase_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; first = self.invoke(self.admission(), home)
            challenge = first["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
            self.invoke(self.admission("ack", challenge), home)
            targets = [
                next(path for path in self.files(home, "deliveries") if '"kind":"delivery"' in path.read_text(encoding="utf-8")),
                next(path for path in self.files(home, "observations") if '"kind":"observation"' in path.read_text(encoding="utf-8")),
                self.files(home, "transactions")[0],
            ]
            for target in targets:
                with self.subTest(target=target.name):
                    original = target.read_bytes(); target.write_text("{", encoding="utf-8")
                    with self.assertRaises(h2.H2Error): h2.verify(home)
                    target.write_bytes(original)

    def test_duplicate_and_wrong_ack_are_attributable_or_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; first = self.invoke(self.admission(), home)
            challenge = first["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
            self.assertEqual(self.invoke(self.admission("ack", challenge), home)["status"], "handled")
            self.assertEqual(self.invoke(self.admission("ack", challenge), home)["status"], "handled")
            wrong = self.admission("ack", "challenge:abcdefgh")
            with self.assertRaises(h2.H2Error): self.invoke(wrong, home)

    def test_verifier_refuses_tampered_truncated_missing_and_orphaned_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; self.invoke(self.admission(), home)
            packet = self.files(home, "packets")[0]; packet.write_text("{", encoding="utf-8")
            with self.assertRaises(h2.H2Error): h2.verify(home)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; self.invoke(self.admission(), home)
            self.files(home, "packets")[0].unlink()
            with self.assertRaises(h2.H2Error): h2.verify(home)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; self.invoke(self.admission(), home)
            orphan = home / "crossings" / "hermes" / "packets" / "2026" / "01" / "packet-orphan.json"
            orphan.parent.mkdir(parents=True, exist_ok=True)
            orphan.write_text("{}", encoding="utf-8")
            with self.assertRaises(h2.H2Error): h2.verify(home)

    def test_h2_lock_is_separate_from_h1_ingest_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            self.invoke(self.admission(), home)
            self.assertFalse((home / "crossings" / ".ledger.lock").exists())
            self.assertFalse((home / "crossings" / "hermes" / ".h2.lock").exists())

    def test_concurrent_and_repeated_pilot_replay_keep_one_random_challenge(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; value = self.admission()
            def call(number):
                source = Path(tmp) / f"admission-{number}.json"; raw = json.dumps(value).encode("utf-8")
                source.write_bytes(raw); return h2.process(source, hashlib.sha256(raw).hexdigest(), home)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                replies = list(pool.map(call, range(4)))
            challenges = {reply["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0] for reply in replies}
            self.assertEqual(len(challenges), 1); self.assertEqual(len(self.files(home, "packets")), 1)
            for _ in range(3): self.assertEqual(call(9)["status"], "handled")
            self.assertEqual(h2.verify(home)["h2_challenges"], 1)

    def test_verifier_refuses_missing_phase_semantic_tamper_and_dead_letter_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; self.invoke(self.admission(), home)
            phase = self.files(home, "transactions")[0]; phase.unlink()
            with self.assertRaises(h2.H2Error): h2.verify(home)

    def test_confirm_refuses_every_tampered_prepared_link(self):
        for target in ("packet", "construction", "phase"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "external-home"
                prepared = self.invoke(self.admission(), home) if False else None
                value = self.admission()
                source = Path(tmp) / "admission.json"; raw = json.dumps(value).encode(); source.write_bytes(raw)
                result = h2.process(source, hashlib.sha256(raw).hexdigest(), home, mode="prepare")
                challenge = result["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
                if target == "packet":
                    item = self.files(home, "packets")[0]; body = json.loads(item.read_text()); body["sha256"] = "0" * 64; item.write_text(json.dumps(body), encoding="utf-8")
                elif target == "construction":
                    item = self.files(home, "receipts")[0]; body = json.loads(item.read_text()); body["transaction_id"] = "transaction:h2-tampered"; body["sha256"] = h2._digest({k:v for k,v in body.items() if k != "sha256"}); item.write_text(json.dumps(body), encoding="utf-8")
                else:
                    item = self.files(home, "transactions")[0]; body = json.loads(item.read_text()); body["phase"] = "delivery-published"; item.write_text(json.dumps(body), encoding="utf-8")
                with self.assertRaises(h2.H2Error): h2.confirm(challenge, home)
                self.assertFalse(self.files(home, "deliveries"))

    def test_ack_before_delivery_and_delayed_confirm_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            value = self.admission(); source = Path(tmp) / "admission.json"; raw = json.dumps(value).encode(); source.write_bytes(raw)
            reply = h2.process(source, hashlib.sha256(raw).hexdigest(), home, mode="prepare")
            challenge = reply["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
            h2.confirm(challenge, home)
            early = self.admission("ack", challenge); early["captured_at"] = "2000-01-01T00:00:00Z"
            with self.assertRaises(h2.H2Error): self.invoke(early, home)
            self.assertEqual(self.invoke(self.admission("ack", challenge), home)["status"], "handled")

    def test_verifier_refuses_moved_receipt_and_dead_letter(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; self.invoke(self.admission(), home)
            delivery = self.files(home, "deliveries")[0]
            moved = home / "crossings" / "hermes" / "receipts" / delivery.relative_to(home / "crossings" / "hermes" / "deliveries")
            moved.parent.mkdir(parents=True, exist_ok=True); delivery.replace(moved)
            with self.assertRaises(h2.H2Error): h2.verify(home)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            with self.assertRaises(h2.H2Error): self.invoke(self.admission("ack", "challenge:abcdefgh"), home)
            dead = self.files(home, "dead-letter")[0]
            moved = dead.with_name("wrong.json"); dead.replace(moved)
            with self.assertRaises(h2.H2Error): h2.verify(home)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"; self.invoke(self.admission(), home)
            receipt = self.files(home, "deliveries")[0]; value = json.loads(receipt.read_text(encoding="utf-8")); value["outcome"] = "refused"; value["sha256"] = h2._digest({k:v for k,v in value.items() if k != "sha256"}); receipt.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(h2.H2Error): h2.verify(home)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            with self.assertRaises(h2.H2Error): self.invoke(self.admission("ack", "challenge:abcdefgh"), home)
            dead = self.files(home, "dead-letter")[0]; value = json.loads(dead.read_text(encoding="utf-8")); value["failure"] = "tampered"; dead.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(h2.H2Error): h2.verify(home)

    def test_unknown_ack_attempt_is_verifiable_then_moved_or_tampered_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            with self.assertRaises(h2.H2Error): self.invoke(self.admission("ack", "challenge:abcdefgh"), home)
            self.assertEqual(h2.verify(home)["integrity"], "ok")
            dead = self.files(home, "dead-letter")[0]
            original = dead.read_bytes()
            dead.with_name("wrong.json").write_bytes(original); dead.unlink()
            with self.assertRaises(h2.H2Error): h2.verify(home)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            with self.assertRaises(h2.H2Error): self.invoke(self.admission("ack", "challenge:abcdefgh"), home)
            dead = self.files(home, "dead-letter")[0]
            value = json.loads(dead.read_text(encoding="utf-8")); value["failure"] = "tampered"
            dead.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(h2.H2Error): h2.verify(home)

    def test_confirm_and_verify_bind_h1_actor_runtime_identity(self):
        for target, key, value in (
            ("source", "actor.actor_id", "actor:other"),
            ("source", "actor.runtime_id", "runtime:other"),
            ("return", "actor.speaker_position", "user"),
            ("return", "actor.runtime_id", "runtime:other"),
            ("return", "source.native_event_id", "synthetic-native"),
        ):
            with self.subTest(target=target, key=key), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "external-home"
                admission = self.admission(); source_path = Path(tmp) / "admission.json"; raw = json.dumps(admission).encode(); source_path.write_bytes(raw)
                prepared = h2.process(source_path, hashlib.sha256(raw).hexdigest(), home, mode="prepare")
                tx = h2._transactions(home / "crossings" / "hermes")[h2._event_id(admission["message_id"])]
                wanted = tx["source_event"] if target == "source" else tx["return_event"]
                for event_file in (home / "crossings" / "events").glob("*/*/*.jsonl"):
                    rows = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]
                    for row in rows:
                        if row["event_id"] == wanted:
                            parent, leaf = key.split(".")
                            row[parent][leaf] = value
                    event_file.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
                challenge = prepared["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
                with self.assertRaises(h2.H2Error): h2.confirm(challenge, home)

    def test_replay_receipt_links_are_resolved_and_transaction_bound(self):
        def replay_path(home):
            return next(path for path in self.files(home, "receipts") if json.loads(path.read_text(encoding="utf-8"))["kind"] == "replay")
        def redigest(path, original_id):
            value = json.loads(path.read_text(encoding="utf-8"))
            value["original_receipt_id"] = original_id
            value["sha256"] = h2._digest({key: item for key, item in value.items() if key != "sha256"})
            path.write_text(json.dumps(value), encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "external-home"
            first = self.invoke(self.admission(message="first"), home)
            challenge = first["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
            self.invoke(self.admission("ack", challenge, message="first-ack"), home)
            self.invoke(self.admission("ack", challenge, message="first-ack"), home)
            replay = replay_path(home)
            self.assertEqual(h2.verify(home)["integrity"], "ok")
            original = json.loads(replay.read_text(encoding="utf-8"))["original_receipt_id"]
            for case, replacement in (("missing", "receipt:missing"), ("self", None), ("wrong-kind", None)):
                with self.subTest(case=case):
                    if case == "self":
                        replacement = json.loads(replay.read_text(encoding="utf-8"))["receipt_id"]
                    elif case == "wrong-kind":
                        replacement = next(json.loads(path.read_text(encoding="utf-8"))["receipt_id"] for path in self.files(home, "receipts") if json.loads(path.read_text(encoding="utf-8"))["kind"] == "construction")
                    redigest(replay, replacement)
                    with self.assertRaises(h2.H2Error): h2.verify(home)
                    redigest(replay, original)
            second = self.invoke(self.admission(message="second"), home)
            second_challenge = second["reply"].split("HOUSE-PILOT-ACK ")[1].split()[0]
            self.invoke(self.admission("ack", second_challenge, message="second-ack"), home)
            cross = next(json.loads(path.read_text(encoding="utf-8"))["receipt_id"] for path in self.files(home, "observations") if json.loads(path.read_text(encoding="utf-8"))["challenge_id"] == second_challenge)
            redigest(replay, cross)
            with self.assertRaises(h2.H2Error): h2.verify(home)
