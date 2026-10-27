"""Best-effort second pass for lane and neutral creep position evidence.

This uses gem's documented ReplayParser callback and public Entity accessors.
The entity class names and cell fields are replay-schema dependent; missing data
is returned as a gap, never filled with guessed positions.
"""

from __future__ import annotations

from collections import defaultdict
from math import hypot
from statistics import median
from typing import Any


def static_map_features() -> list[dict[str, Any]]:
    """Return 7.41 map landmarks from gem's bundled coordinate catalog."""
    from gem.catalog.map import load_map_constants, load_neutral_camps

    constants = load_map_constants()
    features = []
    for key, name in (("top_lotus", "上方莲花池"), ("bottom_lotus", "下方莲花池")):
        point = constants.get("regions", {}).get("lotus_pools", {}).get(key)
        if point and point.get("x") is not None and point.get("y") is not None:
            features.append({"id": f"lotus:{key}", "kind": "lotus_pool", "team": None,
                             "evidence": "gem_map_catalog_7_41",
                             "name": name, "x": point["x"], "y": point["y"]})
    for camp in load_neutral_camps():
        features.append({"id": f"camp:{camp['id']}", "kind": "neutral_camp", "team": None,
                         "evidence": "gem_map_catalog_7_41",
                         "name": camp["type"], "x": camp["x"], "y": camp["y"]})
    # Twin Gate positions are stable map landmarks. These coordinates were
    # read from the 7.41 replay entity and retained as the local map reference.
    for key, name, x, y in (
        ("northwest", "西北双生传送门", 9926.0, 23983.0),
        ("southeast", "东南双生传送门", 22810.0, 9070.0),
    ):
        features.append({"id": f"twin_gate:{key}", "kind": "twin_gate", "team": None,
                         "evidence": "replay_map_reference_7_41", "name": name, "x": x, "y": y})
    return features


