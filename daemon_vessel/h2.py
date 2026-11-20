"""Offline H2 Discord/Hermes adapter with immutable, recoverable receipts.

H2 serializes its own durable publication.  H1 ingestion deliberately occurs
outside that lock, because ``crossing.ingest`` owns the non-reentrant H1 lock.
Every H2 artifact is immutable and dated; retries reconstruct the same chain
from a deterministic transaction identity rather than a mutable sidecar.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import pathlib
import secrets
import time
import threading
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from . import crossing


MODULE_DIR = pathlib.Path(__file__).resolve().parent
SCHEMAS = MODULE_DIR / "schemas"
UTC = dt.timezone.utc
_PROCESS_LOCK = threading.RLock()


class H2Error(crossing.CrossingError):
    """A sanitized H2 refusal."""


@contextlib.contextmanager
def _h2_lock(root: pathlib.Path) -> Iterable[None]:
    """Bounded retry around H1's stale/live Windows-safe lock primitive."""
    for attempt in range(400):
        try:
            with crossing.serialization_lock(root / ".h2.lock"):
                yield
                return
        except crossing.CrossingError as exc:
            if exc.code != "ledger_busy" or attempt == 399:
                raise
            time.sleep(0.01)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise H2Error("refused: invalid H2 timestamp", code="receipt_integrity") from exc
    if parsed.tzinfo is None:
        raise H2Error("refused: invalid H2 timestamp", code="receipt_integrity")
    return parsed.astimezone(UTC)


def _schema(name: str) -> Draft202012Validator:
    try:
        value = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(value)
        return Draft202012Validator(value, format_checker=FormatChecker())
    except Exception as exc:
        raise H2Error("refused: invalid H2 schema", code="schema_validation") from exc


ADMISSION_VALIDATOR = _schema("hermes-admission.schema.json")
RECEIPT_VALIDATOR = _schema("h2-receipt.schema.json")


def _validate(validator: Draft202012Validator, value: object, label: str) -> dict[str, Any]:
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors or not isinstance(value, dict):
        raise H2Error(f"refused: invalid {label}", code="schema_validation")
    return value


def _validate_admission(value: object) -> dict[str, Any]:
    return _validate(ADMISSION_VALIDATOR, value, "Hermes admission")


def _validate_receipt(value: object) -> dict[str, Any]:
    receipt = _validate(RECEIPT_VALIDATOR, value, "H2 receipt")
    digest = receipt.get("sha256")
    scoped = {key: item for key, item in receipt.items() if key != "sha256"}
    if digest != _digest(scoped):
        raise H2Error("refused: H2 receipt digest mismatch", code="receipt_integrity")
    allowed = {"construction": "constructed", "delivery": "submitted_to_gateway", "observation": "round_trip_observed", "refusal": "refused", "replay": "duplicate", "duplicate": "duplicate"}
    if allowed.get(receipt["kind"]) != receipt["outcome"]:
        raise H2Error("refused: invalid H2 receipt semantics", code="receipt_integrity")
    if receipt["kind"] == "observation" and receipt["ack_event"] is None:
        raise H2Error("refused: invalid H2 receipt acknowledgement link", code="receipt_integrity")
    if receipt["kind"] in {"construction", "delivery", "refusal", "duplicate"} and receipt["ack_event"] is not None:
        raise H2Error("refused: invalid H2 receipt acknowledgement link", code="receipt_integrity")
    if receipt["kind"] == "refusal" and not receipt["failure"]:
        raise H2Error("refused: refusal receipt lacks a failure code", code="receipt_integrity")
    if receipt["kind"] in {"replay", "duplicate"} and not receipt["original_receipt_id"]:
        raise H2Error("refused: replay receipt lacks original link", code="receipt_integrity")
    return receipt


