"""H1 local-only house crossing ledger.

This module validates synthetic crossing artifacts and records them beneath an
explicit external continuity home. It never connects a room, delivers a
packet, or mutates canonical continuity.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


MODULE_DIR = pathlib.Path(__file__).resolve().parent
REPOSITORY_ROOT = MODULE_DIR.parent
SCHEMAS = MODULE_DIR / "schemas"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.!:-]{0,255}$")
LOCK_STALE_AFTER = dt.timedelta(minutes=15)
FORMAT_CHECKER = FormatChecker()


if "date-time" not in FORMAT_CHECKER.checkers:
    @FORMAT_CHECKER.checks("date-time")
    def _is_date_time(value: object) -> bool:
        if not isinstance(value, str) or "T" not in value:
            return False
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None


class CrossingError(RuntimeError):
    """A deliberate, sanitized H1 refusal."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "crossing_refused",
        path: str | None = None,
        keyword: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.keyword = keyword


@dataclass(frozen=True)
class CrossingPaths:
    home: pathlib.Path

    @property
    def lock(self) -> pathlib.Path:
        return self.home / "crossings" / ".ledger.lock"

    @property
    def transactions(self) -> pathlib.Path:
        return self.home / "crossings" / "transactions"

    def month(self, kind: str, when: dt.datetime) -> pathlib.Path:
        return self.home / kind / f"{when:%Y}" / f"{when:%m}"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_bytes(value: object, *, newline: bool = True) -> bytes:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + ("\n" if newline else "")).encode("utf-8")


def _safe_id(value: object, prefix: str | None = None) -> str | None:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        return None
    if prefix is not None and not value.startswith(prefix):
        return None
    return value


