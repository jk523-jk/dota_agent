"""Resolve Captains Mode draft ownership from roster and observed 7.41f turn order."""

from __future__ import annotations

from typing import Any


# Relative to the team that makes the first pick (step 8). Checked against
# four public 7.41f OpenDota professional match drafts on 2026-10-26.
FIRST_PICK_SIDE = (1, 1, 0, 0, 1, 0, 0, 1, 0, 1, 1, 0,
                   0, 1, 1, 0, 0, 1, 1, 0, 1, 0, 1, 0)
ACTIONS = "BBBBBBBPPBBBPPPPPPBBBBPP"


def resolve_draft_teams(rows: list[dict[str, Any]], players: list[dict[str, Any]],
                        game_mode: int | None) -> list[dict[str, Any]]:
    rows.sort(key=lambda row: row.get("raw", {}).get("tick", 999999999))
    hero_team = {player["hero"]: player["team"] for player in players if player.get("hero")}
    for row in rows:
        if row.get("action") == "pick" and row.get("hero") in hero_team:
            row["team"] = hero_team[row["hero"]]
            row.setdefault("raw", {})["team_source"] = "postgame_roster"
        else:
            row.setdefault("raw", {})["team_source"] = "game_rules_unverified"
    action_string = "".join("P" if row.get("action") == "pick" else "B" for row in rows)
    if game_mode not in (None, 2) or len(rows) != 24 or action_string != ACTIONS:
        return rows
    first_picker = rows[7].get("team")
    if first_picker not in (2, 3) or rows[7]["raw"].get("team_source") != "postgame_roster":
        return rows
    other = 5 - first_picker
    expected = [first_picker if side else other for side in FIRST_PICK_SIDE]
    # The postgame roster must agree with every pick before inferring bans.
    if any(row["action"] == "pick" and row["team"] != expected[i] for i, row in enumerate(rows)):
        return rows
    for i, row in enumerate(rows):
        if row["action"] == "ban":
            row["team"] = expected[i]
            row["raw"]["team_source"] = "captains_mode_741f_pattern"
    return rows
