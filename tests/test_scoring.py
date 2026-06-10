"""
Comprehensive tests for scoring.py.

Covers every points rule, advancement bonus, standings ranking, tie handling,
and prize-split edge cases.
"""

import pytest

from scoring import (
    ADVANCE_FROM_GROUPS_POINTS,
    GROUP_DRAW_POINTS,
    ROUND_WIN_POINTS,
    Match,
    calculate_standings,
    points_for_match,
    points_for_team,
    split_prizes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def group_match(home, away, home_score, away_score, completed=True):
    return Match(
        id="g",
        round="group",
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        completed=completed,
        date="2026-06-10",
    )


def knockout_match(round_, home, away, home_score, away_score, winner_override=None, completed=True):
    return Match(
        id="k",
        round=round_,
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        completed=completed,
        date="2026-06-20",
        winner_override=winner_override,
    )


# ---------------------------------------------------------------------------
# points_for_match — group stage
# ---------------------------------------------------------------------------

class TestGroupStagePoints:
    def test_home_win(self):
        m = group_match("France", "Morocco", 2, 0)
        assert points_for_match("France", m) == 3

    def test_away_win(self):
        m = group_match("France", "Morocco", 0, 1)
        assert points_for_match("Morocco", m) == 3

    def test_draw_gives_both_teams_one_point(self):
        m = group_match("Germany", "USA", 1, 1)
        assert points_for_match("Germany", m) == GROUP_DRAW_POINTS
        assert points_for_match("USA", m) == GROUP_DRAW_POINTS

    def test_loser_gets_zero(self):
        m = group_match("France", "Morocco", 2, 0)
        assert points_for_match("Morocco", m) == 0

    def test_team_not_in_match_gets_zero(self):
        m = group_match("France", "Morocco", 2, 0)
        assert points_for_match("Brazil", m) == 0

    def test_incomplete_match_gives_zero(self):
        m = group_match("France", "Morocco", None, None, completed=False)
        assert points_for_match("France", m) == 0
        assert points_for_match("Morocco", m) == 0

    def test_zero_zero_draw(self):
        m = group_match("Italy", "Spain", 0, 0)
        assert points_for_match("Italy", m) == 1
        assert points_for_match("Spain", m) == 1


# ---------------------------------------------------------------------------
# points_for_match — knockout rounds
# ---------------------------------------------------------------------------

class TestKnockoutPoints:
    @pytest.mark.parametrize("round_, expected", [
        ("round_of_32", 8),
        ("round_of_16", 12),
        ("quarter_final", 18),
        ("semi_final", 25),
        ("final", 40),
    ])
    def test_winner_gets_correct_points(self, round_, expected):
        m = knockout_match(round_, "France", "Morocco", 1, 0)
        assert points_for_match("France", m) == expected

    @pytest.mark.parametrize("round_", [
        "round_of_32", "round_of_16", "quarter_final", "semi_final", "final"
    ])
    def test_loser_gets_zero(self, round_):
        m = knockout_match(round_, "France", "Morocco", 2, 0)
        assert points_for_match("Morocco", m) == 0

    def test_penalty_winner_gets_points(self):
        # Regulation ends 1-1; Morocco wins on penalties
        m = knockout_match("round_of_16", "France", "Morocco", 1, 1, winner_override="Morocco")
        assert points_for_match("Morocco", m) == 12
        assert points_for_match("France", m) == 0

    def test_penalty_winner_away_team(self):
        m = knockout_match("quarter_final", "Brazil", "Argentina", 0, 0, winner_override="Argentina")
        assert points_for_match("Argentina", m) == 18
        assert points_for_match("Brazil", m) == 0

    def test_knockout_draw_without_override_gives_no_points(self):
        # Shouldn't happen in practice but the model must be safe
        m = knockout_match("round_of_32", "France", "Morocco", 1, 1)
        assert points_for_match("France", m) == 0
        assert points_for_match("Morocco", m) == 0


# ---------------------------------------------------------------------------
# points_for_match — third-place playoff
# ---------------------------------------------------------------------------

class TestThirdPlaceIgnored:
    def test_winner_gets_zero(self):
        m = knockout_match("third_place", "Croatia", "Morocco", 2, 1)
        assert points_for_match("Croatia", m) == 0

    def test_loser_gets_zero(self):
        m = knockout_match("third_place", "Croatia", "Morocco", 2, 1)
        assert points_for_match("Morocco", m) == 0


# ---------------------------------------------------------------------------
# points_for_team
# ---------------------------------------------------------------------------

class TestPointsForTeam:
    def test_single_win(self):
        matches = [group_match("France", "Morocco", 2, 0)]
        assert points_for_team("France", matches, set()) == 3

    def test_multiple_matches_accumulate(self):
        matches = [
            group_match("France", "Morocco", 2, 0),   # win  → 3
            group_match("France", "Germany", 1, 1),   # draw → 1
            group_match("Brazil", "France", 0, 1),    # win  → 3
        ]
        assert points_for_team("France", matches, set()) == 7

    def test_advance_from_groups_bonus_added(self):
        matches = [group_match("France", "Morocco", 2, 0)]
        assert points_for_team("France", matches, {"France"}) == 3 + ADVANCE_FROM_GROUPS_POINTS

    def test_team_not_in_advanced_gets_no_bonus(self):
        matches = [group_match("France", "Morocco", 2, 0)]
        assert points_for_team("Morocco", matches, set()) == 0

    def test_no_matches_but_advanced(self):
        # Edge: team in advanced_teams with no match records yet
        assert points_for_team("France", [], {"France"}) == ADVANCE_FROM_GROUPS_POINTS

    def test_no_matches_not_advanced(self):
        assert points_for_team("France", [], set()) == 0

    def test_full_tournament_run(self):
        matches = [
            group_match("France", "A", 1, 0),           # group win  → 3
            group_match("France", "B", 1, 1),           # group draw → 1
            group_match("France", "C", 2, 0),           # group win  → 3
            knockout_match("round_of_32", "France", "D", 2, 0),    # → 8
            knockout_match("round_of_16", "France", "E", 1, 0),    # → 12
            knockout_match("quarter_final", "France", "F", 2, 1),  # → 18
            knockout_match("semi_final", "France", "G", 1, 0),     # → 25
            knockout_match("final", "France", "H", 2, 1),          # → 40
        ]
        expected = 3 + 1 + 3 + ADVANCE_FROM_GROUPS_POINTS + 8 + 12 + 18 + 25 + 40
        assert points_for_team("France", matches, {"France"}) == expected


# ---------------------------------------------------------------------------
# calculate_standings
# ---------------------------------------------------------------------------

class TestCalculateStandings:
    def test_simple_distinct_ranking(self):
        players = {"Alice": ["France"], "Bob": ["Morocco"], "Carol": ["Germany"]}
        matches = [
            group_match("France", "Morocco", 2, 0),     # France win → Alice 3, Bob 0
            group_match("Germany", "Brazil", 1, 1),     # draw      → Carol 1
        ]
        standings = calculate_standings(players, matches, set())
        assert standings[0] == {"rank": 1, "player": "Alice", "points": 3}
        assert standings[1] == {"rank": 2, "player": "Carol", "points": 1}
        assert standings[2] == {"rank": 3, "player": "Bob", "points": 0}

    def test_tied_players_share_rank(self):
        players = {"Alice": ["France"], "Bob": ["Morocco"]}
        matches = [group_match("France", "Morocco", 1, 1)]
        standings = calculate_standings(players, matches, set())
        assert standings[0]["rank"] == 1
        assert standings[1]["rank"] == 1
        assert standings[0]["points"] == 1
        assert standings[1]["points"] == 1

    def test_three_way_tie_all_rank_one(self):
        players = {"Alice": ["A"], "Bob": ["B"], "Carol": ["C"]}
        matches = [
            group_match("A", "X", 1, 1),  # draw → 1 pt each for A, X
            group_match("B", "Y", 1, 1),
            group_match("C", "Z", 1, 1),
        ]
        standings = calculate_standings(players, matches, set())
        assert all(s["rank"] == 1 for s in standings)

    def test_rank_skips_after_tie(self):
        # Two players tied at rank 1 — next player should be rank 3
        players = {"Alice": ["France"], "Bob": ["Morocco"], "Carol": ["Germany"]}
        matches = [
            group_match("France", "Brazil", 1, 1),   # Alice → 1
            group_match("Morocco", "Brazil", 1, 1),  # Bob   → 1
            # Carol's Germany has no matches → 0 pts
        ]
        standings = calculate_standings(players, matches, set())
        carol = next(s for s in standings if s["player"] == "Carol")
        assert carol["rank"] == 3

    def test_player_with_multiple_countries(self):
        players = {"Alice": ["France", "Brazil"], "Bob": ["Morocco"]}
        matches = [
            group_match("France", "Morocco", 2, 0),   # France win
            group_match("Brazil", "Germany", 1, 0),   # Brazil win
        ]
        standings = calculate_standings(players, matches, set())
        alice = next(s for s in standings if s["player"] == "Alice")
        assert alice["points"] == 6  # 3 + 3

    def test_advance_bonus_included_in_standings(self):
        players = {"Alice": ["France"], "Bob": ["Morocco"]}
        matches = [group_match("France", "Morocco", 2, 0)]
        advanced = {"France", "Morocco"}
        standings = calculate_standings(players, matches, advanced)
        alice = next(s for s in standings if s["player"] == "Alice")
        bob = next(s for s in standings if s["player"] == "Bob")
        assert alice["points"] == 3 + 5
        assert bob["points"] == 0 + 5

    def test_empty_players_returns_empty(self):
        assert calculate_standings({}, [], set()) == []

    def test_alphabetical_tiebreak_within_group(self):
        # Alphabetical ordering within a tie group ensures stable output
        players = {"Zara": ["France"], "Alice": ["Morocco"]}
        matches = [group_match("France", "Morocco", 1, 1)]
        standings = calculate_standings(players, matches, set())
        assert standings[0]["player"] == "Alice"
        assert standings[1]["player"] == "Zara"


# ---------------------------------------------------------------------------
# split_prizes
# ---------------------------------------------------------------------------

class TestSplitPrizes:
    def test_clean_three_player_split(self):
        standings = [
            {"rank": 1, "player": "Alice", "points": 30},
            {"rank": 2, "player": "Bob", "points": 20},
            {"rank": 3, "player": "Carol", "points": 10},
        ]
        prizes = split_prizes(standings, 1000.0)
        assert prizes["Alice"] == pytest.approx(600.0)
        assert prizes["Bob"] == pytest.approx(300.0)
        assert prizes["Carol"] == pytest.approx(100.0)

    def test_tie_for_first_splits_top_two_prizes(self):
        standings = [
            {"rank": 1, "player": "Alice", "points": 30},
            {"rank": 1, "player": "Bob", "points": 30},
            {"rank": 3, "player": "Carol", "points": 10},
        ]
        prizes = split_prizes(standings, 1000.0)
        # 1st + 2nd = (60+30)% split two ways = 45% each
        assert prizes["Alice"] == pytest.approx(450.0)
        assert prizes["Bob"] == pytest.approx(450.0)
        assert prizes["Carol"] == pytest.approx(100.0)

    def test_tie_for_second_splits_second_and_third_prizes(self):
        standings = [
            {"rank": 1, "player": "Alice", "points": 30},
            {"rank": 2, "player": "Bob", "points": 20},
            {"rank": 2, "player": "Carol", "points": 20},
        ]
        prizes = split_prizes(standings, 1000.0)
        assert prizes["Alice"] == pytest.approx(600.0)
        # 2nd + 3rd = (30+10)% split two ways = 20% each
        assert prizes["Bob"] == pytest.approx(200.0)
        assert prizes["Carol"] == pytest.approx(200.0)

    def test_three_way_tie_splits_entire_pool_equally(self):
        standings = [
            {"rank": 1, "player": "Alice", "points": 30},
            {"rank": 1, "player": "Bob", "points": 30},
            {"rank": 1, "player": "Carol", "points": 30},
        ]
        prizes = split_prizes(standings, 1000.0)
        # All three share 60+25+15 = 100% of pool
        assert prizes["Alice"] == pytest.approx(1000.0 / 3)
        assert prizes["Bob"] == pytest.approx(1000.0 / 3)
        assert prizes["Carol"] == pytest.approx(1000.0 / 3)

    def test_fourth_place_receives_nothing(self):
        standings = [
            {"rank": 1, "player": "Alice", "points": 40},
            {"rank": 2, "player": "Bob", "points": 30},
            {"rank": 3, "player": "Carol", "points": 20},
            {"rank": 4, "player": "Dave", "points": 10},
        ]
        prizes = split_prizes(standings, 1000.0)
        assert "Dave" not in prizes
        assert prizes["Alice"] == pytest.approx(600.0)
        assert prizes["Bob"] == pytest.approx(300.0)
        assert prizes["Carol"] == pytest.approx(100.0)

    def test_prizes_sum_to_prize_pool(self):
        standings = [
            {"rank": 1, "player": "A", "points": 30},
            {"rank": 2, "player": "B", "points": 20},
            {"rank": 3, "player": "C", "points": 10},
            {"rank": 4, "player": "D", "points": 5},
        ]
        prizes = split_prizes(standings, 1000.0)
        assert sum(prizes.values()) == pytest.approx(1000.0)

    def test_tie_for_first_and_second_prizes_sum_to_pool(self):
        standings = [
            {"rank": 1, "player": "A", "points": 30},
            {"rank": 1, "player": "B", "points": 30},
            {"rank": 3, "player": "C", "points": 10},
        ]
        prizes = split_prizes(standings, 1000.0)
        assert sum(prizes.values()) == pytest.approx(1000.0)

    def test_only_one_player(self):
        standings = [{"rank": 1, "player": "Alice", "points": 50}]
        prizes = split_prizes(standings, 1000.0)
        # Only 1st place is filled; Alice takes 60% only
        assert prizes["Alice"] == pytest.approx(600.0)

    def test_two_players(self):
        standings = [
            {"rank": 1, "player": "Alice", "points": 50},
            {"rank": 2, "player": "Bob", "points": 20},
        ]
        prizes = split_prizes(standings, 1000.0)
        assert prizes["Alice"] == pytest.approx(600.0)
        assert prizes["Bob"] == pytest.approx(300.0)

    def test_prize_pool_scales_correctly(self):
        standings = [
            {"rank": 1, "player": "Alice", "points": 30},
            {"rank": 2, "player": "Bob", "points": 20},
            {"rank": 3, "player": "Carol", "points": 10},
        ]
        prizes = split_prizes(standings, 500.0)
        assert prizes["Alice"] == pytest.approx(300.0)
        assert prizes["Bob"] == pytest.approx(150.0)
        assert prizes["Carol"] == pytest.approx(50.0)
