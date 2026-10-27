from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from gem.state.game_clock import GameClock, GamePause
from gem.proto.dota_usermessages_pb2 import CHAT_MESSAGE_GLYPH_USED, CHAT_MESSAGE_SCAN_USED

from dota_review.bp import PATCH_741F_START, predict, train_model
from dota_review.core import Thresholds, active_wards, companionship, position_at
from dota_review.draft import ACTIONS, FIRST_PICK_SIDE, resolve_draft_teams
from dota_review.entities import observations_from_creeps, track_creeps
from dota_review.export import write_csv, write_html
from dota_review.ingest import normalize_match
from dota_review.paths import DATA_ROOT, match_dir, match_time_text
from dota_review.storage import Store
from dota_review.teams import allowed_match, canonical_team


def fixture(path: Path) -> dict:
    path.write_bytes(b"PBDEMS2\x00" + b"synthetic")
    clock = GameClock(game_start_tick=0, pauses=[GamePause(600, 900)])
    rune = NS(tick=930, game_time_s=None, rune_type=8, location_x=16000, location_y=16000)
    player = NS(player_id=0, team=2, hero_name="npc_dota_hero_axe", player_name="one",
                account_id=42, position_log=[(570, 9000.0, 9000.0), (930, 10000.0, 10000.0)],
                times_min=[0, 1800], game_times_min=[0, 50], net_worth_t_min=[600, 2000],
                gold_t_min=[100, 300], total_earned_xp_t_min=[0, 1500], lh_t_min=[0, 20],
                runes_log=[rune], lane=1)
    ward = NS(tick=570, killed_tick=930, expires_tick=2000, team=2, ward_type="observer",
              x=11000.0, y=12000.0, player_id=0)
    draft = NS(tick=0, slot_index=0, team=2, is_pick=True, hero_name=player.hero_name, hero_id=2)
    match = NS(game_clock=clock, players=[player], wards=[ward], smoke_events=[],
               combat_log=[], objectives=[], draft=[draft], post_game_tick=1800,
               game_end_tick=1800, duration=50, match_id=1234, radiant_win=True,
               radiant_team_name="A", dire_team_name="B", parse_error=None,
               truncated_at_tick=None, leagueid=99)
    return normalize_match(match, path)


