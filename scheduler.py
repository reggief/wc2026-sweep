"""
Two APScheduler background jobs:

  poll_matches   — every 30 minutes, fetches latest data from worldcup26.ir
                   and updates sweep.json. Silently skips on API errors.

  daily_summary  — 9am Melbourne time (Australia/Melbourne). Reports on
                   matches completed since the last daily send. Silent if
                   there were none.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import claude_client
import state
import teams
import whapi
from scoring import Match, calculate_standings

log = logging.getLogger(__name__)

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")


# ---------------------------------------------------------------------------
# Poll job
# ---------------------------------------------------------------------------

def poll_matches() -> None:
    """
    Fetch latest match data and group standings from worldcup26.ir, update state.
    Any exception from the API is caught and the cycle is skipped silently.
    """
    try:
        raw_games, raw_groups, raw_teams = teams.fetch_all()
    except Exception as exc:
        log.warning("poll_matches: API error, skipping cycle: %s", exc)
        return

    try:
        current_state = state.load()
    except Exception as exc:
        log.error("poll_matches: failed to load state: %s", exc)
        return

    team_map = teams.build_team_map(raw_teams)
    winner_overrides: dict[str, str] = current_state.get("winner_overrides", {})

    parsed_matches = [
        teams.parse_match(g, team_map, winner_overrides) for g in raw_games
    ]
    advanced = teams.compute_advanced_teams(raw_groups, team_map)
    team_flags = teams.build_team_flags(raw_teams)

    current_state["matches"] = [state.match_to_dict(m) for m in parsed_matches]
    current_state["advanced_teams"] = advanced
    current_state["team_flags"] = team_flags
    current_state["last_polled"] = datetime.now(timezone.utc).isoformat()

    try:
        state.save(current_state)
    except Exception as exc:
        log.error("poll_matches: failed to save state: %s", exc)

    log.info(
        "poll_matches: %d matches, %d advanced teams",
        len(parsed_matches),
        len(advanced),
    )

    _check_for_champion(current_state)


def _check_for_champion(current_state: dict) -> None:
    """
    If the Final has a completed result and the champion message hasn't been
    sent yet, send it now.
    """
    if current_state.get("champion_announced"):
        return

    final_match = next(
        (m for m in current_state.get("matches", []) if m["round"] == "final" and m["completed"]),
        None,
    )
    if not final_match:
        return

    # Determine winner name
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
# Daily summary job
# ---------------------------------------------------------------------------

def daily_summary() -> None:
    """
    Run at 9am Melbourne time. Sends a message about matches completed since
    the last daily send. Silent if there are none.
    """
    try:
        current_state = state.load()
    except Exception as exc:
        log.error("daily_summary: failed to load state: %s", exc)
        return

    last_sent_raw: str | None = current_state.get("last_daily_sent")
    last_sent = (
        datetime.fromisoformat(last_sent_raw)
        if last_sent_raw
        else datetime.fromtimestamp(0, tz=timezone.utc)
    )

    now_utc = datetime.now(timezone.utc)

    all_matches = state.matches_from_state(current_state)

    # Matches that completed after the last daily send
    def _match_dt(m: Match) -> datetime:
        return datetime.fromisoformat(m.date.replace("Z", "+00:00"))

    yesterday_completed = [
        m for m in all_matches
        if m.completed
        and _match_dt(m) > last_sent
        and _match_dt(m) < now_utc
    ]

    if not yesterday_completed:
        log.info("daily_summary: no new completed matches since last send")
        return

    players: dict[str, list[str]] = current_state.get("players", {})
    advanced_set = set(current_state.get("advanced_teams", []))
    standings = calculate_standings(players, all_matches, advanced_set)

    match_dicts = [state.match_to_dict(m) for m in yesterday_completed]

    try:
        text = claude_client.daily_message(current_state, match_dicts, standings)
        whapi.send_message(text)
        current_state["last_daily_sent"] = now_utc.isoformat()
        state.save(current_state)
        log.info("daily_summary: sent message covering %d matches", len(yesterday_completed))
    except Exception as exc:
        log.error("daily_summary: failed to send message: %s", exc)


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=str(MELBOURNE_TZ))

    # Poll every 30 minutes
    scheduler.add_job(poll_matches, "interval", minutes=30, id="poll_matches")

    # Daily at 9am Melbourne time
    scheduler.add_job(
        daily_summary,
        CronTrigger(hour=9, minute=0, timezone=MELBOURNE_TZ),
        id="daily_summary",
    )

    return scheduler
