from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

STATE_DIR = pathlib.Path.cwd() / "state"
STATE_PATH = STATE_DIR / "current-shrine-state.json"


def build_current_shrine_state() -> dict:
    """Build the first shrine-facing state contract.

    For now this is intentionally tiny and mostly hard-coded.
    The next step is to replace the placeholder episode/capture data
    with values read from the episodic archive.
    """
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "phase": "vessel-formation",
        "currentMood": "clarifying",
        "dominantSymbols": ["🫀", "😈", "🌀"],
        "recentEpisodes": [
            {
                "id": "EP-014",
                "title": "architecture separation clarified",
                "salience": 4,
                "source": "glitch-episodic-archive",
            }
        ],
        "recentCaptures": [
            {
                "id": "CAP-2026-04-15-fox-weather",
                "title": "wet paper bag full of foxes",
                "promote": False,
                "source": "glitch-episodic-archive",
            }
        ],
        "openThreads": [
            "archive reader implementation",
            "shrine state generation",
            "signal shrine rendering contract",
        ],
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
            "nextMove": "Implement archive_reader.py and generate this file from real episode and capture data.",
        },
    }


def write_current_shrine_state(path: pathlib.Path = STATE_PATH) -> pathlib.Path:
    """Write the current shrine state JSON to disk and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state = build_current_shrine_state()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
