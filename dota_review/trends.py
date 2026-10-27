"""Multi-match team trends from user-confirmed player identity mappings."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import companionship, economy_at
from .storage import Store
from .teams import allowed_match, canonical_team


def build_trends(store: Store) -> list[dict[str, Any]]:
    mappings = store.player_mappings()
    entries = []
    for summary in reversed(store.list_matches()):
        if not allowed_match(summary):
            continue
        match = store.load(summary["id"])
        links = companionship(match["players"], match["positions"], match["duration_s"])
        for team in (2, 3):
            team_name = match["radiant_name"] if team == 2 else match["dire_name"]
            if canonical_team(team_name) is None:
                continue
            roster = [p for p in match["players"] if p["team"] == team]
            mapped = {mappings[p["account_id"]] for p in roster if p.get("account_id") in mappings}
            carry = next((p for p in roster if p.get("role") == 1), None)
            curve = match["economy"].get(str(carry["slot"]), []) if carry else []
            row = {"match_key": match["id"], "match_id": match.get("match_id"),
                   "team": team, "team_name": team_name,
                   "mapped_players": sorted(mapped), "start_time": match["metadata"].get("start_time"),
                   "won": None if match.get("radiant_win") is None else int(bool(match["radiant_win"]) == (team == 2)),
                   "observer_wards": sum(w["team"] == team and w["kind"] == "observer" for w in match["wards"]),
                   "sentry_wards": sum(w["team"] == team and w["kind"] == "sentry" for w in match["wards"]),
                   "smokes": sum(e["team"] == team and e["type"] == "smoke" for e in match["events"]),
                   "first_bounty": sum(e["team"] == team and e["type"] == "rune" and
                                       e["subtype"] == "bounty" and -60 <= e["time_s"] <= 30
                                       for e in match["events"]),
                   "pull_candidates": sum(o["team"] == team and o["type"] == "pull" and
                                          o["evidence"] != "insufficient" for o in match["observations"]),
                   "carry_nw_10": (economy_at(curve, 600) or {}).get("net_worth"),
                   "carry_nw_20": (economy_at(curve, 1200) or {}).get("net_worth")}
            for roles in ("2+4", "2+5", "2+4+5"):
                relevant = [link for link in links if link["team"] == team and link["roles"] == roles]
                row[f"link_{roles}_count"] = len(relevant)
                row[f"link_{roles}_seconds"] = sum(link["duration_s"] for link in relevant)
            entries.append(row)
    # Only user-confirmed player mappings establish identity. Three overlapping
    # players connect rosters across matches, preserving substitutes.
    parent = list(range(len(entries)))
    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, left in enumerate(entries):
        for j in range(i):
            if len(set(left["mapped_players"]) & set(entries[j]["mapped_players"])) >= 3:
                parent[find(i)] = find(j)
    grouped: dict[int, list[int]] = defaultdict(list)
    for i in range(len(entries)):
        grouped[find(i)].append(i)
    for indices in grouped.values():
        names = [entries[i]["team_name"] for i in indices]
        most_common = max(set(names), key=lambda name: (names.count(name), name))
        most_common = canonical_team(most_common) or most_common
        identity = (f"{most_common} · 映射确认" if len(indices) > 1 else
                    f"{most_common} · {entries[indices[0]]['match_id'] or entries[indices[0]]['match_key']} 未匹配")
        for i in indices:
            entries[i]["team_identity"] = identity
            entries[i]["identity_matches"] = len(indices)
            entries[i]["mapped_players"] = ", ".join(entries[i]["mapped_players"])
    return entries


def write_trends_csv(rows: list[dict[str, Any]], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else []
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return target
