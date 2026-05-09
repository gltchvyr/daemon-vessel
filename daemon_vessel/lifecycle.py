from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
from textwrap import dedent

MODULE_DIR = pathlib.Path(__file__).resolve().parent
ROOT = MODULE_DIR.parent
MEMORY_DIR = ROOT / "memory"
DELETION_DIR = ROOT / "memory" / "deletions"


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def slugify(value: str, max_length: int = 48) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return (value[:max_length].strip("-") or "memory")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text
    raw_frontmatter = parts[0][4:]
    body = parts[1]
    data: dict[str, str] = {}
    for line in raw_frontmatter.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        data[key.strip()] = raw_value.strip().strip('"').strip("'")
    return data, body


def iter_memory_files(include_deleted: bool = False) -> list[pathlib.Path]:
    if not MEMORY_DIR.exists():
        return []
    files = sorted(MEMORY_DIR.rglob("*.md"), reverse=True)
    if include_deleted:
        return files
    return [path for path in files if "deletions" not in path.parts]


def read_memory(path: pathlib.Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    return split_frontmatter(text)


def title_from_body(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def deletion_status(frontmatter: dict[str, str], body: str) -> str:
    for key in ("deletion_status", "status"):
        value = frontmatter.get(key, "").lower()
        if value in {"soft_deleted", "hard_deleted", "scope_revoked", "superseded", "deleted"}:
            return value
    lowered = body.lower()
    if "deletion:" in lowered and "status: active" not in lowered:
        return "review"
    return "active"


def score_memory(path: pathlib.Path, frontmatter: dict[str, str], body: str, query: str) -> tuple[int, list[str]]:
    query_terms = [term for term in re.split(r"\s+", query.lower()) if term]
    haystack_name = path.name.lower()
    haystack_fm = "\n".join(f"{k}: {v}" for k, v in frontmatter.items()).lower()
    haystack_body = body.lower()

    score = 0
    reasons: list[str] = []

    for term in query_terms:
        if term in haystack_name:
            score += 5
            reasons.append(f"filename contains `{term}`")
        if term in haystack_fm:
            score += 3
            reasons.append(f"frontmatter contains `{term}`")
        if term in haystack_body:
            score += 1
            reasons.append(f"body contains `{term}`")

    salience = frontmatter.get("salience")
    if salience and salience.isdigit():
        score += min(int(salience), 5)
        reasons.append(f"salience {salience}")

    status = deletion_status(frontmatter, body)
    if status != "active":
        score -= 100
        reasons.append(f"excluded/penalized because deletion status is `{status}`")

    return score, reasons


def cmd_recall(args: argparse.Namespace) -> int:
    matches: list[tuple[int, pathlib.Path, dict[str, str], str, list[str]]] = []
    for path in iter_memory_files(include_deleted=args.include_deleted):
        frontmatter, body = read_memory(path)
        score, reasons = score_memory(path, frontmatter, body, args.query)
        if score > 0 or (args.include_deleted and reasons):
            matches.append((score, path, frontmatter, body, reasons))

    matches.sort(key=lambda item: item[0], reverse=True)

    if not matches:
        print(f"No memory candidates found for: {args.query}")
        return 0

    for score, path, frontmatter, body, reasons in matches[: args.limit]:
        title = title_from_body(body, path.stem)
        rel_path = path.relative_to(ROOT)
        print(f"\n{title}")
        print(f"path: {rel_path}")
        print(f"score: {score}")
        if args.why:
            print("why retrieved:")
            for reason in reasons[:8]:
                print(f"- {reason}")
    return 0


def cmd_lineage(args: argparse.Namespace) -> int:
    query = args.target.lower()
    found = False
    for path in iter_memory_files(include_deleted=True):
        if query not in path.name.lower() and query not in path.read_text(encoding="utf-8").lower():
            continue
        found = True
        frontmatter, body = read_memory(path)
        rel_path = path.relative_to(ROOT)
        print(f"\n{rel_path}")
        print(f"title: {title_from_body(body, path.stem)}")
        for key in ("id", "source", "source_capture", "source_traces", "derived_from", "created", "created_reason", "status", "deletion_status"):
            if key in frontmatter:
                print(f"{key}: {frontmatter[key]}")
        print(f"deletion_status: {deletion_status(frontmatter, body)}")
    if not found:
        print(f"No lineage candidates found for: {args.target}")
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    target = pathlib.Path(args.target)
    if not target.is_absolute():
        target = ROOT / target

    if not target.exists():
        print(f"Target not found: {target}")
        return 1

    timestamp = now_utc()
    DELETION_DIR.mkdir(parents=True, exist_ok=True)
    deletion_id = timestamp.strftime("DEL-%Y%m%d-%H%M%S")
    deletion_path = DELETION_DIR / f"{deletion_id}-{slugify(target.stem)}.md"
    rel_target = target.relative_to(ROOT)

    deletion_record = dedent(
        f"""
        ---
        id: {deletion_id}
        date: {timestamp.date().isoformat()}
        status: requested
        target_path: {rel_target}
        request_type: {args.mode}
        requested_by: cli
        reason: {args.reason!r}
        cascade_reviewed: false
        completed_at: null
        ---

        # {deletion_id}: {args.mode} request

        ## Target

        `{rel_target}`

        ## Reason

        {args.reason}

        ## Cascade review

        - [ ] source reviewed
        - [ ] indexes reviewed
        - [ ] context packets reviewed
        - [ ] shrine state reviewed
        - [ ] derived memories reviewed
        """
    ).strip() + "\n"
    deletion_path.write_text(deletion_record, encoding="utf-8")

    if args.mode == "hard_delete":
        if not args.apply:
            print(f"Wrote hard-delete request: {deletion_path}")
            print("Target not removed because --apply was not provided.")
            return 0
        target.unlink()
        print(f"Hard deleted target and wrote request: {deletion_path}")
        return 0

    if args.apply:
        original = target.read_text(encoding="utf-8")
        marker = dedent(
            f"""
            \n\n---\n\n## Lifecycle note\n\nDeletion status: {args.mode}\nDeletion request: `{deletion_path.relative_to(ROOT)}`\nDeletion reason: {args.reason}\nDeletion requested at: {timestamp.isoformat()}\n            """
        ).rstrip() + "\n"
        target.write_text(original.rstrip() + marker, encoding="utf-8")
        print(f"Marked target with lifecycle note: {target}")

    print(f"Wrote deletion request: {deletion_path}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    files = iter_memory_files(include_deleted=True)
    active = deleted = missing_reason = missing_source = 0
    for path in files:
        frontmatter, body = read_memory(path)
        status = deletion_status(frontmatter, body)
        if status == "active":
            active += 1
        else:
            deleted += 1
        combined = "\n".join([*frontmatter.keys(), body]).lower()
        if "created_reason" not in combined and "why it matters" not in combined:
            missing_reason += 1
            if args.verbose:
                print(f"missing reason: {path.relative_to(ROOT)}")
        if "source" not in frontmatter and "source_trace" not in combined and "source capture" not in combined:
            missing_source += 1
            if args.verbose:
                print(f"missing source: {path.relative_to(ROOT)}")

    print("Memory lifecycle audit")
    print(f"- markdown files inspected: {len(files)}")
    print(f"- active-ish files: {active}")
    print(f"- deletion/revocation/supersession markers: {deleted}")
    print(f"- missing explicit reason markers: {missing_reason}")
    print(f"- missing explicit source markers: {missing_source}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daemon-lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)

    recall = subparsers.add_parser("recall", help="Search memory traces with retrieval reasons.")
    recall.add_argument("query")
    recall.add_argument("--limit", type=int, default=5)
    recall.add_argument("--why", action="store_true")
    recall.add_argument("--include-deleted", action="store_true")
    recall.set_defaults(func=cmd_recall)

    lineage = subparsers.add_parser("lineage", help="Show simple provenance/lifecycle hints for matching memories.")
    lineage.add_argument("target")
    lineage.set_defaults(func=cmd_lineage)

    forget = subparsers.add_parser("forget", help="Create a deletion/revocation/supersession request.")
    forget.add_argument("target")
    forget.add_argument("--mode", choices=["soft_delete", "hard_delete", "scope_revocation", "supersession", "source_invalidation"], default="soft_delete")
    forget.add_argument("--reason", required=True)
    forget.add_argument("--apply", action="store_true", help="Apply marker or hard deletion in addition to writing request.")
    forget.set_defaults(func=cmd_forget)

    audit = subparsers.add_parser("audit", help="Inspect memory files for source/reason/deletion markers.")
    audit.add_argument("--verbose", action="store_true")
    audit.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
