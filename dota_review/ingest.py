"""Convert gem-dota's public ParsedMatch fields to a stable local schema."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .draft import resolve_draft_teams
from .teams import allowed_match
from .paths import match_dir


RUNE_NAMES = {0: "double_damage", 1: "haste", 2: "illusion", 3: "invisibility",
              4: "regeneration", 5: "bounty", 6: "arcane", 7: "water",
              8: "wisdom", 9: "shield"}


def _raw(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        return asdict(item)
    if isinstance(item, dict):
        return dict(item)
    return {}


def _value(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _time(match: Any, tick: int | None) -> int | None:
    if tick is None:
        return None
    clock = getattr(match, "game_clock", None)
    if clock is None:
        return None
    value = clock.game_seconds_at(int(tick))
    return int(value) if value is not None else None


def _dedupe_samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_second = {row["time_s"]: row for row in rows}
    return sorted(by_second.values(), key=lambda row: row["time_s"])


def _infer_roles(players: list[dict[str, Any]], native: list[Any], economy: dict[str, list[dict[str, Any]]]) -> None:
    """Assign tentative positions using gem's lane and 10-minute economy evidence."""
    for team in (2, 3):
        team_players = [(p, n) for p, n in zip(players, native) if p["team"] == team]
        if len(team_players) != 5:
            continue
        worth = {p["slot"]: next((r["net_worth"] for r in reversed(economy.get(str(p["slot"]), []))
                                  if r["time_s"] <= 600 and r.get("net_worth") is not None), 0)
                 for p, _ in team_players}
        if not any(worth.values()):
            continue
        remaining = list(team_players)
        mids = [(p, n) for p, n in remaining if getattr(n, "lane", 0) == 2]
        if len(mids) == 1:
            p, _ = mids[0]
            p["role"] = 2
            remaining.remove(mids[0])
        # Other assignments are candidates, not authoritative role labels.
        remaining.sort(key=lambda pair: worth[pair[0]["slot"]], reverse=True)
        for role, (p, _) in zip((r for r in (1, 2, 3, 4, 5) if r not in [x["role"] for x, _ in team_players]), remaining):
            p["role"] = role
        for p, _ in team_players:
            p["role_source"] = "inferred_lane_and_10min_economy"


