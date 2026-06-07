"""
Tests for teams.py — flag emoji, match parsing, and advancement logic.
Network calls are not made; all data is constructed inline.
"""

import pytest

from teams import (
    ROUND_MAP,
    THIRD_PLACE_QUALIFIERS,
    build_team_flags,
    build_team_map,
    compute_advanced_teams,
    flag_emoji,
    parse_match,
)


# ---------------------------------------------------------------------------
# flag_emoji
# ---------------------------------------------------------------------------

class TestFlagEmoji:
    def test_standard_two_letter_code(self):
        assert flag_emoji("FR") == "🇫🇷"

    def test_lowercase_normalised(self):
        assert flag_emoji("fr") == "🇫🇷"
        assert flag_emoji("au") == "🇦🇺"

    def test_us_flag(self):
        assert flag_emoji("US") == "🇺🇸"

    def test_three_letter_code_returns_empty(self):
        assert flag_emoji("SCO") == ""

    def test_empty_string_returns_empty(self):
        assert flag_emoji("") == ""

    def test_single_letter_returns_empty(self):
        assert flag_emoji("A") == ""


# ---------------------------------------------------------------------------
# build_team_map / build_team_flags
# ---------------------------------------------------------------------------

SAMPLE_TEAMS = [
    {"id": "1", "name_en": "France", "iso2": "FR", "name_fa": "فرانسه"},
    {"id": "2", "name_en": "Morocco", "iso2": "MA", "name_fa": "مراکش"},
    {"id": "3", "name_en": "Scotland", "iso2": "SCO", "name_fa": "اسکاتلند"},
]


class TestBuildTeamMap:
    def test_keys_are_string_ids(self):
        tm = build_team_map(SAMPLE_TEAMS)
        assert "1" in tm
        assert "2" in tm

    def test_name_and_iso2_populated(self):
        tm = build_team_map(SAMPLE_TEAMS)
        assert tm["1"]["name_en"] == "France"
        assert tm["1"]["iso2"] == "FR"
        assert tm["1"]["flag_emoji"] == "🇫🇷"

    def test_non_standard_iso2_flag_is_empty(self):
        tm = build_team_map(SAMPLE_TEAMS)
        assert tm["3"]["flag_emoji"] == ""

    def test_missing_iso2_handled_gracefully(self):
        teams = [{"id": "9", "name_en": "Kosovo"}]  # no iso2 key
        tm = build_team_map(teams)
        assert tm["9"]["iso2"] == ""
        assert tm["9"]["flag_emoji"] == ""


class TestBuildTeamFlags:
    def test_maps_name_to_flag(self):
        flags = build_team_flags(SAMPLE_TEAMS)
        assert flags["France"] == "🇫🇷"
        assert flags["Morocco"] == "🇲🇦"

    def test_non_standard_iso_gives_empty(self):
        flags = build_team_flags(SAMPLE_TEAMS)
        assert flags["Scotland"] == ""


# ---------------------------------------------------------------------------
# parse_match — group stage
# ---------------------------------------------------------------------------

TEAM_MAP = build_team_map(SAMPLE_TEAMS)

RAW_UNSTARTED = {
    "id": "1",
    "type": "group",
    "home_team_id": "1",
    "away_team_id": "2",
    "home_score": "0",
    "away_score": "0",
    "finished": "FALSE",
    "time_elapsed": "notstarted",
    "local_date": "06/11/2026 13:00",
    "group": "A",
    "matchday": "1",
    "home_team_name_en": "France",
    "away_team_name_en": "Morocco",
}

RAW_FINISHED_WIN = {**RAW_UNSTARTED, "home_score": "2", "away_score": "0", "finished": "TRUE"}
RAW_FINISHED_DRAW = {**RAW_UNSTARTED, "home_score": "1", "away_score": "1", "finished": "TRUE"}


