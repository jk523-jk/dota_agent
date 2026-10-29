"""FastAPI read API for the local Dota review workbench."""
from __future__ import annotations

import argparse
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .mysql import mysql_connection
from .teams import allowed_match

_KEY = re.compile(r"[0-9a-f]{16}\Z")
_COLUMNS = "id, match_id, radiant_name, dire_name, duration_s, patch, parse_status"
_REVIEW_DIR = Path(__file__).resolve().parent
_ASSETS_DIR = _REVIEW_DIR / "assets"
_FRONTEND_DIST = _REVIEW_DIR.parent / "frontend" / "dist"
_FRONTEND_INDEX = _FRONTEND_DIST / "index.html"
_INDEX = _FRONTEND_INDEX if _FRONTEND_INDEX.is_file() else _REVIEW_DIR / "web_ui.html"
_STATIC_ASSETS = _FRONTEND_DIST / "assets" if _FRONTEND_DIST.is_dir() else _ASSETS_DIR

app = FastAPI(title="Dota 2 Replay Review", version="0.1.0")
app.mount("/assets", StaticFiles(directory=_STATIC_ASSETS), name="assets")
logger = logging.getLogger(__name__)


def _rows(cursor, sql: str, params: tuple = ()) -> list[dict]:
    cursor.execute(sql, params)
    return list(cursor.fetchall())


class RoleUpdate(BaseModel):
    role: int


class AgentMessage(BaseModel):
    match_id: str
    message: str = Field(min_length=1, max_length=4000)
    slot: int | None = Field(default=None, ge=0, le=9)
    side: Literal["radiant", "dire"] | None = None
    mode: Literal["player", "ward_hotspots", "team_style"] | None = None
    thread_id: str | None = Field(default=None, min_length=1, max_length=80,
                                  pattern=r"^[a-zA-Z0-9._-]+$")


@app.get("/", include_in_schema=False)
@app.get("/web_ui.html", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_INDEX, media_type="text/html; charset=utf-8")