class ReviewTests(unittest.TestCase):
    def test_selected_team_aliases(self) -> None:
        for name in ("OG", "Team Yandex", "PARIVISION", "Team Spirit", "XG",
                     "1w", "Team Liquid", "PSG.LGD", "Team Tidebound"):
            self.assertIsNotNone(canonical_team(name), name)
        self.assertIsNone(canonical_team("Nemiga Gaming"))
        self.assertTrue(allowed_match({"radiant_name": "OG", "dire_name": "BetBoom Team"}))
        self.assertFalse(allowed_match({"radiant_name": "Nemiga", "dire_name": "Dawn Bulls"}))

    def test_per_match_folder_labels_teams_time_and_event(self) -> None:
        match = {"id": "abc", "match_id": "9027799260", "radiant_name": "Team Yandex",
                 "dire_name": "Team Spirit", "metadata": {"start_time": 1791046373,
                 "league_name": "BLAST SLAM VIII", "leagueid": 19102}}
        folder = match_dir(match)
        self.assertTrue(folder.is_relative_to(DATA_ROOT))
        self.assertEqual(folder.parent.name, "yandex——spirit")
        self.assertIn("2026-10-04_00-52_CST__BLAST SLAM VIII__9027799260", folder.name)
        self.assertIn("北京时间", match_time_text(match))

    def test_pause_clock_and_ward_half_open(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = fixture(Path(folder) / "test.dem")
        self.assertEqual(data["positions"]["0"][1]["time_s"], 21)
        self.assertEqual(data["wards"][0]["time_s"], 19)
        self.assertEqual(data["wards"][0]["end_s"], 21)
        self.assertEqual(len(active_wards(data["wards"], 20)), 1)
        self.assertEqual(active_wards(data["wards"], 21), [])
        self.assertEqual(data["events"][0]["time_s"], 21)

    def test_store_export_duplicate_and_escape(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = fixture(root / "test.dem")
            data["players"][0]["name"] = "</script><img src=x>"
            with Store(root / "review.sqlite3") as store:
                self.assertTrue(store.save(data))
                self.assertFalse(store.save(data))
                loaded = store.load(data["id"])
                self.assertEqual(loaded["players"][0]["name"], "</script><img src=x>")
                store.set_role(data["id"], 0, 1)
                self.assertEqual(store.load(data["id"])["players"][0]["role_source"], "manual")
                store.map_player("42", "carry")
                self.assertEqual(store.player_mappings()["42"], "carry")
            report = write_html(loaded, root / "out" / "report.html")
            csvs = write_csv(loaded, root / "out")
            html = report.read_text(encoding="utf-8")
            self.assertNotIn("</script><img", html)
            self.assertIn("\\u003c/script\\u003e", html)
            self.assertTrue((root / "out" / "events.csv").exists())
            self.assertGreater(len(csvs), 5)

    def test_position_gaps_and_companionship(self) -> None:
        self.assertIsNone(position_at([{"time_s": 0, "x": 0, "y": 0}], 5))
        players = [{"slot": 0, "team": 2, "role": 2}, {"slot": 1, "team": 2, "role": 4},
                   {"slot": 2, "team": 2, "role": 5}]
        tracks = {str(i): [{"time_s": t, "x": i * 10, "y": 0} for t in range(11)] for i in range(3)}
        rows = companionship(players, tracks, 10, Thresholds(together_min_seconds=8))
        self.assertEqual({row["roles"] for row in rows}, {"2+4", "2+5", "2+4+5"})

    def test_creep_sampling_stops_when_entity_leaves_and_at_match_end(self) -> None:
        from gem.state.entities import EntityOp

        class Entity:
            def __init__(self, class_name="CDOTA_NPC_Creep_Neutral", index=7, team=None, cell_x=100, cell_y=100, unit_id=None):
                self.class_name, self.index, self.team = class_name, index, team
                self.cell_x, self.cell_y, self.unit_id = cell_x, cell_y, unit_id
            def get_index(self): return self.index
            def get_serial(self): return 3
            def get_class_name(self): return self.class_name
            def get(self, name): return self.unit_id if name == "m_iUnitNameStringTableIndex" else None
            def get_uint32(self, name): return self.cell_x if name.endswith("m_cellX") else self.cell_y
            def get_float32(self, name): return 0.0
            def get_int32(self, name): return self.team

        class Parser:
            parse_error = None
            def __init__(self, path):
                self.game_time_s = 0
                self.string_tables = NS(get_by_name=lambda name: None)
            def on_entity(self, callback): self.entity_callback = callback
            def on_tick_start(self, callback): self.tick_callback = callback
            def on_chat_event(self, callback): self.chat_callback = callback
            def parse(self):
                entity = Entity()
                self.entity_callback(entity, EntityOp.CREATED_ENTERED)
                self.entity_callback(Entity("CDOTA_BaseNPC_Tower", 8, 2, 90, 95, 42), EntityOp.CREATED_ENTERED)
                self.entity_callback(Entity("CDOTA_BaseNPC_Shop", 9, 5, 50, 60, -1), EntityOp.CREATED_ENTERED)
                self.tick_callback(0)
                self.entity_callback(entity, EntityOp.LEFT)
                self.game_time_s = 2
                self.tick_callback(1)
                self.entity_callback(entity, EntityOp.UPDATED_ENTERED)
                self.game_time_s = 4
                self.tick_callback(2)
                self.game_time_s = 6
                self.tick_callback(3)
                self.entity_callback(entity, EntityOp.DELETED_LEFT)
                self.game_time_s = 4
                self.chat_callback(NS(type=CHAT_MESSAGE_GLYPH_USED, playerid_1=3, value=0), 4)
                self.chat_callback(NS(type=CHAT_MESSAGE_SCAN_USED, playerid_1=-1, value=2), 4)
                self.game_time_s = 8
                self.tick_callback(4)

        with patch("gem.parser.ReplayParser", Parser):
            samples, error, features, events = track_creeps("unused.dem", duration_s=4)
        self.assertIsNone(error)
        self.assertEqual([sample["time_s"] for sample in samples], [0, 4])
        self.assertEqual({sample["entity_id"] for sample in samples}, {"7:3"})
        self.assertEqual(features, [
            {"id": "8:3", "kind": "tower", "team": 2, "name": None, "x": 11520.0, "y": 12160.0},
            {"id": "9:3", "kind": "shop", "team": 5, "name": None, "x": 6400.0, "y": 7680.0}])
        self.assertEqual([(event["type"], event["team"], event["time_s"]) for event in events],
                         [("glyph", 3, 4), ("scan", 2, 4)])

    def test_entity_based_pull_candidate(self) -> None:
        samples = []
        for second in (2, 4, 6):
            samples += [{"entity_id": "lane:1", "kind": "lane", "team": 2,
                         "time_s": second, "x": 8071, "y": 15831},
                        {"entity_id": "neutral:1", "kind": "neutral", "team": None,
                         "time_s": second, "x": 8120, "y": 15880}]
        rows = observations_from_creeps(samples)
        self.assertTrue(any(r["type"] == "pull" and r["evidence"].startswith("candidate") for r in rows))

    def test_refresh_map_data_only_replaces_derived_rows(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = fixture(root / "refresh.dem")
            data["map_features"] = [{"id": "1:2", "kind": "tower", "team": 2,
                                    "name": "npc_dota_goodguys_tower1_mid", "x": 10000, "y": 12000}]
            data["events"].extend([
                {"time_s": 5, "type": "tower", "subtype": "npc_dota_goodguys_tower1_mid", "team": 2,
                 "description": "tower", "evidence": "combat_log_tower_kill", "raw": {}},
                {"time_s": 6, "type": "glyph", "subtype": "used", "team": 3,
                 "description": "glyph", "evidence": "replay_chat_event", "raw": {"tick": 6}},
                {"time_s": 7, "type": "scan", "subtype": "used", "team": 2,
                 "description": "scan", "evidence": "replay_chat_event", "raw": {"tick": 7}}])
            data["metadata"]["ability_event_coverage"] = {"glyph": "complete", "scan": "partial"}
            with Store(root / "review.sqlite3") as store:
                self.assertTrue(store.save(data))
                data["map_features"][0]["x"] = 11000
                self.assertTrue(store.refresh_map_data(data))
                self.assertFalse(store.refresh_map_data({**data, "sha256": "wrong"}))
                feature = dict(store.db.execute("SELECT * FROM map_features WHERE match_key=?", (data["id"],)).fetchone())
                self.assertEqual(feature["x"], 11000)
                types = [row[0] for row in store.db.execute("SELECT type FROM events WHERE match_key=? ORDER BY ordinal", (data["id"],))]
                self.assertEqual(types.count("glyph"), 1)
                self.assertEqual(types.count("scan"), 1)
                self.assertEqual(types.count("rune"), 1)
                metadata = json.loads(store.db.execute("SELECT metadata_json FROM matches WHERE id=?", (data["id"],)).fetchone()[0])
                self.assertEqual(metadata["ability_event_coverage"], {"glyph": "complete", "scan": "partial"})

    def test_bp_rejects_future_training(self) -> None:
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "db.sqlite3") as store:
            with store.db:
                store.db.execute("""INSERT INTO bp_models
                    (patch,cutoff,validation_end,train_n,valid_n,brier,baseline_brier,ece,accepted,model_json,sample_ids_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    ("7.41f", 900, 1000, 200, 50, .2, .25, .05, 1,
                     json.dumps({"weights": {}, "bias": 0}), "[]"))
            self.assertIsNone(predict(store, 999, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10]))
            self.assertAlmostEqual(predict(store, 1001, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10]), .5)

    def test_captains_mode_ban_ownership_uses_validated_turn_pattern(self) -> None:
        rows = [{"team": 2, "action": "pick" if action == "P" else "ban",
                 "hero": f"hero{i}", "raw": {"tick": i}} for i, action in enumerate(ACTIONS)]
        players = [{"hero": f"hero{i}", "team": 3 if FIRST_PICK_SIDE[i] else 2}
                   for i, action in enumerate(ACTIONS) if action == "P"]
        resolved = resolve_draft_teams(rows, players, 2)
        self.assertEqual([row["team"] for row in resolved],
                         [3 if side else 2 for side in FIRST_PICK_SIDE])
        self.assertEqual(resolved[0]["raw"]["team_source"], "captains_mode_741f_pattern")

    def test_model_records_time_split_and_validation_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "db.sqlite3") as store:
            with store.db:
                store.db.execute("INSERT INTO api_cache(key,payload_json) VALUES (?,?)",
                                 ("constants/patch", json.dumps([{"id": 60, "name": "7.41"}])))
                for i in range(60):
                    picks = ([{"is_pick": True, "team": 0, "hero_id": j} for j in range(1, 6)] +
                             [{"is_pick": True, "team": 1, "hero_id": j} for j in range(6, 11)])
                    detail = {"match_id": i + 1, "start_time": PATCH_741F_START + i * 60,
                              "patch": 60, "leagueid": 1, "picks_bans": picks,
                              "radiant_win": bool(i % 2)}
                    store.db.execute("INSERT INTO api_cache(key,payload_json) VALUES (?,?)",
                                     (f"matches/{i+1}", json.dumps(detail)))
            model = train_model(store, min_train=40, min_valid=10)
            self.assertEqual(model["train_n"], 48)
            self.assertEqual(model["valid_n"], 12)
            self.assertLess(model["train_end"], model["cutoff"])
            self.assertLessEqual(model["cutoff"], model["validation_end"])
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM bp_models").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
