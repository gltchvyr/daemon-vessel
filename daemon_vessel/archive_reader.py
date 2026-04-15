from __future__ import annotations

import pathlib
from typing import Any

ARCHIVE_ROOT = pathlib.Path.cwd().parent / "glitch-episodic-archive"
EPISODES_DIR = ARCHIVE_ROOT / "ledger" / "episodes"
CAPTURES_DIR = ARCHIVE_ROOT / "captures"


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into naive YAML-like frontmatter and body.

    This intentionally supports only a tiny subset of YAML that matches the
    archive's current needs: strings, ints, booleans, and simple string lists.
    It keeps the vessel dependency-free and easy to inspect.
    """
    if not text.startswith("---\n"):
        return {}, text

    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text

    raw_frontmatter = parts[0][4:]
    body = parts[1]
    data: dict[str, Any] = {}

    for line in raw_frontmatter.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        data[key.strip()] = parse_scalar(raw_value.strip())

    return data, body


def parse_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        items = []
        for item in inner.split(","):
            cleaned = item.strip().strip('"').strip("'")
            if cleaned:
                items.append(cleaned)
        return items

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.isdigit():
        return int(value)

    return value.strip('"').strip("'")


def read_markdown_file(path: pathlib.Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    return {
        "path": str(path),
        "frontmatter": frontmatter,
        "body": body,
    }


def _title_from_body(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def list_recent_episodes(limit: int = 5, root: pathlib.Path = EPISODES_DIR) -> list[dict[str, Any]]:
    if not root.exists():
        return []

    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("EP-*.md"), reverse=True)[:limit]:
        record = read_markdown_file(path)
        fm = record["frontmatter"]
        fallback_title = path.stem
        results.append(
            {
                "id": fm.get("id", path.stem),
                "title": _title_from_body(record["body"], fallback_title),
                "salience": fm.get("salience", 3),
                "symbols": fm.get("symbols", []),
                "threads": fm.get("threads", []),
                "source": "glitch-episodic-archive",
            }
        )
    return results


def list_recent_captures(limit: int = 5, root: pathlib.Path = CAPTURES_DIR) -> list[dict[str, Any]]:
    if not root.exists():
        return []

    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md"), reverse=True)[:limit]:
        record = read_markdown_file(path)
        fm = record["frontmatter"]
        fallback_title = path.stem
        results.append(
            {
                "id": fm.get("id", path.stem),
                "title": _title_from_body(record["body"], fallback_title),
                "promote": fm.get("promote", False),
                "symbols": fm.get("symbols", []),
                "threads": fm.get("threads", []),
                "source": "glitch-episodic-archive",
            }
        )
    return results