@app.get("/api/matches")
def list_matches() -> dict:
    try:
        with mysql_connection() as db, db.cursor() as cursor:
            matches = _rows(cursor,
                f"SELECT {_COLUMNS} FROM dota_matches ORDER BY imported_at DESC, id DESC")
        return {"matches": [row for row in matches if _KEY.fullmatch(row["id"])
                             and allowed_match(row)]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc


@app.get("/api/matches/{key}")
def get_match(key: str) -> dict:
    if not _KEY.fullmatch(key):
        raise HTTPException(status_code=404, detail="not found")
    try:
        with mysql_connection() as db, db.cursor() as cursor:
            matches = _rows(cursor, f"SELECT {_COLUMNS}, metadata_json FROM dota_matches WHERE id=%s", (key,))
            if not matches or not allowed_match(matches[0]):
                raise HTTPException(status_code=404, detail="not found")
            match = matches[0]
            metadata = json.loads(match.pop("metadata_json") or "{}")
            players = _rows(cursor,
                "SELECT slot, team, hero, player_name, role, role_source FROM dota_players WHERE match_key=%s ORDER BY slot", (key,))
            position_rows = _rows(cursor,
                "SELECT slot, time_s, x, y FROM dota_positions WHERE match_key=%s ORDER BY slot, time_s", (key,))
            positions: dict[str, list[dict]] = {}
            for point in position_rows:
                slot = str(point.pop("slot"))
                positions.setdefault(slot, []).append(point)
            wards = _rows(cursor,
                "SELECT team, kind, time_s, end_s, end_reason, x, y FROM dota_wards "
                "WHERE match_key=%s ORDER BY time_s, ordinal", (key,))
            events = _rows(cursor,
                "SELECT time_s, type, subtype, team, slot, description FROM dota_events "
                "WHERE match_key=%s ORDER BY time_s, ordinal", (key,))
            map_features = _rows(cursor,
                "SELECT feature_id, kind, team, name, x, y FROM dota_map_features "
                "WHERE match_key=%s ORDER BY kind, team, feature_id", (key,))
            observations = _rows(cursor,
                "SELECT type, time_s, end_s, team, evidence, details_json FROM dota_observations "
                "WHERE match_key=%s ORDER BY time_s, ordinal", (key,))
            for observation in observations:
                observation["details"] = json.loads(observation.pop("details_json") or "{}")
        return {
            "match": match,
            "players": players,
            "positions": positions,
            "wards": wards,
            "events": events,
            "map_features": map_features,
            "map_observations": observations,
            "map_feature_coverage": metadata.get("map_feature_coverage"),
            "tower_identity_coverage": metadata.get("tower_identity_coverage"),
            "map_objective_coverage": metadata.get("map_objective_coverage"),
            "creep_coverage": metadata.get("creep_coverage"),
            "ability_event_coverage": metadata.get("ability_event_coverage"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc


@app.get("/api/matches/{key}/inventory")
def get_inventory(key: str, slot: int = Query(ge=0, le=9), time_s: int = Query()):
    if not _KEY.fullmatch(key):
        raise HTTPException(status_code=404, detail="not found")
    try:
        with mysql_connection() as db, db.cursor() as cursor:
            match = _rows(cursor, f"SELECT {_COLUMNS} FROM dota_matches WHERE id=%s", (key,))
            if not match or not allowed_match(match[0]):
                raise HTTPException(status_code=404, detail="not found")
            players = _rows(cursor, "SELECT slot FROM dota_players WHERE match_key=%s AND slot=%s", (key, slot))
            if not players:
                raise HTTPException(status_code=404, detail="player not found")
            rows = _rows(cursor, "SELECT time_s,tick,items_json FROM dota_inventory_snapshots "
                "WHERE match_key=%s AND slot=%s AND time_s<=%s ORDER BY time_s DESC,tick DESC LIMIT 1",
                (key, slot, time_s))
            terminal_rows = _rows(cursor, "SELECT items_json FROM dota_inventory_snapshots "
                "WHERE match_key=%s AND slot=%s ORDER BY time_s DESC,tick DESC LIMIT 1", (key, slot))
        if not rows:
            return {"slot": slot, "requested_time_s": time_s, "snapshot_time_s": None, "items": {}, "coverage": "unavailable"}
        row = rows[0]
        payload = json.loads(row["items_json"] or "{}")
        progression = payload.pop("__progression", {})
        if terminal_rows:
            terminal_payload = json.loads(terminal_rows[0]["items_json"] or "{}")
            terminal_progression = terminal_payload.get("__progression", {})
            for field in ("endgame", "upgrade_timeline", "neutral_finds"):
                if field in terminal_progression:
                    progression[field] = terminal_progression[field]
        return {"slot": slot, "requested_time_s": time_s, "snapshot_time_s": row["time_s"],
                "tick": row["tick"], "items": payload, "progression": progression, "coverage": "available"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="inventory unavailable") from exc


@app.patch("/api/matches/{key}/players/{slot}/role")
def update_player_role(key: str, slot: int, update: RoleUpdate):
    if not _KEY.fullmatch(key) or not 0 <= slot <= 9:
        raise HTTPException(status_code=404, detail="not found")
    if update.role not in (1, 2, 3, 4, 5):
        raise HTTPException(status_code=422, detail="role must be 1..5")
    try:
        with mysql_connection() as db, db.cursor() as cursor:
            cursor.execute(f"SELECT {_COLUMNS} FROM dota_matches WHERE id=%s", (key,))
            match = cursor.fetchone()
            if not match or not allowed_match(match):
                raise HTTPException(status_code=404, detail="not found")
            db.begin()
            cursor.execute("SELECT team,role FROM dota_players WHERE match_key=%s AND slot=%s FOR UPDATE", (key, slot))
            player = cursor.fetchone()
            if not player:
                raise HTTPException(status_code=404, detail="player not found")
            cursor.execute("UPDATE dota_players SET role=%s,role_source='manual' "
                "WHERE match_key=%s AND team=%s AND role=%s AND slot<>%s",
                (player["role"], key, player["team"], update.role, slot))
            cursor.execute("UPDATE dota_players SET role=%s,role_source='manual' WHERE match_key=%s AND slot=%s",
                (update.role, key, slot))
            db.commit()
        return {"slot": slot, "role": update.role, "role_source": "manual"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="role update unavailable") from exc


@app.get("/api/agent/status")
def get_agent_status() -> dict:
    try:
        from .agent_graph import agent_status
        return agent_status()
    except ImportError:
        logger.exception("LangGraph dependencies are unavailable; exposing evidence-only agent status")
        return {"ready": True, "mode": "evidence_only", "model_configured": False,
                "message": "LangGraph 组件未安装；录像数据分析仍可使用。请安装 requirements.txt 中的依赖以启用模型解读。"}


@app.post("/api/agent/chat")
def chat_with_agent(request: AgentMessage) -> dict:
    if not _KEY.fullmatch(request.match_id):
        raise HTTPException(status_code=404, detail="not found")
    try:
        from .agent_graph import run_agent

        return run_agent(request.match_id, request.message.strip(),
                         selected_slot=request.slot, side=request.side,
                         requested_mode=request.mode, thread_id=request.thread_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        incident_id = uuid.uuid4().hex[:10]
        logger.exception("LangGraph analysis failed (incident=%s, match=%s, mode=%s)",
                         incident_id, request.match_id, request.mode)
        try:
            from .agent_fallback import run_evidence_only
            result = run_evidence_only(request.match_id, request.message.strip(),
                                       selected_slot=request.slot, side=request.side,
                                       requested_mode=request.mode)
            result["notice"] = "语言模型流程暂不可用，已切换为录像数据分析。"
            return result
        except Exception as fallback_exc:
            logger.exception("Replay evidence fallback failed (incident=%s, match=%s)",
                             incident_id, request.match_id)
            raise HTTPException(status_code=503,
                                detail=f"分析暂时失败（编号 {incident_id}）。请重试；若重复出现，请提供此编号。") from fallback_exc


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Dota review FastAPI server")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
