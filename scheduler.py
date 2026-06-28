"""
Background jobs:

  poll_matches — every 30 minutes, fetches latest data from worldcup26.ir,
                 updates sweep.json, and sends a result message to the group
                 for any completed match that hasn't been announced yet.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

import claude_client
import state
import teams
import whapi
from scoring import Match, calculate_standings

log = logging.getLogger(__name__)

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")

# One-time migration window for state files saved before notified_matches
# tracking existed: matches finished more than this long ago are assumed to
# have already been announced; more recent ones are left unmarked so they
# get (re)sent on the next successful poll.
NOTIFIED_MIGRATION_WINDOW_HOURS = 48


def _migrate_notified_matches(old_state: dict) -> set[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=NOTIFIED_MIGRATION_WINDOW_HOURS)
    notified: set[str] = set()
    for m in old_state.get("matches", []):
        if not m.get("completed"):
            continue
        try:
            match_date = datetime.strptime(m["date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if match_date < cutoff:
            notified.add(m["id"])
    return notified


# ---------------------------------------------------------------------------
# Poll job
# ---------------------------------------------------------------------------

def poll_matches() -> None:
    """
    Fetch latest match data from worldcup26.ir and update state.
    Sends a result message for each batch of completed-but-unannounced matches.
    Silently skips the cycle on any API error.
    """
    try:
        raw_games, raw_groups, raw_teams = teams.fetch_all()
    except Exception as exc:
        log.warning("poll_matches: API error, skipping cycle: %s", exc)
        return

    try:
        old_state = state.load()
    except Exception as exc:
        log.error("poll_matches: failed to load state: %s", exc)
        return

    if "notified_matches" in old_state:
        notified: set[str] = set(old_state["notified_matches"])
    else:
        notified = _migrate_notified_matches(old_state)
        log.info("poll_matches: migrated notified_matches, seeded %d match(es)", len(notified))

    team_map = teams.build_team_map(raw_teams)
    winner_overrides: dict[str, str] = old_state.get("winner_overrides", {})

    parsed_matches = [
        teams.parse_match(g, team_map, winner_overrides) for g in raw_games
    ]
    advanced = teams.compute_advanced_teams(raw_groups, team_map)
    team_flags = teams.build_team_flags(raw_teams)

    new_state = {
        **old_state,
        "matches": [state.match_to_dict(m) for m in parsed_matches],
        "advanced_teams": advanced,
        "team_flags": team_flags,
        "notified_matches": sorted(notified),
        "last_polled": datetime.now(timezone.utc).isoformat(),
    }

    try:
        state.save(new_state)
    except Exception as exc:
        log.error("poll_matches: failed to save state: %s", exc)
        return

    log.info("poll_matches: %d matches, %d advanced teams", len(parsed_matches), len(advanced))

    # Find completed matches that haven't had a result message sent yet
    newly_finished = [
        m for m in parsed_matches
        if m.completed and m.id not in notified
        and m.home_team and m.away_team  # skip undetermined knockout slots
    ]

    if newly_finished:
        _send_match_updates(newly_finished, new_state, notified)

    _check_for_champion(new_state)


def _send_match_updates(new_matches: list[Match], current_state: dict, notified: set[str]) -> None:
    """Send a result message covering one or more unannounced completed matches."""
    players: dict[str, list[str]] = current_state.get("players", {})
    all_matches = state.matches_from_state(current_state)
    advanced_set = set(current_state.get("advanced_teams", []))
    standings = calculate_standings(players, all_matches, advanced_set)
    match_dicts = [state.match_to_dict(m) for m in new_matches]

    try:
        text = claude_client.match_result_message(current_state, match_dicts, standings)
        whapi.send_message(text)
        log.info("Sent result message for %d match(es)", len(new_matches))
    except Exception as exc:
        log.error("Failed to send match result message: %s", exc)
        return  # leave unmarked so the next poll retries

    notified.update(m.id for m in new_matches)
    current_state["notified_matches"] = sorted(notified)
    try:
        state.save(current_state)
    except Exception as exc:
        log.error("poll_matches: failed to persist notified_matches: %s", exc)


def _check_for_champion(current_state: dict) -> None:
    """Send the end-of-tournament message when the Final has a result."""
    if current_state.get("champion_announced"):
        return

    final_match = next(
        (m for m in current_state.get("matches", []) if m["round"] == "final" and m["completed"]),
        None,
    )
    if not final_match:
        return

    m = state.matches_from_state({"matches": [final_match]})[0]
    champion_team = m.winner
    if not champion_team:
        return

    players: dict[str, list[str]] = current_state.get("players", {})
    champion_player = next(
        (player for player, ctries in players.items() if champion_team in ctries),
        None,
    )
    if not champion_player:
        log.warning("Champion team %r not owned by any player", champion_team)
        return

    all_matches = state.matches_from_state(current_state)
    advanced_set = set(current_state.get("advanced_teams", []))
    standings = calculate_standings(players, all_matches, advanced_set)

    try:
        text = claude_client.champion_message(current_state, champion_player, standings)
        whapi.send_message(text)
        current_state["champion_announced"] = True
        state.save(current_state)
        log.info("Champion message sent for player %r", champion_player)
    except Exception as exc:
        log.error("Failed to send champion message: %s", exc)


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=str(MELBOURNE_TZ))
    scheduler.add_job(poll_matches, "interval", minutes=30, id="poll_matches")
    return scheduler
