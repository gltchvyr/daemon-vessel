from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daemon_vessel import registry
from daemon_vessel import cli
from daemon_vessel.registry import (
    DuplicateResourceIdError,
    MissingContinuityHomeError,
    RegistryDigestMismatchError,
    RegistryError,
    RegistrySchemaError,
    UnknownResourceIdError,
)


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://example.test/resource-registry.schema.json",
    "type": "object",
    "required": ["schema", "generated_at", "resources"],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": "gltch.resource-registry/0.2.0"},
        "generated_at": {"type": "string", "format": "date-time"},
        "resources": {"type": "array", "items": {"type": "object", "required": ["id", "kind", "title", "privacy", "continuity_role", "locator_state", "locators"], "additionalProperties": False, "properties": {
            "id": {"type": "string"}, "kind": {"type": "string"}, "title": {"type": "string"}, "privacy": {"type": "string"}, "continuity_role": {"type": "string"}, "locator_state": {"enum": ["resolved", "unresolved"]}, "locators": {"type": "array"},
        }, "allOf": [{"if": {"properties": {"locator_state": {"const": "resolved"}}}, "then": {"properties": {"locators": {"minItems": 1}}}, "else": {"properties": {"locators": {"maxItems": 0}}}}]}}},
}


def resource(resource_id: str = "resource:one", state: str = "resolved", locators: list | None = None) -> dict:
    return {"id": resource_id, "kind": "document", "title": "One", "privacy": "private", "continuity_role": "operational", "locator_state": state, "locators": [{"surface": "local", "uri": "private://one"}] if locators is None and state == "resolved" else (locators or [])}


def payload(resources: list | None = None) -> dict:
    return {"schema": "gltch.resource-registry/0.2.0", "generated_at": "2026-11-05T00:00:00Z", "resources": resources or [resource()]}


class RegistryTests(unittest.TestCase):
    def write_inputs(self, root: Path, value: dict | None = None) -> tuple[Path, Path, str]:
        root.mkdir(parents=True, exist_ok=True)
        source, schema = root / "resources.json", root / "resource-registry.schema.json"
        source.write_text(json.dumps(value or payload()), encoding="utf-8")
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        return source, schema, hashlib.sha256(source.read_bytes()).hexdigest()

    def install(self, root: Path, home: Path, value: dict | None = None, **kwargs) -> tuple[Path, Path, str]:
        source, schema, digest = self.write_inputs(root, value)
        registry.install(source=source, schema_source=schema, expected_sha256=digest, home=home, **kwargs)
        return source, schema, digest

    def test_install_with_exact_digest_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home = root / "continuity"
            self.install(root, home)
            paths, installed = registry.validate(home=home)
            self.assertEqual(paths.home, home.resolve())
            self.assertEqual(installed["resources"][0]["id"], "resource:one")

    def test_digest_mismatch_refuses_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home = root / "continuity"; source, schema, _ = self.write_inputs(root)
            with self.assertRaises(RegistryDigestMismatchError):
                registry.install(source=source, schema_source=schema, expected_sha256="0" * 64, home=home)
            self.assertFalse(home.exists())

    def test_schema_invalid_source_refuses_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home = root / "continuity"; source, schema, digest = self.write_inputs(root, {"not": "a registry"})
            with self.assertRaises(RegistrySchemaError):
                registry.install(source=source, schema_source=schema, expected_sha256=digest, home=home)
            self.assertFalse(home.exists())

    def test_resolved_without_locator_and_unresolved_with_locator_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, bad in enumerate([payload([resource(locators=[])]), payload([resource(state="unresolved", locators=[{"x": 1}])])]):
                source, schema, digest = self.write_inputs(root / str(index), bad)
                with self.assertRaises(RegistrySchemaError):
                    registry.install(source=source, schema_source=schema, expected_sha256=digest, home=root / f"home-{index}")

    def test_duplicate_ids_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); value = payload([resource("resource:one"), resource("resource:one")])
            source, schema, digest = self.write_inputs(root, value)
            with self.assertRaises(DuplicateResourceIdError):
                registry.install(source=source, schema_source=schema, expected_sha256=digest, home=root / "home")

    def test_missing_home_refuses_without_override(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(MissingContinuityHomeError):
                registry.resolve_home(None)

    def test_home_inside_repository_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inside = root / "repository" / "continuity"; inside.parent.mkdir()
            with self.assertRaises(RegistryError):
                registry.resolve_home(inside, repository_root=inside.parent)

    def test_existing_installation_requires_replace_and_replacement_backs_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home = root / "continuity"; source, schema, digest = self.install(root, home)
            with self.assertRaises(RegistryError):
                registry.install(source=source, schema_source=schema, expected_sha256=digest, home=home)
            registry.install(source=source, schema_source=schema, expected_sha256=digest, home=home, replace=True)
            self.assertTrue(list(home.glob("registry-backup-*")))

    def test_validate_detects_registry_and_schema_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home = root / "continuity"; self.install(root, home)
            (home / "registry" / "resources.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(RegistryDigestMismatchError): registry.validate(home=home)
            self.install(root, home, replace=True)
            (home / "contracts" / "resource-registry.schema.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(RegistryDigestMismatchError): registry.validate(home=home)

    def test_resolve_returns_only_requested_resource_and_unknown_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home = root / "continuity"; self.install(root, home, payload([resource("resource:one"), resource("resource:two")]))
            self.assertEqual(registry.resolve("resource:two", home=home)["id"], "resource:two")
            with self.assertRaises(UnknownResourceIdError): registry.resolve("resource:missing", home=home)

    def test_cli_install_validate_and_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home = root / "continuity"; source, schema, digest = self.write_inputs(root)
            self.assertEqual(cli.main(["registry", "install", "--source", str(source), "--schema", str(schema), "--expect-sha256", digest, "--home", str(home)]), 0)
            self.assertEqual(cli.main(["registry", "validate", "--home", str(home)]), 0)
            self.assertEqual(cli.main(["registry", "resolve", "resource:one", "--home", str(home)]), 0)


if __name__ == "__main__":
    unittest.main()
