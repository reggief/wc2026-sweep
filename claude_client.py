"""
Generates WhatsApp message text via the Anthropic Claude API.

Two entry points:
  daily_message(state, yesterday_matches) -> str
  answer_query(state, question)           -> str
"""

from __future__ import annotations

import json
import os

import anthropic

MODEL = "claude-sonnet-4-6"

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
        _client = anthropic.Anthropic(api_key=key)
    return _client


def _format_standings(standings: list[dict]) -> str:
    lines = ["Standings:"]
    for s in standings:
        lines.append(f"{s['rank']}. {s['player']} — {s['points']} pts")
    return "\n".join(lines)


def _format_results(matches: list[dict], team_flags: dict[str, str]) -> str:
    lines = []
    for m in matches:
        hf = team_flags.get(m["home_team"], "")
        af = team_flags.get(m["away_team"], "")
        lines.append(
            f"{hf} {m['home_team']} {m['home_score']}–{m['away_score']} {af} {m['away_team']}"
        )
    return "\n".join(lines)


SCORING_RULES = {
    "group_win": 3,
    "group_draw": 1,
    "advance_from_groups": 5,
    "win_round_of_32": 8,
    "win_round_of_16": 12,
    "win_quarter_final": 18,
    "win_semi_final": 25,
    "win_final": 40,
    "third_place_playoff": "ignored",
    "extra_time_or_penalties": "winner gets the full round bonus regardless",
    "notes": [
        "Best 8 third-placed teams also receive the advance_from_groups bonus",
        "Ties in standings are split equally, no tiebreaker",
        "Prize split is 60/25/15 percent for 1st/2nd/3rd",
    ],
}


def _sweep_context(state: dict) -> str:
    """Serialise the sweep state into a compact context block for Claude."""
    return json.dumps(
        {
            "scoring_rules": SCORING_RULES,
            "all_teams": sorted(state.get("team_flags", {}).keys()),
            "players": state.get("players", {}),
            "advanced_teams": state.get("advanced_teams", []),
            "standings": state.get("standings", []),
        },
        indent=2,
    )


def daily_message(
    state: dict,
    yesterday_matches: list[dict],
    standings: list[dict],
) -> str:
    """
    Generate the full daily WhatsApp message.

    Format:
      [Claude commentary paragraph]
      [results block]
      [standings block]
    """
    results_block = _format_results(yesterday_matches, state.get("team_flags", {}))
    standings_block = _format_standings(standings)
    context = _sweep_context({**state, "standings": standings})

    prompt = f"""You are writing a message for a casual World Cup 2026 sweep competition among friends.
The message goes to a WhatsApp group. Keep it warm, witty, and concise.

Sweep context (who owns which countries):
{context}

Yesterday's results:
{results_block}

Task: Write ONE short paragraph (2-4 sentences) of commentary covering yesterday's matches
and their implications for the sweep. Mention specific players and their countries where
relevant. Do NOT include the results block or standings — those will be appended separately.
Just write the commentary paragraph, nothing else."""

    msg = _get_client().messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    commentary = msg.content[0].text.strip()

    return f"{commentary}\n\n{results_block}\n\n{standings_block}"


def answer_query(state: dict, question: str, standings: list[dict]) -> str:
    """Generate a reply to a natural language query from the WhatsApp group."""
    context = _sweep_context({**state, "standings": standings})
    results_summary = json.dumps(
        [
            {
                "home": m["home_team"],
                "away": m["away_team"],
                "score": f"{m['home_score']}-{m['away_score']}",
                "round": m["round"],
                "completed": m["completed"],
            }
            for m in state.get("matches", [])
            if m.get("completed")
        ],
        indent=2,
    )

    prompt = f"""You are a helpful World Cup 2026 sweep bot responding in a WhatsApp group.
Keep replies concise — this is a chat, not an essay.

Sweep context:
{context}

Completed match results:
{results_summary}

Current standings:
{json.dumps(standings, indent=2)}

User question: {question}

Answer the question directly and helpfully. If you don't have enough information, say so briefly."""

    msg = _get_client().messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def champion_message(state: dict, winner_player: str, standings: list[dict]) -> str:
    """Generate the end-of-tournament congratulations message."""
    context = _sweep_context({**state, "standings": standings})

    prompt = f"""You are announcing the winner of a World Cup 2026 sweep competition in a WhatsApp group.

Sweep context:
{context}

Sweep winner: {winner_player}

Write a short, warm congratulations message (2-3 sentences). Mention the winning country/countries
if known from context. Then output nothing else — the full standings will be appended."""

    msg = _get_client().messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    commentary = msg.content[0].text.strip()
    standings_block = _format_standings(standings)
    return f"{commentary}\n\n{standings_block}"