def normalize_match(match: Any, replay: str | Path) -> dict[str, Any]:
    path = Path(replay)
    if path.suffix.lower() != ".dem":
        raise ValueError("Only .dem replays are accepted")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        if handle.read(8) != b"PBDEMS2\x00":
            raise ValueError("Not a Source 2 .dem replay (missing PBDEMS2 header)")
        handle.seek(0)
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    sha = digest.hexdigest()
    source_players = list(getattr(match, "players", []))
    players = [{"slot": p.player_id, "team": p.team, "hero": p.hero_name,
                "account_id": str(p.account_id) if p.account_id else None,
                "name": p.player_name, "role": None, "role_source": None}
               for p in source_players if p.team in (2, 3) and p.player_id is not None]
    positions: dict[str, list[dict[str, Any]]] = {}
    economy: dict[str, list[dict[str, Any]]] = {}
    for player in source_players:
        slot = str(player.player_id)
        point_rows = []
        for tick, x, y in getattr(player, "position_log", []):
            second = _time(match, tick)
            if second is not None and x is not None and y is not None:
                point_rows.append({"time_s": second, "tick": tick, "x": x, "y": y})
        positions[slot] = _dedupe_samples(point_rows)
        curve_rows = []
        times = getattr(player, "times_min", [])
        game_times = getattr(player, "game_times_min", [])
        for index, tick in enumerate(times):
            second = game_times[index] if index < len(game_times) else _time(match, tick)
            if second is None:
                continue
            def at(name: str) -> Any:
                series = getattr(player, name, [])
                return series[index] if index < len(series) else None
            curve_rows.append({"time_s": int(second), "net_worth": at("net_worth_t_min"),
                               "gold": at("gold_t_min"), "xp": at("total_earned_xp_t_min"),
                               "last_hits": at("lh_t_min")})
        economy[slot] = _dedupe_samples(curve_rows)
    _infer_roles(players, source_players, economy)
    end_tick = getattr(match, "post_game_tick", None) or getattr(match, "game_end_tick", None)
    duration = _time(match, end_tick) or int(getattr(match, "duration", 0))
    wards = []
    for ward in getattr(match, "wards", []):
        start = _time(match, ward.tick)
        if start is None:
            continue
        ends = [(ward.killed_tick, "killed"), (ward.expires_tick, "expired")]
        ends = [(tick, reason) for tick, reason in ends if tick is not None and (end_tick is None or tick <= end_tick)]
        end, reason = min(ends, default=(None, "recording_end"), key=lambda pair: pair[0] or 0)
        wards.append({"time_s": start, "end_s": _time(match, end), "team": ward.team,
                      "kind": ward.ward_type, "x": ward.x, "y": ward.y,
                      "placer_slot": ward.player_id if ward.player_id >= 0 else None,
                      "end_reason": reason, "evidence": "replay_entity", "raw": _raw(ward)})
    events = []
    for player in source_players:
        for rune in getattr(player, "runes_log", []):
            tick = getattr(rune, "tick", None)
            second = getattr(rune, "game_time_s", None)
            if second is None:
                second = _time(match, tick)
            if second is None:
                continue
            kind = RUNE_NAMES.get(getattr(rune, "rune_type", None), "unknown")
            raw_rune = _raw(rune)
            raw_rune["rune_action"] = "picked_up"
            events.append({"time_s": int(second), "type": "rune", "subtype": kind,
                           "team": player.team, "slot": player.player_id,
                           "x": getattr(rune, "location_x", None), "y": getattr(rune, "location_y", None),
                           "description": f"{player.hero_name} 拾取 {kind} 神符",
                           "evidence": "combat_log", "raw": raw_rune})
    # gem 0.13 adds rune entities with spawn/pickup coordinates. Pair them to
    # combat-log pickups; a Bottle pickup may have no combat-log counterpart.
    for rune in getattr(match, "runes", []):
        if rune.end_tick is None or rune.outcome not in ("picked_up", "bottled", "denied"):
            continue
        second = _time(match, rune.end_tick)
        if second is None:
            continue
        kind = RUNE_NAMES.get(rune.rune_type, "unknown")
        candidates = [event for event in events if event["type"] == "rune" and
                      event["subtype"] == kind and event["slot"] == rune.player_id and
                      abs(event["time_s"] - second) <= 3 and event.get("x") is None]
        if candidates and rune.outcome == "picked_up":
            event = min(candidates, key=lambda e: abs(e["time_s"] - second))
            event["x"], event["y"] = rune.x, rune.y
            event["evidence"] = "rune_entity_and_combat_log"
            event["raw"]["rune_entity"] = _raw(rune)
            continue
        if rune.outcome == "denied":
            event_type, action_label = "rune_deny", "摧毁"
        elif rune.outcome == "bottled":
            event_type, action_label = "rune", "装瓶"
        else:
            event_type, action_label = "rune", "拾取"
        events.append({"time_s": second, "type": event_type, "subtype": kind,
                       "team": next((p.team for p in source_players if p.player_id == rune.player_id), None),
                       "slot": rune.player_id, "x": rune.x, "y": rune.y,
                       "description": f"{kind} 神符{action_label}", "evidence": "rune_entity_and_chat",
                       "raw": {**_raw(rune), "rune_action": rune.outcome}})
    for smoke in getattr(match, "smoke_events", []):
        second = getattr(smoke, "activation_game_time_s", None)
        if second is None:
            second = _time(match, smoke.tick)
        if second is None:
            continue
        members = [participant.player_id for participant in getattr(smoke, "participants", [])
                   if participant.player_id is not None]
        events.append({"time_s": int(second), "type": "smoke", "subtype": "activation",
                       "team": smoke.team, "slot": None,
                       "x": smoke.activation_x if smoke.activation_x is not None else smoke.x,
                       "y": smoke.activation_y if smoke.activation_y is not None else smoke.y,
                       "description": f"{smoke.activator} 开雾，记录到 {len(members)} 名参与者",
                       "members": members, "evidence": "item_and_modifier", "raw": _raw(smoke)})
    for entry in getattr(match, "combat_log", []):
        kind = str(getattr(entry.log_type, "name", entry.log_type)).upper()
        if kind != "DEATH" or not getattr(entry, "target_is_hero", False):
            continue
        second = entry.game_time_s if entry.game_time_s is not None else _time(match, entry.tick)
        if second is None:
            continue
        events.append({"time_s": int(second), "type": "kill", "subtype": "hero_death",
                       "team": entry.attacker_team, "slot": None,
                       "x": entry.location_x, "y": entry.location_y,
                       "description": f"{entry.attacker_name} 击杀 {entry.target_name}",
                       "evidence": "combat_log", "raw": _raw(entry)})
    for obj in getattr(match, "objectives", []):
        tick = _value(obj, "tick")
        second = _value(obj, "time")
        if second is None:
            second = _time(match, tick)
        if second is None:
            continue
        subtype = str(_value(obj, "type", "unknown"))
        if subtype == "CHAT_MESSAGE_FIRSTBLOOD":
            continue
        description = str(_value(obj, "key", subtype))
        if subtype == "CHAT_MESSAGE_COURIER_LOST":
            description = "信使被击杀"
        events.append({"time_s": int(second), "type": "objective", "subtype": str(_value(obj, "type", "unknown")),
                       "team": _value(obj, "team"), "slot": _value(obj, "player_slot"),
                       "x": _value(obj, "x"), "y": _value(obj, "y"),
                       "description": description,
                       "evidence": "replay_objective", "raw": _raw(obj)})
    for tower in getattr(match, "towers", []):
        second = _time(match, tower.tick)
        if second is None:
            continue
        team_name = "天辉" if tower.team == 2 else "夜魇"
        suffix = tower.tower_name.removeprefix("npc_dota_goodguys_").removeprefix("npc_dota_badguys_").removeprefix("tower")
        lane = next((label for key, label in (("top", "上路"), ("mid", "中路"), ("bot", "下路")) if key in suffix), "基地")
        tier = next((digit for digit in "1234" if f"tower{digit}" in tower.tower_name), "")
        events.append({"time_s": int(second), "type": "tower", "subtype": tower.tower_name,
                       "team": tower.team if tower.team in (2, 3) else None, "slot": None,
                       "x": None, "y": None, "description": f"{team_name}{lane}{tier}塔被摧毁",
                       "evidence": "combat_log_tower_kill", "raw": _raw(tower)})
    draft = []
    from gem.constants import hero_meta
    for row in getattr(match, "draft", []):
        meta = hero_meta(row.hero_name) if row.hero_name else {}
        draft.append({"team": row.team if row.team in (2, 3) else None,
                      "action": "pick" if row.is_pick else "ban", "hero": row.hero_name,
                      "hero_id": meta.get("id"), "game_time_s": _time(match, row.tick),
                      "raw": {**_raw(row), "opendota_hero_id": meta.get("id")}})
    resolve_draft_teams(draft, players, getattr(match, "game_mode", None))
    observations = [{"type": "lane", "time_s": None, "end_s": None, "team": None,
                     "evidence": "insufficient", "details": {"reason": "no creep entity position series"}},
                    {"type": "pull", "time_s": None, "end_s": None, "team": None,
                     "evidence": "insufficient", "details": {"reason": "no creep-neutral encounter series"}}]
    return {"id": sha[:16], "sha256": sha, "filename": path.name,
            "match_id": str(match.match_id) if match.match_id else None,
            "patch": None, "patch_verified": False, "duration_s": max(0, duration),
            "radiant_name": getattr(match, "radiant_team_name", "") or "天辉",
            "dire_name": getattr(match, "dire_team_name", "") or "夜魇",
            "radiant_win": None if match.radiant_win is None else int(match.radiant_win),
            "parse_status": "partial" if getattr(match, "parse_error", None) else "complete",
            "metadata": {"parser": "gem-dota", "parse_error": str(getattr(match, "parse_error", "")) or None,
                         "truncated_at_tick": getattr(match, "truncated_at_tick", None),
                         "pauses": [_raw(p) for p in getattr(getattr(match, "game_clock", None), "pauses", [])],
                         "leagueid": getattr(match, "leagueid", 0),
            "game_mode": getattr(match, "game_mode", None), "league_name": None,
                         "map_feature_coverage": "unknown", "tower_identity_coverage": "unknown", "ability_event_coverage": {"glyph": "unknown", "scan": "unknown"}},
            "players": players, "positions": positions, "economy": economy,
            "wards": wards, "events": sorted(events, key=lambda row: row["time_s"]),
            "map_features": [], "draft": draft, "observations": observations}


