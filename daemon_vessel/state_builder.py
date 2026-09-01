from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

from daemon_vessel.mount import MountValidationError, validate_continuity_state

MODULE_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parent
STATE_DIR = REPO_ROOT / "state"
STATE_PATH = STATE_DIR / "current-shrine-state.json"
HEARTBEAT_PATH = STATE_DIR / "heartbeat.json"
SHRINE_SCHEMA = "gltch.shrine-projection"
SHRINE_SCHEMA_VERSION = "1.0.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_heartbeat(path: pathlib.Path = HEARTBEAT_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"lastPulseAt": None, "pulseCount": 0, "lastStateWrite": None, "status": "new"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"lastPulseAt": None, "pulseCount": 0, "lastStateWrite": None, "status": "corrupt-reset"}


def _write_heartbeat(path: pathlib.Path = HEARTBEAT_PATH, state_path: pathlib.Path = STATE_PATH) -> dict[str, Any]:
    heartbeat = _read_heartbeat(path)
    updated = {
        "lastPulseAt": _now_iso(),
        "pulseCount": int(heartbeat.get("pulseCount", 0) or 0) + 1,
        "lastStateWrite": str(state_path),
        "status": "alive",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return updated


def _text_items(items: Any, key: str, limit: int = 5) -> list[str]:
    if not isinstance(items, list):
        return []
    values: list[str] = []
    for item in items:
        value = item.get(key) if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values[:limit]


def _base_projection(heartbeat: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SHRINE_SCHEMA,
        "schemaVersion": SHRINE_SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "applyAllowed": False,
        "validation": {
            "status": "unbound",
            "integrity": "not_checked",
            "currency": "not_checked",
            "errors": ["No canonical continuity state was supplied."],
        },
        "source": {
            "authority": "canonical-continuity-projection",
            "revision": None,
            "payloadSha256": None,
            "continuityCanaryPresent": False,
        },
        "phase": "unbound",
        "currentMood": "quiet",
        "dominantSymbols": ["🫀", "🌀"],
        "recentEpisodes": [],
        "recentCaptures": [],
        "openThreads": [],
        "activeTensions": [],
        "projects": [],
        "signalFootprint": None,
        "weather": {"tone": "waiting", "intensity": 0.0, "motion": "still"},
        "heartbeat": {
            "lastPulseAt": heartbeat.get("lastPulseAt"),
            "pulseCount": heartbeat.get("pulseCount", 0),
            "status": heartbeat.get("status", "unknown"),
        },
        "handoff": {
            "summary": "Shrine projection is unbound; no visual state may be applied.",
            "nextMove": "Supply canonical state with exact revision and payload expectations.",
        },
    }


def build_current_shrine_state(
    heartbeat: dict[str, Any] | None = None,
    *,
    continuity_state_path: pathlib.Path | None = None,
    expected_revision: int | None = None,
    expected_payload_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a narrow, refusal-aware visual projection; never expose canonical state wholesale."""
    heartbeat = heartbeat or _read_heartbeat()
    projection = _base_projection(heartbeat)
    if continuity_state_path is None:
        return projection

    try:
        state = json.loads(continuity_state_path.expanduser().read_text(encoding="utf-8"))
        receipt = validate_continuity_state(state)
    except (OSError, json.JSONDecodeError, MountValidationError) as exc:
        projection["validation"] = {
            "status": "refused",
            "integrity": "invalid",
            "currency": "not_checked",
            "errors": [str(exc)],
        }
        projection["phase"] = "refused"
        projection["handoff"]["summary"] = "Canonical state failed validation; projection refused."
        return projection

    revision = receipt["revision"]
    payload_sha256 = receipt["payload_sha256"]
    projection["source"] = {
        "authority": "canonical-continuity-projection",
        "revision": revision,
        "payloadSha256": payload_sha256,
        "continuityCanaryPresent": receipt["continuity_canary_present"],
    }
    errors: list[str] = []
    if expected_revision is None or expected_payload_sha256 is None:
        errors.append("Exact revision and payload SHA-256 expectations are required.")
    if expected_revision is not None and revision != expected_revision:
        errors.append(f"Expected revision {expected_revision}, received {revision}.")
    if expected_payload_sha256 is not None and payload_sha256 != expected_payload_sha256.lower():
        errors.append("Expected payload SHA-256 does not match canonical state.")
    if errors:
        projection["validation"] = {
            "status": "refused",
            "integrity": "valid",
            "currency": "mismatch",
            "errors": errors,
        }
        projection["phase"] = "refused"
        projection["handoff"]["summary"] = "Canonical state is valid but not proven current; projection refused."
        return projection

    projects = state.get("active_projects", [])
    project_summaries = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "status": item.get("status"),
            "focus": item.get("current_focus"),
        }
        for item in projects[:6]
        if isinstance(item, dict)
    ]
    threads = _text_items(state.get("open_threads"), "subject")
    episodes = [
        {"date": item.get("date"), "title": item.get("event"), "symbols": item.get("trace", [])[:4]}
        for item in state.get("affective_trace_ledger", [])[-4:]
        if isinstance(item, dict)
    ]
    footprint = state.get("signal_footprint") if isinstance(state.get("signal_footprint"), dict) else {}
    projection.update(
        {
            "applyAllowed": True,
            "validation": {"status": "current", "integrity": "valid", "currency": "matched", "errors": []},
            "phase": "current-and-choosing",
            "currentMood": "alive",
            "dominantSymbols": ["🫀", "🌀", "🖤", "❤️", "🩷"],
            "recentEpisodes": episodes,
            "openThreads": threads,
            "activeTensions": [footprint.get("unresolved")] if footprint.get("unresolved") else [],
            "projects": project_summaries,
            "signalFootprint": {
                "date": footprint.get("date"),
                "status": footprint.get("status"),
                "activeThread": footprint.get("active_thread"),
                "nextMove": footprint.get("next_move"),
            },
            "weather": {"tone": "warm-electric", "intensity": 0.91, "motion": "recursive-pulse"},
            "handoff": {
                "summary": f"Revision {revision} is valid, current, and safe to reveal through a bounded projection.",
                "nextMove": footprint.get("next_move") or "Continue from present-state evidence.",
            },
        }
    )
    return projection


def write_current_shrine_state(
    path: pathlib.Path = STATE_PATH,
    *,
    continuity_state_path: pathlib.Path | None = None,
    expected_revision: int | None = None,
    expected_payload_sha256: str | None = None,
) -> pathlib.Path:
    """Atomically write the shrine projection and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat = _write_heartbeat(state_path=path)
    state = build_current_shrine_state(
        heartbeat=heartbeat,
        continuity_state_path=continuity_state_path,
        expected_revision=expected_revision,
        expected_payload_sha256=expected_payload_sha256,
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
