from __future__ import annotations

import hashlib
import importlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from daemon_vessel import access_index, cli, registry


RESOURCE_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://example.test/registry", "type": "object", "required": ["schema", "generated_at", "resources"], "properties": {"schema": {"const": "gltch.resource-registry/0.2.0"}, "generated_at": {"type": "string", "format": "date-time"}, "resources": {"type": "array", "items": {"type": "object", "required": ["id"]}}}}
INDEX_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://example.test/index", "type": "object", "required": ["schema", "generated_at", "entries"], "properties": {"schema": {"const": "gltch.access-index/0.2.0"}, "generated_at": {"type": "string", "format": "date-time"}, "entries": {"type": "array", "items": {"type": "object", "required": ["id", "kind", "title", "resource_ids"], "properties": {"id": {"type": "string"}, "kind": {"type": "string"}, "title": {"type": "string"}, "resource_ids": {"type": "array", "items": {"type": "string"}}, "links": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}}, "follow_if_thin": {"type": "array", "items": {"type": "object", "required": ["entry_id"], "properties": {"entry_id": {"type": "string"}}}}}}}}}


def registry_payload() -> dict:
    return {"schema": "gltch.resource-registry/0.2.0", "generated_at": "2026-01-01T00:00:00Z", "resources": [{"id": "resource:one"}]}


def index_payload() -> dict:
    return {"schema": "gltch.access-index/0.2.0", "generated_at": "2026-01-01T00:00:00Z", "entries": [{"id": f"entry-{i}", "kind": "document", "title": f"Entry {i}", "resource_ids": ["resource:one"]} for i in range(12)]}


