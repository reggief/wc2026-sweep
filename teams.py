"""
Fetches and normalises match data from worldcup26.ir.

Public API for the rest of the app:
  fetch_all()           -> (games, groups, teams_list) raw API dicts
  build_team_map()      -> {team_id: {name_en, iso2, flag_emoji}}
  build_team_flags()    -> {team_name_en: flag_emoji}
  parse_match()         -> scoring.Match
  compute_advanced_teams() -> list[str] of team name_en values
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from scoring import Match

API_BASE = "https://worldcup26.ir"
HTTP_TIMEOUT = 10.0

# Map worldcup26.ir 'type' values to scoring.py round strings
ROUND_MAP: dict[str, str] = {
    "group": "group",
    "r32": "round_of_32",
    "r16": "round_of_16",
    "qf": "quarter_final",
    "sf": "semi_final",
    "final": "final",
    "third": "third_place",
}

# 2026 format: 12 groups, best 8 third-placed teams advance
GROUP_COUNT = 12
THIRD_PLACE_QUALIFIERS = 8

# Matches played per team to complete the group stage
GROUP_STAGE_MATCHES = 3


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch_all() -> tuple[list[dict], list[dict], list[dict]]:
    """Return (games, groups, teams) raw API lists. Raises on HTTP error."""
    client = httpx.Client(timeout=HTTP_TIMEOUT)
    with client:
        games = client.get(f"{API_BASE}/get/games").raise_for_status().json()["games"]
        groups = client.get(f"{API_BASE}/get/groups").raise_for_status().json()["groups"]
        teams = client.get(f"{API_BASE}/get/teams").raise_for_status().json()["teams"]
    return games, groups, teams


# ---------------------------------------------------------------------------
# Flag emoji
# ---------------------------------------------------------------------------

def flag_emoji(iso2: str) -> str:
    """
    Regional-indicator flag emoji from a 2-letter ISO 3166-1 alpha-2 code.
    Returns '' for codes that aren't exactly 2 ASCII letters (e.g. 'SCO').
    """
    if len(iso2) != 2 or not iso2.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2.upper())


# ---------------------------------------------------------------------------
# Team lookup helpers
# ---------------------------------------------------------------------------

def build_team_map(teams: list[dict]) -> dict[str, dict]:
    """Return {team_id_str: {name_en, iso2, flag_emoji}} from the /get/teams list."""
    return {
        str(t["id"]): {
            "name_en": t["name_en"],
            "iso2": t.get("iso2", ""),
            "flag_emoji": flag_emoji(t.get("iso2", "")),
        }
        for t in teams
    }


def build_team_flags(teams: list[dict]) -> dict[str, str]:
    """Return {team_name_en: flag_emoji} for use when formatting messages."""
    return {t["name_en"]: flag_emoji(t.get("iso2", "")) for t in teams}


# ---------------------------------------------------------------------------
# Match parsing
# ---------------------------------------------------------------------------

def _parse_local_date(local_date: str) -> datetime:
    """
    Parse 'MM/DD/YYYY HH:MM' and treat it as UTC.

    The API provides local tournament times (US/Mexico/Canada, UTC-4 to UTC-7).
    Treating them as UTC introduces a ~4-7 hour error, which is well within the
    24-hour Melbourne daily window, so the date filtering in the scheduler is
    not affected.
    """
    return datetime.strptime(local_date, "%m/%d/%Y %H:%M").replace(tzinfo=timezone.utc)


def parse_match(
    raw: dict,
    team_map: dict[str, dict],
    winner_overrides: dict[str, str] | None = None,
) -> Match:
    """
    Convert a raw game dict from /get/games into a scoring.Match.

    winner_overrides — {game_id: winning_team_name_en} stored in sweep.json.
    Used to record the winner of matches decided by penalty shootout, where
    the API only stores the regulation+ET scoreline (often a draw).

    For knockout games not yet determined (home_team_id == "0") team names
    will be empty strings; those matches have completed=False so they
    contribute no points.
    """
    game_id = str(raw["id"])
    finished = str(raw.get("finished", "FALSE")).upper() == "TRUE"

    # Group games embed team names; future knockout slots don't
    home_tid = str(raw.get("home_team_id", "0"))
    away_tid = str(raw.get("away_team_id", "0"))
    home_name = raw.get("home_team_name_en") or team_map.get(home_tid, {}).get("name_en", "")
    away_name = raw.get("away_team_name_en") or team_map.get(away_tid, {}).get("name_en", "")

    home_score: int | None = None
    away_score: int | None = None
    if finished:
        try:
            home_score = int(raw["home_score"])
            away_score = int(raw["away_score"])
        except (KeyError, ValueError, TypeError):
            pass

    dt = _parse_local_date(raw["local_date"])

    override = (winner_overrides or {}).get(game_id)

    return Match(
        id=game_id,
        round=ROUND_MAP.get(raw.get("type", ""), raw.get("type", "")),
        home_team=home_name,
        away_team=away_name,
        home_score=home_score,
        away_score=away_score,
        completed=finished,
        date=dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        winner_override=override,
    )


# ---------------------------------------------------------------------------
# Advancement logic
# ---------------------------------------------------------------------------

def _sort_key(team_entry: dict) -> tuple[int, int, int]:
    return (
        int(team_entry.get("pts", 0)),
        int(team_entry.get("gd", 0)),
        int(team_entry.get("gf", 0)),
    )


def compute_advanced_teams(
    groups: list[dict],
    team_map: dict[str, dict],
) -> list[str]:
    """
    Determine which teams have advanced from the group stage.

    Returns team name_en values for:
    - 1st and 2nd place of every completed group
    - Best THIRD_PLACE_QUALIFIERS third-placed teams (only after all groups
      complete, because the ranking compares across all groups)
    """
    advanced: list[str] = []
    third_place_entries: list[dict] = []
    completed_groups = 0

    for group in groups:
        members = group.get("teams", [])
        if len(members) < 3:
            continue

        # Group is complete when any team has played GROUP_STAGE_MATCHES games
        if not any(int(t.get("mp", 0)) >= GROUP_STAGE_MATCHES for t in members):
            continue

        completed_groups += 1
        ranked = sorted(members, key=_sort_key, reverse=True)

        def name(entry: dict) -> str:
            return team_map.get(str(entry["team_id"]), {}).get("name_en", "")

        for pos in range(min(2, len(ranked))):
            n = name(ranked[pos])
            if n:
                advanced.append(n)

        if len(ranked) >= 3:
            third_place_entries.append(ranked[2])

    # Best third-placed teams are only finalised once all groups are done
    if completed_groups == GROUP_COUNT and third_place_entries:
        best_thirds = sorted(third_place_entries, key=_sort_key, reverse=True)
        for entry in best_thirds[:THIRD_PLACE_QUALIFIERS]:
            n = team_map.get(str(entry["team_id"]), {}).get("name_en", "")
            if n:
                advanced.append(n)

    return advanced
