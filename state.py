"""
Load and save sweep.json — the single source of truth for bot state.

Schema of sweep.json:
{
  "players":         {"PlayerName": ["TeamA", "TeamB"], ...},
  "advanced_teams":  ["TeamA", ...],
  "matches":         [{"id", "round", "home_team", "away_team",
                       "home_score", "away_score", "completed",
                       "date", "winner_override"}, ...],
  "winner_overrides": {"game_id": "winning_team_name", ...},
  "team_flags":      {"TeamName": "🇦🇺", ...},
  "last_polled":     "2026-06-11T10:00:00Z" | null,
  "last_daily_sent": "2026-06-12T23:00:00Z" | null
}
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from scoring import Match

DEFAULT_DATA_PATH = Path("data/sweep.json")


def _data_path() -> Path:
    raw = os.environ.get("DATA_PATH")
    return Path(raw) if raw else DEFAULT_DATA_PATH


def load() -> dict:
    path = _data_path()
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save(state: dict) -> None:
    path = _data_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def matches_from_state(state: dict) -> list[Match]:
    return [
        Match(
            id=m["id"],
            round=m["round"],
            home_team=m["home_team"],
            away_team=m["away_team"],
            home_score=m.get("home_score"),
            away_score=m.get("away_score"),
            completed=m["completed"],
            date=m["date"],
            winner_override=m.get("winner_override"),
        )
        for m in state.get("matches", [])
    ]


def match_to_dict(m: Match) -> dict:
    return asdict(m)