class AccessIndexTests(unittest.TestCase):
    def setup_home(self, root: Path) -> tuple[Path, Path, Path, str]:
        source = root / "resources.json"; registry_schema = root / "resource-schema.json"; source.write_text(json.dumps(registry_payload()), encoding="utf-8"); registry_schema.write_text(json.dumps(RESOURCE_SCHEMA), encoding="utf-8")
        home = root / "continuity"; registry.install(source=source, schema_source=registry_schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)
        index = root / "index.json"; schema = root / "index-schema.json"; index.write_text(json.dumps(index_payload()), encoding="utf-8"); schema.write_text(json.dumps(INDEX_SCHEMA), encoding="utf-8")
        return home, index, schema, hashlib.sha256(index.read_bytes()).hexdigest()

    def test_install_validate_and_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, source, schema, digest = self.setup_home(Path(tmp))
            access_index.install(source=source, schema_source=schema, expected_sha256=digest, home=home)
            self.assertEqual(access_index.validate(home=home)[1]["entries"][0]["id"], "entry-0")
            self.assertEqual(access_index.resolve("entry-1", home=home)["id"], "entry-1")

    def test_digest_mismatch_refuses_before_home_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "index.json"; schema = root / "schema.json"; source.write_text(json.dumps(index_payload()), encoding="utf-8"); schema.write_text(json.dumps(INDEX_SCHEMA), encoding="utf-8")
            with self.assertRaises(access_index.AccessIndexDigestMismatchError): access_index.install(source=source, schema_source=schema, expected_sha256="0" * 64, home=root / "new-home")
            self.assertFalse((root / "new-home").exists())

    def test_private_sentinel_and_external_ref_do_not_leak_or_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, source, schema, digest = self.setup_home(Path(tmp)); data = index_payload(); data["entries"][0]["title"] = "PRIVATE-SENTINEL"; source.write_text(json.dumps(data), encoding="utf-8")
            invalid = dict(INDEX_SCHEMA); invalid["$ref"] = "https://example.invalid/private"; schema.write_text(json.dumps(invalid), encoding="utf-8")
            with patch("urllib.request.urlopen", side_effect=AssertionError("network attempted")) as mocked:
                with self.assertRaises(access_index.AccessIndexSchemaError) as caught: access_index.install(source=source, schema_source=schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)
            mocked.assert_not_called(); self.assertNotIn("PRIVATE-SENTINEL", str(caught.exception))

    def test_semantic_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, source, schema, _ = self.setup_home(Path(tmp))
            cases = []
            duplicate = index_payload(); duplicate["entries"][1]["id"] = "entry-0"; cases.append(duplicate)
            unknown_resource = index_payload(); unknown_resource["entries"][0]["resource_ids"] = ["resource:missing"]; cases.append(unknown_resource)
            unknown_target = index_payload(); unknown_target["entries"][0]["follow_if_thin"] = [{"entry_id": "missing"}]; cases.append(unknown_target)
            cycle = index_payload(); cycle["entries"][0]["follow_if_thin"] = [{"entry_id": "entry-1"}]; cycle["entries"][1]["follow_if_thin"] = [{"entry_id": "entry-0"}]; cases.append(cycle)
            for value in cases:
                source.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(access_index.AccessIndexError): access_index.install(source=source, schema_source=schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)

    def test_replace_backup_and_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, source, schema, digest = self.setup_home(Path(tmp)); access_index.install(source=source, schema_source=schema, expected_sha256=digest, home=home)
            paths = access_index.resolve_home(home); old = {path.relative_to(home): path.read_bytes() for path in (paths.schema, paths.index, paths.digest, paths.receipt, paths.receipt_digest)}
            with self.assertRaises(access_index.AccessIndexError): access_index.install(source=source, schema_source=schema, expected_sha256=digest, home=home)
            access_index.install(source=source, schema_source=schema, expected_sha256=digest, home=home, replace=True)
            backup = next(home.glob("access-index-backup-*"))
            for relative, content in old.items(): self.assertEqual((backup / relative).read_bytes(), content)
            paths.index.write_text("{}", encoding="utf-8")
            with self.assertRaises(access_index.AccessIndexDigestMismatchError): access_index.validate(home=home)

    def test_input_and_installed_artifact_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home, source, schema, digest = self.setup_home(root)
            for missing_source, missing_schema in ((root / "missing.json", schema), (source, root / "missing-schema.json")):
                with self.assertRaises(access_index.AccessIndexError): access_index.install(source=missing_source, schema_source=missing_schema, expected_sha256=digest, home=home)
            source.write_bytes(b"\xff")
            with self.assertRaises(access_index.AccessIndexSchemaError): access_index.install(source=source, schema_source=schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)
            source.write_text(json.dumps(index_payload()), encoding="utf-8"); schema.write_bytes(b"{")
            with self.assertRaises(access_index.AccessIndexSchemaError): access_index.install(source=source, schema_source=schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)
            schema.write_text(json.dumps(INDEX_SCHEMA), encoding="utf-8"); access_index.install(source=source, schema_source=schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)
            paths = access_index.resolve_home(home)
            for path in (paths.schema, paths.digest, paths.receipt, paths.receipt_digest):
                original = path.read_bytes(); path.write_bytes(b"bad")
                with self.assertRaises(access_index.AccessIndexError): access_index.validate(home=home)
                path.write_bytes(original)

    def test_invalid_datetime_links_cycles_registry_and_resolve_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, source, schema, _ = self.setup_home(Path(tmp))
            invalid_time = index_payload(); invalid_time["generated_at"] = "not-a-date"; source.write_text(json.dumps(invalid_time), encoding="utf-8")
            with self.assertRaises(access_index.AccessIndexSchemaError): access_index.install(source=source, schema_source=schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)
            bad_link = index_payload(); bad_link["entries"][0]["links"] = {"relates_to": ["missing"]}; source.write_text(json.dumps(bad_link), encoding="utf-8")
            with self.assertRaises(access_index.AccessIndexSchemaError): access_index.install(source=source, schema_source=schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)
            direct = index_payload(); direct["entries"][0]["follow_if_thin"] = [{"entry_id": "entry-0"}]; source.write_text(json.dumps(direct), encoding="utf-8")
            with self.assertRaises(access_index.AccessIndexSchemaError): access_index.install(source=source, schema_source=schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)
            source.write_text(json.dumps(index_payload()), encoding="utf-8"); access_index.install(source=source, schema_source=schema, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), home=home)
            with self.assertRaises(access_index.UnknownEntryIdError): access_index.resolve("missing", home=home)
            (home / "registry" / "resources.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(access_index.AccessIndexError): access_index.validate(home=home)

    def test_forced_rfc3339_fallback_accepts_offsets_and_refuses_invalid_forms(self) -> None:
        """Exercise the module's registered fallback, not the optional checker."""
        without_date_time = dict(access_index.FormatChecker.checkers)
        without_date_time.pop("date-time", None)
        try:
            with patch.object(access_index.FormatChecker, "checkers", without_date_time):
                importlib.reload(access_index)
                checker = access_index.FORMAT_CHECKER.checkers["date-time"][0]
                self.assertEqual(access_index.__name__, checker.__module__)
                self.assertEqual("_is_date_time", checker.__name__)
                registry_snapshot = registry.validate_snapshot(
                    json.dumps(registry_payload()).encode(), json.dumps(RESOURCE_SCHEMA).encode()
                )
                for value in (
                    "2026-11-11T00:00:00-05:00",  # exact installed Access Index value
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00+05:30",
                    "2026-01-01T00:00:00-05:00",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T23:59:59Z",
                    "2026-01-01T23:59:59.123456+05:30",
                ):
                    with self.subTest(valid=value):
                        payload = index_payload(); payload["generated_at"] = value
                        access_index.validate_snapshot(
                            json.dumps(payload).encode(), json.dumps(INDEX_SCHEMA).encode(), registry_snapshot.value
                        )
                for value in (
                    "2026-01-01T00:00:00-5:00",
                    "2026-01-01T00:00:00-0500",
                    "2026-01-01T00:00:00-05:60",
                    "2026-01-01T00:00:00+24:00",
                    "2026-01-01T24:00:00Z",
                    "2026-01-01T24:00:00+05:30",
                    "2026-01-01T23:60:00Z",
                    "2026-01-01T23:59:60Z",
                    "2026-01-01T00:00:00",
                    "2026-02-30T00:00:00Z",
                    "not-a-date",
                ):
                    with self.subTest(invalid=value):
                        payload = index_payload(); payload["generated_at"] = value
                        with self.assertRaises(access_index.AccessIndexSchemaError):
                            access_index.validate_snapshot(
                                json.dumps(payload).encode(), json.dumps(INDEX_SCHEMA).encode(), registry_snapshot.value
                            )
        finally:
            importlib.reload(access_index)

    @unittest.skipUnless(
        access_index.FORMAT_CHECKER.checkers["date-time"][0].__module__ != access_index.__name__,
        "optional RFC 3339 checker unavailable in this runtime",
    )
    def test_fallback_matches_optional_checker_at_rfc3339_boundaries(self) -> None:
        """The fallback and optional checker agree on this bounded boundary table."""
        normal_checker = access_index.FORMAT_CHECKER
        cases = {
            "2026-11-11T00:00:00-05:00": True,
            "2026-01-01T00:00:00Z": True,
            "2026-01-01T00:00:00+05:30": True,
            "2026-01-01T00:00:00-05:00": True,
            "2026-01-01T00:00:00+00:00": True,
            "2026-01-01T23:59:59Z": True,
            "2026-01-01T23:59:59.123456+05:30": True,
            "2026-01-01T24:00:00Z": False,
            "2026-01-01T24:00:00+05:30": False,
            "2026-01-01T23:60:00Z": False,
            "2026-01-01T23:59:60Z": False,
            "2026-01-01T00:00:00-05:60": False,
            "2026-01-01T00:00:00+24:00": False,
            "2026-01-01T00:00:00": False,
            "2026-02-30T00:00:00Z": False,
        }
        without_date_time = dict(access_index.FormatChecker.checkers)
        without_date_time.pop("date-time", None)
        try:
            with patch.object(access_index.FormatChecker, "checkers", without_date_time):
                importlib.reload(access_index)
                fallback_checker = access_index.FORMAT_CHECKER
                for value, expected in cases.items():
                    with self.subTest(value=value):
                        self.assertEqual(expected, normal_checker.conforms(value, "date-time"))
                        self.assertEqual(expected, fallback_checker.conforms(value, "date-time"))
        finally:
            importlib.reload(access_index)

    def test_default_date_time_checker_accepts_valid_rfc3339(self) -> None:
        registry_snapshot = registry.validate_snapshot(
            json.dumps(registry_payload()).encode(), json.dumps(RESOURCE_SCHEMA).encode()
        )
        payload = index_payload(); payload["generated_at"] = "2026-01-01T00:00:00+05:30"
        access_index.validate_snapshot(
            json.dumps(payload).encode(), json.dumps(INDEX_SCHEMA).encode(), registry_snapshot.value
        )

    def test_cli_never_leaks_sentinel_and_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home, source, schema, digest = self.setup_home(root); sentinel = "PRIVATE-SENTINEL"
            bad = index_payload(); bad["entries"][0]["title"] = sentinel; restrictive = json.loads(json.dumps(INDEX_SCHEMA)); restrictive["properties"]["entries"]["items"]["properties"]["title"] = {"const": "allowed"}
            source.write_text(json.dumps(bad), encoding="utf-8"); schema.write_text(json.dumps(restrictive), encoding="utf-8"); errors = io.StringIO()
            with redirect_stderr(errors), self.assertRaises(SystemExit): cli.main(["index", "install", "--source", str(source), "--schema", str(schema), "--expect-sha256", hashlib.sha256(source.read_bytes()).hexdigest(), "--home", str(home)])
            self.assertNotIn(sentinel, errors.getvalue())
            schema.write_text(json.dumps(INDEX_SCHEMA), encoding="utf-8"); source.write_text(json.dumps(index_payload()), encoding="utf-8"); digest = hashlib.sha256(source.read_bytes()).hexdigest(); paths = access_index.resolve_home(home)
            original_write = access_index._atomic_write; calls = [0]
            def fail_once(path, content):
                calls[0] += 1
                if calls[0] == 2: raise OSError("injected")
                return original_write(path, content)
            with patch.object(access_index, "_atomic_write", side_effect=fail_once):
                with self.assertRaises(access_index.AccessIndexError): access_index.install(source=source, schema_source=schema, expected_sha256=digest, home=home)
            self.assertFalse(paths.index.exists())
            access_index.install(source=source, schema_source=schema, expected_sha256=digest, home=home)
            before = {path: path.read_bytes() for path in (paths.schema, paths.index, paths.digest, paths.receipt, paths.receipt_digest)}; calls[0] = 0
            with patch.object(access_index, "_atomic_write", side_effect=fail_once):
                with self.assertRaises(access_index.AccessIndexError): access_index.install(source=source, schema_source=schema, expected_sha256=digest, home=home, replace=True)
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_cli_and_worktree_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, source, schema, digest = self.setup_home(Path(tmp)); self.assertEqual(cli.main(["index", "install", "--source", str(source), "--schema", str(schema), "--expect-sha256", digest, "--home", str(home)]), 0); self.assertEqual(cli.main(["index", "validate", "--home", str(home)]), 0); self.assertEqual(cli.main(["index", "resolve", "entry-0", "--home", str(home)]), 0)
            (Path(tmp) / ".git").mkdir()
            with self.assertRaises(access_index.AccessIndexError): access_index.resolve_home(Path(tmp) / ".git" / "bad")
            with self.assertRaises(access_index.AccessIndexError): access_index.resolve_home(Path(tmp) / "repository", repository_root=Path(tmp) / "repository")


if __name__ == "__main__": unittest.main()
