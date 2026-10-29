"""Reparse locally available replays and backfill MySQL item snapshots.

Run only after scripts/upgrade_mysql_interactive.sql has been applied.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota_review.mysql import mysql_config
from dota_review.entities import (add_invoker_orb_selection_evidence, add_tormentor_shard_evidence,
                                  track_creeps, upgrade_progression_by_slot)

REPLAY_ROOT = ROOT / "dota_data" / "matches"
SQLITE = ROOT / "dota_data" / "review.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-key", help="只补录一个 16 位本地比赛键")
    args = parser.parse_args()
    source = sqlite3.connect(f"file:{SQLITE.resolve().as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    matches = source.execute("SELECT id,match_id,duration_s FROM matches ORDER BY id").fetchall()
    source.close()
    if args.match_key:
        matches = [row for row in matches if row["id"] == args.match_key]
        if not matches:
            parser.error("找不到该比赛键")

    db = pymysql.connect(**mysql_config())
    try:
        with db.cursor() as cursor:
            for match in matches:
                if not match["match_id"]:
                    continue
                cursor.execute("SELECT slot,time_s,tick,items_json FROM dota_inventory_snapshots WHERE match_key=%s "
                               "ORDER BY slot,time_s DESC,tick DESC", (match["id"],))
                latest_by_slot = {}
                for row in cursor.fetchall():
                    latest_by_slot.setdefault(int(row["slot"]), row)
                tail_rows = list(latest_by_slot.values())
                versions = [json.loads(row["items_json"] or "{}").get("__progression", {}).get("timeline_version")
                            for row in tail_rows]
                if tail_rows and all(version == 5 for version in versions):
                    print(f"{match['id']}: 已补录，跳过")
                    continue
                replay = next((path for path in REPLAY_ROOT.rglob("replay.dem")
                               if str(match["match_id"]) in path.parent.name), None)
                if replay is None:
                    print(f"{match['id']}: 本地录像缺失，跳过")
                    continue
                print(f"{match['id']}: 开始解析 {replay.parent.name}", flush=True)
                try:
                    import gem
                    parsed = gem.parse(str(replay))
                except Exception as exc:
                    print(f"{match['id']}: 解析失败 ({type(exc).__name__})")
                    continue
                finds: dict[int, list[dict]] = {}
                for event in parsed.neutral_item_finds:
                    try:
                        slot = int(event.player_id)
                    except (TypeError, ValueError):
                        continue
                    if not 0 <= slot <= 9:
                        continue
                    tick = getattr(event, "tick", None)
                    seconds = parsed.game_clock.game_seconds_at(tick) if parsed.game_clock and tick is not None else None
                    finds.setdefault(slot, []).append({
                        "tick": tick, "time_s": int(seconds) if seconds is not None else None,
                        "item": getattr(event, "item_key", None), "tier": getattr(event, "item_tier", None),
                    })
                snapshots: list[dict] = []
                _, inventory_error, _, _ = track_creeps(
                    str(replay), duration_s=match["duration_s"] or None, inventory_output=snapshots)
                if not snapshots:
                    print(f"{match['id']}: 没有可用装备快照" + (f"（{inventory_error}）" if inventory_error else ""))
                    continue
                stored_rows = [{**row, "items_json": json.dumps(row["items"], ensure_ascii=False)}
                               for row in snapshots]
                progression_by_slot = upgrade_progression_by_slot(parsed.players, [
                    {"slot": int(row["slot"]), "time_s": int(row["time_s"]), "tick": int(row["tick"]),
                     "items": row["items"], "progression": row.get("progression", {})}
                    for row in stored_rows
                ])
                parsed_teams = {int(player.player_id): int(player.team) for player in parsed.players}
                tormentor_events = []
                for tormentor in parsed.tormentors:
                    seconds = parsed.game_clock.game_seconds_at(tormentor.tick) if parsed.game_clock else None
                    team = tormentor.killer_team if tormentor.killer_team in (2, 3) else parsed_teams.get(tormentor.killer_player_id)
                    if seconds is not None and team in (2, 3):
                        tormentor_events.append({"time_s": int(seconds), "team": team})
                add_tormentor_shard_evidence(progression_by_slot, parsed.players, tormentor_events)
                add_invoker_orb_selection_evidence(progression_by_slot, parsed.players, [
                    {"slot": int(row["slot"]), "time_s": int(row["time_s"]), "tick": int(row["tick"]),
                     "items": row["items"], "progression": row.get("progression", {})}
                    for row in stored_rows
                ])
                last_row_by_slot = {}
                for row in stored_rows:
                    last_row_by_slot[int(row["slot"])] = row
                updates = []
                for row in stored_rows:
                    slot = int(row["slot"])
                    tick = int(row["tick"])
                    items = dict(row["items"])
                    progression = dict(row.get("progression") or {})
                    if row == last_row_by_slot.get(slot):
                        progression.update(progression_by_slot.get(slot, {}))
                        progression["neutral_finds"] = finds.get(slot, [])
                        progression["timeline_version"] = 5
                    items["__progression"] = progression
                    updates.append((json.dumps(items, ensure_ascii=False), match["id"], slot,
                                    int(row["time_s"]), tick))
                cursor.executemany("INSERT INTO dota_inventory_snapshots "
                    "(items_json,match_key,slot,time_s,tick) VALUES (%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE items_json=VALUES(items_json)", updates)
                db.commit()
                print(f"{match['id']}: 刷新 {len(updates):,} 条快照并补充卡尔元素选择记录")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
