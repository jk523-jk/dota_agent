"""Workspace-owned location for replay and analysis data."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import re
import shutil

from .teams import canonical_team


DATA_ROOT = Path(__file__).resolve().parent.parent / "dota_data"


def default_db() -> Path:
    return DATA_ROOT / "review.sqlite3"


def replay_dir() -> Path:
    return DATA_ROOT / "matches"


def report_dir() -> Path:
    return DATA_ROOT / "reports"


TEAM_LABELS = {"OG": "OG", "Team Yandex": "yandex", "PARIVISION": "pari",
               "Team Spirit": "spirit", "Xtreme Gaming": "xg", "1win": "1w",
               "Team Liquid": "liquid", "LGD": "lgd", "Tidebound": "tidebound"}


def _safe_name(value: str, limit: int = 70) -> str:
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return result[:limit] or "unknown"


def match_dir(data: dict) -> Path:
    """Group one game by opponent pair, local start time, event and match ID."""
    labels = [TEAM_LABELS.get(canonical_team(data.get(side)), data.get(side) or "unknown")
              for side in ("radiant_name", "dire_name")]
    pair = _safe_name("——".join(labels))
    meta = data.get("metadata", {})
    start = meta.get("start_time")
    if isinstance(start, int) and start > 0:
        cst = timezone(timedelta(hours=8))
        date_label = datetime.fromtimestamp(start, cst).strftime("%Y-%m-%d_%H-%M_CST")
    else:
        date_label = "时间待核实"
    event = _safe_name(str(meta.get("league_name") or f"赛事ID-{meta.get('leagueid') or '未知'}"))
    identity = _safe_name(str(data.get("match_id") or data["id"]))
    return replay_dir() / pair / _safe_name(f"{date_label}__{event}__{identity}", 120)


def match_time_text(data: dict) -> str:
    start = data.get("metadata", {}).get("start_time")
    if not isinstance(start, int) or start <= 0:
        return "比赛时间待核实"
    cst = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(start, cst).strftime("%Y-%m-%d %H:%M 北京时间")


def relocate_match_folder(data: dict) -> Path:
    """Rename a match folder after authoritative date or event metadata arrives."""
    target = match_dir(data)
    previous = data.get("metadata", {}).get("folder_path")
    if previous:
        old = Path(previous).resolve()
        if not old.is_relative_to(DATA_ROOT.resolve()):
            raise ValueError("existing match folder is outside dota_data")
        if old != target.resolve() and old.exists():
            if target.exists():
                raise FileExistsError(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(target))
    target.mkdir(parents=True, exist_ok=True)
    data["metadata"]["folder_path"] = str(target)
    if (target / "replay.dem").exists():
        data["metadata"]["replay_path"] = str(target / "replay.dem")
    return target