def normalize_map_features(replay_features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge replay landmarks with catalog markers and collapse entity aliases."""
    dynamic: list[dict[str, Any]] = []
    for source in replay_features:
        feature = dict(source)
        # Lotus pools are catalog landmarks; replay entities are transient and
        # commonly duplicate the two stable map positions.
        if feature.get("kind") == "lotus_pool":
            continue
        if feature.get("kind") == "twin_gate":
            continue
        name = str(feature.get("name") or "").lower()
        if feature.get("kind") == "tower" and "watch_tower" in name:
            feature["kind"] = "watch_tower"
        if feature.get("kind") == "tormentor":
            if name.startswith("npc_dota_hero_"):
                continue
            feature["name"] = "npc_dota_miniboss"
        dynamic.append(feature)

    # Repeated entity serials can sample different points inside one pit.
    # Use the cluster median so the marker sits at the pit center instead of
    # whichever entity update happened to arrive first.
    collapsed: list[dict[str, Any]] = []
    for feature in dynamic:
        if feature.get("kind") in ("roshan", "tormentor"):
            nearby = next((prior for prior in collapsed
                           if prior.get("kind") == feature["kind"] and
                           hypot(float(prior["x"]) - float(feature["x"]),
                                 float(prior["y"]) - float(feature["y"])) <= 250), None)
            if nearby is None:
                feature["_positions"] = [(float(feature["x"]), float(feature["y"]))]
                collapsed.append(feature)
            else:
                nearby["_positions"].append((float(feature["x"]), float(feature["y"])))
        else:
            collapsed.append(feature)
    for feature in collapsed:
        points = feature.pop("_positions", None)
        if points:
            feature["x"] = median(x for x, _ in points)
            feature["y"] = median(y for _, y in points)
    return static_map_features() + collapsed


def track_creeps(path: str, sample_step_s: int = 2, duration_s: int | None = None, game_clock: Any = None) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    from gem.parser import ReplayParser
    from gem.state.entities import EntityOp

    parser = ReplayParser(path)
    active: dict[tuple[int, int], tuple[Any, str]] = {}
    samples: list[dict[str, Any]] = []
    features: dict[tuple[int, int], dict[str, Any]] = {}
    usage_events: list[dict[str, Any]] = []
    last_second: int | None = None

    def on_entity(entity: Any, operation: EntityOp) -> None:
        identity = (entity.get_index(), entity.get_serial())
        class_name = entity.get_class_name()
        identity_text = class_name.lower()
        feature_kind = None
        if "twin_gate" in identity_text or "twin gate" in identity_text:
            feature_kind = "twin_gate"
        elif "watch_tower" in identity_text:
            feature_kind = "watch_tower"
        elif "fort" in identity_text or "ancient" in identity_text:
            feature_kind = "ancient"
        elif "tower" in identity_text:
            feature_kind = "tower"
        elif "barracks" in identity_text or "rax" in identity_text:
            feature_kind = "barracks"
        elif "building" in identity_text:
            # Some Source 2 building classes are generic; resolve their unit
            # names after replay parsing and retain only map structures.
            feature_kind = "building_candidate"
        elif "shop" in identity_text:
            feature_kind = "shop"
        elif "tormentor" in identity_text or "miniboss" in identity_text:
            feature_kind = "tormentor"
        elif "roshan" in identity_text and "banner" not in identity_text:
            feature_kind = "roshan"
        elif "lotus" in identity_text and ("pool" in identity_text or "fountain" in identity_text):
            feature_kind = "lotus_pool"
        if feature_kind:
            cell_x = entity.get_uint32("CBodyComponent.m_cellX")
            cell_y = entity.get_uint32("CBodyComponent.m_cellY")
            vec_x = entity.get_float32("CBodyComponent.m_vecX")
            vec_y = entity.get_float32("CBodyComponent.m_vecY")
            if None not in (cell_x, cell_y, vec_x, vec_y):
                unit_name_id = entity.get("m_iUnitNameStringTableIndex")
                features[identity] = {"id": f"{identity[0]}:{identity[1]}",
                                      "kind": feature_kind, "team": entity.get_int32("m_iTeamNum"),
                                      "unit_name_id": unit_name_id, "name": None,
                                      "x": cell_x * 128 + vec_x, "y": cell_y * 128 + vec_y}
        if operation.has(EntityOp.LEFT) or operation.has(EntityOp.DELETED):
            active.pop(identity, None)
            return
        class_name_lower = class_name.lower()
        if "creep" not in class_name_lower:
            return
        if "neutral" in class_name_lower:
            kind = "neutral"
        elif "lane" in class_name_lower or "siege" in class_name_lower or "ranged" in class_name_lower or "melee" in class_name_lower:
            kind = "lane"
        else:
            return
        active[identity] = (entity, kind)

    def on_tick(_: int) -> None:
        nonlocal last_second
        second = parser.game_time_s
        if second is None or second < -90 or (duration_s is not None and second > duration_s) or second == last_second or second % sample_step_s:
            return
        last_second = second
        for identity, (entity, kind) in list(active.items()):
            cell_x = entity.get_uint32("CBodyComponent.m_cellX")
            cell_y = entity.get_uint32("CBodyComponent.m_cellY")
            vec_x = entity.get_float32("CBodyComponent.m_vecX")
            vec_y = entity.get_float32("CBodyComponent.m_vecY")
            if None in (cell_x, cell_y, vec_x, vec_y):
                continue
            team = entity.get_int32("m_iTeamNum")
            if kind == "lane" and team not in (2, 3):
                continue
            samples.append({"entity_id": f"{identity[0]}:{identity[1]}", "kind": kind,
                            "team": team if team in (2, 3) else None, "time_s": int(second),
                            "x": cell_x * 128 + vec_x, "y": cell_y * 128 + vec_y})

    def on_chat_event(event: Any, tick: int) -> None:
        from gem.proto.dota_usermessages_pb2 import DOTA_CHAT_MESSAGE
        try:
            event_type = DOTA_CHAT_MESSAGE.Name(event.type)
        except ValueError:
            return
        if event_type == "CHAT_MESSAGE_GLYPH_USED":
            event_kind, team = "glyph", event.playerid_1
        elif event_type == "CHAT_MESSAGE_SCAN_USED":
            event_kind, team = "scan", event.value
        else:
            return
        second = game_clock.game_seconds_at(tick) if game_clock is not None else parser.game_time_s
        if second is None or second < -90 or (duration_s is not None and second > duration_s) or team not in (2, 3):
            return
        usage_events.append({"time_s": int(second), "type": event_kind, "subtype": "used", "team": team,
                             "evidence": "replay_chat_event", "raw": {"tick": tick, "type": event_type,
                             "playerid_1": event.playerid_1, "value": event.value}})

    parser.on_entity(on_entity)
    parser.on_tick_start(on_tick)
    parser.on_chat_event(on_chat_event)
    parser.parse()
    unit_names = parser.string_tables.get_by_name("UnitNames")
    for feature in features.values():
        unit_id = feature.pop("unit_name_id", None)
        entry = unit_names.items.get(unit_id) if unit_names and unit_id is not None else None
        if entry:
            feature["name"] = entry[0]
        if feature["kind"] == "building_candidate":
            name = str(feature.get("name") or "").lower()
            if "watch_tower" in name:
                feature["kind"] = "watch_tower"
            elif "tower" in name:
                feature["kind"] = "tower"
            elif "rax" in name or "barracks" in name:
                feature["kind"] = "barracks"
            elif "fort" in name or "ancient" in name:
                feature["kind"] = "ancient"
            elif "shop" in name:
                feature["kind"] = "shop"
            else:
                feature["kind"] = "unmapped_building"
    features = {identity: feature for identity, feature in features.items()
                if feature.get("kind") != "unmapped_building"}
    return samples, parser.parse_error, list(features.values()), usage_events


def observations_from_creeps(samples: list[dict[str, Any]], sample_step_s: int = 2,
                             pull_radius: float = 450, camp_radius: float = 900,
                             pull_min_seconds: int = 4) -> list[dict[str, Any]]:
    if not samples:
        return [{"type": "lane", "time_s": None, "end_s": None, "team": None,
                 "evidence": "insufficient", "details": {"reason": "no creep entity positions"}},
                {"type": "pull", "time_s": None, "end_s": None, "team": None,
                 "evidence": "insufficient", "details": {"reason": "no creep-neutral encounter positions"}}]
    from gem.catalog.map import load_neutral_camps
    from gem.extractors.lane import lane_for_cell

    by_time: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in samples:
        by_time[row["time_s"]].append(row)
    lanes: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in samples:
        if row["kind"] != "lane" or row["team"] not in (2, 3):
            continue
        lane = lane_for_cell(round(row["x"] / 128), round(row["y"] / 128))
        if lane in (1, 2, 3):
            lanes[(row["time_s"] // 20 * 20, row["team"], lane)].append(row)
    result = []
    for (time_s, team, lane), rows in sorted(lanes.items()):
        if len({r["entity_id"] for r in rows}) < 2:
            continue
        result.append({"type": "lane", "time_s": time_s, "end_s": time_s + 20,
                       "team": team, "evidence": "creep_entity_samples",
                       "details": {"lane": lane, "x": median(r["x"] for r in rows),
                                   "y": median(r["y"] for r in rows), "sample_count": len(rows)}})
    camps = load_neutral_camps()
    camp_by_id = {camp["id"]: camp for camp in camps}
    hits: dict[tuple[int, str, int], list[int]] = defaultdict(list)
    for second, rows in by_time.items():
        if second < 0 or second > 900:
            continue
        neutrals = [r for r in rows if r["kind"] == "neutral"]
        for lane_creep in (r for r in rows if r["kind"] == "lane"):
            nearby_camps = [c for c in camps if hypot(lane_creep["x"]-c["x"], lane_creep["y"]-c["y"]) <= camp_radius]
            if not nearby_camps:
                continue
            if not any(hypot(lane_creep["x"]-n["x"], lane_creep["y"]-n["y"]) <= pull_radius for n in neutrals):
                continue
            camp = min(nearby_camps, key=lambda c: hypot(lane_creep["x"]-c["x"], lane_creep["y"]-c["y"]))
            hits[(lane_creep["team"], lane_creep["entity_id"], camp["id"])].append(second)
    pull_fragments = []
    for (team, entity_id, camp_id), times in hits.items():
        times = sorted(set(times))
        start = prev = times[0]
        for second in times[1:] + [times[-1] + sample_step_s * 3]:
            if second - prev <= sample_step_s * 2:
                prev = second
                continue
            if prev - start + sample_step_s >= pull_min_seconds:
                pull_fragments.append({"team": team, "camp_id": camp_id, "time_s": start,
                                       "end_s": prev + sample_step_s, "entity_id": entity_id,
                                       "seconds": [t for t in times if start <= t <= prev]})
            start = prev = second
    merged: list[dict[str, Any]] = []
    for fragment in sorted(pull_fragments, key=lambda x: (x["team"], x["camp_id"], x["time_s"])):
        if (merged and merged[-1]["team"] == fragment["team"] and
                merged[-1]["camp_id"] == fragment["camp_id"] and
                fragment["time_s"] <= merged[-1]["end_s"] + sample_step_s * 2):
            merged[-1]["end_s"] = max(merged[-1]["end_s"], fragment["end_s"])
            merged[-1]["entities"].add(fragment["entity_id"])
            merged[-1]["seconds"].update(fragment["seconds"])
        else:
            merged.append({"team": fragment["team"], "camp_id": fragment["camp_id"],
                           "time_s": fragment["time_s"], "end_s": fragment["end_s"],
                           "entities": {fragment["entity_id"]}, "seconds": set(fragment["seconds"])})
    for item in merged:
        contested = any(other is not item and other["camp_id"] == item["camp_id"] and
                        other["team"] != item["team"] and
                        other["time_s"] < item["end_s"] and item["time_s"] < other["end_s"]
                        for other in merged)
        result.append({"type": "pull", "time_s": item["time_s"], "end_s": item["end_s"],
                       "team": item["team"],
                       "evidence": "ambiguous_contested_camp" if contested else "candidate_creep_neutral_proximity",
                       "details": {"camp_id": item["camp_id"],
                                   "x": camp_by_id[item["camp_id"]]["x"],
                                   "y": camp_by_id[item["camp_id"]]["y"],
                                   "lane_creep_entities": sorted(item["entities"]),
                                   "sample_seconds": sorted(item["seconds"]),
                                   "rule": {"pull_radius": pull_radius, "camp_radius": camp_radius}}})

    # Camp-state records describe sampled evidence only. A missing neutral can
    # also mean the replay did not expose that entity, so these remain candidates.
    neutral_by_camp: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for second, rows in by_time.items():
        neutrals = [r for r in rows if r["kind"] == "neutral"]
        for camp in camps:
            nearby = [r for r in neutrals if hypot(r["x"] - camp["x"], r["y"] - camp["y"]) <= camp_radius]
            if nearby:
                neutral_by_camp[camp["id"]][second].update(r["entity_id"] for r in nearby)
    for camp in camps:
        timeline = neutral_by_camp[camp["id"]]
        if not timeline:
            continue
        observed_times = sorted(by_time)
        counts = {second: len(timeline.get(second, set())) for second in observed_times}
        # Empty intervals require two consecutive samples to avoid treating one
        # missed entity update as a clear.
        for index in range(1, len(observed_times) - 1):
            second = observed_times[index]
            previous, following = observed_times[index - 1], observed_times[index + 1]
            if (second - previous <= sample_step_s * 2 and following - second <= sample_step_s * 2
                    and counts[previous] > 0 and counts[second] == 0 and counts[following] == 0):
                result.append({"type": "camp_clear_candidate", "time_s": second,
                               "end_s": following, "team": None,
                               "evidence": "neutral_entity_absence_after_presence",
                               "details": {"camp_id": camp["id"], "x": camp["x"], "y": camp["y"],
                                           "previous_neutral_count": counts[previous],
                                           "empty_samples": 2, "classification": "empty_observed_candidate"}})
        # A stack candidate is a count increase across a minute spawn boundary
        # while the previous wave was still observed immediately before it.
        for second in observed_times:
            if second <= 0 or second % 60 or second not in timeline:
                continue
            before = [t for t in observed_times if second - sample_step_s * 2 <= t < second]
            after = [t for t in observed_times if second <= t <= second + sample_step_s * 2]
            if not before or not after:
                continue
            before_count = max(counts[t] for t in before)
            after_count = max(counts[t] for t in after)
            if before_count > 0 and after_count > before_count:
                result.append({"type": "camp_stack_candidate", "time_s": second,
                               "end_s": second + sample_step_s * 2, "team": None,
                               "evidence": "neutral_entity_count_increase_at_spawn_boundary",
                               "details": {"camp_id": camp["id"], "x": camp["x"], "y": camp["y"],
                                           "count_before": before_count, "count_after": after_count,
                                           "classification": "stack_candidate"}})
    if not any(r["type"] == "lane" for r in result):
        result.append({"type": "lane", "time_s": None, "end_s": None, "team": None,
                       "evidence": "insufficient", "details": {"reason": "too few lane creep samples"}})
    if not any(r["type"] == "pull" for r in result):
        result.append({"type": "pull", "time_s": None, "end_s": None, "team": None,
                       "evidence": "insufficient", "details": {"reason": "no qualifying creep-neutral proximity"}})
    return result
