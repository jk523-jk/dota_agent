from contextlib import contextmanager
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from dota_review import api


class Cursor:
    def __init__(self, rows, player=None):
        self.rows = rows
        self.player = player
        self.result = []
        self.one = None
        self.updates = []

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def execute(self, sql, params=()):
        if "FROM dota_matches" in sql:
            self.result = [{"id": "a" * 16, "radiant_name": "OG", "dire_name": "Liquid", "match_id": "123"}]
            self.one = self.result[0]
        elif "FROM dota_players" in sql and "role" in sql:
            self.one = self.player
        elif "FROM dota_players" in sql:
            self.result = [{"slot": params[1]}]
        elif "FROM dota_inventory_snapshots" in sql:
            self.result = self.rows
        elif sql.lstrip().startswith("UPDATE"):
            self.updates.append((sql, params))
    def fetchall(self): return self.result
    def fetchone(self): return self.one
    def executemany(self, *_): pass


class Connection:
    def __init__(self, cursor): self._cursor = cursor
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def cursor(self): return self._cursor
    def begin(self): pass
    def commit(self): pass


class ApiTests(unittest.TestCase):
    def test_inventory_uses_the_latest_snapshot_before_selected_time(self):
        cursor = Cursor([{"time_s": 54, "tick": 1830, "items_json": '{"0":"item_blink"}'}])
        @contextmanager
        def connect(): yield Connection(cursor)
        with patch.object(api, "mysql_connection", connect):
            data = api.get_inventory("a" * 16, 0, 55)
        self.assertEqual(data["snapshot_time_s"], 54)
        self.assertEqual(data["items"], {"0": "item_blink"})
        self.assertEqual(data["coverage"], "available")

    def test_inventory_reports_missing_snapshot(self):
        cursor = Cursor([])
        @contextmanager
        def connect(): yield Connection(cursor)
        with patch.object(api, "mysql_connection", connect):
            data = api.get_inventory("a" * 16, 0, 55)
        self.assertEqual(data["items"], {})
        self.assertEqual(data["coverage"], "unavailable")

    def test_role_update_writes_manual_role_and_rejects_bad_role(self):
        cursor = Cursor([], player={"team": 2, "role": 2})
        @contextmanager
        def connect(): yield Connection(cursor)
        with patch.object(api, "mysql_connection", connect):
            result = api.update_player_role("a" * 16, 0, api.RoleUpdate(role=2))
            with self.assertRaises(HTTPException) as error:
                api.update_player_role("a" * 16, 0, api.RoleUpdate(role=6))
        self.assertEqual(result["role_source"], "manual")
        self.assertEqual(len(cursor.updates), 2)
        self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
