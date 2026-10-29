"""Loopback-only, read-only HTTP view of the local review database."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .paths import default_db
from .teams import allowed_match

# Replay keys are the first 16 lowercase hex characters of the replay SHA-256.
_KEY = re.compile(r"[0-9a-f]{16}\Z")
_HOST = re.compile(r"(?:127\.0\.0\.1|localhost)(?::([0-9]{1,5}))?\Z", re.IGNORECASE)
_MATCH_COLUMNS = "id, match_id, radiant_name, dire_name, duration_s, patch, parse_status"
_HTML = Path(__file__).resolve().with_name("web_ui.html")
_ASSETS = Path(__file__).resolve().parent / "assets"
_MAP = _ASSETS / "dota2_map_7_41.png"
_MAP_ICONS = {
    name: _ASSETS / "icons" / name
    for name in ("tower-45-minimap.png", "tower-90-minimap.png", "roshan-minimap.png",
                 "tormentor-minimap.png", "twin-gate-minimap.png")
}


def _connect(db_path: Path) -> sqlite3.Connection:
    # as_uri() escapes spaces, ? and # and handles Windows drive letters.
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _matches(db: sqlite3.Connection) -> dict:
    rows = db.execute(f"SELECT {_MATCH_COLUMNS} FROM matches ORDER BY imported_at DESC, id DESC")
    # Discard malformed keys too: every advertised item must have a safe detail URL.
    return {"matches": [dict(row) for row in rows
                        if isinstance(row["id"], str) and _KEY.fullmatch(row["id"])
                        and allowed_match(dict(row))]}


def _detail(db: sqlite3.Connection, key: str) -> dict | None:
    row = db.execute(f"SELECT {_MATCH_COLUMNS} FROM matches WHERE id=?", (key,)).fetchone()
    if row is None or not allowed_match(dict(row)):
        return None
    players = [dict(r) for r in db.execute(
        "SELECT slot, team, hero, player_name FROM players WHERE match_key=? ORDER BY slot", (key,))]
    positions: dict[str, list[dict]] = {}
    for r in db.execute("SELECT slot, time_s, x, y FROM positions WHERE match_key=? ORDER BY slot, time_s", (key,)):
        point = dict(r)
        positions.setdefault(str(point.pop("slot")), []).append(point)
    wards = [dict(r) for r in db.execute(
        "SELECT team, kind, time_s, end_s, end_reason, x, y FROM wards WHERE match_key=? ORDER BY time_s, ordinal", (key,))]
    events = [dict(r) for r in db.execute(
        "SELECT time_s, type, subtype, team, slot, description FROM events WHERE match_key=? ORDER BY time_s, ordinal", (key,))]
    features_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='map_features'").fetchone()
    map_features = [dict(r) for r in db.execute(
        "SELECT feature_id,kind,team,name,x,y FROM map_features WHERE match_key=? ORDER BY kind,team,feature_id", (key,))] if features_table else []
    observations_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()
    map_observations = []
    if observations_table:
        for r in db.execute(
            "SELECT type,time_s,end_s,team,evidence,details_json FROM observations WHERE match_key=? ORDER BY time_s,ordinal", (key,)):
            observation = dict(r)
            observation["details"] = json.loads(observation.pop("details_json"))
            map_observations.append(observation)
    metadata = json.loads(db.execute("SELECT metadata_json FROM matches WHERE id=?", (key,)).fetchone()[0])
    return {"match": dict(row), "players": players, "positions": positions, "wards": wards, "events": events,
            "map_features": map_features, "map_observations": map_observations,
            "map_feature_coverage": metadata.get("map_feature_coverage"),
            "tower_identity_coverage": metadata.get("tower_identity_coverage"),
            "map_objective_coverage": metadata.get("map_objective_coverage"),
            "creep_coverage": metadata.get("creep_coverage"),
            "ability_event_coverage": metadata.get("ability_event_coverage")}


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, db_path: Path):
        self.db_path = Path(db_path)
        super().__init__(("127.0.0.1", port), ReviewHandler)


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "DotaReview"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # Request targets are untrusted and can contain sensitive information.
        pass

    def _reply(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(payload)

    def _json(self, status: int, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._reply(status, payload, "application/json; charset=utf-8")

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        # Never echo request targets, database errors or local paths.
        self._json(code, {"error": "request failed"})

    def _host_ok(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1:
            return False
        match = _HOST.fullmatch(hosts[0])
        return bool(match and (match.group(1) is None or 0 < int(match.group(1)) <= 65535))

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        if not self._host_ok():
            self._json(400, {"error": "invalid host"})
            return False
        return True

    def handle_expect_100(self) -> bool:
        # This server accepts no request bodies.
        self._json(417, {"error": "request failed"})
        return False

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json(400, {"error": "invalid host"})
            return
        # Exact raw request targets only: no query strings, escapes or traversal.
        if self.path in ("/", "/web_ui.html"):
            try:
                html = _HTML.read_bytes()
            except OSError:
                self._json(404, {"error": "not found"})
                return
            self._reply(200, html, "text/html; charset=utf-8")
            return
        if self.path == "/assets/dota2_map_7_41.png":
            try:
                image = _MAP.read_bytes()
            except OSError:
                self._json(404, {"error": "not found"})
                return
            self._reply(200, image, "image/png")
            return
        if self.path.startswith("/assets/icons/"):
            icon_name = self.path.removeprefix("/assets/icons/")
            icon_path = _MAP_ICONS.get(icon_name)
            if icon_path is None:
                self._json(404, {"error": "not found"})
                return
            try:
                icon = icon_path.read_bytes()
            except OSError:
                self._json(404, {"error": "not found"})
                return
            self._reply(200, icon, "image/png")
            return
        key = None
        if self.path != "/api/matches":
            prefix = "/api/matches/"
            if not self.path.startswith(prefix) or not _KEY.fullmatch(self.path[len(prefix):]):
                self._json(404, {"error": "not found"})
                return
            key = self.path[len(prefix):]
        try:
            with closing(_connect(self.server.db_path)) as db:
                # Keep validation and all detail queries in one read snapshot.
                db.execute("BEGIN")
                data = _matches(db) if key is None else _detail(db, key)
            if data is None:
                self._json(404, {"error": "not found"})
            else:
                self._json(200, data)
        except (BrokenPipeError, ConnectionResetError):
            # A browser can cancel a request while switching matches.
            return
        except (sqlite3.Error, OSError, ValueError, TypeError, UnicodeError):
            self._json(500, {"error": "database unavailable"})

    def _reject_method(self) -> None:
        if not self._host_ok():
            self._json(400, {"error": "invalid host"})
        else:
            self._json(405, {"error": "method not allowed"})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_TRACE = do_CONNECT = _reject_method


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only local Dota review web server")
    parser.add_argument("--db", type=Path, default=default_db())
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    with ReviewServer(args.port, args.db) as server:
        print(f"Listening on http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
