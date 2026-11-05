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
import shutil
import tempfile
from dataclasses import dataclass

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError


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


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _load_json(path: pathlib.Path, label: str) -> object:
    if not path.is_file():
        raise MissingRegistryFileError(f"refused: missing {label}: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistrySchemaError(f"refused: unreadable {label}") from exc


def _validate(registry: object, schema: object) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(registry))
    except SchemaError as exc:
        raise RegistrySchemaError("refused: invalid registry schema") from exc
    if errors:
        raise RegistrySchemaError(f"refused: registry does not conform to schema ({errors[0].message})")
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
    actual = _sha256(source) if source.is_file() else None
    if actual != expected:
        raise RegistryDigestMismatchError("refused: registry digest does not match --expect-sha256")
    registry = _load_json(source, "source registry")
    schema = _load_json(schema_source, "source schema")
    _validate(registry, schema)
    existing = any(path.exists() for path in (paths.schema, paths.registry, paths.digest, paths.receipt))
    if existing and not replace:
        raise RegistryError("refused: registry already installed; pass --replace to preserve a backup and replace it")
    if existing:
        _backup_existing(paths)
    schema_digest = _sha256(schema_source)
    receipt = {
        "schema_id": schema.get("$id") if isinstance(schema, dict) else None,
        "registry_sha256": actual,
        "schema_sha256": schema_digest,
        "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_filenames": {"registry": source.name, "schema": schema_source.name},
    }
    _atomic_write(paths.schema, schema_source.read_bytes())
    _atomic_write(paths.registry, source.read_bytes())
    _atomic_write(paths.digest, f"{actual}\n".encode("ascii"))
    _atomic_write(paths.receipt, (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return paths


def validate(*, home: str | pathlib.Path | None = None) -> tuple[RegistryPaths, dict]:
    paths = resolve_home(home)
    registry = _load_json(paths.registry, "installed registry")
    schema = _load_json(paths.schema, "installed schema")
    receipt = _load_json(paths.receipt, "installation receipt")
    if not isinstance(receipt, dict):
        raise RegistrySchemaError("refused: installation receipt must be an object")
    stored_digest = paths.digest.read_text(encoding="ascii").strip().lower() if paths.digest.is_file() else ""
    actual_digest = _sha256(paths.registry)
    schema_digest = _sha256(paths.schema)
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
