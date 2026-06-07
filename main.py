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


@asynccontextmanager
async def lifespan(app: FastAPI):
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

        _handle_query(msg["text"])

    return {"ok": True}


def _handle_query(text: str) -> None:
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
        reply = claude_client.answer_query(current_state, text, standings)
        whapi.send_message(reply)
    except Exception as exc:
        log.error("Failed to answer query: %s", exc)
