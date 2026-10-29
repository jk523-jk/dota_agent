"""SQLite persistence. Every displayed timestamp is the normalized game clock."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .draft import resolve_draft_teams


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS matches (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
 match_id TEXT, patch TEXT, patch_verified INTEGER NOT NULL DEFAULT 0,
 duration_s INTEGER NOT NULL DEFAULT 0, radiant_name TEXT, dire_name TEXT,
 radiant_win INTEGER, parse_status TEXT NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS players (
 match_key TEXT NOT NULL, slot INTEGER NOT NULL, team INTEGER NOT NULL,
 hero TEXT, account_id TEXT, player_name TEXT, role INTEGER, role_source TEXT,
 PRIMARY KEY(match_key,slot), FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS positions (
 match_key TEXT NOT NULL, slot INTEGER NOT NULL, time_s INTEGER NOT NULL,
 tick INTEGER, x REAL NOT NULL, y REAL NOT NULL,
 PRIMARY KEY(match_key,slot,time_s), FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_positions_time ON positions(match_key,time_s);
CREATE TABLE IF NOT EXISTS creeps (
 match_key TEXT NOT NULL, entity_id TEXT NOT NULL, kind TEXT NOT NULL, team INTEGER,
 time_s INTEGER NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
 PRIMARY KEY(match_key,entity_id,time_s), FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_creeps_time ON creeps(match_key,time_s);
CREATE TABLE IF NOT EXISTS map_features (
 match_key TEXT NOT NULL, feature_id TEXT NOT NULL, kind TEXT NOT NULL, team INTEGER,
 name TEXT, x REAL NOT NULL, y REAL NOT NULL, evidence TEXT NOT NULL,
 PRIMARY KEY(match_key,feature_id), FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS economy (
 match_key TEXT NOT NULL, slot INTEGER NOT NULL, time_s INTEGER NOT NULL,
 net_worth REAL, gold REAL, xp REAL, last_hits INTEGER,
 PRIMARY KEY(match_key,slot,time_s), FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS wards (
 match_key TEXT NOT NULL, ordinal INTEGER NOT NULL, team INTEGER, kind TEXT,
 time_s INTEGER NOT NULL, end_s INTEGER, x REAL, y REAL, placer_slot INTEGER,
 end_reason TEXT, evidence TEXT NOT NULL, raw_json TEXT NOT NULL,
 PRIMARY KEY(match_key,ordinal), FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS events (
 match_key TEXT NOT NULL, ordinal INTEGER NOT NULL, time_s INTEGER NOT NULL,
 type TEXT NOT NULL, subtype TEXT, team INTEGER, slot INTEGER, x REAL, y REAL,
 description TEXT, evidence TEXT NOT NULL, raw_json TEXT NOT NULL,
 PRIMARY KEY(match_key,ordinal), FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(match_key,time_s);
CREATE TABLE IF NOT EXISTS inventory_snapshots (
 match_key TEXT NOT NULL, slot INTEGER NOT NULL, time_s INTEGER NOT NULL, tick INTEGER NOT NULL,
 items_json TEXT NOT NULL, PRIMARY KEY(match_key,slot,tick),
 FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_inventory_time ON inventory_snapshots(match_key,slot,time_s,tick);
CREATE TABLE IF NOT EXISTS draft (
 match_key TEXT NOT NULL, ordinal INTEGER NOT NULL, team INTEGER, action TEXT,
 hero TEXT, game_time_s INTEGER, raw_json TEXT NOT NULL,
 PRIMARY KEY(match_key,ordinal), FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS observations (
 match_key TEXT NOT NULL, ordinal INTEGER NOT NULL, type TEXT NOT NULL,
 time_s INTEGER, end_s INTEGER, team INTEGER, evidence TEXT NOT NULL,
 details_json TEXT NOT NULL, PRIMARY KEY(match_key,ordinal),
 FOREIGN KEY(match_key) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS player_map (
 account_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS thresholds (name TEXT PRIMARY KEY, value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS api_cache (
 key TEXT PRIMARY KEY, fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bp_models (
 id INTEGER PRIMARY KEY AUTOINCREMENT, patch TEXT NOT NULL, trained_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 cutoff INTEGER NOT NULL, train_start INTEGER, train_end INTEGER, validation_end INTEGER,
 train_n INTEGER NOT NULL, valid_n INTEGER NOT NULL,
 brier REAL NOT NULL, baseline_brier REAL NOT NULL, ece REAL NOT NULL,
 accepted INTEGER NOT NULL, model_json TEXT NOT NULL, sample_ids_json TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(bp_models)")}
        for column in ("train_start", "train_end", "validation_end"):
            if column not in columns:
                self.db.execute(f"ALTER TABLE bp_models ADD COLUMN {column} INTEGER")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save(self, data: dict[str, Any]) -> bool:
        """Atomically insert; return False when the exact replay was imported already."""
        if self.db.execute("SELECT 1 FROM matches WHERE sha256=?", (data["sha256"],)).fetchone():
            return False
        key = data["id"]
        with self.db:
            self.db.execute("""INSERT INTO matches
                (id,sha256,filename,match_id,patch,patch_verified,duration_s,radiant_name,dire_name,
                 radiant_win,parse_status,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, data["sha256"], data["filename"], data.get("match_id"), data.get("patch"),
                 int(data.get("patch_verified", False)), data.get("duration_s", 0),
                 data.get("radiant_name"), data.get("dire_name"), data.get("radiant_win"),
                 data.get("parse_status", "complete"), json.dumps(data.get("metadata", {}), ensure_ascii=False)))
            self.db.executemany("""INSERT INTO players
                (match_key,slot,team,hero,account_id,player_name,role,role_source)
                VALUES (?,?,?,?,?,?,?,?)""", [
                (key, p["slot"], p["team"], p.get("hero"), p.get("account_id"),
                 p.get("name"), p.get("role"), p.get("role_source")) for p in data.get("players", [])])
            self.db.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?)", [
                (key, int(slot), p["time_s"], p.get("tick"), p["x"], p["y"])
                for slot, rows in data.get("positions", {}).items() for p in rows])
            self.db.executemany("INSERT OR REPLACE INTO creeps VALUES (?,?,?,?,?,?,?)", [
                (key, c["entity_id"], c["kind"], c.get("team"), c["time_s"], c["x"], c["y"])
                for c in data.get("creeps", [])])
            self.db.executemany("INSERT OR REPLACE INTO map_features VALUES (?,?,?,?,?,?,?,?)", [
                (key, f["id"], f["kind"], f.get("team"), f.get("name"), f["x"], f["y"], f.get("evidence", "replay_entity"))
                for f in data.get("map_features", [])])
            self.db.executemany("INSERT INTO economy VALUES (?,?,?,?,?,?,?)", [
                (key, int(slot), row["time_s"], row.get("net_worth"), row.get("gold"),
                 row.get("xp"), row.get("last_hits"))
                for slot, rows in data.get("economy", {}).items() for row in rows])
            self.db.executemany("INSERT INTO inventory_snapshots VALUES (?,?,?,?,?)", [
                (key, row["slot"], row["time_s"], row["tick"],
                 json.dumps({**row.get("items", {}),
                             **({"__progression": row["progression"]} if row.get("progression") else {})},
                            ensure_ascii=False))
                for row in data.get("inventory_snapshots", [])])
            self.db.executemany("INSERT INTO wards VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
                (key, i, w.get("team"), w.get("kind"), w["time_s"], w.get("end_s"),
                 w.get("x"), w.get("y"), w.get("placer_slot"), w.get("end_reason"),
                 w.get("evidence", "replay"), json.dumps(w.get("raw", {}), ensure_ascii=False, default=str))
                for i, w in enumerate(data.get("wards", []))])
            self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
                (key, i, e["time_s"], e["type"], e.get("subtype"), e.get("team"),
                 e.get("slot"), e.get("x"), e.get("y"), e.get("description"),
                 e.get("evidence", "replay"), json.dumps(e.get("raw", {}), ensure_ascii=False, default=str))
                for i, e in enumerate(data.get("events", []))])
            self.db.executemany("INSERT INTO draft VALUES (?,?,?,?,?,?,?)", [
                (key, i, d.get("team"), d.get("action"), d.get("hero"),
                 d.get("game_time_s"), json.dumps(d.get("raw", {}), ensure_ascii=False, default=str))
                for i, d in enumerate(data.get("draft", []))])
            self.db.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)", [
                (key, i, o["type"], o.get("time_s"), o.get("end_s"), o.get("team"),
                 o.get("evidence", "inferred"), json.dumps(o.get("details", {}), ensure_ascii=False, default=str))
                for i, o in enumerate(data.get("observations", []))])
        return True

    def refresh_map_data(self, data: dict[str, Any]) -> bool:
        row = self.db.execute("SELECT sha256,metadata_json FROM matches WHERE id=?", (data["id"],)).fetchone()
        if row is None or row["sha256"] != data["sha256"]:
            return False
        metadata = json.loads(row["metadata_json"])
        for key in ("map_feature_coverage", "tower_identity_coverage", "map_objective_coverage",
                    "creep_coverage", "ability_event_coverage", "creep_parse_error", "observation_parse_error"):
            if key in data.get("metadata", {}):
                metadata[key] = data["metadata"][key]
        features = data.get("map_features", [])
        events = [event for event in data.get("events", []) if event.get("type") in ("tower", "glyph", "scan")]
        with self.db:
            self.db.execute("DELETE FROM map_features WHERE match_key=?", (data["id"],))
            self.db.executemany("INSERT INTO map_features VALUES (?,?,?,?,?,?,?,?)", [
                (data["id"], f["id"], f["kind"], f.get("team"), f.get("name"), f["x"], f["y"], f.get("evidence", "replay_entity"))
                for f in features])
            self.db.execute("DELETE FROM creeps WHERE match_key=?", (data["id"],))
            self.db.executemany("INSERT INTO creeps VALUES (?,?,?,?,?,?,?)", [
                (data["id"], c["entity_id"], c["kind"], c.get("team"), c["time_s"], c["x"], c["y"])
                for c in data.get("creeps", [])])
            self.db.execute("DELETE FROM observations WHERE match_key=?", (data["id"],))
            self.db.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)", [
                (data["id"], i, o["type"], o.get("time_s"), o.get("end_s"), o.get("team"),
                 o.get("evidence", "inferred"), json.dumps(o.get("details", {}), ensure_ascii=False, default=str))
                for i, o in enumerate(data.get("observations", []))])
            self.db.execute("DELETE FROM events WHERE match_key=? AND type IN ('tower','glyph','scan') "
                            "AND evidence IN ('combat_log_tower_kill','replay_chat_event')", (data["id"],))
            ordinal = self.db.execute("SELECT COALESCE(MAX(ordinal),-1)+1 FROM events WHERE match_key=?", (data["id"],)).fetchone()[0]
            self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
                (data["id"], ordinal + i, event["time_s"], event["type"], event.get("subtype"),
                 event.get("team"), event.get("slot"), event.get("x"), event.get("y"),
                 event.get("description"), event.get("evidence", "replay"),
                 json.dumps(event.get("raw", {}), ensure_ascii=False, default=str))
                for i, event in enumerate(events)])
            self.db.execute("UPDATE matches SET metadata_json=? WHERE id=?",
                            (json.dumps(metadata, ensure_ascii=False), data["id"]))
        return True

    def list_matches(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM matches ORDER BY imported_at DESC,id DESC")]

    def load(self, key: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM matches WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        result["players"] = []
        for row in self.db.execute("SELECT * FROM players WHERE match_key=? ORDER BY slot", (key,)):
            player = dict(row)
            player["name"] = player.pop("player_name")
            result["players"].append(player)
        result["positions"] = {}
        for r in self.db.execute("SELECT slot,time_s,tick,x,y FROM positions WHERE match_key=? ORDER BY slot,time_s", (key,)):
            point = dict(r)
            result["positions"].setdefault(str(point.pop("slot")), []).append(point)
        result["inventory_snapshots"] = []
        for r in self.db.execute("SELECT slot,time_s,tick,items_json FROM inventory_snapshots "
                                 "WHERE match_key=? ORDER BY time_s,tick,slot", (key,)):
            snapshot = dict(r)
            snapshot["items"] = json.loads(snapshot.pop("items_json") or "{}")
            snapshot["progression"] = snapshot["items"].pop("__progression", {})
            result["inventory_snapshots"].append(snapshot)
        result["creeps"] = [dict(r) for r in self.db.execute(
            "SELECT entity_id,kind,team,time_s,x,y FROM creeps WHERE match_key=? ORDER BY time_s,entity_id", (key,))]
        result["economy"] = {}
        for r in self.db.execute("SELECT slot,time_s,net_worth,gold,xp,last_hits FROM economy WHERE match_key=? ORDER BY slot,time_s", (key,)):
            sample = dict(r)
            result["economy"].setdefault(str(sample.pop("slot")), []).append(sample)
        result["wards"] = [self._json_row(r) for r in self.db.execute(
            "SELECT * FROM wards WHERE match_key=? ORDER BY time_s,ordinal", (key,))]
        result["events"] = [self._json_row(r) for r in self.db.execute(
            "SELECT * FROM events WHERE match_key=? ORDER BY time_s,ordinal", (key,))]
        result["draft"] = [self._json_row(r) for r in self.db.execute(
            "SELECT * FROM draft WHERE match_key=? ORDER BY ordinal", (key,))]
        resolve_draft_teams(result["draft"], result["players"], result["metadata"].get("game_mode"))
        for row in result["draft"]:
            if not row["raw"].get("opendota_hero_id") and row.get("hero"):
                try:
                    from gem.constants import hero_meta
                    row["raw"]["opendota_hero_id"] = hero_meta(row["hero"]).get("id")
                except ImportError:
                    pass
        result["observations"] = [self._json_row(r) for r in self.db.execute(
            "SELECT * FROM observations WHERE match_key=? ORDER BY time_s,ordinal", (key,))]
        return result

    @staticmethod
    def _json_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for column in ("raw_json", "details_json"):
            if column in item:
                item[column[:-5] if column == "raw_json" else "details"] = json.loads(item.pop(column))
        return item

    def set_role(self, match_key: str, slot: int, role: int) -> None:
        if role not in (1, 2, 3, 4, 5):
            raise ValueError("role must be 1..5")
        with self.db:
            current = self.db.execute("SELECT team,role FROM players WHERE match_key=? AND slot=?",
                                      (match_key, slot)).fetchone()
            if current is None:
                raise KeyError((match_key, slot))
            self.db.execute("""UPDATE players SET role=?,role_source='manual'
                WHERE match_key=? AND team=? AND role=? AND slot<>?""",
                (current["role"], match_key, current["team"], role, slot))
            cursor = self.db.execute("UPDATE players SET role=?,role_source='manual' WHERE match_key=? AND slot=?",
                                     (role, match_key, slot))
            if cursor.rowcount != 1:
                raise KeyError((match_key, slot))

    def map_player(self, account_id: str, canonical_name: str) -> None:
        with self.db:
            self.db.execute("""INSERT INTO player_map(account_id,canonical_name) VALUES (?,?)
                ON CONFLICT(account_id) DO UPDATE SET canonical_name=excluded.canonical_name,
                updated_at=CURRENT_TIMESTAMP""", (account_id, canonical_name))

    def player_mappings(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.db.execute("SELECT account_id,canonical_name FROM player_map")}

    def mapping_candidates(self, account_id: str) -> list[str]:
        """Prior observed names for the same stable account ID, newest first."""
        rows = self.db.execute("""SELECT DISTINCT player_name FROM players
            WHERE account_id=? AND player_name IS NOT NULL AND player_name!=''
            ORDER BY player_name""", (account_id,))
        return [row[0] for row in rows]

    def get_thresholds(self) -> dict[str, float]:
        return {row[0]: row[1] for row in self.db.execute("SELECT name,value FROM thresholds")}

    def set_thresholds(self, values: dict[str, float]) -> None:
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO thresholds(name,value) VALUES (?,?)", values.items())

    def replace_observations(self, match_key: str, observations: list[dict[str, Any]]) -> None:
        with self.db:
            self.db.execute("DELETE FROM observations WHERE match_key=?", (match_key,))
            self.db.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)", [
                (match_key, i, item["type"], item.get("time_s"), item.get("end_s"), item.get("team"),
                 item.get("evidence", "inferred"), json.dumps(item.get("details", {}), ensure_ascii=False, default=str))
                for i, item in enumerate(observations)])