def _read_admission(path: str | pathlib.Path, expected: str) -> dict[str, Any]:
    try:
        raw = pathlib.Path(path).read_bytes()
    except OSError as exc:
        raise H2Error("refused: unreadable admission", code="unreadable_input") from exc
    if hashlib.sha256(raw).hexdigest() != expected:
        raise H2Error("refused: source digest mismatch", code="source_digest_mismatch")
    try:
        return _validate_admission(json.loads(raw.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise H2Error("refused: unreadable admission", code="invalid_json") from exc


def _root(paths: crossing.CrossingPaths) -> pathlib.Path:
    return paths.home / "crossings" / "hermes"


def _relative(paths: crossing.CrossingPaths, path: pathlib.Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.home.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise H2Error("refused: H2 path escapes continuity home", code="unsafe_locator") from exc


def _dated(root: pathlib.Path, kind: str, timestamp: str, identity: str) -> pathlib.Path:
    when = _parse_time(timestamp)
    safe = identity.replace(":", "-")
    return root / kind / f"{when:%Y}" / f"{when:%m}" / f"{safe}.json"


def _immutable(path: pathlib.Path, value: object) -> pathlib.Path:
    content = _canonical(value)
    try:
        crossing._atomic_write(path, content, immutable=True)
    except crossing.CrossingError as exc:
        if path.exists() and path.read_bytes() == content:
            return path
        raise H2Error("refused: immutable H2 artifact collision", code="immutable_collision") from exc
    except OSError as exc:
        raise H2Error("refused: unable to publish H2 artifact", code="transaction_write_failed") from exc
    return path


def _load(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise H2Error(f"refused: unreadable {label}", code="receipt_integrity") from exc
    if not isinstance(value, dict):
        raise H2Error(f"refused: invalid {label}", code="receipt_integrity")
    return value


def _files(root: pathlib.Path, kind: str) -> Iterable[pathlib.Path]:
    directory = root / kind
    return sorted(directory.glob("*/*/*.json")) if directory.exists() else ()


def _event_id(native_id: str) -> str:
    material = "surface:discord-hermes\0" + native_id
    return "event:discord-hermes-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _room_id(chat_id: str) -> str:
    return "room:discord-local-" + hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:24]


def _tx_id(source_event: str) -> str:
    return "transaction:h2-" + hashlib.sha256(source_event.encode("utf-8")).hexdigest()[:32]


def _challenge(*_ignored: str) -> str:
    """Return a cryptographically random challenge for one persisted reservation."""
    return "challenge:" + secrets.token_urlsafe(24).replace("-", "A").replace("_", "B")


def _event(
    admission: dict[str, Any],
    *,
    event_id: str,
    content: str,
    ingested_at: str,
    actor_id: str = "actor:julie",
    position: str = "user",
    native_id: str | None = None,
    derived_from: list[str] | None = None,
    mode: str = "hermes_gateway",
    fidelity: str = "rendered_content",
) -> dict[str, Any]:
    source = admission["source"]
    is_tool = actor_id == "actor:tool"
    return {
        "schema": "gltch.house-event/0.1.0",
        "event_id": event_id,
        "occurred_at": admission["captured_at"] if not is_tool else ingested_at,
        "ingested_at": ingested_at,
        "source": {
            "surface_id": "surface:discord-hermes",
            "room_id": _room_id(source["chat_id"]),
            "thread_id": source["thread_id"],
            "native_event_id": native_id,
        },
        "actor": {
            "actor_id": actor_id,
            "speaker_position": position,
            "runtime_id": "runtime:hermes-house-crossing-plugin" if is_tool else "runtime:hermes-discord-gateway",
            "runtime_evidence": "observed",
        },
        "content": {
            "media_type": "text/plain",
            "fidelity": fidelity,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "inline": content,
            "ref": None,
            "language": None,
        },
        "privacy_class": "local_private",
        "authority_class": "historical_evidence",
        "lineage": {"reply_to": [], "corrects": [], "supersedes": [], "derived_from": derived_from or []},
        "attachments": [],
        "continuity": {"candidate_delta_ref": None, "mutation_authorized": False},
        "transport": {
            "mode": mode,
            "receipt_id": None,
            "raw_bytes_available": False,
            "rendered_content_available": not is_tool,
        },
    }


def _reply(tx: dict[str, Any]) -> str:
    return (
        f"H2 pilot accepted. {tx['source_event']} {tx['packet_id']} {tx['challenge_id']} "
        f"HOUSE-PILOT-ACK {tx['challenge_id']} Continuity remained read-only."
    )


def _transaction(admission: dict[str, Any]) -> dict[str, Any]:
    source_event = _event_id(admission["message_id"])
    transaction_id = _tx_id(source_event)
    started_at = _now()
    challenge_id = _challenge()
    packet_id = "packet:h2-" + hashlib.sha256(source_event.encode("utf-8")).hexdigest()[:40]
    return_event = "event:h2-return-" + hashlib.sha256((source_event + challenge_id).encode("utf-8")).hexdigest()[:32]
    receipt_id = "receipt:h2-construction-" + hashlib.sha256((transaction_id + "\0construction\0").encode("utf-8")).hexdigest()[:32]
    tx = {
        "schema": "gltch.h2-transaction/0.1.0", "transaction_id": transaction_id,
        "started_at": started_at, "source_event": source_event, "packet_id": packet_id,
        "return_event": return_event, "challenge_id": challenge_id,
        "construction_receipt_id": receipt_id, "message_id": admission["message_id"],
        "body_sha256": hashlib.sha256(admission["body"].encode("utf-8")).hexdigest(),
        "session_key_sha256": admission["session_key_sha256"], "captured_at": admission["captured_at"],
    }
    tx["reply_sha256"] = hashlib.sha256(_reply(tx).encode("utf-8")).hexdigest()
    return tx


def _receipt(
    tx: dict[str, Any], kind: str, outcome: str, *, timestamp: str,
    ack_event: str | None = None, failure: str | None = None, original_receipt_id: str | None = None,
) -> dict[str, Any]:
    material = tx["transaction_id"] + "\0" + kind + "\0" + (ack_event or "")
    if kind in {"refusal", "replay", "duplicate"}:
        material += "\0" + secrets.token_hex(16)
    receipt_id = "receipt:h2-" + kind + "-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    value = {
        "schema": "gltch.h2-receipt/0.1.0", "receipt_id": receipt_id, "created_at": timestamp,
        "transaction_id": tx["transaction_id"], "kind": kind, "outcome": outcome,
        "source_event": tx["source_event"], "packet_id": tx["packet_id"], "return_event": tx["return_event"],
        "challenge_id": tx["challenge_id"], "ack_event": ack_event, "session_key_sha256": tx["session_key_sha256"],
        "failure": failure, "original_receipt_id": original_receipt_id, "artifact_paths": [],
    }
    value["sha256"] = _digest(value)
    return value


def _write_receipt(paths: crossing.CrossingPaths, tx: dict[str, Any], kind: str, outcome: str, *, timestamp: str, ack_event: str | None = None, failure: str | None = None, original_receipt_id: str | None = None) -> dict[str, Any]:
    receipt = _receipt(tx, kind, outcome, timestamp=timestamp, ack_event=ack_event, failure=failure, original_receipt_id=original_receipt_id)
    location = {
        "delivery": "deliveries",
        "observation": "observations",
    }.get(kind, "dead-letter" if outcome == "refused" else "receipts")
    path = _dated(_root(paths), location, timestamp, receipt["receipt_id"])
    receipt["artifact_paths"] = [_relative(paths, path)]
    receipt["sha256"] = _digest({key: item for key, item in receipt.items() if key != "sha256"})
    _validate_receipt(receipt)
    _immutable(path, receipt)
    return receipt


def _advance(paths: crossing.CrossingPaths, tx: dict[str, Any], phase: str) -> None:
    """Publish a dated immutable phase marker; useful for crash reconstruction."""
    value = {"schema": "gltch.h2-phase/0.1.0", "transaction_id": tx["transaction_id"], "phase": phase, "created_at": _now(), "transaction_sha256": _digest(tx)}
    path = _root(paths) / "transactions" / tx["transaction_id"].replace(":", "-") / "phases" / (phase + ".json")
    if path.exists():
        existing = _load(path, "H2 transaction marker")
        if existing.get("transaction_sha256") != value["transaction_sha256"] or existing.get("phase") != phase:
            raise H2Error("refused: H2 phase collision", code="receipt_integrity")
        return
    _immutable(path, value)


def _packet(tx: dict[str, Any], source_event: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": "gltch.context-packet/0.1.0", "packet_id": tx["packet_id"], "orientation": "H2 offline pilot trial",
        "purpose": "Deterministic acknowledgement round trip; read-only.", "created_at": tx["started_at"], "sha256": "0" * 64,
        "access_index_nodes": [], "registry_resources": [],
        "sources": [{"event_id": tx["source_event"], "actor_id": "actor:julie", "speaker_position": "user", "source_locator": "h2://source/" + tx["source_event"], "authority_class": "historical_evidence", "provenance": "post_gateway_admission", "fidelity": source_event["content"]["fidelity"]}],
        "corrections": [], "open_commitments": [], "unresolved": [],
        "runtime_seam": [{"runtime_id": "runtime:hermes-discord-gateway", "evidence": "observed"}, {"runtime_id": "runtime:hermes-house-crossing-plugin", "evidence": "observed"}],
        "budgets": {"hops": 1, "resources": 1, "tokens": 256},
        "omissions": [{"kind": "canonical_continuity", "reason": "not authorized"}, {"kind": "diary_archive_registry", "reason": "outside H2 trial"}],
        "compaction_ledger": [{"operation": "none", "source_refs": [tx["source_event"]], "output_ref": None, "fidelity_loss": "none"}],
        "mutation_authorized": False, "construction_receipt_id": tx["construction_receipt_id"],
    }
    value["sha256"] = _digest({key: item for key, item in value.items() if key != "sha256"})
    return crossing.validate_packet(value)


def _transactions(root: pathlib.Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    transaction_root = root / "transactions"
    if not transaction_root.exists():
        return found
    for path in sorted(transaction_root.glob("*/reservation.json")):
        value = _load(path, "H2 reservation")
        identifier = value.get("source_event")
        required = {
            "schema", "transaction_id", "started_at", "source_event", "packet_id",
            "return_event", "challenge_id", "construction_receipt_id", "message_id",
            "body_sha256", "session_key_sha256", "captured_at", "reply_sha256",
            "transaction_sha256",
        }
        strings = required - {"transaction_sha256"}
        if (set(value) != required or any(not isinstance(value.get(key), str) or not value[key] for key in strings) or
                value.get("schema") != "gltch.h2-transaction/0.1.0" or
                not isinstance(value.get("transaction_sha256"), str) or
                not isinstance(identifier, str) or identifier in found or
                path.parent.name != str(value.get("transaction_id", "")).replace(":", "-") or
                value.get("transaction_sha256") != _digest({key: item for key, item in value.items() if key != "transaction_sha256"})):
            raise H2Error("refused: H2 transaction collision", code="receipt_integrity")
        found[identifier] = value
    return found


def _reserve(paths: crossing.CrossingPaths, admission: dict[str, Any]) -> dict[str, Any]:
    root = _root(paths)
    candidate = _transaction(admission)
    with _h2_lock(root):
        existing = _transactions(root).get(candidate["source_event"])
        if existing is not None:
            if existing.get("body_sha256") != candidate["body_sha256"] or existing.get("message_id") != candidate["message_id"]:
                raise H2Error("refused: divergent message-ID collision", code="source_key_collision")
            return existing
        path = root / "transactions" / candidate["transaction_id"].replace(":", "-") / "reservation.json"
        candidate["transaction_sha256"] = _digest(candidate)
        _immutable(path, candidate)
        return candidate


def _find_delivery(paths: crossing.CrossingPaths, challenge_id: str) -> dict[str, Any] | None:
    for path in _files(_root(paths), "deliveries"):
        value = _validate_receipt(_load(path, "H2 receipt"))
        if value["kind"] == "delivery" and value["challenge_id"] == challenge_id:
            return value
    return None


def _h1_success(paths: crossing.CrossingPaths, event_id: str) -> bool:
    return crossing._find_success_receipt(paths, event_id) is not None


def _validate_receipt_at(
    paths: crossing.CrossingPaths, path: pathlib.Path, directory: str, kinds: set[str], tx: dict[str, Any]
) -> dict[str, Any]:
    """Load a receipt only from its canonical dated location and lineage."""
    receipt = _validate_receipt(_load(path, "H2 receipt"))
    expected = _dated(_root(paths), directory, receipt["created_at"], receipt["receipt_id"])
    linked = ("transaction_id", "source_event", "packet_id", "return_event", "challenge_id", "session_key_sha256")
    if (
        receipt["kind"] not in kinds or path != expected or
        receipt["artifact_paths"] != [_relative(paths, path)] or
        any(receipt[key] != tx[key] for key in linked)
    ):
        raise H2Error("refused: H2 receipt placement or linkage mismatch", code="receipt_integrity")
    return receipt


def _validate_phase(paths: crossing.CrossingPaths, tx: dict[str, Any], phase: str) -> None:
    path = _root(paths) / "transactions" / tx["transaction_id"].replace(":", "-") / "phases" / (phase + ".json")
    marker = _load(path, "H2 transaction marker")
    if (
        set(marker) != {"schema", "transaction_id", "phase", "created_at", "transaction_sha256"} or
        marker.get("schema") != "gltch.h2-phase/0.1.0" or marker.get("transaction_id") != tx["transaction_id"] or
        marker.get("phase") != phase or marker.get("transaction_sha256") != _digest(tx)
    ):
        raise H2Error("refused: invalid H2 phase marker", code="receipt_integrity")
    _parse_time(str(marker["created_at"]))


def _validate_h1_identity(tx: dict[str, Any], source: dict[str, Any] | None, returned: dict[str, Any] | None) -> None:
    """Bind H2 provenance to the exact H1 speaker and runtime tuples."""
    if source is None or returned is None:
        raise H2Error("refused: H1 event provenance mismatch", code="receipt_integrity")
    source_actor = source.get("actor", {})
    source_surface = source.get("source", {})
    source_content = source.get("content", {})
    source_transport = source.get("transport", {})
    returned_actor = returned.get("actor", {})
    returned_surface = returned.get("source", {})
    returned_content = returned.get("content", {})
    returned_transport = returned.get("transport", {})
    if (
        source_surface.get("surface_id") != "surface:discord-hermes" or
        source_surface.get("native_event_id") != tx["message_id"] or
        source_actor.get("actor_id") != "actor:julie" or
        source_actor.get("speaker_position") != "user" or
        source_actor.get("runtime_id") != "runtime:hermes-discord-gateway" or
        source_actor.get("runtime_evidence") != "observed" or
        source_content.get("sha256") != tx["body_sha256"] or
        source_content.get("fidelity") != "rendered_content" or
        source_transport.get("mode") != "hermes_gateway" or
        source_transport.get("raw_bytes_available") is not False or
        source_transport.get("rendered_content_available") is not True or
        returned_surface.get("surface_id") != "surface:discord-hermes" or
        returned_surface.get("native_event_id") is not None or
        returned_actor.get("actor_id") != "actor:tool" or
        returned_actor.get("speaker_position") != "tool" or
        returned_actor.get("runtime_id") != "runtime:hermes-house-crossing-plugin" or
        returned_actor.get("runtime_evidence") != "observed" or
        returned_content.get("sha256") != tx["reply_sha256"] or
        returned_content.get("fidelity") != "exact_bytes_unverified" or
        returned_transport.get("mode") != "prepared_for_gateway" or
        returned_transport.get("raw_bytes_available") is not False or
        returned_transport.get("rendered_content_available") is not False or
        returned.get("lineage", {}).get("derived_from") != [tx["source_event"]]
    ):
        raise H2Error("refused: H1 event provenance mismatch", code="receipt_integrity")


def _validate_dead_letter_attempt(paths: crossing.CrossingPaths, path: pathlib.Path, attempt: dict[str, Any]) -> None:
    """Validate a non-receipt unknown-ACK refusal without inventing lineage."""
    required = {"schema", "attempt_id", "created_at", "command", "message_id_sha256", "challenge_id", "failure", "sha256"}
    expected = _dated(_root(paths), "dead-letter", str(attempt.get("created_at", "")), str(attempt.get("attempt_id", "")))
    if (
        set(attempt) != required or path != expected or
        attempt.get("schema") != "gltch.h2-dead-letter/0.1.0" or
        attempt.get("command") != "ack" or
        not isinstance(attempt.get("attempt_id"), str) or not attempt["attempt_id"].startswith("attempt:h2-") or
        not isinstance(attempt.get("message_id_sha256"), str) or len(attempt["message_id_sha256"]) != 64 or
        not isinstance(attempt.get("challenge_id"), str) or not attempt["challenge_id"].startswith("challenge:") or
        not isinstance(attempt.get("failure"), str) or not attempt["failure"] or
        attempt.get("sha256") != _digest({key: item for key, item in attempt.items() if key != "sha256"})
    ):
        raise H2Error("refused: invalid H2 dead letter", code="receipt_integrity")
    _parse_time(str(attempt["created_at"]))


def _validate_prepared_chain(paths: crossing.CrossingPaths, tx: dict[str, Any]) -> dict[str, Any]:
    """Prove every immutable artifact required before gateway submission."""
    root = _root(paths)
    reservations = _transactions(root)
    if reservations.get(tx["source_event"]) != tx:
        raise H2Error("refused: reservation integrity failure", code="prepared_chain_incomplete")
    packet_path = _dated(root, "packets", tx["started_at"], tx["packet_id"])
    packet = _load(packet_path, "H2 packet")
    if (
        packet.get("sha256") != _digest({key: item for key, item in packet.items() if key != "sha256"}) or
        packet.get("packet_id") != tx["packet_id"] or packet.get("created_at") != tx["started_at"] or
        packet.get("construction_receipt_id") != tx["construction_receipt_id"]
    ):
        raise H2Error("refused: prepared packet integrity failure", code="prepared_chain_incomplete")
    packet = crossing.validate_packet(packet)
    if len(packet.get("sources", [])) != 1 or packet["sources"][0].get("event_id") != tx["source_event"]:
        raise H2Error("refused: prepared packet lineage failure", code="prepared_chain_incomplete")
    receipt_path = _dated(root, "receipts", tx["started_at"], tx["construction_receipt_id"])
    construction = _validate_receipt_at(paths, receipt_path, "receipts", {"construction"}, tx)
    if construction["receipt_id"] != tx["construction_receipt_id"]:
        raise H2Error("refused: construction receipt identity mismatch", code="prepared_chain_incomplete")
    for phase in ("source-persisted", "packet-published", "return-persisted"):
        _validate_phase(paths, tx, phase)
    _, events, _ = crossing._scan_events(paths)
    source, returned = events.get(tx["source_event"]), events.get(tx["return_event"])
    if not _h1_success(paths, tx["source_event"]) or not _h1_success(paths, tx["return_event"]):
        raise H2Error("refused: prepared H1 lineage failure", code="prepared_chain_incomplete")
    _validate_h1_identity(tx, source, returned)
    return packet


def _ingest_retry(event: dict[str, Any], paths: crossing.CrossingPaths) -> None:
    """Do not turn a live H1 lock contender into a second H2 chain."""
    for attempt in range(400):
        try:
            crossing.ingest(event, home=paths.home)
            return
        except crossing.CrossingError as exc:
            if exc.code != "ledger_busy" or attempt == 399:
                raise
            time.sleep(0.01)


def _pilot(paths: crossing.CrossingPaths, admission: dict[str, Any], *, confirm: bool = True) -> dict[str, str]:
    tx = _reserve(paths, admission)
    source = _event(admission, event_id=tx["source_event"], content=admission["body"], ingested_at=tx["started_at"], native_id=admission["message_id"])
    # H1 owns its lock.  No H2 lock is held across this call.
    _ingest_retry(source, paths)
    _advance(paths, tx, "source-persisted")
    construction = _write_receipt(paths, tx, "construction", "constructed", timestamp=tx["started_at"])
    if construction["receipt_id"] != tx["construction_receipt_id"]:
        raise H2Error("refused: construction receipt identity mismatch", code="receipt_integrity")
    packet = _packet(tx, source)
    _immutable(_dated(_root(paths), "packets", tx["started_at"], tx["packet_id"]), packet)
    _advance(paths, tx, "packet-published")
    reply = _reply(tx)
    returned = _event(admission, event_id=tx["return_event"], content=reply, ingested_at=tx["started_at"], actor_id="actor:tool", position="tool", derived_from=[tx["source_event"]], mode="prepared_for_gateway", fidelity="exact_bytes_unverified")
    _ingest_retry(returned, paths)
    _advance(paths, tx, "return-persisted")
    if confirm:
        _confirm(paths, tx)
    return {"status": "handled", "reply": reply}


def _confirm(paths: crossing.CrossingPaths, tx: dict[str, Any]) -> dict[str, str]:
    """Record submission only after the caller has accepted the prepared reply."""
    with _h2_lock(_root(paths)):
        _validate_prepared_chain(paths, tx)
        existing = _find_delivery(paths, tx["challenge_id"])
        if existing is None:
            _write_receipt(paths, tx, "delivery", "submitted_to_gateway", timestamp=_now())
        _advance(paths, tx, "delivery-published")
    return {"status": "handled", "reply": _reply(tx)}


def _ack(paths: crossing.CrossingPaths, admission: dict[str, Any]) -> dict[str, str]:
    with _h2_lock(_root(paths)):
        delivery = _find_delivery(paths, admission["challenge_id"])
        if delivery is None:
            raise H2Error("refused: acknowledgement is unknown", code="ack_unknown")
        if delivery["session_key_sha256"] != admission["session_key_sha256"]:
            raise H2Error("refused: acknowledgement session mismatch", code="ack_session_mismatch")
        tx = _transactions(_root(paths)).get(delivery["source_event"])
        captured = _parse_time(admission["captured_at"])
        issued = _parse_time(delivery["created_at"])
        if tx is None or captured < issued or captured > issued + dt.timedelta(minutes=15):
            raise H2Error("refused: acknowledgement expired", code="ack_expired")
    ack_id = _event_id(admission["message_id"])
    with _h2_lock(_root(paths)):
        for path in _files(_root(paths), "observations"):
            receipt = _validate_receipt(_load(path, "H2 receipt"))
            if receipt["kind"] == "observation" and receipt["challenge_id"] == tx["challenge_id"]:
                if receipt["ack_event"] == ack_id:
                    _write_receipt(paths, tx, "replay", "duplicate", timestamp=_now(), ack_event=ack_id, original_receipt_id=receipt["receipt_id"])
                    return {"status": "handled", "reply": "H2 round trip already observed. Continuity remained read-only."}
                raise H2Error("refused: acknowledgement already observed", code="ack_already_observed")
    event = _event(admission, event_id=ack_id, content=tx["challenge_id"], ingested_at=_now(), native_id=admission["message_id"], derived_from=[tx["source_event"], tx["return_event"]])
    _ingest_retry(event, paths)
    with _h2_lock(_root(paths)):
        for path in _files(_root(paths), "observations"):
            receipt = _validate_receipt(_load(path, "H2 receipt"))
            if receipt["kind"] == "observation" and receipt["challenge_id"] == tx["challenge_id"]:
                if receipt["ack_event"] == ack_id:
                    _write_receipt(paths, tx, "replay", "duplicate", timestamp=_now(), ack_event=ack_id, original_receipt_id=receipt["receipt_id"])
                    return {"status": "handled", "reply": "H2 round trip already observed. Continuity remained read-only."}
                raise H2Error("refused: acknowledgement already observed", code="ack_already_observed")
        _advance(paths, tx, "ack-persisted")
        _write_receipt(paths, tx, "observation", "round_trip_observed", timestamp=_now(), ack_event=ack_id)
        _advance(paths, tx, "observation-published")
    return {"status": "handled", "reply": "H2 round trip observed. Continuity remained read-only."}


def _record_refusal(paths: crossing.CrossingPaths, admission: dict[str, Any], error: H2Error) -> None:
    """Leave a sanitized immutable dead-letter when a chain can be identified."""
    try:
        transaction: dict[str, Any] | None = None
        if admission["command"] == "pilot":
            source_event = _event_id(admission["message_id"])
            transaction = _transactions(_root(paths)).get(source_event) or _transaction(admission)
        else:
            delivery = _find_delivery(paths, admission["challenge_id"])
            if delivery is not None:
                transaction = _transactions(_root(paths)).get(delivery["source_event"])
        if transaction is not None:
            _write_receipt(paths, transaction, "refusal", "refused", timestamp=_now(), failure=error.code)
        else:
            # Unknown ACKs cannot truthfully claim a packet/return lineage.  They
            # still receive an immutable, hashed, deliberately non-contentful
            # attempt record for audit without inventing a transaction.
            timestamp = _now()
            attempt = {
                "schema": "gltch.h2-dead-letter/0.1.0",
                "attempt_id": "attempt:h2-" + secrets.token_hex(16),
                "created_at": timestamp,
                "command": admission.get("command"),
                "message_id_sha256": hashlib.sha256(str(admission.get("message_id", "")).encode("utf-8")).hexdigest(),
                "challenge_id": admission.get("challenge_id"),
                "failure": error.code,
            }
            attempt["sha256"] = _digest(attempt)
            _immutable(_dated(_root(paths), "dead-letter", timestamp, attempt["attempt_id"]), attempt)
    except (H2Error, OSError, KeyError, TypeError):
        # Never turn the original sanitized refusal into an internal detail.
        return


def process(source: str | pathlib.Path, expect_sha256: str, home: str | pathlib.Path, *, mode: str = "process") -> dict[str, str]:
    expected = crossing.validate_expected_sha256(expect_sha256)
    assert expected is not None
    admission = _read_admission(source, expected)
    paths = crossing.resolve_home(home)
    try:
        # Process-local serialization complements the on-disk lock and keeps
        # H1's non-reentrant ledger lock out of concurrent H2 transitions.
        with _PROCESS_LOCK:
            if admission["command"] == "ack":
                return _ack(paths, admission)
            return _pilot(paths, admission, confirm=mode != "prepare")
    except H2Error as error:
        _record_refusal(paths, admission, error)
        raise
    except crossing.CrossingError as exc:
        error = H2Error("refused: H2 operation failed", code=exc.code)
        _record_refusal(paths, admission, error)
        raise error from exc


def confirm(challenge_id: str, home: str | pathlib.Path) -> dict[str, str]:
    """Confirm an already prepared reply after the caller's private checks."""
    paths = crossing.resolve_home(home)
    transaction: dict[str, Any] | None = None
    with _h2_lock(_root(paths)):
        for candidate in _transactions(_root(paths)).values():
            if candidate.get("challenge_id") == challenge_id:
                transaction = candidate
                break
    if transaction is not None:
        return _confirm(paths, transaction)
    raise H2Error("refused: unknown prepared challenge", code="ack_unknown")


def verify(home: str | pathlib.Path) -> dict[str, Any]:
    paths = crossing.resolve_home(home)
    report = crossing.verify(home=paths.home)
    root = _root(paths)
    transactions = _transactions(root)
    packets: dict[str, dict[str, Any]] = {}
    for path in _files(root, "packets"):
        packet = _load(path, "H2 packet")
        packet_id = packet.get("packet_id")
        if (not isinstance(packet_id, str) or packet_id in packets or
                path != _dated(root, "packets", str(packet.get("created_at", "")), packet_id)):
            raise H2Error("refused: packet identity collision", code="receipt_integrity")
        if packet.get("sha256") != _digest({key: item for key, item in packet.items() if key != "sha256"}):
            raise H2Error("refused: packet digest mismatch", code="receipt_integrity")
        packets[packet_id] = crossing.validate_packet(packet)
    receipts: list[dict[str, Any]] = []
    locations = (("receipts", {"construction", "replay", "duplicate"}), ("deliveries", {"delivery"}), ("observations", {"observation"}))
    for directory, kinds in locations:
        for path in _files(root, directory):
            receipt = _validate_receipt(_load(path, "H2 receipt"))
            expected = _dated(root, directory, receipt["created_at"], receipt["receipt_id"])
            if receipt["kind"] not in kinds or path != expected or receipt["artifact_paths"] != [_relative(paths, path)]:
                raise H2Error("refused: H2 receipt placement mismatch", code="receipt_integrity")
            tx = transactions.get(receipt["source_event"])
            linked = ("transaction_id", "source_event", "packet_id", "return_event", "challenge_id", "session_key_sha256")
            if tx is None or any(receipt[key] != tx[key] for key in linked):
                raise H2Error("refused: H2 receipt linkage mismatch", code="receipt_integrity")
            receipts.append(receipt)
    _, events, _ = crossing._scan_events(paths)
    for marker_path in sorted((root / "transactions").glob("*/phases/*.json")) if (root / "transactions").exists() else ():
        marker = _load(marker_path, "H2 transaction marker")
        tx = next((item for item in transactions.values() if item["transaction_id"] == marker.get("transaction_id")), None)
        phase = marker.get("phase")
        expected = root / "transactions" / str(marker.get("transaction_id", "")).replace(":", "-") / "phases" / (str(phase) + ".json")
        if tx is None or phase not in {"source-persisted", "packet-published", "return-persisted", "delivery-published", "ack-persisted", "observation-published"} or marker_path != expected:
            raise H2Error("refused: invalid or orphaned H2 phase marker", code="receipt_integrity")
        _validate_phase(paths, tx, str(phase))
    required_phases = {"source-persisted", "packet-published", "return-persisted", "delivery-published"}
    for tx in transactions.values():
        phase_root = root / "transactions" / tx["transaction_id"].replace(":", "-") / "phases"
        if not required_phases <= ({path.stem for path in phase_root.glob("*.json")} if phase_root.exists() else set()):
            raise H2Error("refused: missing H2 phase marker", code="receipt_integrity")
    constructions = [value for value in receipts if value["kind"] == "construction"]
    deliveries = [value for value in receipts if value["kind"] == "delivery"]
    observations = [value for value in receipts if value["kind"] == "observation"]
    if len({value["challenge_id"] for value in deliveries}) != len(deliveries):
        raise H2Error("refused: duplicate H2 delivery", code="receipt_integrity")
    if {item["source_event"] for item in deliveries} != set(transactions):
        raise H2Error("refused: missing or orphaned H2 delivery", code="receipt_integrity")
    for delivery in deliveries:
        tx = transactions.get(delivery["source_event"])
        packet = packets.get(delivery["packet_id"])
        if tx is None or packet is None or delivery["return_event"] != tx.get("return_event") or delivery["challenge_id"] != tx.get("challenge_id"):
            raise H2Error("refused: dangling H2 delivery", code="receipt_integrity")
        construction = next((item for item in constructions if item["receipt_id"] == tx["construction_receipt_id"]), None)
        if construction is None or construction["source_event"] != tx["source_event"] or construction["packet_id"] != tx["packet_id"]:
            raise H2Error("refused: missing construction receipt", code="receipt_integrity")
        if packet["construction_receipt_id"] != tx["construction_receipt_id"] or packet["sources"][0]["event_id"] != tx["source_event"]:
            raise H2Error("refused: packet lineage mismatch", code="receipt_integrity")
        if not _h1_success(paths, tx["source_event"]) or not _h1_success(paths, tx["return_event"]):
            raise H2Error("refused: missing linked H1 receipt", code="receipt_integrity")
        _validate_h1_identity(tx, events.get(tx["source_event"]), events.get(tx["return_event"]))
    for observation in observations:
        delivery = next((item for item in deliveries if item["challenge_id"] == observation["challenge_id"]), None)
        ack = events.get(observation["ack_event"]) if observation["ack_event"] is not None else None
        if delivery is None or observation["ack_event"] is None or ack is None or not _h1_success(paths, observation["ack_event"]) or ack["lineage"]["derived_from"] != [delivery["source_event"], delivery["return_event"]] or ack["content"]["inline"] != delivery["challenge_id"]:
            raise H2Error("refused: dangling H2 observation", code="receipt_integrity")
        phase_root = root / "transactions" / delivery["transaction_id"].replace(":", "-") / "phases"
        if not {"ack-persisted", "observation-published"} <= {path.stem for path in phase_root.glob("*.json")}:
            raise H2Error("refused: missing H2 observation phase", code="receipt_integrity")
    for path in _files(root, "dead-letter"):
        dead = _load(path, "H2 dead letter")
        if dead.get("schema") == "gltch.h2-dead-letter/0.1.0":
            _validate_dead_letter_attempt(paths, path, dead)
        elif dead.get("schema") == "gltch.h2-receipt/0.1.0":
            receipt = _validate_receipt(dead)
            expected = _dated(root, "dead-letter", receipt["created_at"], receipt["receipt_id"])
            tx = transactions.get(receipt["source_event"])
            linked = ("transaction_id", "source_event", "packet_id", "return_event", "challenge_id", "session_key_sha256")
            if (receipt["kind"] != "refusal" or path != expected or
                    receipt["artifact_paths"] != [_relative(paths, path)] or tx is None or
                    any(receipt[key] != tx[key] for key in linked)):
                raise H2Error("refused: H2 receipt placement or linkage mismatch", code="receipt_integrity")
            receipts.append(receipt)
        else:
            raise H2Error("refused: invalid H2 dead letter", code="receipt_integrity")
    receipt_ids: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        receipt_id = receipt["receipt_id"]
        if receipt_id in receipt_ids:
            raise H2Error("refused: duplicate H2 receipt identity", code="receipt_integrity")
        receipt_ids[receipt_id] = receipt
    linked = ("transaction_id", "source_event", "packet_id", "return_event", "challenge_id", "session_key_sha256", "ack_event")
    for receipt in receipts:
        if receipt["kind"] not in {"replay", "duplicate"}:
            continue
        original_id = receipt["original_receipt_id"]
        original = receipt_ids.get(original_id)
        if (
            original is None or original_id == receipt["receipt_id"] or
            original["kind"] != "observation" or original["outcome"] != "round_trip_observed" or
            any(receipt[key] != original[key] for key in linked)
        ):
            raise H2Error("refused: dangling H2 replay receipt", code="receipt_integrity")
    if set(packets) != {delivery["packet_id"] for delivery in deliveries}:
        raise H2Error("refused: orphaned H2 packet", code="receipt_integrity")
    return {**report, "h2_challenges": len(deliveries), "h2_observations": len(observations)}