class TestParseMatchGroup:
    def test_unstarted_match(self):
        m = parse_match(RAW_UNSTARTED, TEAM_MAP)
        assert m.id == "1"
        assert m.round == "group"
        assert m.home_team == "France"
        assert m.away_team == "Morocco"
        assert m.completed is False
        assert m.home_score is None
        assert m.away_score is None

    def test_finished_win(self):
        m = parse_match(RAW_FINISHED_WIN, TEAM_MAP)
        assert m.completed is True
        assert m.home_score == 2
        assert m.away_score == 0
        assert m.winner == "France"

    def test_finished_draw(self):
        m = parse_match(RAW_FINISHED_DRAW, TEAM_MAP)
        assert m.is_draw is True
        assert m.winner is None

    def test_date_parsed_to_iso_utc(self):
        m = parse_match(RAW_UNSTARTED, TEAM_MAP)
        assert m.date == "2026-06-11T13:00:00Z"

    def test_round_map_applied(self):
        for api_type, expected in [
            ("group", "group"),
            ("r32", "round_of_32"),
            ("r16", "round_of_16"),
            ("qf", "quarter_final"),
            ("sf", "semi_final"),
            ("final", "final"),
            ("third", "third_place"),
        ]:
            raw = {**RAW_UNSTARTED, "type": api_type}
            m = parse_match(raw, TEAM_MAP)
            assert m.round == expected, f"Failed for type={api_type!r}"


# ---------------------------------------------------------------------------
# parse_match — knockout (pre-determined and penalty override)
# ---------------------------------------------------------------------------

RAW_KNOCKOUT_UNDETERMINED = {
    "id": "73",
    "type": "r32",
    "home_team_id": "0",
    "away_team_id": "0",
    "home_score": "0",
    "away_score": "0",
    "finished": "FALSE",
    "time_elapsed": "notstarted",
    "local_date": "06/28/2026 12:00",
    "group": "R32",
    "matchday": "4",
    "home_team_label": "Runner-up Group A",
    "away_team_label": "Runner-up Group B",
}

RAW_KNOCKOUT_DETERMINED = {
    "id": "73",
    "type": "r32",
    "home_team_id": "1",
    "away_team_id": "2",
    "home_score": "1",
    "away_score": "1",
    "finished": "TRUE",
    "time_elapsed": "FT",
    "local_date": "06/28/2026 12:00",
}


class TestParseMatchKnockout:
    def test_undetermined_teams_are_empty_strings(self):
        m = parse_match(RAW_KNOCKOUT_UNDETERMINED, TEAM_MAP)
        assert m.home_team == ""
        assert m.away_team == ""
        assert m.completed is False

    def test_determined_teams_from_team_map(self):
        # No embedded name fields — must come from team_map
        m = parse_match(RAW_KNOCKOUT_DETERMINED, TEAM_MAP)
        assert m.home_team == "France"
        assert m.away_team == "Morocco"

    def test_penalty_winner_override_applied(self):
        overrides = {"73": "Morocco"}
        m = parse_match(RAW_KNOCKOUT_DETERMINED, TEAM_MAP, winner_overrides=overrides)
        assert m.winner_override == "Morocco"
        assert m.winner == "Morocco"
        assert m.is_draw is False

    def test_no_override_draw_stays_draw(self):
        m = parse_match(RAW_KNOCKOUT_DETERMINED, TEAM_MAP)
        assert m.winner is None
        assert m.is_draw is True

    def test_override_for_different_game_not_applied(self):
        overrides = {"99": "France"}
        m = parse_match(RAW_KNOCKOUT_DETERMINED, TEAM_MAP, winner_overrides=overrides)
        assert m.winner_override is None


# ---------------------------------------------------------------------------
# compute_advanced_teams
# ---------------------------------------------------------------------------

def _make_team_entry(team_id: str, pts: int, gd: int, gf: int, mp: int = 3) -> dict:
    return {
        "team_id": team_id,
        "mp": str(mp),
        "pts": str(pts),
        "gd": str(gd),
        "gf": str(gf),
        "ga": "0",
        "w": "0",
        "d": "0",
        "l": "0",
    }


def _make_group(name: str, entries: list[dict]) -> dict:
    return {"name": name, "_id": f"g{name}", "teams": entries}


def _make_full_team_map(n: int) -> dict[str, dict]:
    """Create a team_map with n teams, IDs '1' through str(n)."""
    return {
        str(i): {"name_en": f"Team{i}", "iso2": "AA", "flag_emoji": ""}
        for i in range(1, n + 1)
    }


