from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Points earned by winning a match in each round
ROUND_WIN_POINTS: dict[str, int] = {
    "group": 3,
    "round_of_32": 8,
    "round_of_16": 12,
    "quarter_final": 18,
    "semi_final": 25,
    "final": 40,
}

GROUP_DRAW_POINTS = 1
ADVANCE_FROM_GROUPS_POINTS = 5

# Prize pool percentage split for 1st / 2nd / 3rd
PRIZE_SPLITS = [0.60, 0.25, 0.15]

# Rounds that contribute zero points to the sweep
IGNORED_ROUNDS = {"third_place"}


@dataclass
class Match:
    id: str
    round: str
    home_team: str
    away_team: str
    home_score: Optional[int]
    away_score: Optional[int]
    completed: bool
    date: str  # "YYYY-MM-DD"
    # Set when a knockout match is decided by extra time / penalties and the
    # regulation+ET scoreline is a draw.
    winner_override: Optional[str] = field(default=None)

    @property
    def winner(self) -> Optional[str]:
        if not self.completed or self.home_score is None or self.away_score is None:
            return None
        if self.winner_override:
            return self.winner_override
        if self.home_score > self.away_score:
            return self.home_team
        if self.away_score > self.home_score:
            return self.away_team
        return None

    @property
    def is_draw(self) -> bool:
        if not self.completed or self.home_score is None or self.away_score is None:
            return False
        if self.winner_override:
            return False
        return self.home_score == self.away_score


def points_for_match(team: str, match: Match) -> int:
    """Points a single team earns from a single completed match."""
    if not match.completed:
        return 0
    if match.round in IGNORED_ROUNDS:
        return 0
    if team not in (match.home_team, match.away_team):
        return 0
    if match.winner == team:
        return ROUND_WIN_POINTS.get(match.round, 0)
    if match.is_draw and match.round == "group":
        return GROUP_DRAW_POINTS
    return 0


def points_for_team(
    team: str,
    matches: list[Match],
    advanced_teams: set[str],
) -> int:
    """Total sweep points accumulated by one country across all matches."""
    total = sum(points_for_match(team, m) for m in matches)
    if team in advanced_teams:
        total += ADVANCE_FROM_GROUPS_POINTS
    return total


def calculate_standings(
    players: dict[str, list[str]],
    matches: list[Match],
    advanced_teams: set[str],
) -> list[dict]:
    """
    Return a ranked list of players.

    Each entry: {"rank": int, "player": str, "points": int}
    Players tied on points share the same rank.
    Within a tied group players are sorted alphabetically so output is stable.
    """
    scores: dict[str, int] = {
        player: sum(points_for_team(t, matches, advanced_teams) for t in teams)
        for player, teams in players.items()
    }

    sorted_entries = sorted(scores.items(), key=lambda x: (-x[1], x[0]))

    standings: list[dict] = []
    i = 0
    while i < len(sorted_entries):
        pts = sorted_entries[i][1]
        j = i
        while j < len(sorted_entries) and sorted_entries[j][1] == pts:
            j += 1
        rank = i + 1
        for name, _ in sorted_entries[i:j]:
            standings.append({"rank": rank, "player": name, "points": pts})
        i = j

    return standings


def split_prizes(standings: list[dict], prize_pool: float) -> dict[str, float]:
    """
    Allocate prize money according to PRIZE_SPLITS (60/25/15).

    When players are tied across prize positions their combined shares are
    averaged and distributed equally.  Players outside the top 3 positions
    receive nothing.
    """
    by_rank: dict[int, list[str]] = {}
    for entry in standings:
        by_rank.setdefault(entry["rank"], []).append(entry["player"])

    result: dict[str, float] = {}
    position = 0  # next un-allocated prize index

    for rank in sorted(by_rank.keys()):
        players = by_rank[rank]
        n = len(players)
        slots = range(position, min(position + n, len(PRIZE_SPLITS)))
        if slots:
            share = sum(PRIZE_SPLITS[s] for s in slots) / n
            for player in players:
                result[player] = prize_pool * share
        position += n
        if position >= len(PRIZE_SPLITS):
            break

    return result
