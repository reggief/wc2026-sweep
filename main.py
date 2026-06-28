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

# TEMPORARY one-off admin trigger — sends a pre-written message verbatim.
# Remove this block (and the check for it in webhook()) once used.
_ADMIN_TRIGGER = "post breakdown"
_ADMIN_MESSAGE = """📊 Group Stage Complete — Full Points Breakdown

Here's exactly how everyone's points have built up so far (group stage results + the 5pt bonus for advancing):

1. Reg — 44 pts
🇫🇷 France: 3W 0D 0L, 9 pts + 5 advanced = 14
🇭🇷 Croatia: 2W 0D 1L, 6 pts + 5 advanced = 11
🇪🇬 Egypt: 1W 2D 0L, 5 pts + 5 advanced = 10
🇧🇦 Bosnia & Herzegovina: 1W 1D 1L, 4 pts + 5 advanced = 9

2. Chris — 43 pts
🇲🇽 Mexico: 3W 0D 0L, 9 pts + 5 advanced = 14
🇩🇪 Germany: 2W 0D 1L, 6 pts + 5 advanced = 11
🇩🇿 Algeria: 1W 1D 1L, 4 pts + 5 advanced = 9
🇵🇾 Paraguay: 1W 1D 1L, 4 pts + 5 advanced = 9

3. Samuel — 40 pts
🇨🇭 Switzerland: 2W 1D 0L, 7 pts + 5 advanced = 12
🇵🇹 Portugal: 1W 2D 0L, 5 pts + 5 advanced = 10
🇸🇪 Sweden: 1W 1D 1L, 4 pts + 5 advanced = 9
🇬🇭 Ghana: 1W 1D 1L, 4 pts + 5 advanced = 9

4. Matt — 36 pts
🇳🇱 Netherlands: 2W 1D 0L, 7 pts + 5 advanced = 12
🇲🇦 Morocco: 2W 1D 0L, 7 pts + 5 advanced = 12
🇦🇺 Australia: 1W 1D 1L, 4 pts + 5 advanced = 9
🏴 Scotland: 1W 0D 2L, 3 pts, eliminated = 3

5. Sarah — 34 pts
🇦🇷 Argentina: 3W 0D 0L, 9 pts + 5 advanced = 14
🇨🇦 Canada: 1W 1D 1L, 4 pts + 5 advanced = 9
🇨🇻 Cape Verde: 0W 3D 0L, 3 pts + 5 advanced = 8
🇹🇷 Turkey: 1W 0D 2L, 3 pts, eliminated = 3

5. Bart — 34 pts
🇧🇷 Brazil: 2W 1D 0L, 7 pts + 5 advanced = 12
🇺🇸 United States: 2W 0D 1L, 6 pts + 5 advanced = 11
🇨🇮 Ivory Coast: 2W 0D 1L, 6 pts + 5 advanced = 11
🇵🇦 Panama: 0W 0D 3L, 0 pts, eliminated = 0

7. Darcy — 31 pts
🇪🇸 Spain: 2W 1D 0L, 7 pts + 5 advanced = 12
🇨🇩 DR Congo: 1W 1D 1L, 4 pts + 5 advanced = 9
🇪🇨 Ecuador: 1W 1D 1L, 4 pts + 5 advanced = 9
🇨🇿 Czech Republic: 0W 1D 2L, 1 pt, eliminated = 1

8. Oli — 30 pts
🇨🇴 Colombia: 2W 1D 0L, 7 pts + 5 advanced = 12
🇧🇪 Belgium: 1W 2D 0L, 5 pts + 5 advanced = 10
🇸🇳 Senegal: 1W 0D 2L, 3 pts + 5 advanced = 8
🇯🇴 Jordan: 0W 0D 3L, 0 pts, eliminated = 0

9. Sammy — 27 pts
🇳🇴 Norway: 2W 0D 1L, 6 pts + 5 advanced = 11
🇯🇵 Japan: 1W 2D 0L, 5 pts + 5 advanced = 10
🇰🇷 South Korea: 1W 0D 2L, 3 pts, eliminated = 3
🇮🇷 Iran: 0W 3D 0L, 3 pts, eliminated = 3

10. Tess — 24 pts
🏴 England: 2W 1D 0L, 7 pts + 5 advanced = 12
🇦🇹 Austria: 1W 1D 1L, 4 pts + 5 advanced = 9
🇺🇾 Uruguay: 0W 2D 1L, 2 pts, eliminated = 2
🇶🇦 Qatar: 0W 1D 2L, 1 pt, eliminated = 1

Knockout rounds are worth a lot more (8/12/18/25/40 pts per win), so plenty can still change!"""


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _sync_players_from_env()
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

        # TEMPORARY one-off admin trigger — see _ADMIN_TRIGGER above
        if _ADMIN_TRIGGER in msg["text"].lower():
            try:
                whapi.send_message(_ADMIN_MESSAGE)
                log.info("Sent admin breakdown message")
            except Exception as exc:
                log.error("Failed to send admin breakdown message: %s", exc)
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