class TestComputeAdvancedTeams:
    def test_top_two_from_completed_group_advance(self):
        groups = [
            _make_group("A", [
                _make_team_entry("1", 9, 6, 7),   # 1st
                _make_team_entry("2", 6, 2, 4),   # 2nd
                _make_team_entry("3", 3, -2, 2),  # 3rd
                _make_team_entry("4", 0, -6, 1),  # 4th
            ])
        ]
        team_map = _make_full_team_map(4)
        advanced = compute_advanced_teams(groups, team_map)
        assert "Team1" in advanced
        assert "Team2" in advanced
        assert "Team3" not in advanced
        assert "Team4" not in advanced

    def test_incomplete_group_not_included(self):
        groups = [
            _make_group("A", [
                _make_team_entry("1", 3, 1, 1, mp=1),  # only 1 game played
                _make_team_entry("2", 0, -1, 0, mp=1),
                _make_team_entry("3", 0, 0, 0, mp=0),
                _make_team_entry("4", 0, 0, 0, mp=0),
            ])
        ]
        team_map = _make_full_team_map(4)
        advanced = compute_advanced_teams(groups, team_map)
        assert advanced == []

    def test_third_place_not_added_until_all_groups_complete(self):
        # Only 1 of 12 groups complete — third-place teams should not be added yet
        groups = [
            _make_group("A", [
                _make_team_entry("1", 9, 6, 7),
                _make_team_entry("2", 6, 2, 4),
                _make_team_entry("3", 3, -2, 2),
                _make_team_entry("4", 0, -6, 1),
            ])
        ]
        team_map = _make_full_team_map(4)
        advanced = compute_advanced_teams(groups, team_map)
        assert "Team3" not in advanced
        assert len(advanced) == 2  # only top 2 from group A

    def test_best_eight_third_place_when_all_groups_complete(self):
        """With 12 completed groups, the 8 best third-placed teams advance."""
        # Build 12 groups, 4 teams each (IDs 1–48).
        # 2nd place always has 7 pts; 3rd-place pts vary 1–12 (one per group).
        # This keeps 3rd-place clearly below 2nd in every group so the rank
        # assignments are unambiguous.
        groups = []
        team_map: dict[str, dict] = {}
        group_letters = "ABCDEFGHIJKL"

        for g_idx, letter in enumerate(group_letters):
            third_pts = g_idx + 1           # 1 (group A) … 12 (group L)
            pts_by_pos = [9, 7, third_pts, 0]
            members = []
            for pos in range(4):
                tid = str(g_idx * 4 + pos + 1)
                entry = _make_team_entry(tid, pts_by_pos[pos], 0, pos + 1)
                members.append(entry)
                team_map[tid] = {"name_en": f"T{tid}", "iso2": "AA", "flag_emoji": ""}
            groups.append(_make_group(letter, members))

        advanced = compute_advanced_teams(groups, team_map)

        # 12 groups × top 2 = 24, plus 8 best third-placed = 32
        assert len(advanced) == 24 + THIRD_PLACE_QUALIFIERS

        # Best 8 third-placed: groups E-L (g_idx 4–11, pts 5–12)
        for g_idx in range(4, 12):
            tid = str(g_idx * 4 + 3)   # pos==2 team is third-placed
            assert f"T{tid}" in advanced

        # Worst 4 third-placed: groups A-D (g_idx 0–3, pts 1–4) — do NOT advance
        for g_idx in range(0, 4):
            tid = str(g_idx * 4 + 3)
            assert f"T{tid}" not in advanced

    def test_tiebreaker_by_goal_difference(self):
        """When pts tie, goal difference breaks the tie."""
        groups = [
            _make_group("A", [
                _make_team_entry("1", 6, 5, 7),   # 1st (higher gd)
                _make_team_entry("2", 6, 1, 4),   # 2nd (lower gd)
                _make_team_entry("3", 3, -2, 2),
                _make_team_entry("4", 0, -4, 1),
            ])
        ]
        team_map = _make_full_team_map(4)
        advanced = compute_advanced_teams(groups, team_map)
        assert "Team1" in advanced
        assert "Team2" in advanced

    def test_empty_groups_returns_empty(self):
        assert compute_advanced_teams([], {}) == []
