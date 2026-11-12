"""Private, local Access Index installation and bounded resolution."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import tempfile
from dataclasses import dataclass

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from . import registry


class AccessIndexError(RuntimeError):
    """A deliberate refusal to install, validate, or resolve an Access Index."""


class AccessIndexDigestMismatchError(AccessIndexError):
    pass


class AccessIndexSchemaError(AccessIndexError):
    pass


class DuplicateEntryIdError(AccessIndexError):
    pass


class UnknownEntryIdError(AccessIndexError):
    pass


MODULE_DIR = pathlib.Path(__file__).resolve().parent
REPOSITORY_ROOT = MODULE_DIR.parent
FORMAT_CHECKER = FormatChecker()


if "date-time" not in FORMAT_CHECKER.checkers:
    @FORMAT_CHECKER.checks("date-time")
    def _is_date_time(value: object) -> bool:
        if not isinstance(value, str) or "T" not in value or not re.search(r"(?:Z|[+-]\\d{2}:\\d{2})$", value):
            return False
        try:
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True


@dataclass(frozen=True)
class AccessIndexPaths:
    home: pathlib.Path

    @property
    def schema(self) -> pathlib.Path:
        return self.home / "contracts" / "access-index.schema.json"

    @property
    def index(self) -> pathlib.Path:
        return self.home / "index" / "access-index.json"

    @property
    def digest(self) -> pathlib.Path:
        return self.home / "index" / "access-index.sha256"

    @property
    def receipt(self) -> pathlib.Path:
        return self.home / "index" / "install-receipt.json"

    @property
    def receipt_digest(self) -> pathlib.Path:
        return self.home / "index" / "install-receipt.sha256"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_home(home: str | pathlib.Path | None, *, repository_root: pathlib.Path = REPOSITORY_ROOT) -> AccessIndexPaths:
    selected = home if home is not None else os.environ.get("GLITCH_CONTINUITY_HOME")
    if not selected:
        raise AccessIndexError("refused: GLITCH_CONTINUITY_HOME or --home is required")
    candidate = pathlib.Path(selected).expanduser().resolve(strict=False)
    root = repository_root.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        pass
    else:
        raise AccessIndexError("refused: continuity home cannot be inside the daemon-vessel repository")
    if any(parent.joinpath(".git").exists() for parent in (candidate, *candidate.parents)):
        raise AccessIndexError("refused: continuity home cannot be within a Git worktree")
    return AccessIndexPaths(candidate)


def _read_bytes(path: pathlib.Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise AccessIndexError(f"refused: missing {label}") from exc
    except OSError as exc:
        raise AccessIndexError(f"refused: unreadable {label}") from exc


def _decode_json(content: bytes, label: str) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AccessIndexSchemaError(f"refused: unreadable {label}") from exc


def _validation_error(error: object) -> AccessIndexSchemaError:
    path = "$"
    for part in getattr(error, "absolute_path", ()):
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if isinstance(part, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) else ".<property>")
    return AccessIndexSchemaError(f"refused: access index validation failed at {path} ({getattr(error, 'validator', None) or 'unknown'})")


def _validate_schema(index: object, schema: object) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        if not isinstance(schema, dict) or not isinstance(schema.get("$id"), str):
            raise AccessIndexSchemaError("refused: invalid access index schema")
        refs = Registry().with_resource(schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012))
        errors = list(Draft202012Validator(schema, registry=refs, format_checker=FORMAT_CHECKER).iter_errors(index))
    except SchemaError as exc:
        raise AccessIndexSchemaError("refused: invalid access index schema") from exc
    except Unresolvable as exc:
        raise AccessIndexSchemaError("refused: unresolved schema reference") from exc
    if errors:
        raise _validation_error(errors[0])


def _validate_semantics(index: object, installed_registry: dict) -> None:
    if not isinstance(index, dict) or not isinstance(index.get("entries"), list):
        raise AccessIndexSchemaError("refused: access index root must be an object")
    entries = index["entries"]
    if not 12 <= len(entries) <= 20:
        raise AccessIndexSchemaError("refused: access index must contain 12 through 20 entries")
    ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    if len(ids) != len(entries) or len(ids) != len(set(ids)):
        raise DuplicateEntryIdError("refused: duplicate stable entry IDs")
    entry_ids = set(ids)
    resource_ids = {item.get("id") for item in installed_registry.get("resources", []) if isinstance(item, dict)}
    for entry in entries:
        for resource_id in entry.get("resource_ids", []):
            if resource_id not in resource_ids:
                raise AccessIndexSchemaError("refused: unknown registered resource ID")
        for targets in entry.get("links", {}).values():
            if any(target not in entry_ids for target in targets):
                raise AccessIndexSchemaError("refused: unknown typed-link target")
        if any(item.get("entry_id") not in entry_ids for item in entry.get("follow_if_thin", [])):
            raise AccessIndexSchemaError("refused: unknown fallback target")
    graph = {entry["id"]: [item["entry_id"] for item in entry.get("follow_if_thin", [])] for entry in entries}
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node: str) -> None:
        if node in visiting:
            raise AccessIndexSchemaError("refused: follow_if_thin cycle")
        if node not in visited:
            visiting.add(node)
            for target in graph[node]: visit(target)
            visiting.remove(node); visited.add(node)
    for node in graph: visit(node)


def _atomic_write(path: pathlib.Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent); temp = pathlib.Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists(): temp.unlink()


def _backup(paths: AccessIndexPaths) -> None:
    existing = [path for path in (paths.schema, paths.index, paths.digest, paths.receipt, paths.receipt_digest) if path.exists()]
    if not existing: return
    backup = paths.home / f"access-index-backup-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    if backup.exists(): raise AccessIndexError("refused: backup destination already exists")
    for source in existing:
        destination = backup / source.relative_to(paths.home); destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination)


def _replace_complete_installation(paths: AccessIndexPaths, contents: dict[pathlib.Path, bytes]) -> None:
    """Replace all live files or restore the exact former installation."""
    previous: dict[pathlib.Path, bytes | None] = {}
    for path in contents:
        previous[path] = path.read_bytes() if path.exists() else None
    try:
        for path, content in contents.items():
            _atomic_write(path, content)
    except OSError as exc:
        rollback_error = None
        for path, old in previous.items():
            try:
                if old is None:
                    if path.exists(): path.unlink()
                else:
                    _atomic_write(path, old)
            except OSError as restore_exc:
                rollback_error = restore_exc
        if rollback_error:
            raise AccessIndexError("refused: access index write failed and rollback failed") from rollback_error
        raise AccessIndexError("refused: unable to write access index installation") from exc


def install(*, source: pathlib.Path, schema_source: pathlib.Path, expected_sha256: str, home: str | pathlib.Path | None = None, replace: bool = False) -> AccessIndexPaths:
    paths = resolve_home(home)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise AccessIndexDigestMismatchError("refused: --expect-sha256 must be a lowercase SHA-256 digest")
    source_bytes = _read_bytes(source, "source access index"); schema_bytes = _read_bytes(schema_source, "source schema")
    actual = _sha256(source_bytes)
    if actual != expected_sha256: raise AccessIndexDigestMismatchError("refused: access index digest does not match --expect-sha256")
    index = _decode_json(source_bytes, "source access index"); schema = _decode_json(schema_bytes, "source schema")
    _validate_schema(index, schema)
    try:
        _, installed_registry = registry.validate(home=paths.home)
    except registry.RegistryError as exc:
        raise AccessIndexError("refused: installed Resource Registry is missing or invalid") from exc
    _validate_semantics(index, installed_registry)
    existing = any(path.exists() for path in (paths.schema, paths.index, paths.digest, paths.receipt))
    if existing and not replace: raise AccessIndexError("refused: access index already installed; pass --replace to preserve a backup and replace it")
    if existing: _backup(paths)
    schema_digest = _sha256(schema_bytes)
    receipt = {"schema_id": schema.get("$id") if isinstance(schema, dict) else None, "index_sha256": actual, "schema_sha256": schema_digest, "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "entry_count": len(index["entries"]), "source_filenames": {"index": source.name, "schema": schema_source.name}}
    receipt_bytes = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _replace_complete_installation(paths, {
        paths.schema: schema_bytes, paths.index: source_bytes, paths.digest: f"{actual}\n".encode("ascii"),
        paths.receipt: receipt_bytes, paths.receipt_digest: f"{_sha256(receipt_bytes)}\n".encode("ascii"),
    })
    return paths


def validate(*, home: str | pathlib.Path | None = None) -> tuple[AccessIndexPaths, dict]:
    paths = resolve_home(home)
    index_bytes = _read_bytes(paths.index, "installed access index"); schema_bytes = _read_bytes(paths.schema, "installed schema"); receipt_bytes = _read_bytes(paths.receipt, "installation receipt"); digest_bytes = _read_bytes(paths.digest, "installed access index digest"); receipt_digest_bytes = _read_bytes(paths.receipt_digest, "installed receipt digest")
    index = _decode_json(index_bytes, "installed access index"); schema = _decode_json(schema_bytes, "installed schema"); receipt = _decode_json(receipt_bytes, "installation receipt")
    try: digest = digest_bytes.decode("ascii")
    except UnicodeError as exc: raise AccessIndexDigestMismatchError("refused: malformed installed access index digest") from exc
    if not re.fullmatch(r"[0-9a-f]{64}\n", digest): raise AccessIndexDigestMismatchError("refused: malformed installed access index digest")
    try: receipt_digest = receipt_digest_bytes.decode("ascii")
    except UnicodeError as exc: raise AccessIndexDigestMismatchError("refused: malformed installed receipt digest") from exc
    if not re.fullmatch(r"[0-9a-f]{64}\n", receipt_digest): raise AccessIndexDigestMismatchError("refused: malformed installed receipt digest")
    actual = _sha256(index_bytes); schema_digest = _sha256(schema_bytes)
    if not isinstance(receipt, dict) or digest[:-1] != actual or receipt.get("index_sha256") != actual: raise AccessIndexDigestMismatchError("refused: installed access index digest mismatch")
    if receipt_digest[:-1] != _sha256(receipt_bytes): raise AccessIndexDigestMismatchError("refused: installed receipt digest mismatch")
    if receipt.get("schema_sha256") != schema_digest or not isinstance(schema, dict) or receipt.get("schema_id") != schema.get("$id"): raise AccessIndexDigestMismatchError("refused: installed schema digest mismatch")
    _validate_schema(index, schema)
    try: _, installed_registry = registry.validate(home=paths.home)
    except registry.RegistryError as exc: raise AccessIndexError("refused: installed Resource Registry is missing or invalid") from exc
    _validate_semantics(index, installed_registry)
    return paths, index


def resolve(entry_id: str, *, home: str | pathlib.Path | None = None) -> dict:
    _, index = validate(home=home)
    for entry in index["entries"]:
        if entry["id"] == entry_id: return entry
    raise UnknownEntryIdError("refused: unknown entry ID")
