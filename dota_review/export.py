"""Portable HTML and CSV exports from the same normalized match snapshot."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .bp import draft_descriptors
from .core import companionship, rune_windows, smoke_followups, Thresholds


def enriched(data: dict[str, Any], thresholds: Thresholds = Thresholds()) -> dict[str, Any]:
    copy = dict(data)
    # Compact the large entity track for a practical single-file offline report.
    copy["creeps"] = [[row["time_s"], round(row["x"]), round(row["y"]),
                       row.get("team") or 0, "n" if row["kind"] == "neutral" else "l"]
                      for row in data.get("creeps", [])]
    copy["companionship"] = companionship(data["players"], data["positions"], data["duration_s"], thresholds)
    copy["rune_windows"] = rune_windows(data["events"], data["positions"], data["players"])
    smokes = [e for e in data["events"] if e["type"] == "smoke"]
    copy["smoke_followups"] = smoke_followups(smokes, data["events"])
    copy["bp_descriptors"] = draft_descriptors(data["draft"])
    return copy


def write_html(data: dict[str, Any], path: str | Path, thresholds: Thresholds = Thresholds()) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(enriched(data, thresholds), ensure_ascii=False, separators=(",", ":"))
    # An inert JSON script element still needs its closing tag escaped.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = Path(__file__).with_name("report.html").read_text(encoding="utf-8")
    target.write_text(template.replace("__MATCH_JSON__", payload), encoding="utf-8")
    return target


def write_csv(data: dict[str, Any], directory: str | Path) -> list[Path]:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    outputs = []
    tables = {
        "players": data["players"],
        "positions": [{"slot": slot, **row} for slot, rows in data["positions"].items() for row in rows],
        "creeps": data.get("creeps", []),
        "economy": [{"slot": slot, **row} for slot, rows in data["economy"].items() for row in rows],
        "wards": data["wards"],
        "events": data["events"],
        "draft": data["draft"],
        "observations": data["observations"],
        "companionship": companionship(data["players"], data["positions"], data["duration_s"]),
    }
    for name, rows in tables.items():
        path = root / f"{name}.csv"
        columns = sorted({key for row in rows for key in row if key not in ("raw", "details", "raw_json", "details_json")})
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        outputs.append(path)
    return outputs
