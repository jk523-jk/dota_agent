"""Compare a locally imported professional replay against saved OpenDota JSON.

Example:
  python scripts/verify_public_match.py --db .cache/verify.sqlite3 \
      --match-key fcfd35177dd5a5e0 --api-json .cache/opendota_match.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dota_review.storage import Store  # noqa: E402


def verify(match: dict, api: dict) -> dict[str, object]:
    draft = [(row["team"] - 2, row["action"] == "pick", row["raw"].get("opendota_hero_id"))
             for row in match["draft"]]
    api_draft = [(row["team"], row["is_pick"], row["hero_id"]) for row in api.get("picks_bans") or []]
    rune_count = sum(event["type"] == "rune" and event.get("raw", {}).get("rune_action", "picked_up") == "picked_up"
                     for event in match["events"])
    api_runes = sum(len(player.get("runes_log") or []) for player in api["players"])
    ward_counts = {kind: sum(ward["kind"] == kind for ward in match["wards"])
                   for kind in ("observer", "sentry")}
    api_wards = {"observer": sum(len(player.get("obs_log") or []) for player in api["players"]),
                 "sentry": sum(len(player.get("sen_log") or []) for player in api["players"])}
    smokes = sum(event["type"] == "smoke" for event in match["events"])
    api_smokes = sum((player.get("item_uses") or {}).get("smoke_of_deceit", 0) for player in api["players"])
    return {"match_id": match["match_id"], "complete": match["parse_status"] == "complete",
            "draft_exact": draft == api_draft, "runes": [rune_count, api_runes],
            "wards": [ward_counts, api_wards], "smokes": [smokes, api_smokes],
            "pause_count": [len(match["metadata"].get("pauses", [])), len(api.get("pauses") or [])],
            "hero_position_samples": sum(len(rows) for rows in match["positions"].values()),
            "creep_entity_samples": len(match.get("creeps", []))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--match-key", required=True)
    parser.add_argument("--api-json", type=Path, required=True)
    args = parser.parse_args()
    with Store(args.db) as store:
        match = store.load(args.match_key)
    api = json.loads(args.api_json.read_text(encoding="utf-8"))
    result = verify(match, api)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    required = (result["complete"] and result["draft_exact"] and
                result["runes"][0] == result["runes"][1] and
                result["wards"][0] == result["wards"][1] and
                result["smokes"][0] == result["smokes"][1] and
                result["pause_count"][0] == result["pause_count"][1])
    if not required:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
