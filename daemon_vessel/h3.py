"""Offline, bounded H3 context hydration with immutable recovery records."""
from __future__ import annotations

import datetime as dt
import ctypes
import hashlib
import json
import os
import pathlib
import re
import stat
import tempfile
from collections.abc import Mapping
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from . import access_index, crossing, registry

SCHEMAS = pathlib.Path(__file__).with_name("schemas")
UTC = dt.timezone.utc
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_HOPS, MAX_RESOURCES = 4, 12
MAX_SOURCE_BYTES = 262144
_AUTHORITY = {"original": 0, "authoritative_mirror": 1, "reviewed_copy": 2, "derived": 3, "pointer": 4}
_INTENT_EDGES = {
    "current": ("contains", "relates_to", "entry_point_for"),
    "origin": ("first_appears_in", "derived_from", "contained_by"),
    "evolution": ("revises", "supersedes", "reinterpreted_in", "derived_from"),
    "difference": ("relates_to", "revises", "supersedes"),
    "correction": ("revises", "reinterpreted_in", "derived_from"),
    "project": ("contains", "contained_by", "entry_point_for", "relates_to"),
    "room": ("contains", "contained_by", "relates_to"),
    "open": ("contains", "relates_to", "entry_point_for", "derived_from"),
}


class H3Error(crossing.CrossingError):
    """Content-free H3 refusal suitable for a public CLI."""


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return dt.datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _id(kind: str, material: str) -> str:
    return f"{kind}:h3-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:40]}"


def _local_refs(value: object) -> None:
    if isinstance(value, dict):
        if "$ref" in value and (not isinstance(value["$ref"], str) or not value["$ref"].startswith("#")):
            raise H3Error("refused: H3 schema has external reference", code="external_schema_reference")
        for child in value.values(): _local_refs(child)
    elif isinstance(value, list):
        for child in value: _local_refs(child)


def _validator(filename: str) -> Draft202012Validator:
    try:
        schema = json.loads((SCHEMAS / filename).read_text(encoding="utf-8")); _local_refs(schema)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, format_checker=FormatChecker())
    except H3Error: raise
    except Exception as exc: raise H3Error("refused: invalid H3 schema", code="schema_validation") from exc


REQUEST = _validator("context-request.schema.json")
PACKET = _validator("context-packet-v0.2.schema.json")
CANDIDATE = _validator("candidate-delta.schema.json")
RECEIPT = _validator("h3-receipt.schema.json")
HISTORICAL_DAEMON_PACKET = _validator("context-packet.schema.json")
HISTORICAL_RETURN_POINT_PACKET = _validator("context-packet-return-point-v0.1.schema.json")


def _valid(validator: Draft202012Validator, value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or list(validator.iter_errors(value)):
        raise H3Error(f"refused: invalid {label}", code="schema_validation")
    return value


def validate_packet_version(value: object, boundary: str) -> dict[str, Any]:
    """Explicit version/boundary dispatch; 0.1.0's collision is never inferred."""
    if not isinstance(value, dict) or not isinstance(value.get("schema"), str):
        raise H3Error("refused: invalid context packet", code="schema_validation")
    choices = {
        "h3": ("gltch.context-packet/0.2.0", PACKET),
        "daemon-vessel-0.1": ("gltch.context-packet/0.1.0", HISTORICAL_DAEMON_PACKET),
        "return-point-0.1": ("gltch.context-packet/0.1.0", HISTORICAL_RETURN_POINT_PACKET),
    }
    expected = choices.get(boundary)
    if expected is None or value["schema"] != expected[0]:
        raise H3Error("refused: context packet version is not accepted at this boundary", code="schema_validation")
    return _valid(expected[1], value, "context packet")


def _read_snapshot(path: str | pathlib.Path, expected: str, validator: Draft202012Validator, label: str) -> tuple[bytes, dict[str, Any]]:
    if not SHA256.fullmatch(expected): raise H3Error("refused: invalid expected digest", code="invalid_expected_digest")
    try: raw = pathlib.Path(path).read_bytes()
    except OSError as exc: raise H3Error(f"refused: unreadable {label}", code="unreadable_input") from exc
    if hashlib.sha256(raw).hexdigest() != expected: raise H3Error("refused: source digest mismatch", code="source_digest_mismatch")
    try: value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc: raise H3Error(f"refused: invalid {label}", code="invalid_json") from exc
    if raw != _canonical(value):
        raise H3Error(f"refused: noncanonical {label} bytes", code="noncanonical_input")
    return raw, _valid(validator, value, label)


def request_validate(source: str | pathlib.Path, expected: str) -> dict[str, Any]: return _read_snapshot(source, expected, REQUEST, "request")[1]
def candidate_validate(source: str | pathlib.Path, expected: str) -> dict[str, Any]:
    value = _read_snapshot(source, expected, CANDIDATE, "candidate")[1]
    if value["sha256"] != _digest({key: child for key, child in value.items() if key != "sha256"}):
        raise H3Error("refused: candidate digest mismatch", code="source_digest_mismatch")
    return value
def _home(home: str | pathlib.Path) -> pathlib.Path: return crossing.resolve_home(home).home


def _safe_relative(locator: object) -> pathlib.PurePosixPath:
    if not isinstance(locator, str) or not locator or "\x00" in locator or locator.startswith(("//", "\\\\")) or ":" in locator or "?" in locator or "#" in locator:
        raise H3Error("refused: unsafe local locator", code="unsafe_locator")
    result = pathlib.PurePosixPath(locator.replace("\\", "/"))
    if result.is_absolute() or ".." in result.parts or not result.parts: raise H3Error("refused: unsafe local locator", code="unsafe_locator")
    return result


def _inside(home: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    root, resolved = home.resolve(strict=False), path.resolve(strict=False)
    try: resolved.relative_to(root)
    except ValueError as exc: raise H3Error("refused: path escapes external home", code="unsafe_locator") from exc
    return resolved


def _relative(home: pathlib.Path, path: pathlib.Path) -> str: return _inside(home, path).relative_to(home.resolve(strict=False)).as_posix()


def _reparse_or_link(path: pathlib.Path) -> bool:
    """Reject every link-like path component, including Windows reparse points."""
    try:
        info = path.lstat()
    except OSError:
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & reparse)


def _windows_handle_path(path: pathlib.Path | None = None, fd: int | None = None) -> str:
    """Return the resolved Win32 object path from an opened handle, or refuse."""
    if os.name != "nt":
        raise OSError("Windows handle helper used on a non-Windows platform")
    kernel = ctypes.windll.kernel32
    handle = None
    owned = False
    if fd is not None:
        import msvcrt
        handle = msvcrt.get_osfhandle(fd)
    elif path is not None:
        handle = kernel.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x02000000, None)
        if handle == -1:
            raise OSError("unable to open Windows home handle")
        owned = True
    else:
        raise OSError("missing Windows handle target")
    try:
        size = kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not size:
            raise OSError("unable to resolve Windows handle")
        buffer = ctypes.create_unicode_buffer(size + 1)
        if not kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0):
            raise OSError("unable to resolve Windows handle")
        return buffer.value.rstrip("\\").casefold()
    finally:
        if owned:
            kernel.CloseHandle(handle)


def _date_path(home: pathlib.Path, group: str, stamp: str, identity: str) -> pathlib.Path:
    try: when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc: raise H3Error("refused: invalid timestamp", code="schema_validation") from exc
    return home / "context" / group / f"{when:%Y}" / f"{when:%m}" / (identity.replace(":", "-") + ".json")


