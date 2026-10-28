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
from dota_review.entities import track_creeps

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
                replay = next((path for path in REPLAY_ROOT.rglob("replay.dem")
                               if str(match["match_id"]) in path.parent.name), None)
                if replay is None:
                    print(f"{match['id']}: 本地录像缺失，跳过")
                    continue
                snapshots: list[dict] = []
                try:
                    track_creeps(str(replay), duration_s=match["duration_s"] or None,
                                 inventory_output=snapshots)
                except Exception as exc:
                    print(f"{match['id']}: 解析失败 ({type(exc).__name__})")
                    continue
                if not snapshots:
                    print(f"{match['id']}: 没有可用装备快照")
                    continue
                cursor.execute("DELETE FROM dota_inventory_snapshots WHERE match_key=%s", (match["id"],))
                cursor.executemany("INSERT INTO dota_inventory_snapshots "
                    "(match_key,slot,time_s,tick,items_json) VALUES (%s,%s,%s,%s,%s)", [
                        (match["id"], row["slot"], row["time_s"], row["tick"],
                         json.dumps(row["items"], ensure_ascii=False)) for row in snapshots])
                db.commit()
                print(f"{match['id']}: 写入 {len(snapshots):,} 条装备快照")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