def parse_replay(path: str | Path) -> dict[str, Any]:
    try:
        import gem
    except ImportError as exc:
        raise RuntimeError("gem-dota is required. Install requirements.txt in the active Python environment.") from exc
    match = gem.parse(str(path))
    data = normalize_match(match, path)
    if not allowed_match(data):
        raise ValueError("比赛不在指定战队范围：OG、Yandex、PARI、Spirit、XG、1win、Liquid、LGD、Tidebound")
    try:
        from .entities import normalize_map_features, observations_from_creeps, track_creeps
        inventory_samples: list[dict[str, Any]] = []
        samples, error, replay_features, ability_events = track_creeps(
            str(path), duration_s=data["duration_s"] or None, game_clock=getattr(match, "game_clock", None),
            inventory_output=inventory_samples)
        data["inventory_snapshots"] = inventory_samples
        data["metadata"]["inventory_coverage"] = "complete" if inventory_samples and error is None else "partial" if inventory_samples else "unknown"
        data["creeps"] = samples
        # Save entity landmarks before deriving lane/camp observations: an
        # observation-analysis error must never discard tower/Roshan positions.
        data["map_features"] = normalize_map_features(replay_features)
        data["events"].extend(ability_events)
        data["events"].sort(key=lambda row: row["time_s"])
        complete = error is None and not getattr(match, "parse_error", None) and getattr(match, "truncated_at_tick", None) is None
        replay_features = [feature for feature in data["map_features"] if feature["kind"] not in ("lotus_pool", "neutral_camp")]
        feature_counts = {kind: sum(feature["kind"] == kind for feature in replay_features) for kind in ("tower", "shop")}
        feature_coverage = "complete" if complete and feature_counts == {"tower": 22, "shop": 4} else "partial" if replay_features else "unknown"
        coverage = "complete" if complete else "partial"
        data["metadata"]["map_feature_coverage"] = feature_coverage
        landmark_counts = {kind: sum(feature["kind"] == kind for feature in data["map_features"])
                           for kind in ("lotus_pool", "neutral_camp")}
        data["metadata"]["map_objective_coverage"] = (
            "complete" if complete and landmark_counts == {"lotus_pool": 2, "neutral_camp": 28}
            else "partial" if any(landmark_counts.values()) else "unknown")
        data["metadata"]["creep_coverage"] = "complete" if complete and samples else "partial" if samples else "unknown"
        named_towers = sum(feature["kind"] == "tower" and bool(feature.get("name")) for feature in replay_features)
        data["metadata"]["tower_identity_coverage"] = "complete" if complete and named_towers == 22 else "partial" if replay_features else "unknown"
        data["metadata"]["ability_event_coverage"] = {"glyph": coverage, "scan": coverage}
        data["metadata"]["creep_parse_error"] = error
        try:
            data["observations"] = observations_from_creeps(samples)
            data["metadata"]["observation_parse_error"] = None
        except Exception as exc:
            data["metadata"]["observation_parse_error"] = str(exc)
            data["observations"] = [{"type": kind, "time_s": None, "end_s": None, "team": None,
                                     "evidence": "insufficient", "details": {"reason": "observation analysis failed"}}
                                    for kind in ("lane", "pull")]
    except Exception as exc:
        data["creeps"] = []
        data["map_features"] = []
        data["metadata"]["map_feature_coverage"] = "unknown"
        data["metadata"]["map_objective_coverage"] = "unknown"
        data["metadata"]["creep_coverage"] = "unknown"
        data["metadata"]["tower_identity_coverage"] = "unknown"
        data["metadata"]["ability_event_coverage"] = {"glyph": "unknown", "scan": "unknown"}
        data["metadata"]["creep_parse_error"] = str(exc)
    return data