def _write_immutable(path: pathlib.Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == content: return False
        raise H3Error("refused: immutable artifact collision", code="immutable_collision")
    fd, name = tempfile.mkstemp(prefix=".h3-", dir=path.parent); temp = pathlib.Path(name)
    try:
        with os.fdopen(fd, "wb") as stream: stream.write(content); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            if path.read_bytes() == content: return False
            raise H3Error("refused: immutable artifact collision", code="immutable_collision")
        os.replace(temp, path); return True
    finally: temp.unlink(missing_ok=True)


def _write_json(path: pathlib.Path, value: object) -> bool: return _write_immutable(path, _canonical(value))


def _json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise H3Error("refused: installed inputs unavailable", code="installed_input_invalid") from exc
    if not isinstance(value, dict):
        raise H3Error("refused: installed inputs unavailable", code="installed_input_invalid")
    return value


def _digest_file(raw: bytes, label: str) -> str:
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
        raise H3Error("refused: installed inputs unavailable", code="installed_input_invalid") from exc
    if not re.fullmatch(r"[0-9a-f]{64}\n", text):
        raise H3Error("refused: installed inputs unavailable", code="installed_input_invalid")
    return text[:-1]


def _installed(home: pathlib.Path) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Consume only the public installed-evidence snapshot boundaries."""
    try:
        registry_snapshot = registry.validate_installed_snapshot(home=home)
        index_snapshot = access_index.validate_installed_snapshot(registry_snapshot.value, home=home)
    except (OSError, ValueError, registry.RegistryError, access_index.AccessIndexError, H3Error) as exc:
        if isinstance(exc, H3Error): raise
        raise H3Error("refused: installed inputs unavailable", code="installed_input_invalid") from exc
    return index_snapshot.value, registry_snapshot.value, index_snapshot.sha256, registry_snapshot.sha256


def _transaction_id(request_id: str) -> str: return _id("transaction", request_id)
def _receipt_id(kind: str, request_id: str, packet_id: str | None, candidate_id: str | None, suffix: str = "") -> str: return _id("receipt", "\0".join((kind, request_id, str(packet_id), str(candidate_id), suffix)))


def _receipt(*, kind: str, outcome: str, request_id: str, packet_id: str | None, candidate_id: str | None, index_sha: str, registry_sha: str, paths: list[str], failure: str | None = None, original: str | None = None, suffix: str = "", created_at: str | None = None) -> dict[str, Any]:
    # A candidate receipt needs its stable receipt id before its final path can
    # be calculated; callers replace this schema-valid staging path atomically.
    value = {"schema":"gltch.h3-receipt/0.1.0", "receipt_id":_receipt_id(kind, request_id, packet_id, candidate_id, suffix), "created_at":created_at or _now(), "transaction_id":_transaction_id(request_id), "kind":kind, "outcome":outcome, "request_id":request_id, "packet_id":packet_id, "candidate_id":candidate_id, "access_index_sha256":index_sha, "registry_sha256":registry_sha, "artifact_paths":paths or ["context/transactions/pending"], "failure":failure, "original_receipt_id":original, "sha256":"0"*64}
    value["sha256"] = _digest({key: child for key, child in value.items() if key != "sha256"})
    return _valid(RECEIPT, value, "H3 receipt")


def _artifacts(home: pathlib.Path, group: str) -> Iterable[pathlib.Path]:
    root = home / "context" / group
    if not root.exists(): return []
    found: list[pathlib.Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise H3Error("refused: unexpected H3 artifact path", code="receipt_integrity")
        if path.is_dir():
            if len(relative.parts) not in {1, 2}:
                raise H3Error("refused: unexpected H3 artifact directory", code="receipt_integrity")
            continue
        if len(relative.parts) != 3 or path.suffix != ".json":
            raise H3Error("refused: unexpected H3 artifact path", code="receipt_integrity")
        found.append(path)
    return found


def _read_artifact(path: pathlib.Path, validator: Draft202012Validator, label: str) -> dict[str, Any]:
    try: raw = path.read_bytes(); value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise H3Error(f"refused: invalid H3 {label} artifact", code="receipt_integrity") from exc
    value = _valid(validator, value, f"H3 {label}")
    if raw != _canonical(value): raise H3Error("refused: noncanonical H3 artifact", code="receipt_integrity")
    return value


def _phase_value(path: pathlib.Path, required: dict[str, object]) -> None:
    try: raw = path.read_bytes(); value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise H3Error("refused: invalid H3 transaction phase", code="receipt_integrity") from exc
    if not isinstance(value, dict) or raw != _canonical(value) or set(value) != set(required) or any(value.get(key) != expected for key, expected in required.items()):
        raise H3Error("refused: invalid H3 transaction phase", code="receipt_integrity")


def _find(home: pathlib.Path, group: str, key: str, identity: str, validator: Draft202012Validator) -> tuple[pathlib.Path, dict[str, Any]] | None:
    hits = []
    for path in _artifacts(home, group):
        value = _read_artifact(path, validator, group)
        if value.get(key) == identity: hits.append((path, value))
    if len(hits) > 1: raise H3Error("refused: duplicate H3 artifact identity", code="receipt_integrity")
    return hits[0] if hits else None


def _phase(home: pathlib.Path, request_id: str, phase: str, value: dict[str, Any]) -> None:
    _write_json(home / "context" / "transactions" / _transaction_id(request_id).replace(":", "-") / f"{phase}.json", value)


def _resource_locators(resource: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], pathlib.PurePosixPath]]:
    """Return all eligible local locators in stable authority order.

    Pointer locators deliberately remain out of this list: they are evidence of
    a thin resource, never hydrated source content.
    """
    if resource.get("locator_state") != "resolved": return []
    locators = [value for value in resource.get("locators", []) if isinstance(value, Mapping)]
    locators.sort(key=lambda value: (_AUTHORITY.get(value.get("authority"), 99), value.get("uri", "")))
    selected = []
    for locator in locators:
        if locator.get("status") != "active" or locator.get("access_mode") != "local" or locator.get("authority") == "pointer": continue
        try: selected.append((locator, _safe_relative(locator.get("uri"))))
        except H3Error: continue
    return selected


def _resource_locator(resource: Mapping[str, Any]) -> tuple[Mapping[str, Any], pathlib.PurePosixPath] | None:
    """Compatibility helper for callers that need only the first candidate."""
    choices = _resource_locators(resource)
    return choices[0] if choices else None


def _tokens(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _source_read(home: pathlib.Path, resource: dict[str, Any], locator: dict[str, Any], relative: pathlib.PurePosixPath, remaining_chars: int, remaining_tokens: int | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Bounded, non-network source read. Returns source, unresolved, ledger, thin."""
    resource_id = resource["id"]
    candidate = home / relative
    path = _inside(home, candidate)
    unresolved: list[dict[str, Any]] = []; ledger: list[dict[str, Any]] = []
    parts = list(relative.parts)
    components = [home.joinpath(*parts[:count]) for count in range(1, len(parts) + 1)]
    if any(_reparse_or_link(component) for component in components) or any(parent.joinpath(".git").exists() for parent in (candidate.parent, *candidate.parents)):
        return None, [{"code":"known_unreachable","detail":"approved local source unavailable","source_refs":[resource_id]}], ledger, True
    # Windows has no POSIX O_NOFOLLOW open flag.  Its lstat/reparse checks plus
    # handle identity checks below are the platform-specific equivalent.  POSIX
    # must have both O_NOFOLLOW and O_DIRECTORY: without them, containment is
    # not provable and acquisition fails closed.
    nofollow = 0 if os.name == "nt" else getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or (os.name != "nt" and directory is None):
        return None, [{"code":"known_unreachable","detail":"approved local source cannot be safely opened","source_refs":[resource_id]}], ledger, True
    home_fd = None; directory_fds: list[int] = []
    try:
        # Component-by-component openat traversal keeps the verified descriptor
        # chain authoritative.  No multi-component child path is ever opened
        # relative to home, so an ancestor replacement cannot redirect the
        # final source open outside the directory chain we actually hold.
        if os.name != "nt":
            home_fd = os.open(home, os.O_RDONLY | directory | nofollow)
            home_before = os.fstat(home_fd)
            if not stat.S_ISDIR(home_before.st_mode):
                raise OSError("continuity home is not a directory")
            parent_fd = home_fd
            for component in parts[:-1]:
                if component in {"", ".", ".."}:
                    raise OSError("unsafe local locator component")
                child_fd = os.open(component, os.O_RDONLY | directory | nofollow, dir_fd=parent_fd)
                child_stat = os.fstat(child_fd)
                if not stat.S_ISDIR(child_stat.st_mode):
                    os.close(child_fd); raise OSError("source ancestor is not a directory")
                directory_fds.append(child_fd); parent_fd = child_fd
            leaf = parts[-1] if parts else ""
            if leaf in {"", ".", ".."}:
                raise OSError("unsafe local locator leaf")
            fd = os.open(leaf, os.O_RDONLY | nofollow, dir_fd=parent_fd)
        else:
            home_before = os.stat(home, follow_symlinks=False)
            fd = os.open(candidate, os.O_RDONLY | nofollow)
    except OSError:
        for directory_fd in directory_fds: os.close(directory_fd)
        if home_fd is not None: os.close(home_fd)
        return None, [{"code":"known_unreachable","detail":"approved local source unreadable","source_refs":[resource_id]}], ledger, True
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return None, [{"code":"known_unreachable","detail":"approved local source unavailable","source_refs":[resource_id]}], ledger, True
        # A single os.read is not a completeness guarantee: pipes, networked
        # filesystems, and test seams may return short reads.  Bound every read
        # and continue until EOF or the one-byte overflow sentinel is reached.
        chunks: list[bytes] = []
        remaining = MAX_SOURCE_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        # Windows needs an extra path identity check because its standard
        # descriptor APIs cannot express POSIX openat component traversal.
        current = os.stat(candidate, follow_symlinks=False) if os.name == "nt" else after
        home_after = os.fstat(home_fd) if home_fd is not None else os.stat(home, follow_symlinks=False)
        if os.name == "nt":
            opened_home = _windows_handle_path(path=home)
            opened_source = _windows_handle_path(fd=fd)
            if opened_source != opened_home and not opened_source.startswith(opened_home + "\\"):
                return None, [{"code":"known_unreachable","detail":"approved local source escaped opened home","source_refs":[resource_id]}], ledger, True
    except OSError:
        return None, [{"code":"known_unreachable","detail":"approved local source unreadable","source_refs":[resource_id]}], ledger, True
    finally:
        os.close(fd)
        for directory_fd in reversed(directory_fds): os.close(directory_fd)
        if home_fd is not None: os.close(home_fd)
    if ((before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size) or
            (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino) or
            (home_before.st_dev, home_before.st_ino) != (home_after.st_dev, home_after.st_ino)):
        return None, [{"code":"known_unreachable","detail":"approved local source changed during acquisition","source_refs":[resource_id]}], ledger, True
    if len(raw) > MAX_SOURCE_BYTES:
        return None, [{"code":"budget_exhausted","detail":"bounded source byte ceiling reached","source_refs":[resource_id]}], ledger, True
    if not raw:
        return None, [{"code":"known_unreachable","detail":"approved local source is empty","source_refs":[resource_id]}], ledger, True
    suffix = candidate.suffix.lower()
    if suffix not in {".txt", ".md", ".json", ".jsonl"}:
        return None, [{"code":"unsupported_media","detail":"approved local source media is unsupported","source_refs":[resource_id]}], ledger, True
    actual, expected = hashlib.sha256(raw).hexdigest(), locator.get("content_hash")
    if isinstance(expected, str) and SHA256.fullmatch(expected) and expected != actual:
        return None, [{"code":"digest_mismatch","detail":"approved local source digest mismatch","source_refs":[resource_id]}], ledger, True
    try: text = raw.decode("utf-8")
    except UnicodeError:
        return None, [{"code":"unsupported_media","detail":"approved local source is not UTF-8 text","source_refs":[resource_id]}], ledger, True
    if suffix == ".json":
        try: json.loads(text)
        except json.JSONDecodeError:
            return None, [{"code":"unsupported_media","detail":"approved JSON source is invalid","source_refs":[resource_id]}], ledger, True
    if remaining_chars <= 0:
        return None, [{"code":"budget_exhausted","detail":"character ceiling reached","source_refs":[resource_id]}], ledger, True
    excerpt = text[:remaining_chars]
    if remaining_tokens is not None and _tokens(excerpt) > remaining_tokens:
        words = re.findall(r"\S+\s*", excerpt); excerpt = "".join(words[:remaining_tokens])
    if not excerpt:
        return None, [{"code":"budget_exhausted","detail":"token ceiling reached","source_refs":[resource_id]}], ledger, True
    source_id = _id("source", resource_id)
    truncated = len(excerpt) != len(text)
    ledger.append({"operation":"extract" if truncated else "none", "source_refs":[source_id], "output_ref":f"{source_id}#chars=0:{len(excerpt)}", "fidelity_loss":"inline excerpt truncated; resource digest remains separately verified" if truncated else "none"})
    if truncated:
        unresolved.append({"code":"budget_exhausted","detail":"bounded inline extraction truncated","source_refs":[resource_id]})
    return {"source_id":source_id,"resource_id":resource_id,"event_id":None,"actor_id":None,"speaker_position":"unknown","authority":locator["authority"],"continuity_role":resource["continuity_role"],"privacy":resource["privacy"],"provenance":"installed_local_source","fidelity":"exact_bytes_verified" if isinstance(expected, str) and SHA256.fullmatch(expected) else "exact_bytes_unverified","content":{"media_type":"application/json" if suffix == ".json" else "text/plain","sha256":actual,"inline":excerpt,"ref":None,"chars":len(excerpt)}}, unresolved, ledger, truncated


def _hydrate(request: dict[str, Any], home: pathlib.Path, index: dict[str, Any], resources: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = {item.get("id"):item for item in index.get("entries", []) if isinstance(item, Mapping) and isinstance(item.get("id"), str)}
    catalog = {item.get("id"):item for item in resources.get("resources", []) if isinstance(item, Mapping) and isinstance(item.get("id"), str)}
    unknown = [seed for seed in request["seed_ids"] if seed not in nodes]
    if unknown: raise H3Error("refused: explicit seed is unknown", code="unknown_seed")
    queue = [(seed, 0, "seed") for seed in request["seed_ids"]]; visited: list[str] = []; used: list[str] = []; sources: list[dict[str, Any]] = []; traversal: list[dict[str, Any]] = []; unresolved: list[dict[str, Any]] = []; ledger: list[dict[str, Any]] = []
    while queue:
        node_id, hop, _ = queue.pop(0)
        if node_id in visited: continue
        if hop > request["budgets"]["max_hops"] or hop > MAX_HOPS: unresolved.append({"code":"budget_exhausted","detail":"hop ceiling reached","source_refs":[node_id]}); continue
        node = nodes[node_id]
        if node.get("status", "active") != "active":
            unresolved.append({"code":"access_not_available","detail":"access index entry is not active","source_refs":[node_id]}); continue
        entry_namespaces = set(node.get("namespaces", []))
        if entry_namespaces and not entry_namespaces.intersection(request["namespaces"]):
            unresolved.append({"code":"namespace_refused","detail":"access index namespace is not allowed","source_refs":[node_id]}); continue
        visited.append(node_id)
        before = len(sources)
        unresolved_before = len(unresolved)
        for resource_id in node.get("resource_ids", []):
            if resource_id in used: continue
            if len(used) >= min(request["budgets"]["max_resources"], MAX_RESOURCES): unresolved.append({"code":"budget_exhausted","detail":"resource ceiling reached","source_refs":[resource_id]}); break
            resource = catalog.get(resource_id)
            if resource is None: raise H3Error("refused: installed index has unknown resource", code="installed_input_invalid")
            if resource.get("privacy") not in request["allowed_privacy"]: unresolved.append({"code":"privacy_refused","detail":"source privacy is not allowed","source_refs":[resource_id]}); continue
            used.append(resource_id); choices = _resource_locators(resource)
            if not choices:
                code = "pointer_dead_end" if any(locator.get("authority") == "pointer" and locator.get("status") == "active" for locator in resource.get("locators", []) if isinstance(locator, Mapping)) else "access_not_available"
                unresolved.append({"code":code,"detail":"no approved local locator","source_refs":[resource_id]}); continue
            remaining = request["budgets"]["max_chars"] - sum(row["content"]["chars"] for row in sources)
            token_limit = request["budgets"]["max_tokens"] or None
            remaining_tokens = None if token_limit is None else token_limit - sum(_tokens(row["content"]["inline"] or "") for row in sources)
            for locator, relative in choices:
                source, outcomes, operations, _thin = _source_read(home, resource, locator, relative, remaining, remaining_tokens)
                unresolved.extend(outcomes); ledger.extend(operations)
                if source is not None:
                    sources.append(source)
                    break
        # A hard resource/character/token ceiling ends traversal immediately.
        # If the thin branch carried explicit fallbacks, record each prevented
        # fallback rather than silently dropping an otherwise relevant edge.
        exhausted = any(row["code"] == "budget_exhausted" for row in unresolved[unresolved_before:])
        if exhausted:
            if len(sources) == before:
                for fallback in node.get("follow_if_thin", []):
                    target = fallback.get("entry_id") if isinstance(fallback, Mapping) else None
                    if isinstance(target, str) and target not in visited:
                        unresolved.append({"code":"budget_exhausted","detail":"budget ceiling reached before thin fallback traversal","source_refs":[node_id,target]})
            break
        thin = len(sources) == before
        if thin:
            for fallback in node.get("follow_if_thin", []):
                target = fallback.get("entry_id") if isinstance(fallback, Mapping) else None
                if not isinstance(target, str) or target in visited:
                    continue
                if hop + 1 <= min(request["budgets"]["max_hops"], MAX_HOPS):
                    queue.append((target, hop+1, "fallback")); traversal.append({"from":node_id,"to":target,"edge":"follow_if_thin","reason":"ordered installed fallback after thin branch","hop":hop+1})
                else:
                    unresolved.append({"code":"budget_exhausted","detail":"hop ceiling reached before thin fallback traversal","source_refs":[node_id,target]})
        for edge in _INTENT_EDGES[request["intent"]]:
            for target in node.get("links", {}).get(edge, []):
                if target in visited:
                    continue
                if hop + 1 <= min(request["budgets"]["max_hops"], MAX_HOPS):
                    queue.append((target, hop+1, edge)); traversal.append({"from":node_id,"to":target,"edge":edge,"reason":"intent-relevant installed typed edge","hop":hop+1})
                else:
                    unresolved.append({"code":"budget_exhausted","detail":"hop ceiling reached before relevant edge traversal","source_refs":[node_id,target]})
    return sources, visited, used, traversal, unresolved, ledger


def _make_packet(request: dict[str, Any], home: pathlib.Path, index: dict[str, Any], resources: dict[str, Any]) -> dict[str, Any]:
    sources, nodes, used, traversal, unresolved, ledger = _hydrate(request, home, index, resources); packet_id = _id("packet", request["request_id"])
    omissions = [{"kind":"source","reason":row["detail"],"source_refs":row["source_refs"]} for row in unresolved]
    value = {"schema":"gltch.context-packet/0.2.0","packet_id":packet_id,"created_at":request["created_at"],"request":{key:request[key] for key in ("request_id","task","intent","surface_id","room_id","namespaces","allowed_privacy","seed_ids")},"purpose":"bounded local context hydration","orientation":"deterministic explicit-seed traversal with ordered thin fallback","status":"grounded" if sources and not unresolved else "partial" if sources else "unresolved","sha256":"0"*64,"seeds":request["seed_ids"],"access_index_nodes":nodes,"registry_resources":used,"sources":sources,"traversal":traversal,"corrections":[],"open_commitments":[],"unresolved":unresolved,"runtime_seam":[],"budgets":{**request["budgets"],"used_hops":max([0,*[row["hop"] for row in traversal]]),"used_resources":len(used),"used_chars":sum(row["content"]["chars"] for row in sources)},"omissions":omissions,"compaction_ledger":ledger or [{"operation":"none","source_refs":[],"output_ref":None,"fidelity_loss":"none"}],"mutation_authorized":False,"canonical_effect":"none","construction_receipt_id":_receipt_id("construction",request["request_id"],packet_id,None)}
    value["sha256"] = _digest({key:child for key,child in value.items() if key != "sha256"}); return _valid(PACKET, value, "H3 packet")


def _verify_existing_packet_for_recovery(root: pathlib.Path, request_path: pathlib.Path, raw_request: bytes, request: dict[str, Any], packet_path: pathlib.Path, packet: dict[str, Any], index: dict[str, Any], resources: dict[str, Any], index_sha: str, registry_sha: str) -> tuple[pathlib.Path, dict[str, Any]]:
    """Prove an extant packet before repairing its receipt or phase boundary."""
    if request_path.read_bytes() != raw_request or _read_artifact(request_path, REQUEST, "request") != request:
        raise H3Error("refused: persisted H3 request differs", code="receipt_integrity")
    if _inside(root, packet_path) != _inside(root, _date_path(root, "packets", packet["created_at"], packet["packet_id"])):
        raise H3Error("refused: existing H3 packet placement mismatch", code="receipt_integrity")
    if packet["sha256"] != _digest({key:value for key,value in packet.items() if key != "sha256"}):
        raise H3Error("refused: existing H3 packet digest mismatch", code="receipt_integrity")
    expected_packet = _make_packet(request, root, index, resources)
    if packet != expected_packet or _canonical(packet) != _canonical(expected_packet):
        raise H3Error("refused: existing H3 packet reconstruction mismatch", code="receipt_integrity")
    receipt_path = _date_path(root,"receipts",packet["created_at"],packet["construction_receipt_id"])
    expected_receipt = _receipt(kind="construction",outcome="constructed",request_id=request["request_id"],packet_id=packet["packet_id"],candidate_id=None,index_sha=index_sha,registry_sha=registry_sha,paths=[_relative(root,request_path),_relative(root,packet_path),_relative(root,receipt_path)],created_at=packet["created_at"])
    if receipt_path.exists() and _read_artifact(receipt_path, RECEIPT, "receipt") != expected_receipt:
        raise H3Error("refused: existing H3 construction receipt differs", code="receipt_integrity")
    transaction = root / "context" / "transactions" / _transaction_id(request["request_id"]).replace(":", "-")
    for name, value in (("packet", {"packet_id":packet["packet_id"],"packet_path":_relative(root,packet_path)}), ("construction", {"receipt_id":packet["construction_receipt_id"],"receipt_path":_relative(root,receipt_path)})):
        phase_path = transaction / f"{name}.json"
        if phase_path.exists(): _phase_value(phase_path, value)
    return receipt_path, expected_receipt


def _derive_duplicate_receipt(root: pathlib.Path, candidate_path: pathlib.Path, candidate: dict[str, Any], original_path: pathlib.Path, original: dict[str, Any], index_sha: str, registry_sha: str) -> tuple[pathlib.Path, dict[str, Any], pathlib.Path, dict[str, Any]]:
    """Derive the sole attributable duplicate receipt from durable bytes."""
    candidate_raw = candidate_path.read_bytes(); original_raw = original_path.read_bytes()
    if candidate_raw != _canonical(candidate) or original_raw != _canonical(original):
        raise H3Error("refused: noncanonical duplicate derivation input", code="receipt_integrity")
    attempt = hashlib.sha256(candidate_raw).hexdigest()
    if (original["kind"] != "candidate" or original["candidate_id"] != candidate["candidate_id"] or
            original["packet_id"] != candidate["packet_id"] or original["request_id"] != candidate["request_id"]):
        raise H3Error("refused: duplicate original receipt lineage", code="receipt_integrity")
    receipt = _receipt(kind="duplicate",outcome="duplicate",request_id=candidate["request_id"],packet_id=candidate["packet_id"],candidate_id=candidate["candidate_id"],index_sha=index_sha,registry_sha=registry_sha,paths=[],original=original["receipt_id"],suffix=attempt,created_at=candidate["created_at"])
    receipt_path = _date_path(root,"receipts",receipt["created_at"],receipt["receipt_id"])
    receipt["artifact_paths"]=[_relative(root,candidate_path),_relative(root,receipt_path)]
    receipt["sha256"]=_digest({key:value for key,value in receipt.items() if key != "sha256"})
    phase_path = root / "context" / "transactions" / _transaction_id(candidate["request_id"]).replace(":", "-") / f"duplicate-{receipt['receipt_id'].replace(':', '-')}.json"
    phase = {"receipt_id":receipt["receipt_id"],"receipt_path":_relative(root,receipt_path),"original_receipt_id":original["receipt_id"]}
    return receipt_path, receipt, phase_path, phase


def _refusal(home: pathlib.Path, request_id: str, packet_id: str | None, candidate_id: str | None, index_sha: str, registry_sha: str, code: str, *, attempt_digest: str | None = None, created_at: str | None = None) -> tuple[pathlib.Path, dict[str, Any]]:
    # The condition is identity material, not merely receipt payload: distinct
    # refusal causes for one request must have distinct immutable paths.
    attempt = attempt_digest or hashlib.sha256((request_id + "\0" + code).encode("utf-8")).hexdigest()
    if not SHA256.fullmatch(attempt):
        raise H3Error("refused: invalid refusal attempt identity", code="receipt_integrity")
    receipt = _receipt(kind="refusal",outcome="refused",request_id=request_id,packet_id=packet_id,candidate_id=candidate_id,index_sha=index_sha,registry_sha=registry_sha,paths=[],failure=code,suffix=code + "\0" + attempt,created_at=created_at)
    path = _date_path(home,"dead-letter",receipt["created_at"],receipt["receipt_id"]); receipt["artifact_paths"]=[_relative(home,path)]; receipt["sha256"]=_digest({key:child for key,child in receipt.items() if key != "sha256"}); _write_json(path,receipt)
    _phase(home, request_id, f"refusal-{receipt['receipt_id'].replace(':', '-')}", {"receipt_id":receipt["receipt_id"],"receipt_path":_relative(home,path),"failure":code,"attempt_digest":attempt})
    return path, receipt


def build(source: str | pathlib.Path, expected: str, home: str | pathlib.Path) -> dict[str, Any]:
    raw, request = _read_snapshot(source,expected,REQUEST,"request")
    if request["budgets"]["max_hops"] > MAX_HOPS or request["budgets"]["max_resources"] > MAX_RESOURCES: raise H3Error("refused: H3 budget exceeds hard limit",code="budget_refused")
    root = _home(home)
    with crossing.serialization_lock(root / "context" / ".h3.lock"):
        index, resources, index_sha, registry_sha = _installed(root); request_path = _date_path(root,"requests",request["created_at"],request["request_id"])
        for previous_path in _artifacts(root, "requests"):
            previous = _read_artifact(previous_path, REQUEST, "request")
            if previous["request_id"] == request["request_id"] and previous_path != request_path:
                _refusal(root, request["request_id"], None, None, index_sha, registry_sha, "request_collision", attempt_digest=hashlib.sha256(raw).hexdigest(), created_at=request["created_at"])
                raise H3Error("refused: divergent request collision", code="immutable_collision")
            if previous["request_id"] == request["request_id"] and previous_path.read_bytes() != raw:
                _refusal(root, request["request_id"], None, None, index_sha, registry_sha, "request_collision", attempt_digest=hashlib.sha256(raw).hexdigest(), created_at=request["created_at"])
                raise H3Error("refused: divergent request collision", code="immutable_collision")
        _write_immutable(request_path,raw); _phase(root,request["request_id"],"request",{"request_id":request["request_id"],"request_path":_relative(root,request_path)})
        existing = _find(root,"packets","packet_id",_id("packet",request["request_id"]),PACKET)
        if existing is not None:
            if existing[1]["request"]["request_id"] != request["request_id"]: raise H3Error("refused: divergent packet collision",code="immutable_collision")
            packet_path, packet = existing
            receipt_path, receipt = _verify_existing_packet_for_recovery(root,request_path,raw,request,packet_path,packet,index,resources,index_sha,registry_sha)
            _write_json(receipt_path,receipt)
            _phase(root,request["request_id"],"packet",{"packet_id":packet["packet_id"],"packet_path":_relative(root,packet_path)})
            _phase(root,request["request_id"],"construction",{"receipt_id":receipt["receipt_id"],"receipt_path":_relative(root,receipt_path)})
            return packet
        try: packet = _make_packet(request,root,index,resources)
        except H3Error as exc: _refusal(root,request["request_id"],None,None,index_sha,registry_sha,exc.code or "build_refused",attempt_digest=hashlib.sha256(raw).hexdigest(),created_at=request["created_at"]); raise
        packet_path = _date_path(root,"packets",packet["created_at"],packet["packet_id"]); receipt_path = _date_path(root,"receipts",packet["created_at"],packet["construction_receipt_id"])
        receipt = _receipt(kind="construction",outcome="constructed",request_id=request["request_id"],packet_id=packet["packet_id"],candidate_id=None,index_sha=index_sha,registry_sha=registry_sha,paths=[_relative(root,request_path),_relative(root,packet_path),_relative(root,receipt_path)],created_at=packet["created_at"])
        _write_json(packet_path,packet); _phase(root,request["request_id"],"packet",{"packet_id":packet["packet_id"],"packet_path":_relative(root,packet_path)}); _write_json(receipt_path,receipt); _phase(root,request["request_id"],"construction",{"receipt_id":receipt["receipt_id"],"receipt_path":_relative(root,receipt_path)}); return packet


def append_h3_candidate(source: str | pathlib.Path, expected: str, home: str | pathlib.Path) -> dict[str, Any]:
    raw,candidate = _read_snapshot(source,expected,CANDIDATE,"candidate")
    if candidate["sha256"] != _digest({key:value for key,value in candidate.items() if key != "sha256"}): raise H3Error("refused: candidate digest mismatch",code="source_digest_mismatch")
    root = _home(home)
    with crossing.serialization_lock(root / "context" / ".h3.lock"):
        index,resources,index_sha,registry_sha = _installed(root); linked = _find(root,"packets","packet_id",candidate["packet_id"],PACKET)
        if linked is None or linked[1]["request"]["request_id"] != candidate["request_id"]:
            _refusal(root,candidate["request_id"],candidate["packet_id"],candidate["candidate_id"],index_sha,registry_sha,"packet_link_invalid",attempt_digest=hashlib.sha256(raw).hexdigest(),created_at=candidate["created_at"])
            raise H3Error("refused: candidate packet link unavailable",code="packet_link_invalid")
        packet_request = linked[1]["request"]
        source_ids = {source["source_id"] for source in linked[1]["sources"]}
        candidate_refs = set(candidate["evidence_source_refs"])
        proposal_refs = {reference for proposal in candidate["proposals"] for reference in proposal["evidence_refs"]}
        if (candidate["surface_id"] != packet_request["surface_id"] or candidate["room_id"] != packet_request["room_id"] or
                not candidate_refs <= source_ids or not proposal_refs <= candidate_refs):
            _refusal(root,candidate["request_id"],candidate["packet_id"],candidate["candidate_id"],index_sha,registry_sha,"candidate_lineage_invalid",attempt_digest=hashlib.sha256(raw).hexdigest(),created_at=candidate["created_at"])
            raise H3Error("refused: candidate lineage is invalid",code="packet_link_invalid")
        existing = _find(root,"candidates","candidate_id",candidate["candidate_id"],CANDIDATE)
        # Candidate admission is never based on a merely schema-valid packet.
        # Existing candidates are reconciled below, including a missing receipt phase.
        # Both admission and repair re-establish the immutable request/packet/
        # construction chain.  Only the candidate boundary itself may be
        # incomplete during recovery.
        boundary = None
        if existing is not None:
            receipt_count = sum(1 for path in _artifacts(root, "receipts") if _read_artifact(path, RECEIPT, "receipt").get("kind") == "candidate" and _read_artifact(path, RECEIPT, "receipt").get("candidate_id") == candidate["candidate_id"])
            marker = root / "context" / "transactions" / _transaction_id(candidate["request_id"]).replace(":", "-") / f"candidate-{candidate['candidate_id'].replace(':', '-')}.json"
            boundary = "complete" if receipt_count == 1 and marker.exists() else "receipt_only" if receipt_count == 1 else "phase_only" if marker.exists() else "candidate_only"
        # An exception is permitted only for an actually incomplete candidate
        # boundary.  A complete retry is verified normally and remains a no-op.
        has_duplicate = existing is not None and any(_read_artifact(path,RECEIPT,"receipt").get("kind") == "duplicate" and _read_artifact(path,RECEIPT,"receipt").get("candidate_id") == candidate["candidate_id"] for path in _artifacts(root,"receipts"))
        recovery_id = candidate["candidate_id"] if existing is not None and not has_duplicate and boundary in {"candidate_only", "receipt_only"} else None
        duplicate_recovery_id = None
        if existing is not None and boundary == "complete":
            duplicates = [(path,value) for path,value in ((path,_read_artifact(path,RECEIPT,"receipt")) for path in _artifacts(root,"receipts")) if value.get("kind") == "duplicate" and value.get("candidate_id") == candidate["candidate_id"]]
            if len(duplicates) == 1:
                duplicate_phase = root / "context" / "transactions" / _transaction_id(candidate["request_id"]).replace(":", "-") / f"duplicate-{duplicates[0][1]['receipt_id'].replace(':', '-')}.json"
                if not duplicate_phase.exists(): duplicate_recovery_id = candidate["candidate_id"]
        _verify_under_lock(root, recovery_candidate_id=recovery_id, recovery_boundary=boundary if recovery_id is not None else None, recovery_duplicate_candidate_id=duplicate_recovery_id, recovery_duplicate_boundary="duplicate_phase_only" if duplicate_recovery_id is not None else None)
        if existing is not None:
            if existing[1] == candidate:
                receipt_values = [_read_artifact(path, RECEIPT, "receipt") for path in _artifacts(root, "receipts")]
                original_receipt = next((value for value in receipt_values if value.get("kind") == "candidate" and value.get("candidate_id") == candidate["candidate_id"]), None)
                phase_name = f"candidate-{candidate['candidate_id'].replace(':', '-')}"
                if original_receipt is None:
                    candidate_path = existing[0]
                    original_receipt = _receipt(kind="candidate",outcome="candidate_recorded",request_id=candidate["request_id"],packet_id=candidate["packet_id"],candidate_id=candidate["candidate_id"],index_sha=index_sha,registry_sha=registry_sha,paths=[],created_at=candidate["created_at"])
                    receipt_path = _date_path(root,"receipts",original_receipt["created_at"],original_receipt["receipt_id"])
                    original_receipt["artifact_paths"]=[_relative(root,linked[0]),_relative(root,candidate_path),_relative(root,receipt_path)]
                    original_receipt["sha256"]=_digest({key:value for key,value in original_receipt.items() if key != "sha256"})
                    _write_json(receipt_path,original_receipt)
                    _phase(root,candidate["request_id"],phase_name,{"candidate_id":candidate["candidate_id"],"candidate_path":_relative(root,candidate_path),"receipt_id":original_receipt["receipt_id"]})
                    return candidate
                _phase(root,candidate["request_id"],phase_name,{"candidate_id":candidate["candidate_id"],"candidate_path":_relative(root,existing[0]),"receipt_id":original_receipt["receipt_id"]})
                # A byte-identical occurrence is attributable, not a no-op:
                # one deterministic duplicate receipt and phase are derived
                # from the durable candidate/original-receipt bytes.
                original_path = next(path for path in _artifacts(root,"receipts") if _read_artifact(path,RECEIPT,"receipt").get("receipt_id") == original_receipt["receipt_id"])
                duplicate_path, duplicate, duplicate_phase, duplicate_phase_value = _derive_duplicate_receipt(root,existing[0],candidate,original_path,original_receipt,index_sha,registry_sha)
                _write_json(duplicate_path,duplicate)
                _write_json(duplicate_phase,duplicate_phase_value)
                return candidate
            _refusal(root,candidate["request_id"],candidate["packet_id"],candidate["candidate_id"],index_sha,registry_sha,"candidate_collision",attempt_digest=hashlib.sha256(raw).hexdigest(),created_at=candidate["created_at"]); raise H3Error("refused: divergent candidate collision",code="immutable_collision")
        candidate_path = _date_path(root,"candidates",candidate["created_at"],candidate["candidate_id"]); receipt = _receipt(kind="candidate",outcome="candidate_recorded",request_id=candidate["request_id"],packet_id=candidate["packet_id"],candidate_id=candidate["candidate_id"],index_sha=index_sha,registry_sha=registry_sha,paths=[],created_at=candidate["created_at"]); receipt_path = _date_path(root,"receipts",receipt["created_at"],receipt["receipt_id"]); receipt["artifact_paths"]=[_relative(root,linked[0]),_relative(root,candidate_path),_relative(root,receipt_path)]; receipt["sha256"]=_digest({key:value for key,value in receipt.items() if key != "sha256"}); _write_immutable(candidate_path,raw); _write_json(receipt_path,receipt); _phase(root,candidate["request_id"],f"candidate-{candidate['candidate_id'].replace(':','-')}",{"candidate_id":candidate["candidate_id"],"candidate_path":_relative(root,candidate_path),"receipt_id":receipt["receipt_id"]}); return candidate


candidate_append = append_h3_candidate


def _verify_under_lock(home: str | pathlib.Path, *, recovery_candidate_id: str | None = None, recovery_boundary: str | None = None, recovery_duplicate_candidate_id: str | None = None, recovery_duplicate_boundary: str | None = None) -> dict[str, Any]:
    root = _home(home); index,resources,index_sha,registry_sha = _installed(root); packets: dict[str,tuple[pathlib.Path,dict[str,Any]]] = {}; candidates: dict[str,tuple[pathlib.Path,dict[str,Any]]] = {}; receipts: dict[str,tuple[pathlib.Path,dict[str,Any]]] = {}
    context = root / "context"
    if context.exists():
        allowed_groups = {"requests", "packets", "candidates", "receipts", "dead-letter", "transactions", ".h3.lock"}
        if any(path.name not in allowed_groups for path in context.iterdir()):
            raise H3Error("refused: unknown H3 context group", code="receipt_integrity")
    transactions = context / "transactions"
    if transactions.exists():
        for path in transactions.rglob("*"):
            relative = path.relative_to(transactions)
            if (path.is_dir() and len(relative.parts) != 1) or (path.is_file() and (len(relative.parts) != 2 or path.suffix != ".json")):
                raise H3Error("refused: unexpected H3 transaction path", code="receipt_integrity")
    for group,validator,key,target in (("packets",PACKET,"packet_id",packets),("candidates",CANDIDATE,"candidate_id",candidates),("receipts",RECEIPT,"receipt_id",receipts),("dead-letter",RECEIPT,"receipt_id",receipts)):
        for path in _artifacts(root,group):
            value = _read_artifact(path,validator,group)
            if (group == "receipts" and value["kind"] == "refusal") or (group == "dead-letter" and value["kind"] != "refusal"):
                raise H3Error("refused: H3 receipt stored in wrong group", code="receipt_integrity")
            if value["sha256"] != _digest({item:content for item,content in value.items() if item != "sha256"}): raise H3Error("refused: H3 artifact digest mismatch",code="receipt_integrity")
            if value[key] in target or _inside(root,path) != _inside(root,_date_path(root,group,value["created_at"],value[key])): raise H3Error("refused: H3 artifact identity or placement mismatch",code="receipt_integrity")
            target[value[key]]=(path,value)
    requests: dict[str, pathlib.Path] = {}
    for path in _artifacts(root, "requests"):
        request = _read_artifact(path, REQUEST, "request")
        request_id = request["request_id"]
        if request_id in requests or _inside(root, path) != _inside(root, _date_path(root, "requests", request["created_at"], request_id)):
            raise H3Error("refused: H3 request identity or placement mismatch", code="receipt_integrity")
        requests[request_id] = path
    # One typed global registry prevents a schema-valid artifact from being
    # reused under another kind or transaction identity.
    global_ids: dict[str, tuple[str, pathlib.Path, dict[str, Any] | None]] = {}
    def register(identity: str, kind: str, path: pathlib.Path, value: dict[str, Any] | None) -> None:
        if identity in global_ids:
            raise H3Error("refused: duplicate cross-kind H3 artifact identity", code="receipt_integrity")
        global_ids[identity] = (kind, path, value)
    for identity, path in requests.items():
        register(identity, "request", path, _read_artifact(path, REQUEST, "request"))
    for identity, (path, value) in packets.items(): register(identity, "packet", path, value)
    for identity, (path, value) in candidates.items(): register(identity, "candidate", path, value)
    for identity, (path, value) in receipts.items(): register(identity, "receipt", path, value)
    if recovery_candidate_id is not None:
        if recovery_boundary not in {"candidate_only", "receipt_only"} or recovery_candidate_id not in candidates:
            raise H3Error("refused: invalid recovery exception", code="receipt_integrity")
    if recovery_duplicate_candidate_id is not None:
        if recovery_duplicate_boundary != "duplicate_phase_only" or recovery_duplicate_candidate_id not in candidates:
            raise H3Error("refused: invalid duplicate recovery exception", code="receipt_integrity")
    for _,packet in packets.values():
        receipt = receipts.get(packet["construction_receipt_id"])
        if packet["request"]["request_id"] not in requests or receipt is None: raise H3Error("refused: incomplete H3 construction chain",code="receipt_integrity")
        immutable_request = _read_artifact(requests[packet["request"]["request_id"]], REQUEST, "request")
        request_projection = {key: immutable_request[key] for key in ("request_id","task","intent","surface_id","room_id","namespaces","allowed_privacy","seed_ids")}
        if (packet["request"] != request_projection or packet["seeds"] != immutable_request["seed_ids"] or
                any(packet["budgets"].get(key) != immutable_request["budgets"][key] for key in ("max_hops","max_resources","max_chars","max_tokens"))):
            raise H3Error("refused: immutable H3 request projection mismatch", code="receipt_integrity")
        value = receipt[1]
        if (value["kind"] != "construction" or value["packet_id"] != packet["packet_id"] or
                value["request_id"] != packet["request"]["request_id"] or value["access_index_sha256"] != index_sha or
                value["registry_sha256"] != registry_sha or
                set(value["artifact_paths"]) != {_relative(root, requests[packet["request"]["request_id"]]), _relative(root, packets[packet["packet_id"]][0]), _relative(root, receipt[0])}):
            raise H3Error("refused: invalid H3 construction linkage",code="receipt_integrity")
        used_hops = max([0, *[edge["hop"] for edge in packet["traversal"]]])
        used_chars = sum(source["content"]["chars"] for source in packet["sources"])
        used_tokens = sum(_tokens(source["content"]["inline"] or "") for source in packet["sources"])
        expected_omissions = [{"kind":"source","reason":row["detail"],"source_refs":row["source_refs"]} for row in packet["unresolved"]]
        source_ids = {source["source_id"] for source in packet["sources"]}
        ledger_valid = all(set(row["source_refs"]) <= source_ids and (row["operation"] != "extract" or isinstance(row["output_ref"], str)) for row in packet["compaction_ledger"])
        extracted = {ref for row in packet["compaction_ledger"] if row["operation"] == "extract" for ref in row["source_refs"]}
        source_hashes_valid = all(
            source["content"]["chars"] == len(source["content"]["inline"] or "") and
            (source["source_id"] in extracted or source["fidelity"] != "exact_bytes_verified" or hashlib.sha256((source["content"]["inline"] or "").encode("utf-8")).hexdigest() == source["content"]["sha256"])
            for source in packet["sources"]
        )
        if (value["transaction_id"] != _transaction_id(packet["request"]["request_id"]) or packet["budgets"]["max_hops"] > MAX_HOPS or packet["budgets"]["max_resources"] > MAX_RESOURCES or
                packet["budgets"]["used_hops"] != used_hops or packet["budgets"]["used_resources"] != len(packet["registry_resources"]) or packet["budgets"]["used_chars"] != used_chars or
                (packet["budgets"]["max_tokens"] != 0 and used_tokens > packet["budgets"]["max_tokens"]) or packet["omissions"] != expected_omissions or not ledger_valid or not source_hashes_valid):
            raise H3Error("refused: invalid H3 packet semantics",code="receipt_integrity")
        # The packet is not self-authenticating merely because its own digest
        # and local arithmetic agree.  Re-run the deterministic hydration
        # model from the durable request and installed evidence, then compare
        # the complete canonical packet.  This is the reconstruction boundary
        # for traversal, source acquisition, omissions and compaction.
        try:
            reconstructed = _make_packet(immutable_request, root, index, resources)
        except H3Error as exc:
            raise H3Error("refused: H3 packet reconstruction failed", code="receipt_integrity") from exc
        if packet != reconstructed:
            raise H3Error("refused: reconstructed H3 packet differs", code="receipt_integrity")
        if any(source["authority"] not in {"original","authoritative_mirror","reviewed_copy","derived","pointer"} or source["resource_id"] not in packet["registry_resources"] or any(token in str(source).lower() for token in ("file://", "password=", "token=", "signature=")) for source in packet["sources"]): raise H3Error("refused: unsafe H3 packet provenance",code="receipt_integrity")
        transaction = root / "context" / "transactions" / _transaction_id(packet["request"]["request_id"]).replace(":", "-")
        _phase_value(transaction / "request.json", {"request_id":packet["request"]["request_id"], "request_path":_relative(root, requests[packet["request"]["request_id"]])})
        _phase_value(transaction / "packet.json", {"packet_id":packet["packet_id"], "packet_path":_relative(root, packets[packet["packet_id"]][0])})
        _phase_value(transaction / "construction.json", {"receipt_id":value["receipt_id"], "receipt_path":_relative(root, receipt[0])})
    for candidate_path, candidate in candidates.values():
        if candidate["sha256"] != _digest({key:value for key,value in candidate.items() if key != "sha256"}):
            raise H3Error("refused: H3 candidate digest mismatch", code="receipt_integrity")
        if candidate["packet_id"] not in packets or packets[candidate["packet_id"]][1]["request"]["request_id"] != candidate["request_id"]:
            raise H3Error("refused: invalid H3 candidate chain",code="receipt_integrity")
        packet_path, packet = packets[candidate["packet_id"]]
        request = packet["request"]
        source_ids = {source["source_id"] for source in packet["sources"]}
        candidate_refs = set(candidate["evidence_source_refs"])
        proposal_refs = {reference for proposal in candidate["proposals"] for reference in proposal["evidence_refs"]}
        candidate_receipts = [(path, value) for path, value in receipts.values() if value["kind"] == "candidate" and value["candidate_id"] == candidate["candidate_id"]]
        transaction = root / "context" / "transactions" / _transaction_id(candidate["request_id"]).replace(":", "-")
        marker = transaction / f"candidate-{candidate['candidate_id'].replace(':', '-')}.json"
        state = "complete" if len(candidate_receipts) == 1 and marker.exists() else "receipt_only" if len(candidate_receipts) == 1 else "phase_only" if marker.exists() else "candidate_only"
        if state == "phase_only":
            raise H3Error("refused: phase without candidate receipt", code="receipt_integrity")
        if candidate["candidate_id"] == recovery_candidate_id and recovery_boundary != state:
            raise H3Error("refused: wrong candidate recovery boundary", code="receipt_integrity")
        # Recovery may name exactly one incomplete candidate boundary.  Any
        # unrelated incomplete candidate remains an integrity failure.
        if candidate["candidate_id"] == recovery_candidate_id and state in {"candidate_only", "phase_only"}:
            continue
        if (candidate["surface_id"] != request["surface_id"] or candidate["room_id"] != request["room_id"] or
                not candidate_refs <= source_ids or not proposal_refs <= candidate_refs or len(candidate_receipts) != 1):
            raise H3Error("refused: invalid H3 candidate chain",code="receipt_integrity")
        candidate_receipt_path, candidate_receipt = candidate_receipts[0]
        if (candidate_receipt["request_id"] != candidate["request_id"] or candidate_receipt["packet_id"] != candidate["packet_id"] or
                candidate_receipt["access_index_sha256"] != index_sha or candidate_receipt["registry_sha256"] != registry_sha or
                set(candidate_receipt["artifact_paths"]) != {_relative(root, packet_path), _relative(root, candidate_path), _relative(root, candidate_receipt_path)}):
            raise H3Error("refused: invalid H3 candidate receipt",code="receipt_integrity")
        if marker.exists() or candidate["candidate_id"] != recovery_candidate_id:
            _phase_value(marker, {"candidate_id":candidate["candidate_id"], "candidate_path":_relative(root,candidate_path), "receipt_id":candidate_receipt["receipt_id"]})
        duplicates = [(path,value) for path,value in receipts.values() if value["kind"] == "duplicate" and value["candidate_id"] == candidate["candidate_id"]]
        if len(duplicates) > 1:
            raise H3Error("refused: surplus H3 duplicate receipt", code="receipt_integrity")
        if duplicates:
            duplicate_path, duplicate = duplicates[0]
            expected_path, expected_duplicate, duplicate_phase, expected_phase = _derive_duplicate_receipt(root,candidate_path,candidate,candidate_receipt_path,candidate_receipt,index_sha,registry_sha)
            if duplicate_path != expected_path or duplicate != expected_duplicate:
                raise H3Error("refused: duplicate receipt reconstruction mismatch", code="receipt_integrity")
            if duplicate_phase.exists():
                _phase_value(duplicate_phase, expected_phase)
            elif recovery_duplicate_candidate_id != candidate["candidate_id"]:
                raise H3Error("refused: missing duplicate receipt phase", code="receipt_integrity")
    for receipt_path,receipt in receipts.values():
        if receipt["transaction_id"] != _transaction_id(receipt["request_id"]): raise H3Error("refused: H3 receipt transaction mismatch",code="receipt_integrity")
        if receipt["access_index_sha256"] != index_sha or receipt["registry_sha256"] != registry_sha: raise H3Error("refused: H3 receipt installed-input mismatch",code="receipt_integrity")
        if receipt["packet_id"] is not None and receipt["packet_id"] not in packets and receipt["kind"] != "refusal": raise H3Error("refused: dangling H3 receipt packet",code="receipt_integrity")
        if receipt["candidate_id"] is not None and receipt["candidate_id"] not in candidates and receipt["kind"] != "refusal": raise H3Error("refused: dangling H3 receipt candidate",code="receipt_integrity")
        for artifact in receipt["artifact_paths"]:
            if not _inside(root,root/_safe_relative(artifact)).exists(): raise H3Error("refused: missing receipt-linked artifact",code="receipt_integrity")
        if receipt["kind"] == "duplicate":
            original = receipts.get(receipt["original_receipt_id"])
            if (original is None or original[1]["kind"] != "candidate" or
                    original[1]["candidate_id"] != receipt["candidate_id"] or original[1]["packet_id"] != receipt["packet_id"]):
                raise H3Error("refused: invalid H3 duplicate receipt lineage",code="receipt_integrity")
            candidate_path = candidates[receipt["candidate_id"]][0]
            if set(receipt["artifact_paths"]) != {_relative(root, candidate_path), _relative(root, receipt_path)}:
                raise H3Error("refused: duplicate receipt claims canonical artifact", code="receipt_integrity")
        if receipt["kind"] == "refusal":
            if set(receipt["artifact_paths"]) != {_relative(root, receipt_path)}:
                raise H3Error("refused: refusal receipt claims canonical artifact", code="receipt_integrity")
            marker = root / "context" / "transactions" / _transaction_id(receipt["request_id"]).replace(":", "-") / f"refusal-{receipt['receipt_id'].replace(':', '-')}.json"
            try:
                phase = json.loads(marker.read_text(encoding="utf-8"))
                attempt = phase.get("attempt_digest") if isinstance(phase, dict) else None
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise H3Error("refused: invalid refusal phase", code="receipt_integrity") from exc
            if not isinstance(attempt, str) or not SHA256.fullmatch(attempt) or receipt["receipt_id"] != _receipt_id("refusal", receipt["request_id"], receipt["packet_id"], receipt["candidate_id"], receipt["failure"] + "\0" + attempt):
                raise H3Error("refused: refusal identity reconstruction failed", code="receipt_integrity")
            _phase_value(marker, {"receipt_id":receipt["receipt_id"], "receipt_path":_relative(root, receipt_path), "failure":receipt["failure"], "attempt_digest":attempt})
    for receipt_path, receipt in receipts.values():
        if _relative(root, receipt_path) not in receipt["artifact_paths"]: raise H3Error("refused: receipt does not link itself",code="receipt_integrity")
    # Reconstruct reverse lineage: durable request, packet, and candidate
    # artifacts are each claimed exactly once by their owning receipt chain.
    claims: dict[str, int] = {}
    for _, receipt in receipts.values():
        if receipt["kind"] in {"duplicate", "refusal"}:
            continue
        for artifact in receipt["artifact_paths"]:
            claims[artifact] = claims.get(artifact, 0) + 1
    packet_request_ids = {packet["request"]["request_id"] for _, packet in packets.values()}
    singleton = {_relative(root, requests[request_id]) for request_id in packet_request_ids} | {_relative(root, path) for path, _ in candidates.values()}
    if recovery_candidate_id in candidates:
        singleton.discard(_relative(root, candidates[recovery_candidate_id][0]))
    packet_paths = {_relative(root, path) for path, _ in packets.values()}
    if any(claims.get(path, 0) != 1 for path in singleton) or any(claims.get(path, 0) < 1 for path in packet_paths):
        raise H3Error("refused: incomplete reverse H3 lineage", code="receipt_integrity")
    # A refusal may be attributable to a durable request (build failed after
    # request publication) or to an attempted candidate whose packet/request
    # was never canonicalized.  Only the former claims a request artifact.
    referenced_requests = {packet["request"]["request_id"] for _, packet in packets.values()}
    referenced_requests |= {receipt["request_id"] for _, receipt in receipts.values() if receipt["kind"] == "refusal" and receipt["request_id"] in requests}
    if set(requests) != referenced_requests:
        raise H3Error("refused: orphan H3 request artifact", code="receipt_integrity")
    expected_phases: set[pathlib.Path] = set()
    for packet_path, packet in packets.values():
        transaction = transactions / _transaction_id(packet["request"]["request_id"]).replace(":", "-")
        expected_phases.update({transaction / "request.json", transaction / "packet.json", transaction / "construction.json"})
    for candidate_path, candidate in candidates.values():
        complete = any(value["kind"] == "candidate" and value["candidate_id"] == candidate["candidate_id"] for _, value in receipts.values())
        marker = transactions / _transaction_id(candidate["request_id"]).replace(":", "-") / f"candidate-{candidate['candidate_id'].replace(':', '-')}.json"
        if complete and (marker.exists() or candidate["candidate_id"] != recovery_candidate_id):
            expected_phases.add(transactions / _transaction_id(candidate["request_id"]).replace(":", "-") / f"candidate-{candidate['candidate_id'].replace(':', '-')}.json")
        for receipt_path, receipt in receipts.values():
            if receipt["kind"] == "duplicate" and receipt["candidate_id"] == candidate["candidate_id"]:
                phase = transactions / _transaction_id(candidate["request_id"]).replace(":", "-") / f"duplicate-{receipt['receipt_id'].replace(':', '-')}.json"
                if not (recovery_duplicate_candidate_id == candidate["candidate_id"] and recovery_duplicate_boundary == "duplicate_phase_only" and not phase.exists()):
                    expected_phases.add(phase)
    for receipt_path, receipt in receipts.values():
        if receipt["kind"] == "refusal":
            transaction = transactions / _transaction_id(receipt["request_id"]).replace(":", "-")
            if receipt["request_id"] in requests:
                expected_phases.add(transaction / "request.json")
                _phase_value(transaction / "request.json", {"request_id":receipt["request_id"], "request_path":_relative(root,requests[receipt["request_id"]])})
            expected_phases.add(transaction / f"refusal-{receipt['receipt_id'].replace(':', '-')}.json")
    actual_phases = set(transactions.rglob("*.json")) if transactions.exists() else set()
    expected_transactions = {phase.parent for phase in expected_phases}
    actual_transactions = {path for path in transactions.iterdir() if path.is_dir()} if transactions.exists() else set()
    if actual_phases != expected_phases or actual_transactions != expected_transactions:
        raise H3Error("refused: unexpected or missing H3 transaction phase", code="receipt_integrity")
    return {"integrity":"ok","h3_packets":len(packets),"h3_candidates":len(candidates),"h3_receipts":len(receipts)}


def verify(home: str | pathlib.Path) -> dict[str, Any]:
    """Verify a stable serialized H3 snapshot through the public lock boundary."""
    root = _home(home)
    with crossing.serialization_lock(root / "context" / ".h3.lock"):
        return _verify_under_lock(root)
