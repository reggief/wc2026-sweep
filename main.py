"""
FastAPI application entry point.

  POST /webhook   — receives inbound WhatsApp messages from Whapi.Cloud
  GET  /health    — simple liveness check
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

import claude_client
import scheduler as sched
import state
import whapi
from scoring import calculate_standings

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

BOT_TRIGGER = "worldcupbot"


def _sync_players_from_env() -> None:
    """
    If PLAYERS_JSON is set, write it into sweep.json as the players mapping.
    This is the mechanism for updating the draw without needing shell access.
    """
    raw = os.environ.get("PLAYERS_JSON", "").strip()
    if not raw:
        return
    import json as _json
    try:
        players = _json.loads(raw)
    except Exception as exc:
        log.error("PLAYERS_JSON is not valid JSON: %s", exc)
        return
    current = state.load()
    current["players"] = players
    state.save(current)
    log.info("Players mapping loaded from PLAYERS_JSON: %d players", len(players))


def _sync_winner_overrides_from_env() -> None:
    """
    If WINNER_OVERRIDES_JSON is set, merge it into sweep.json as winner_overrides.
    Use this to record penalty shootout winners that the API can't detect.

    Format: {"match_id": "winning_team_name_en", ...}
    Example: {"74": "Paraguay", "88": "France"}

    Merges with any existing overrides so previously set values are preserved.
    """
    raw = os.environ.get("WINNER_OVERRIDES_JSON", "").strip()
    if not raw:
        return
    import json as _json
    try:
        overrides = _json.loads(raw)
    except Exception as exc:
        log.error("WINNER_OVERRIDES_JSON is not valid JSON: %s", exc)
        return
    current = state.load()
    current.setdefault("winner_overrides", {}).update(overrides)
    state.save(current)
    log.info("Winner overrides applied from WINNER_OVERRIDES_JSON: %s", overrides)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _sync_players_from_env()
    _sync_winner_overrides_from_env()
    _scheduler = sched.create_scheduler()
    _scheduler.start()
    log.info("Scheduler started")
    # Run an immediate poll on startup to hydrate state
    sched.poll_matches()
    yield
    _scheduler.shutdown(wait=False)
    log.info("Scheduler stopped")


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    messages = whapi.parse_webhook(payload)

    for msg in messages:
        # Ignore messages sent by the bot itself
        if msg["is_from_me"]:
            continue

        # Only respond when triggered
        if BOT_TRIGGER not in msg["text"].lower():
            continue

        # Only respond in the configured group
        target_group = os.environ.get("WHAPI_GROUP_ID", "")
        if target_group and msg["group_id"] != target_group:
            continue

        _handle_query(msg["text"], sender_phone=msg["sender_phone"], sender_name=msg["sender_name"])

    return {"ok": True}


def _handle_query(text: str, sender_phone: str = "", sender_name: str = "") -> None:
    try:
        current_state = state.load()
    except Exception as exc:
        log.error("Failed to load state for query: %s", exc)
        return

    players: dict[str, list[str]] = current_state.get("players", {})
    all_matches = state.matches_from_state(current_state)
    advanced_set = set(current_state.get("advanced_teams", []))
    standings = calculate_standings(players, all_matches, advanced_set)

    try:
        reply = claude_client.answer_query(current_state, text, standings, sender_phone=sender_phone, sender_name=sender_name)
        whapi.send_message(reply)
    except Exception as exc:
        log.error("Failed to answer query: %s", exc)