def import_managed_replay(path: str | Path) -> dict[str, Any]:
    """Parse a replay and retain its files in a named per-match folder."""
    source = Path(path).resolve()
    data = parse_replay(source)
    league_id = data["metadata"].get("leagueid")
    if league_id:
        from gem.constants import league_name
        data["metadata"]["league_name"] = league_name(league_id)
    details = None
    if data.get("match_id"):
        try:
            request = urllib.request.Request(
                f"https://api.opendota.com/api/matches/{data['match_id']}",
                headers={"User-Agent": "DotaReview/0.1", "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                details = json.load(response)
            if isinstance(details.get("start_time"), int):
                data["metadata"]["start_time"] = details["start_time"]
                data["metadata"]["start_time_source"] = "OpenDota"
            event = details.get("league") or {}
            if event.get("name"):
                data["metadata"]["league_name"] = event["name"]
            data["metadata"]["opendota_patch_id"] = details.get("patch")
        except Exception as exc:
            data["metadata"]["metadata_lookup_error"] = str(exc)
    destination_dir = match_dir(data)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / "replay.dem"
    if source != destination.resolve() and not destination.exists():
        shutil.copy2(source, destination)
    if details is not None:
        (destination_dir / "opendota.json").write_text(json.dumps(details, ensure_ascii=False), encoding="utf-8")
    data["metadata"]["folder_path"] = str(destination_dir)
    data["metadata"]["replay_path"] = str(destination)
    return data
