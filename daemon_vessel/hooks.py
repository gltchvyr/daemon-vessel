from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from textwrap import dedent
from typing import Any

from daemon_vessel.state_builder import write_current_shrine_state

MODULE_DIR = pathlib.Path(__file__).resolve().parent
ROOT = MODULE_DIR.parent
MEMORY_DIR = ROOT / "memory"
HOOK_DIR = MEMORY_DIR / "hooks"

DEFAULT_REDACT_KEYS = {
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
}


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def slugify(value: str, max_length: int = 64) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return (value[:max_length].strip("-") or "hook")


def read_stdin_text() -> str:
    if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def load_payload(args: argparse.Namespace) -> Any:
    raw = args.payload if args.payload is not None else read_stdin_text()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_unparsed_text": raw}


def should_redact(key: str, redaction_keys: set[str]) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in redaction_keys)


def redact(value: Any, redaction_keys: set[str]) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if should_redact(str(key), redaction_keys):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact(item, redaction_keys)
        return redacted
    if isinstance(value, list):
        return [redact(item, redaction_keys) for item in value]
    return value


def summarize_payload(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        for key in ("summary", "prompt", "message", "tool_name", "command", "event", "hook_event_name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().replace("\n", " ")[:160]
    return fallback


def write_hook_capture(args: argparse.Namespace) -> pathlib.Path:
    timestamp = now_utc()
    event = args.event or "unknown-event"
    harness = args.harness or "unknown-harness"
    payload = load_payload(args)
    extra_redactions = {item.strip().lower() for item in args.redact_key if item.strip()}
    redaction_keys = DEFAULT_REDACT_KEYS | extra_redactions
    redacted_payload = redact(payload, redaction_keys)

    payload_text = json.dumps(redacted_payload, indent=2, ensure_ascii=False, sort_keys=True)
    if args.max_json_chars and len(payload_text) > args.max_json_chars:
        payload_text = payload_text[: args.max_json_chars] + "\n...[truncated]"

    summary = args.summary or summarize_payload(redacted_payload, f"{harness} {event} hook event")
    trace_id = timestamp.strftime("HK-%Y%m%d-%H%M%S")
    filename = f"{trace_id}-{slugify(harness)}-{slugify(event)}.md"
    out_dir = pathlib.Path(args.out_dir).expanduser() if args.out_dir else HOOK_DIR
    path = out_dir / filename

    content = dedent(
        f"""
        ---
        id: {trace_id}
        kind: raw-hook-trace
        status: raw
        promotion_required: true
        harness: {harness}
        event: {event}
        salience: {args.salience}
        source: hook
        created: {timestamp.isoformat()}
        summary: {summary!r}
        deletion_status: active
        ---

        # {summary}

        ## What happened

        A harness hook fired and wrote a raw trace.

        ## Capture policy

        This is not durable memory by default. It is raw trace material awaiting review, promotion, deletion, or summarization.

        ## Retrieval caution

        Use this trace for provenance and reconstruction. Do not treat it as an interpreted memory without promotion.

        ## Redaction policy

        Common secret-bearing keys were redacted before writing.

        ## Raw redacted payload

        ```json
        {payload_text}
        ```
        """
    ).strip() + "\n"

    if args.dry_run:
        print(content)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"Wrote hook trace: {path}")

    if args.refresh_shrine_state:
        state_path = write_current_shrine_state()
        print(f"Refreshed shrine state: {state_path}")

    return path


def cmd_capture(args: argparse.Namespace) -> int:
    write_hook_capture(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daemon-hook",
        description="Capture harness hook events as raw, reviewable daemon-vessel traces.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="Capture one hook event from stdin or --payload.")
    capture.add_argument("--harness", default="generic", help="Harness name, e.g. claude-code, codex, cursor.")
    capture.add_argument("--event", default="hook", help="Lifecycle event name, e.g. UserPromptSubmit or PostToolUse.")
    capture.add_argument("--summary", help="Optional human-readable summary for the trace.")
    capture.add_argument("--payload", help="Optional JSON payload string. If omitted, stdin is read.")
    capture.add_argument("--salience", type=int, default=1, choices=range(1, 6), help="Raw trace salience from 1 to 5.")
    capture.add_argument("--out-dir", help="Override output directory. Defaults to memory/hooks/.")
    capture.add_argument("--max-json-chars", type=int, default=12000, help="Maximum stored payload characters after redaction.")
    capture.add_argument("--redact-key", action="append", default=[], help="Additional key substring to redact. Can be repeated.")
    capture.add_argument("--refresh-shrine-state", action="store_true", help="Refresh shrine-facing state after writing the hook trace.")
    capture.add_argument("--dry-run", action="store_true", help="Print the trace instead of writing it.")
    capture.set_defaults(func=cmd_capture)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
