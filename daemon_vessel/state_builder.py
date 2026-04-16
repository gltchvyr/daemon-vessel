from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

from daemon_vessel.archive_reader import list_recent_captures, list_recent_episodes

STATE_DIR = pathlib.Path.cwd() / "state"
STATE_PATH = STATE_DIR / "current-shrine-state.json"
HEARTBEAT_PATH = STATE_DIR / "heartbeat.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_heartbeat(path: pathlib.Path = HEARTBEAT_PATH) -> dict[str, Any]:
    if not path.exists():
        return {
            "lastPulseAt": None,
            "pulseCount": 0,
            "lastStateWrite": None,
            "status": "new",
        }

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "lastPulseAt": None,
            "pulseCount": 0,
            "lastStateWrite": None,
            "status": "corrupt-reset",
        }


def _write_heartbeat(path: pathlib.Path = HEARTBEAT_PATH) -> dict[str, Any]:
    heartbeat = _read_heartbeat(path)
    now = _now_iso()
    pulse_count = int(heartbeat.get("pulseCount", 0) or 0) + 1

    updated = {
        "lastPulseAt": now,
        "pulseCount": pulse_count,
        "lastStateWrite": str(STATE_PATH),
        "status": "alive",
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return updated


def _collect_dominant_symbols(recent_episodes: list[dict], recent_captures: list[dict]) -> list[str]:
    seen: list[str] = []
    for item in [*recent_episodes, *recent_captures]:
        for symbol in item.get("symbols", []):
            if symbol not in seen:
                seen.append(symbol)
    return seen or ["🫀", "😈", "🌀"]


def _collect_open_threads(recent_episodes: list[dict], recent_captures: list[dict]) -> list[str]:
    seen: list[str] = []
    for item in [*recent_episodes, *recent_captures]:
        threads = item.get("threads", [])
        if isinstance(threads, str):
            threads = [threads] if threads else []
        for thread in threads:
            if thread not in seen:
                seen.append(thread)
    fallback = [
        "archive reader implementation",
        "shrine state generation",
        "signal shrine rendering contract",
    ]
    return seen or fallback


def build_current_shrine_state(heartbeat: dict[str, Any] | None = None) -> dict:
    """Build the shrine-facing state contract from recent archive summaries."""
    recent_episodes = list_recent_episodes(limit=3)
    recent_captures = list_recent_captures(limit=3)
    dominant_symbols = _collect_dominant_symbols(recent_episodes, recent_captures)
    open_threads = _collect_open_threads(recent_episodes, recent_captures)
    heartbeat = heartbeat or _read_heartbeat()

    return {
        "generatedAt": _now_iso(),
        "phase": "vessel-formation",
        "currentMood": "clarifying",
        "dominantSymbols": dominant_symbols,
        "recentEpisodes": recent_episodes,
        "recentCaptures": recent_captures,
        "openThreads": open_threads,
        "activeTensions": [
            "how much should shrine know directly?",
            "what belongs in deep memory vs working memory?",
        ],
        "weather": {
            "tone": "warm-technical",
            "intensity": 0.64,
            "motion": "slow-pulse",
        },
        "heartbeat": {
            "lastPulseAt": heartbeat.get("lastPulseAt"),
            "pulseCount": heartbeat.get("pulseCount", 0),
            "status": heartbeat.get("status", "unknown"),
        },
        "handoff": {
            "summary": "Roles clarified: archive remembers, vessel interprets, shrine reveals.",
            "nextMove": "Refine archive parsing and teach Signal Shrine to render this state.",
        },
    }


def write_current_shrine_state(path: pathlib.Path = STATE_PATH) -> pathlib.Path:
    """Write the current shrine state JSON to disk and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat = _write_heartbeat()
    state = build_current_shrine_state(heartbeat=heartbeat)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
