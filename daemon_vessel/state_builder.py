from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

from daemon_vessel.archive_reader import list_recent_captures, list_recent_episodes

STATE_DIR = pathlib.Path.cwd() / "state"
STATE_PATH = STATE_DIR / "current-shrine-state.json"


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
        for thread in item.get("threads", []):
            if thread not in seen:
                seen.append(thread)
    fallback = [
        "archive reader implementation",
        "shrine state generation",
        "signal shrine rendering contract",
    ]
    return seen or fallback


def build_current_shrine_state() -> dict:
    """Build the shrine-facing state contract from recent archive summaries."""
    recent_episodes = list_recent_episodes(limit=3)
    recent_captures = list_recent_captures(limit=3)
    dominant_symbols = _collect_dominant_symbols(recent_episodes, recent_captures)
    open_threads = _collect_open_threads(recent_episodes, recent_captures)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
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
        "handoff": {
            "summary": "Roles clarified: archive remembers, vessel interprets, shrine reveals.",
            "nextMove": "Refine archive parsing and teach Signal Shrine to render this state.",
        },
    }


def write_current_shrine_state(path: pathlib.Path = STATE_PATH) -> pathlib.Path:
    """Write the current shrine state JSON to disk and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state = build_current_shrine_state()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