def _sanitized_path(error: object) -> str:
    result = "$"
    for part in getattr(error, "absolute_path", ()):
        if isinstance(part, int):
            result += f"[{part}]"
        elif isinstance(part, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            result += f".{part}"
        else:
            result += ".<property>"
    return result


def _safe_relative(value: str) -> bool:
    if not value or "\x00" in value or "://" in value:
        return False
    posix = pathlib.PurePosixPath(value.replace("\\", "/"))
    windows = pathlib.PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root:
        return False
    return ".." not in posix.parts and ".." not in windows.parts


def _safe_external_locator(value: str) -> bool:
    if not value or "\x00" in value:
        return False
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        return False
    credential_keys = re.compile(
        r"(?:^|[-_])(auth|authorization|credential|key|password|secret|signature|sig|token)(?:$|[-_])",
        re.IGNORECASE,
    )
    if any(credential_keys.search(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        return False
    if parsed.scheme:
        return parsed.scheme.lower() != "file"
    return _safe_relative(value)


def _within(home: pathlib.Path, relative: str) -> pathlib.Path:
    if not _safe_relative(relative):
        raise CrossingError("refused: unsafe continuity-home-relative locator", code="unsafe_locator")
    target = (home / pathlib.PurePosixPath(relative)).resolve(strict=False)
    try:
        target.relative_to(home.resolve(strict=False))
    except ValueError as exc:
        raise CrossingError("refused: locator escapes continuity home", code="unsafe_locator") from exc
    return target


def resolve_home(
    home: str | pathlib.Path | None,
    *,
    repository_root: pathlib.Path = REPOSITORY_ROOT,
) -> CrossingPaths:
    selected = home if home is not None else os.environ.get("GLITCH_CONTINUITY_HOME")
    if selected is None or not str(selected).strip() or "\x00" in str(selected):
        raise CrossingError(
            "refused: GLITCH_CONTINUITY_HOME or --home is required",
            code="missing_continuity_home",
        )
    candidate = pathlib.Path(selected).expanduser().resolve(strict=False)
    if candidate == pathlib.Path(candidate.anchor):
        raise CrossingError(
            "refused: continuity home cannot be a filesystem root",
            code="unsafe_continuity_home",
        )
    root = repository_root.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        pass
    else:
        raise CrossingError(
            "refused: continuity home cannot be inside the repository",
            code="unsafe_continuity_home",
        )
    if any(parent.joinpath(".git").exists() for parent in (candidate, *candidate.parents)):
        raise CrossingError(
            "refused: continuity home cannot be within a Git worktree",
            code="unsafe_continuity_home",
        )
    return CrossingPaths(candidate)


def _read_bytes(path: pathlib.Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise CrossingError(f"refused: missing {label}", code="missing_input") from exc
    except OSError as exc:
        raise CrossingError(f"refused: unreadable {label}", code="unreadable_input") from exc


def decode_json(content: bytes, label: str) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CrossingError(f"refused: unreadable {label}", code="invalid_json") from exc


def read_json_snapshot(path: str | pathlib.Path, label: str) -> tuple[bytes, object]:
    content = _read_bytes(pathlib.Path(path), label)
    return content, decode_json(content, label)


def validate_expected_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if not SHA256_RE.fullmatch(value):
        raise CrossingError(
            "refused: --expect-sha256 must be a lowercase SHA-256 digest",
            code="invalid_expected_digest",
        )
    return value


def _assert_local_refs(schema: object) -> None:
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "$ref" and (not isinstance(value, str) or not value.startswith("#")):
                raise CrossingError(
                    "refused: crossing schema contains an external reference",
                    code="external_schema_reference",
                )
            _assert_local_refs(value)
    elif isinstance(schema, list):
        for value in schema:
            _assert_local_refs(value)


def _load_validator(name: str) -> Draft202012Validator:
    schema_bytes = _read_bytes(SCHEMAS / name, "crossing schema")
    schema = decode_json(schema_bytes, "crossing schema")
    if not isinstance(schema, dict) or not isinstance(schema.get("$id"), str):
        raise CrossingError("refused: invalid crossing schema", code="invalid_schema")
    _assert_local_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
        registry = Registry().with_resource(
            schema["$id"],
            Resource.from_contents(schema, default_specification=DRAFT202012),
        )
        return Draft202012Validator(schema, registry=registry, format_checker=FORMAT_CHECKER)
    except SchemaError as exc:
        raise CrossingError("refused: invalid crossing schema", code="invalid_schema") from exc
    except Exception as exc:
        raise CrossingError("refused: unresolved crossing schema reference", code="schema_reference") from exc


def _validate_schema(value: object, name: str, label: str) -> dict[str, Any]:
    try:
        errors = sorted(_load_validator(name).iter_errors(value), key=lambda error: list(error.absolute_path))
    except CrossingError:
        raise
    except Exception as exc:
        raise CrossingError(
            f"refused: unresolved {label} schema reference",
            code="schema_reference",
        ) from exc
    if errors:
        error = errors[0]
        path = _sanitized_path(error)
        keyword = getattr(error, "validator", None) or "unknown"
        raise CrossingError(
            f"refused: {label} validation failed at {path} ({keyword})",
            code="schema_validation",
            path=path,
            keyword=str(keyword),
        )
    if not isinstance(value, dict):
        raise CrossingError(f"refused: {label} root must be an object", code="schema_validation")
    return value


def validate_event(value: object) -> dict[str, Any]:
    event = _validate_schema(value, "house-event.schema.json", "crossing event")
    content = event["content"]
    transport = event["transport"]
    fidelity = content["fidelity"]
    if fidelity == "exact_bytes_verified" and (
        transport["raw_bytes_available"] is not True
        or not isinstance(content.get("sha256"), str)
        or not SHA256_RE.fullmatch(content["sha256"])
    ):
        raise CrossingError(
            "refused: exact bytes require raw bytes and a SHA-256 digest",
            code="invalid_fidelity_claim",
        )
    if fidelity == "rendered_content" and (
        transport["raw_bytes_available"] is True
        or transport["rendered_content_available"] is not True
    ):
        raise CrossingError(
            "refused: rendered content requires rendered evidence and cannot claim raw bytes",
            code="invalid_fidelity_claim",
        )
    if event["continuity"]["mutation_authorized"] is True:
        raise CrossingError(
            "refused: H1 has no canonical-mutation authority",
            code="canonical_mutation_refused",
        )
    if (content.get("inline") is None) == (content.get("ref") is None):
        raise CrossingError(
            "refused: content must use exactly one of inline or ref",
            code="ambiguous_content_state",
        )
    if content.get("ref") is not None and not _safe_external_locator(content["ref"]):
        raise CrossingError("refused: unsafe content reference", code="unsafe_locator")
    candidate_delta = event["continuity"]["candidate_delta_ref"]
    if candidate_delta is not None and not _safe_external_locator(candidate_delta):
        raise CrossingError("refused: unsafe candidate-delta reference", code="unsafe_locator")
    for relation, entries in event["lineage"].items():
        if event["event_id"] in entries:
            raise CrossingError(
                f"refused: {relation} cannot reference the event itself",
                code="invalid_lineage",
            )
        if len(entries) != len(set(entries)):
            raise CrossingError("refused: duplicate lineage target", code="invalid_lineage")
    for attachment in event["attachments"]:
        locator = attachment["locator"]
        state = attachment["locator_state"]
        if state in {"missing", "unresolved"} and locator is not None:
            raise CrossingError("refused: unresolved attachment cannot have a locator", code="unsafe_locator")
        if state == "local" and (not isinstance(locator, str) or not _safe_relative(locator)):
            raise CrossingError("refused: unsafe local attachment locator", code="unsafe_locator")
        if state in {"stable_identifier", "remote", "temporary"} and locator is None:
            raise CrossingError("refused: resolved attachment requires a locator", code="unsafe_locator")
        if state not in {"local", "missing", "unresolved"} and locator is not None:
            if not isinstance(locator, str) or not _safe_external_locator(locator):
                raise CrossingError("refused: unsafe attachment locator", code="unsafe_locator")
    return event


def validate_packet(value: object) -> dict[str, Any]:
    packet = _validate_schema(value, "context-packet.schema.json", "context packet")
    if packet["mutation_authorized"] is True:
        raise CrossingError(
            "refused: H1 has no canonical-mutation authority",
            code="canonical_mutation_refused",
        )
    for source in packet["sources"]:
        if not _safe_external_locator(source["source_locator"]):
            raise CrossingError("refused: unsafe packet source locator", code="unsafe_locator")
    return packet


def validate_receipt(value: object) -> dict[str, Any]:
    receipt = _validate_schema(value, "crossing-receipt.schema.json", "crossing receipt")
    for locator in receipt["artifact_paths"]:
        if not _safe_relative(locator):
            raise CrossingError("refused: unsafe receipt artifact locator", code="unsafe_locator")
    is_dead_outcome = receipt["outcome"] in {"refused", "failed"}
    if is_dead_outcome and receipt["dead_letter"] is not True:
        raise CrossingError("refused: refused or failed receipt must be a dead letter", code="invalid_receipt")
    if not is_dead_outcome and receipt["dead_letter"] is True:
        raise CrossingError("refused: successful or partial receipt cannot be a dead letter", code="invalid_receipt")
    if receipt["outcome"] == "duplicate" and receipt["idempotent_duplicate"] is not True:
        raise CrossingError("refused: duplicate receipt must be idempotent", code="invalid_receipt")
    if receipt["outcome"] != "duplicate" and receipt["idempotent_duplicate"] is True:
        raise CrossingError("refused: non-duplicate receipt cannot be idempotent", code="invalid_receipt")
    if receipt["outcome"] == "duplicate" and (
        receipt["event_id"] is None or receipt["original_receipt_id"] is None
    ):
        raise CrossingError(
            "refused: duplicate receipt requires event and original receipt links",
            code="invalid_receipt",
        )
    if receipt["outcome"] == "success" and receipt["event_id"] is None:
        raise CrossingError("refused: success receipt requires an event link", code="invalid_receipt")
    has_failure_outcome = receipt["outcome"] in {"refused", "failed", "partial"}
    if has_failure_outcome and receipt["failure"] is None:
        raise CrossingError("refused: unsuccessful receipt requires a failure code", code="invalid_receipt")
    if not has_failure_outcome and receipt["failure"] is not None:
        raise CrossingError("refused: successful receipt cannot carry failure", code="invalid_receipt")
    if receipt["operation"] == "replay" and receipt["replay_of"] is None:
        raise CrossingError("refused: replay receipt requires an original link", code="invalid_receipt")
    return receipt


def _fsync_directory(path: pathlib.Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_temp(directory: pathlib.Path, prefix: str, content: bytes) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=prefix, dir=directory)
    path = pathlib.Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _atomic_write(path: pathlib.Path, content: bytes, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        raise CrossingError("refused: immutable crossing artifact already exists", code="immutable_collision")
    temp = _write_temp(path.parent, f".{path.name}.", content)
    try:
        if immutable and path.exists():
            raise CrossingError("refused: immutable crossing artifact already exists", code="immutable_collision")
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _pid_alive_windows(pid: int) -> bool | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        process_query_limited_information = 0x1000
        still_active = 259
        error_access_denied = 5
        error_invalid_parameter = 87

        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == error_invalid_parameter:
                return False
            if error == error_access_denied:
                return True
            return None
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _pid_alive(pid: int) -> bool | None:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


class _LedgerLock:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.token = uuid.uuid4().hex

    def _payload(self) -> bytes:
        return _canonical_bytes(
            {"pid": os.getpid(), "created_at": _now().isoformat(), "token": self.token}
        )

    def __enter__(self) -> "_LedgerLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CrossingError(
                "refused: unable to create crossing lock",
                code="ledger_lock_failed",
            ) from exc
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                self._recover_stale()
                continue
            except OSError as exc:
                raise CrossingError(
                    "refused: unable to create crossing lock",
                    code="ledger_lock_failed",
                ) from exc
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(self._payload())
                    handle.flush()
                    os.fsync(handle.fileno())
                return self
            except Exception:
                self.path.unlink(missing_ok=True)
                raise
        raise CrossingError("refused: crossing ledger is busy", code="ledger_busy")

    def _recover_stale(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            created = dt.datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
            pid = int(data["pid"])
            token = data["token"]
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise CrossingError("refused: crossing lock is unreadable", code="ledger_busy") from exc
        if created.tzinfo is None or not isinstance(token, str) or _now() - created <= LOCK_STALE_AFTER:
            raise CrossingError("refused: crossing ledger is busy", code="ledger_busy")
        if _pid_alive(pid) is not False:
            raise CrossingError("refused: crossing ledger is busy", code="ledger_busy")
        quarantine = self.path.with_name(f".{self.path.name}.stale.{uuid.uuid4().hex}")
        try:
            os.replace(self.path, quarantine)
            quarantine.unlink(missing_ok=True)
        except OSError as exc:
            raise CrossingError("refused: unable to recover stale crossing lock", code="ledger_busy") from exc

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if data.get("token") == self.token:
            self.path.unlink(missing_ok=True)


def _source_key(event: dict[str, Any]) -> str:
    source = event["source"]
    if source["native_event_id"] is not None:
        return f"native:{source['surface_id']}:{source['native_event_id']}"
    occurred = dt.datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
    bucket = occurred.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H")
    digest = event["content"]["sha256"] or _sha256(_canonical_bytes(event["content"], newline=False))
    material = "|".join((digest, event["actor"]["actor_id"], source["room_id"], bucket))
    return "fallback-hour-utc:" + _sha256(material.encode("utf-8"))


def _ledger_files(paths: CrossingPaths) -> Iterable[pathlib.Path]:
    root = paths.home / "crossings" / "events"
    return sorted(root.glob("*/*/*.jsonl")) if root.exists() else []


def _read_ledger(path: pathlib.Path) -> list[dict[str, Any]]:
    content = _read_bytes(path, "crossing ledger")
    if content and not content.endswith(b"\n"):
        raise CrossingError("refused: ledger has a truncated tail", code="truncated_ledger")
    events: list[dict[str, Any]] = []
    for raw_line in content.splitlines():
        if not raw_line:
            raise CrossingError("refused: ledger contains an empty record", code="invalid_ledger")
        events.append(validate_event(decode_json(raw_line, "crossing ledger record")))
    return events


def _scan_events(paths: CrossingPaths) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    by_source: dict[str, dict[str, Any]] = {}
    for ledger in _ledger_files(paths):
        for event in _read_ledger(ledger):
            event_id = event["event_id"]
            source_key = _source_key(event)
            if event_id in by_id:
                raise CrossingError("refused: duplicate event ID in ledger", code="ledger_identity_collision")
            if source_key in by_source:
                raise CrossingError("refused: duplicate source key in ledger", code="ledger_identity_collision")
            by_id[event_id] = event
            by_source[source_key] = event
            events.append(event)
    return events, by_id, by_source


def _relative(paths: CrossingPaths, path: pathlib.Path) -> str:
    try:
        relative = path.relative_to(paths.home).as_posix()
        path.resolve(strict=False).relative_to(paths.home.resolve(strict=False))
        return relative
    except ValueError as exc:
        raise CrossingError("refused: artifact path escapes continuity home", code="unsafe_locator") from exc


def _receipt(
    *,
    when: dt.datetime,
    outcome: str,
    operation: str = "ingest",
    event: dict[str, Any] | None = None,
    duplicate: bool = False,
    dead_letter: bool = False,
    failure: CrossingError | None = None,
    original_receipt_id: str | None = None,
    retry_of: str | None = None,
    replay_of: str | None = None,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    event_id = _safe_id(event.get("event_id"), "event:") if isinstance(event, dict) else None
    content = event.get("content") if isinstance(event, dict) else None
    transport = event.get("transport") if isinstance(event, dict) else None
    content_sha = content.get("sha256") if isinstance(content, dict) else None
    if not isinstance(content_sha, str) or not SHA256_RE.fullmatch(content_sha):
        content_sha = None
    fidelity = content.get("fidelity") if isinstance(content, dict) else None
    if fidelity not in {
        "exact_bytes_verified", "exact_bytes_unverified", "rendered_content",
        "user_relayed", "derived_summary", "unavailable",
    }:
        fidelity = None
    raw_available = transport.get("raw_bytes_available") if isinstance(transport, dict) else None
    if not isinstance(raw_available, bool):
        raw_available = None
    return {
        "schema": "gltch.crossing-receipt/0.1.0",
        "receipt_id": "receipt:" + uuid.uuid4().hex,
        "created_at": when.isoformat(),
        "operation": operation,
        "outcome": outcome,
        "event_id": event_id,
        "content_sha256": content_sha,
        "transport_fidelity": fidelity,
        "raw_bytes_available": raw_available,
        "idempotent_duplicate": duplicate,
        "dead_letter": dead_letter,
        "failure": None if failure is None else {
            "code": failure.code,
            "path": failure.path,
            "keyword": failure.keyword,
        },
        "original_receipt_id": original_receipt_id,
        "retry_of": retry_of,
        "replay_of": replay_of,
        "transaction_id": transaction_id,
        "artifact_paths": [],
    }


def _receipt_files(paths: CrossingPaths, *, dead: bool = False) -> Iterable[pathlib.Path]:
    name = "dead-letter" if dead else "receipts"
    root = paths.home / "crossings" / name
    return sorted(root.glob("*/*/*.json")) if root.exists() else []


def _read_receipt(path: pathlib.Path) -> dict[str, Any]:
    receipt = validate_receipt(decode_json(_read_bytes(path, "crossing receipt"), "crossing receipt"))
    expected_name = receipt["receipt_id"].replace(":", "-") + ".json"
    if path.name != expected_name:
        raise CrossingError("refused: receipt filename and identity disagree", code="receipt_integrity")
    return receipt


def _find_success_receipt(paths: CrossingPaths, event_id: str) -> str | None:
    for path in _receipt_files(paths):
        receipt = _read_receipt(path)
        if receipt["event_id"] == event_id and receipt["outcome"] == "success":
            return receipt["receipt_id"]
    return None


def _write_standalone_receipt(
    paths: CrossingPaths,
    when: dt.datetime,
    receipt: dict[str, Any],
    *,
    dead: bool = False,
) -> dict[str, Any]:
    kind = "crossings/dead-letter" if dead else "crossings/receipts"
    path = paths.month(kind, when) / (receipt["receipt_id"].replace(":", "-") + ".json")
    receipt["artifact_paths"] = [_relative(paths, path)]
    validate_receipt(receipt)
    try:
        _atomic_write(path, _canonical_bytes(receipt), immutable=True)
    except (OSError, CrossingError) as exc:
        raise CrossingError("refused: unable to write crossing receipt", code="receipt_write_failed") from exc
    return receipt


def _write_dead_letter(paths: CrossingPaths, when: dt.datetime, event: object, error: CrossingError) -> None:
    safe_event = event if isinstance(event, dict) else None
    receipt = _receipt(
        when=when,
        outcome="refused",
        event=safe_event,
        dead_letter=True,
        failure=error,
    )
    _write_standalone_receipt(paths, when, receipt, dead=True)


def _journal_paths(paths: CrossingPaths, journal: dict[str, Any]) -> dict[str, pathlib.Path]:
    required = ("ledger", "receipt", "staged_ledger", "staged_receipt", "previous")
    result: dict[str, pathlib.Path] = {}
    for key in required:
        value = journal.get(key)
        if not isinstance(value, str):
            raise CrossingError("refused: invalid crossing transaction journal", code="transaction_recovery")
        result[key] = _within(paths.home, value)
    return result


def _cleanup_transaction(journal_path: pathlib.Path, members: Iterable[pathlib.Path]) -> None:
    for member in members:
        member.unlink(missing_ok=True)
    journal_path.unlink(missing_ok=True)
    try:
        journal_path.parent.rmdir()
    except OSError:
        pass


def _recover_one(paths: CrossingPaths, journal_path: pathlib.Path) -> str:
    journal = decode_json(_read_bytes(journal_path, "crossing transaction journal"), "crossing transaction journal")
    if not isinstance(journal, dict):
        raise CrossingError("refused: invalid crossing transaction journal", code="transaction_recovery")
    mapped = _journal_paths(paths, journal)
    ledger = mapped["ledger"]
    receipt = mapped["receipt"]
    staged = (mapped["staged_ledger"], mapped["staged_receipt"])
    previous = mapped["previous"]
    expected_ledger = journal.get("ledger_sha256")
    expected_receipt = journal.get("receipt_sha256")
    previous_sha = journal.get("previous_sha256")
    previous_exists = journal.get("previous_exists") is True
    if not all(isinstance(value, str) and SHA256_RE.fullmatch(value) for value in (expected_ledger, expected_receipt)):
        raise CrossingError("refused: invalid crossing transaction journal", code="transaction_recovery")
    ledger_ok = ledger.exists() and _sha256(ledger.read_bytes()) == expected_ledger
    receipt_ok = receipt.exists() and _sha256(receipt.read_bytes()) == expected_receipt
    if ledger_ok and receipt_ok:
        _cleanup_transaction(journal_path, (*staged, previous))
        return "committed"
    if receipt.exists():
        if _sha256(receipt.read_bytes()) != expected_receipt:
            raise CrossingError("refused: transaction receipt changed during recovery", code="transaction_recovery")
        receipt.unlink()
    if previous_exists:
        if not isinstance(previous_sha, str) or not SHA256_RE.fullmatch(previous_sha):
            raise CrossingError("refused: invalid transaction rollback digest", code="transaction_recovery")
        if previous.exists() and _sha256(previous.read_bytes()) == previous_sha:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            os.replace(previous, ledger)
        elif not (ledger.exists() and _sha256(ledger.read_bytes()) == previous_sha):
            raise CrossingError("refused: unable to recover crossing ledger", code="transaction_recovery")
    elif ledger.exists():
        if _sha256(ledger.read_bytes()) != expected_ledger:
            raise CrossingError("refused: unexpected ledger during recovery", code="transaction_recovery")
        ledger.unlink()
    _cleanup_transaction(journal_path, (*staged, previous))
    return "rolled_back"


def _recover_transactions(paths: CrossingPaths) -> dict[str, int]:
    result = {"committed": 0, "rolled_back": 0, "orphans_removed": 0}
    root = paths.transactions
    if not root.exists():
        return result
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            raise CrossingError("refused: invalid crossing transaction entry", code="transaction_recovery")
        journal = directory / "journal.json"
        if journal.exists():
            result[_recover_one(paths, journal)] += 1
        else:
            for child in directory.iterdir():
                child.unlink(missing_ok=True)
            directory.rmdir()
            result["orphans_removed"] += 1
    return result


def _publish_staged(source: pathlib.Path, destination: pathlib.Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    _fsync_directory(destination.parent)


def _commit_event(
    paths: CrossingPaths,
    ledger: pathlib.Path,
    old_ledger: bytes,
    event: dict[str, Any],
    receipt: dict[str, Any],
    *,
    ledger_existed: bool,
) -> dict[str, Any]:
    transaction_id = "transaction:" + uuid.uuid4().hex
    receipt["transaction_id"] = transaction_id
    receipt_when = dt.datetime.fromisoformat(receipt["created_at"].replace("Z", "+00:00"))
    receipt_path = paths.month("crossings/receipts", receipt_when) / (
        receipt["receipt_id"].replace(":", "-") + ".json"
    )
    receipt["artifact_paths"] = [_relative(paths, ledger), _relative(paths, receipt_path)]
    validate_receipt(receipt)
    new_ledger = old_ledger + _canonical_bytes(event)
    receipt_bytes = _canonical_bytes(receipt)
    tx_dir = paths.transactions / transaction_id.replace(":", "-")
    tx_dir.mkdir(parents=True, exist_ok=False)
    staged_ledger = _write_temp(tx_dir, "ledger.", new_ledger)
    staged_receipt = _write_temp(tx_dir, "receipt.", receipt_bytes)
    previous = tx_dir / "previous-ledger"
    if ledger_existed:
        _atomic_write(previous, old_ledger)
    journal_path = tx_dir / "journal.json"
    journal = {
        "schema": "gltch.crossing-transaction/0.1.0",
        "transaction_id": transaction_id,
        "created_at": _now().isoformat(),
        "ledger": _relative(paths, ledger),
        "receipt": _relative(paths, receipt_path),
        "staged_ledger": _relative(paths, staged_ledger),
        "staged_receipt": _relative(paths, staged_receipt),
        "previous": _relative(paths, previous),
        "previous_exists": ledger_existed,
        "previous_sha256": _sha256(old_ledger) if ledger_existed else None,
        "ledger_sha256": _sha256(new_ledger),
        "receipt_sha256": _sha256(receipt_bytes),
    }
    try:
        _atomic_write(journal_path, _canonical_bytes(journal), immutable=True)
        _publish_staged(staged_ledger, ledger)
        _publish_staged(staged_receipt, receipt_path)
        _cleanup_transaction(journal_path, (staged_ledger, staged_receipt, previous))
        return receipt
    except Exception as exc:
        try:
            if journal_path.exists():
                _recover_one(paths, journal_path)
            else:
                _cleanup_transaction(journal_path, (staged_ledger, staged_receipt, previous))
        except Exception as recovery_exc:
            raise CrossingError(
                "refused: crossing write failed and rollback failed",
                code="transaction_recovery",
            ) from recovery_exc
        if isinstance(exc, CrossingError):
            raise
        raise CrossingError("refused: crossing transaction failed", code="transaction_write_failed") from exc


def ingest(value: object, *, home: str | pathlib.Path | None = None) -> dict[str, Any]:
    when = _now()
    paths = resolve_home(home)
    _relative(paths, paths.lock)
    _relative(paths, paths.transactions)
    with _LedgerLock(paths.lock):
        try:
            _recover_transactions(paths)
            event = validate_event(value)
            _, by_id, by_source = _scan_events(paths)
            canonical = _canonical_bytes(event, newline=False)
            event_id = event["event_id"]
            source_key = _source_key(event)
            existing = by_id.get(event_id)
            if existing is not None and _canonical_bytes(existing, newline=False) != canonical:
                raise CrossingError("refused: divergent event-ID collision", code="event_id_collision")
            source_existing = by_source.get(source_key)
            if source_existing is not None and _canonical_bytes(source_existing, newline=False) != canonical:
                raise CrossingError("refused: divergent source-key collision", code="source_key_collision")
            if existing is not None or source_existing is not None:
                original = _find_success_receipt(paths, event_id)
                if original is None:
                    raise CrossingError("refused: duplicate event has no success receipt", code="receipt_integrity")
                receipt = _receipt(
                    when=when,
                    outcome="duplicate",
                    event=event,
                    duplicate=True,
                    original_receipt_id=original,
                )
                return _write_standalone_receipt(paths, when, receipt)
            ledger = paths.month("crossings/events", when) / "events.jsonl"
            ledger_existed = ledger.exists()
            old_ledger = ledger.read_bytes() if ledger_existed else b""
            if old_ledger and not old_ledger.endswith(b"\n"):
                raise CrossingError("refused: ledger has a truncated tail", code="truncated_ledger")
            receipt = _receipt(when=when, outcome="success", event=event)
            return _commit_event(
                paths,
                ledger,
                old_ledger,
                event,
                receipt,
                ledger_existed=ledger_existed,
            )
        except CrossingError as error:
            try:
                _write_dead_letter(paths, when, value, error)
            except CrossingError as dead_error:
                if dead_error.code != "unsafe_locator":
                    raise dead_error from error
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            error = CrossingError("refused: crossing operation failed", code="crossing_operation_failed")
            _write_dead_letter(paths, when, value, error)
            raise error from exc


def verify(*, home: str | pathlib.Path | None = None) -> dict[str, Any]:
    paths = resolve_home(home)
    _relative(paths, paths.lock)
    _relative(paths, paths.transactions)
    with _LedgerLock(paths.lock):
        try:
            recovered = _recover_transactions(paths)
            events, by_id, _ = _scan_events(paths)
            receipts = [_read_receipt(path) for path in _receipt_files(paths)]
            dead_letters = [_read_receipt(path) for path in _receipt_files(paths, dead=True)]
        except CrossingError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise CrossingError(
                "refused: crossing verification failed",
                code="crossing_operation_failed",
            ) from exc
        receipt_by_id: dict[str, dict[str, Any]] = {}
        for receipt in (*receipts, *dead_letters):
            if receipt["receipt_id"] in receipt_by_id:
                raise CrossingError("refused: duplicate receipt ID", code="receipt_integrity")
            receipt_by_id[receipt["receipt_id"]] = receipt
        receipt_ids = set(receipt_by_id)
        successes: dict[str, list[dict[str, Any]]] = {}
        for receipt in receipts:
            for locator in receipt["artifact_paths"]:
                if not _within(paths.home, locator).exists():
                    raise CrossingError("refused: receipt references a missing artifact", code="receipt_integrity")
            if (
                receipt["operation"] == "ingest"
                and receipt["outcome"] == "success"
                and receipt["event_id"] is not None
            ):
                successes.setdefault(receipt["event_id"], []).append(receipt)
            for relation in ("original_receipt_id", "retry_of", "replay_of"):
                target = receipt[relation]
                if target is not None and target not in receipt_ids:
                    raise CrossingError("refused: receipt has an unknown relation", code="receipt_integrity")
            if receipt["outcome"] == "duplicate":
                original = receipt_by_id[receipt["original_receipt_id"]]
                if (
                    original["outcome"] != "success"
                    or original["event_id"] != receipt["event_id"]
                    or original["content_sha256"] != receipt["content_sha256"]
                ):
                    raise CrossingError(
                        "refused: duplicate receipt does not match its original",
                        code="receipt_integrity",
                    )
            if receipt["operation"] == "replay":
                original = receipt_by_id[receipt["replay_of"]]
                if (
                    receipt["event_id"] not in by_id
                    or original["event_id"] != receipt["event_id"]
                ):
                    raise CrossingError(
                        "refused: replay receipt does not match its original event",
                        code="receipt_integrity",
                    )
        for dead in dead_letters:
            if dead["outcome"] not in {"refused", "failed"} or dead["dead_letter"] is not True:
                raise CrossingError("refused: invalid dead-letter receipt", code="receipt_integrity")
            for locator in dead["artifact_paths"]:
                if not _within(paths.home, locator).exists():
                    raise CrossingError("refused: dead letter references a missing artifact", code="receipt_integrity")
        for event_id, event in by_id.items():
            linked = successes.get(event_id, [])
            if len(linked) != 1:
                raise CrossingError("refused: event must have exactly one success receipt", code="receipt_integrity")
            if linked[0]["content_sha256"] != event["content"]["sha256"]:
                raise CrossingError("refused: event and receipt digest disagree", code="receipt_integrity")
        for event_id in successes:
            if event_id not in by_id:
                raise CrossingError("refused: success receipt has no event", code="receipt_integrity")
        return {
            "integrity": "ok",
            "events": len(events),
            "receipts": len(receipts),
            "dead_letters": len(dead_letters),
            "transactions_recovered": recovered,
        }
