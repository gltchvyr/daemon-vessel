"""Private, local Resource Registry installation and resolution.

The registry is intentionally never bundled with this repository: callers must
name an external continuity home explicitly.
"""
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


class RegistryError(RuntimeError):
    """A deliberate refusal to install, validate, or resolve a registry."""


class MissingContinuityHomeError(RegistryError):
    pass


class MissingRegistryFileError(RegistryError):
    pass


class RegistryDigestMismatchError(RegistryError):
    pass


class RegistrySchemaError(RegistryError):
    pass


class DuplicateResourceIdError(RegistryError):
    pass


class UnknownResourceIdError(RegistryError):
    pass


MODULE_DIR = pathlib.Path(__file__).resolve().parent
REPOSITORY_ROOT = MODULE_DIR.parent
FORMAT_CHECKER = FormatChecker()


if "date-time" not in FORMAT_CHECKER.checkers:
    @FORMAT_CHECKER.checks("date-time")
    def _is_date_time(value: object) -> bool:
        if not isinstance(value, str) or "T" not in value or not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", value):
            return False
        try:
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True


@dataclass(frozen=True)
class RegistryPaths:
    home: pathlib.Path

    @property
    def schema(self) -> pathlib.Path:
        return self.home / "contracts" / "resource-registry.schema.json"

    @property
    def registry(self) -> pathlib.Path:
        return self.home / "registry" / "resources.json"

    @property
    def digest(self) -> pathlib.Path:
        return self.home / "registry" / "resources.sha256"

    @property
    def receipt(self) -> pathlib.Path:
        return self.home / "registry" / "install-receipt.json"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_home(home: str | pathlib.Path | None, *, repository_root: pathlib.Path = REPOSITORY_ROOT) -> RegistryPaths:
    selected = home if home is not None else os.environ.get("GLITCH_CONTINUITY_HOME")
    if not selected:
        raise MissingContinuityHomeError("refused: GLITCH_CONTINUITY_HOME or --home is required")
    candidate = pathlib.Path(selected).expanduser().resolve(strict=False)
    root = repository_root.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        pass
    else:
        raise RegistryError("refused: continuity home cannot be inside the daemon-vessel repository")
    # A worktree marker at or below the selected root would make the target a
    # repository, not an external continuity home.
    if any(parent.joinpath(".git").exists() for parent in (candidate, *candidate.parents)):
        raise RegistryError("refused: continuity home cannot be within a Git worktree")
    return RegistryPaths(candidate)


