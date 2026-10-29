"""Read-only, evidence-first analyses used by the Dota replay agent."""
from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from .mysql import mysql_connection
from .teams import allowed_match, canonical_team


def _match(cursor, key: str) -> dict[str, Any]:
    cursor.execute(
        "SELECT id,match_id,radiant_name,dire_name,duration_s,patch,radiant_win,parse_status "
        "FROM dota_matches WHERE id=%s", (key,))
    row = cursor.fetchone()
    if not row or not allowed_match(row):
        raise LookupError("比赛不存在或不在当前资料库范围内")
    return row


def player_analysis(match_key: str, slot: int) -> dict[str, Any]:
    """Summarize a player's replay-backed economy and ward participation."""
    with mysql_connection() as db, db.cursor() as cursor:
        match = _match(cursor, match_key)
        cursor.execute(
            "SELECT slot,team,hero,player_name,account_id,role FROM dota_players "
            "WHERE match_key=%s AND slot=%s", (match_key, slot))
        player = cursor.fetchone()
        if not player:
            raise LookupError("找不到该比赛中的选手")
        cursor.execute(
            "SELECT slot,time_s,net_worth,gold,xp,last_hits FROM dota_economy "
            "WHERE match_key=%s ORDER BY time_s,slot", (match_key,))
        economy = cursor.fetchall()
        cursor.execute(
            "SELECT COUNT(*) placements, SUM(kind='observer') observers, SUM(kind='sentry') sentries "
            "FROM dota_wards WHERE match_key=%s AND placer_slot=%s", (match_key, slot))
        ward_stats = cursor.fetchone() or {}

    by_time: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in economy:
        by_time[int(row["time_s"])][int(row["slot"])] = row
    checkpoints = []
    for target in (600, 900, 1200, 1800):
        available = [second for second in by_time if second <= target]
        if not available:
            continue
        sample_time = max(available)
        rows = by_time[sample_time]
        selected = rows.get(slot)
        if not selected:
            continue
        team_rows = sorted((int(row["slot"]), int(row["net_worth"] or 0))
                           for row in rows.values() if int(row["slot"]) // 5 == slot // 5)
        rank = next((index for index, (candidate, _) in enumerate(
            sorted(team_rows, key=lambda pair: pair[1], reverse=True), 1) if candidate == slot), None)
        checkpoints.append({
            "target_time_s": target,
            "sample_time_s": sample_time,
            "net_worth": int(selected["net_worth"] or 0),
            "gold": int(selected["gold"] or 0),
            "xp": int(selected["xp"] or 0),
            "last_hits": int(selected["last_hits"] or 0),
            "team_net_worth_rank": rank,
            "team_size": len(team_rows),
        })

    latest = next((row for row in reversed(economy) if int(row["slot"]) == slot), None)
    citations = [{"match_id": match_key, "time_s": row["sample_time_s"],
                  "label": f"{row['target_time_s'] // 60} 分钟经济与补刀快照"}
                 for row in checkpoints]
    if ward_stats and int(ward_stats.get("placements") or 0):
        citations.append({"match_id": match_key, "time_s": None, "label": "录像记录的选手眼位放置"})
    return {
        "kind": "player",
        "match": {"id": match_key, "match_id": match.get("match_id"),
                  "radiant_name": match.get("radiant_name"), "dire_name": match.get("dire_name")},
        "player": {**player, "role": int(player["role"]) if player.get("role") is not None else None},
        "checkpoints": checkpoints,
        "latest_economy_time_s": int(latest["time_s"]) if latest else None,
        "ward_placements": int(ward_stats.get("placements") or 0),
        "observer_wards": int(ward_stats.get("observers") or 0),
        "sentry_wards": int(ward_stats.get("sentries") or 0),
        "citations": citations,
        "limitations": ["当前标准化数据没有可靠的击杀者/被击杀者 slot 对应关系，故不推断个人 K/D/A。"],
    }


def ward_hotspots(match_key: str) -> dict[str, Any]:
    """Group replay ward placements into compact spatial hotspots."""
    with mysql_connection() as db, db.cursor() as cursor:
        match = _match(cursor, match_key)
        cursor.execute(
            "SELECT team,kind,time_s,end_s,x,y,placer_slot FROM dota_wards "
            "WHERE match_key=%s AND x IS NOT NULL AND y IS NOT NULL ORDER BY time_s", (match_key,))
        wards = cursor.fetchall()
    groups: dict[tuple[int | None, str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for ward in wards:
        x, y = float(ward["x"]), float(ward["y"])
        groups[(ward["team"], str(ward["kind"] or "unknown"), round(x / 900), round(y / 900))].append(ward)
    hotspots = []
    for (team, kind, _, _), rows in groups.items():
        durations = [int(row["end_s"]) - int(row["time_s"]) for row in rows
                     if row.get("end_s") is not None and int(row["end_s"]) >= int(row["time_s"])]
        hotspots.append({
            "team": int(team) if team is not None else None,
            "kind": kind,
            "x": round(mean(float(row["x"]) for row in rows)),
            "y": round(mean(float(row["y"]) for row in rows)),
            "placements": len(rows),
            "average_duration_s": round(mean(durations)) if durations else None,
            "first_time_s": min(int(row["time_s"]) for row in rows),
            "last_time_s": max(int(row["time_s"]) for row in rows),
        })
    hotspots.sort(key=lambda row: row["placements"], reverse=True)
    totals: dict[str, dict[str, int]] = {"radiant": {}, "dire": {}}
    for ward in wards:
        side = "radiant" if ward["team"] == 2 else "dire" if ward["team"] == 3 else "unknown"
        totals.setdefault(side, {})[str(ward["kind"] or "unknown")] = (
            totals.setdefault(side, {}).get(str(ward["kind"] or "unknown"), 0) + 1)
    top = hotspots[:12]
    return {
        "kind": "ward_hotspots",
        "match": {"id": match_key, "match_id": match.get("match_id"),
                  "radiant_name": match.get("radiant_name"), "dire_name": match.get("dire_name")},
        "placement_count": len(wards), "by_team_and_kind": totals,
        "hotspots": top,
        "citations": [{"match_id": match_key, "time_s": row["first_time_s"],
                       "label": f"{'天辉' if row['team'] == 2 else '夜魇' if row['team'] == 3 else '未知队伍'}"
                                f"{row['kind']} 热区（{row['placements']} 次）"}
                      for row in top],
        "limitations": ["热区按约 900 世界单位的邻近网格聚合；不代表视野覆盖或队伍实际看见的区域。"],
    }


def team_style(match_key: str, side: str) -> dict[str, Any]:
    """Describe observable patterns for a team across imported matches."""
    if side not in ("radiant", "dire"):
        raise ValueError("side must be radiant or dire")
    with mysql_connection() as db, db.cursor() as cursor:
        selected = _match(cursor, match_key)
        team_name = selected["radiant_name"] if side == "radiant" else selected["dire_name"]
        team_key = canonical_team(team_name)
        if not team_key:
            raise LookupError("当前队伍没有可用于跨场统计的标准名称")
        cursor.execute(
            "SELECT id,match_id,radiant_name,dire_name,duration_s,patch,radiant_win "
            "FROM dota_matches ORDER BY imported_at DESC LIMIT 500")
        candidates = cursor.fetchall()
        matches = [row for row in candidates if allowed_match(row) and (
            canonical_team(row.get("radiant_name")) == team_key or
            canonical_team(row.get("dire_name")) == team_key)]
        matches = matches[:100]
        keys = [row["id"] for row in matches]
        event_rows: list[dict[str, Any]] = []
        ward_rows: list[dict[str, Any]] = []
        if keys:
            placeholders = ",".join(["%s"] * len(keys))
            cursor.execute(
                f"SELECT match_key,time_s,type,subtype,team,description FROM dota_events "
                f"WHERE match_key IN ({placeholders}) ORDER BY match_key,time_s", tuple(keys))
            event_rows = cursor.fetchall()
            cursor.execute(
                f"SELECT match_key,team,kind,time_s FROM dota_wards WHERE match_key IN ({placeholders})",
                tuple(keys))
            ward_rows = cursor.fetchall()

    events_by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    wards_by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in event_rows:
        events_by_match[row["match_key"]].append(row)
    for row in ward_rows:
        wards_by_match[row["match_key"]].append(row)
    per_match = []
    for row in matches:
        team_side = 2 if canonical_team(row.get("radiant_name")) == team_key else 3
        events = events_by_match[row["id"]]
        early_kills = [e for e in events if e["type"] == "kill" and e.get("team") == team_side and int(e["time_s"]) <= 900]
        smokes = [e for e in events if e["type"] == "smoke" and e.get("team") == team_side]
        towers = [e for e in events if e["type"] == "tower" and e.get("team") in (2, 3) and e.get("team") != team_side]
        roshans = [e for e in events if e["type"] == "objective" and e.get("team") == team_side and
                   str(e.get("subtype") or "").upper() in {"CHAT_MESSAGE_ROSHAN_KILL", "ROSHAN_KILL"}]
        placed_wards = [w for w in wards_by_match[row["id"]] if w.get("team") == team_side]
        win_value = row.get("radiant_win")
        won = bool(win_value) if team_side == 2 else not bool(win_value) if win_value is not None else None
        per_match.append({
            "id": row["id"], "match_id": row.get("match_id"), "team": team_name,
            "duration_s": int(row["duration_s"] or 0), "won": won,
            "early_kills": len(early_kills), "smokes": len(smokes),
            "wards": len(placed_wards), "towers": len(towers), "roshans": len(roshans),
            "first_smoke_s": min((int(e["time_s"]) for e in smokes), default=None),
        })
    n = len(per_match)
    metrics = {
        "matches": n,
        "win_rate": round(sum(bool(row["won"]) for row in per_match if row["won"] is not None) /
                           max(1, sum(row["won"] is not None for row in per_match)), 3)
                    if any(row["won"] is not None for row in per_match) else None,
        "average_duration_s": round(mean(row["duration_s"] for row in per_match)) if n else None,
        "average_early_kills_0_15m": round(mean(row["early_kills"] for row in per_match), 2) if n else None,
        "average_smokes": round(mean(row["smokes"] for row in per_match), 2) if n else None,
        "average_wards": round(mean(row["wards"] for row in per_match), 2) if n else None,
        "average_towers_taken": round(mean(row["towers"] for row in per_match), 2) if n else None,
        "average_roshans": round(mean(row["roshans"] for row in per_match), 2) if n else None,
        "sample_quality": "low" if n < 5 else "limited" if n < 15 else "usable",
    }
    citations = [{"match_id": row["id"], "time_s": row["first_smoke_s"],
                  "label": f"{row['team']} 对局样本 · {'胜' if row['won'] else '负' if row['won'] is not None else '胜负未知'}"}
                 for row in per_match[:20]]
    return {
        "kind": "team_style", "team": team_name, "canonical_team": team_key,
        "metrics": metrics, "match_samples": per_match[:20], "citations": citations,
        "limitations": ([f"样本只有 {n} 场，战队风格结论仅作描述，不应视为稳定战术画像。"] if n < 15 else []),
    }