def _read_bytes(path: pathlib.Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise MissingRegistryFileError(f"refused: missing {label}") from exc
    except OSError as exc:
        raise MissingRegistryFileError(f"refused: unreadable {label}") from exc


def _decode_json(content: bytes, label: str) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RegistrySchemaError(f"refused: unreadable {label}") from exc


def _sanitized_validation_error(error: object) -> RegistrySchemaError:
    path = "$"
    for part in getattr(error, "absolute_path", ()):
        if isinstance(part, int):
            path += f"[{part}]"
        elif isinstance(part, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            path += f".{part}"
        else:
            path += ".<property>"
    keyword = getattr(error, "validator", None) or "unknown"
    return RegistrySchemaError(f"refused: registry validation failed at {path} ({keyword})")


def _validate(registry: object, schema: object) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        if not isinstance(schema, dict) or not isinstance(schema.get("$id"), str):
            raise RegistrySchemaError("refused: invalid registry schema")
        # Registry() deliberately has no retrieval function: external references
        # fail locally rather than ever becoming network requests.
        reference_registry = Registry().with_resource(
            schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012)
        )
        errors = list(
            Draft202012Validator(
                schema, registry=reference_registry, format_checker=FORMAT_CHECKER
            ).iter_errors(registry)
        )
    except SchemaError as exc:
        raise RegistrySchemaError("refused: invalid registry schema") from exc
    except Unresolvable as exc:
        raise RegistrySchemaError("refused: unresolved schema reference") from exc
    if errors:
        raise _sanitized_validation_error(errors[0])
    if not isinstance(registry, dict):  # covered by schema; retained for type safety
        raise RegistrySchemaError("refused: registry root must be an object")
    ids = [resource["id"] for resource in registry["resources"]]
    if len(ids) != len(set(ids)):
        raise DuplicateResourceIdError("refused: duplicate stable resource IDs")


def _atomic_write(path: pathlib.Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = pathlib.Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _backup_existing(paths: RegistryPaths) -> None:
    existing = [path for path in (paths.schema, paths.registry, paths.digest, paths.receipt) if path.exists()]
    if not existing:
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = paths.home / f"registry-backup-{stamp}"
    if backup.exists():
        raise RegistryError("refused: backup destination already exists")
    for path in existing:
        destination = backup / path.relative_to(paths.home)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def install(*, source: pathlib.Path, schema_source: pathlib.Path, expected_sha256: str, home: str | pathlib.Path | None = None, replace: bool = False) -> RegistryPaths:
    paths = resolve_home(home)
    expected = expected_sha256.lower()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise RegistryDigestMismatchError("refused: --expect-sha256 must be a lowercase SHA-256 digest")
    source_bytes = _read_bytes(source, "source registry")
    schema_bytes = _read_bytes(schema_source, "source schema")
    actual = _sha256(source_bytes)
    if actual != expected:
        raise RegistryDigestMismatchError("refused: registry digest does not match --expect-sha256")
    registry = _decode_json(source_bytes, "source registry")
    schema = _decode_json(schema_bytes, "source schema")
    _validate(registry, schema)
    existing = any(path.exists() for path in (paths.schema, paths.registry, paths.digest, paths.receipt))
    if existing and not replace:
        raise RegistryError("refused: registry already installed; pass --replace to preserve a backup and replace it")
    if existing:
        _backup_existing(paths)
    schema_digest = _sha256(schema_bytes)
    receipt = {
        "schema_id": schema.get("$id") if isinstance(schema, dict) else None,
        "registry_sha256": actual,
        "schema_sha256": schema_digest,
        "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_filenames": {"registry": source.name, "schema": schema_source.name},
    }
    try:
        _atomic_write(paths.schema, schema_bytes)
        _atomic_write(paths.registry, source_bytes)
        _atomic_write(paths.digest, f"{actual}\n".encode("ascii"))
        _atomic_write(paths.receipt, (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        raise RegistryError("refused: unable to write registry installation") from exc
    return paths


def validate(*, home: str | pathlib.Path | None = None) -> tuple[RegistryPaths, dict]:
    paths = resolve_home(home)
    registry_bytes = _read_bytes(paths.registry, "installed registry")
    schema_bytes = _read_bytes(paths.schema, "installed schema")
    receipt_bytes = _read_bytes(paths.receipt, "installation receipt")
    digest_bytes = _read_bytes(paths.digest, "installed registry digest")
    registry = _decode_json(registry_bytes, "installed registry")
    schema = _decode_json(schema_bytes, "installed schema")
    receipt = _decode_json(receipt_bytes, "installation receipt")
    if not isinstance(receipt, dict):
        raise RegistrySchemaError("refused: installation receipt must be an object")
    try:
        stored_digest_text = digest_bytes.decode("ascii")
    except UnicodeError as exc:
        raise RegistryDigestMismatchError("refused: malformed installed registry digest") from exc
    if not re.fullmatch(r"[0-9a-f]{64}\n", stored_digest_text):
        raise RegistryDigestMismatchError("refused: malformed installed registry digest")
    stored_digest = stored_digest_text[:-1]
    actual_digest = _sha256(registry_bytes)
    schema_digest = _sha256(schema_bytes)
    if stored_digest != actual_digest or receipt.get("registry_sha256") != actual_digest:
        raise RegistryDigestMismatchError("refused: installed registry digest mismatch")
    if receipt.get("schema_sha256") != schema_digest:
        raise RegistryDigestMismatchError("refused: installed schema digest mismatch")
    if isinstance(schema, dict) and receipt.get("schema_id") != schema.get("$id"):
        raise RegistrySchemaError("refused: installation receipt schema ID mismatch")
    _validate(registry, schema)
    return paths, registry


def resolve(resource_id: str, *, home: str | pathlib.Path | None = None) -> dict:
    _, registry = validate(home=home)
    for resource in registry["resources"]:
        if resource["id"] == resource_id:
            return resource
    raise UnknownResourceIdError(f"refused: unknown resource ID: {resource_id}")
