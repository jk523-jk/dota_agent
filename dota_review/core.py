"""Small, dependency-free review computations on normalized replay records."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from math import hypot
from typing import Any


@dataclass(frozen=True)
class Thresholds:
    together_radius: float = 1200.0
    together_min_seconds: int = 8
    position_max_age: int = 4
    pull_encounter_radius: float = 450.0
    pull_camp_radius: float = 900.0
    pull_min_seconds: int = 4


def clock_label(seconds: float | int | None) -> str:
    if seconds is None:
        return "?"
    sign = "-" if seconds < 0 else ""
    whole = abs(int(seconds))
    return f"{sign}{whole // 60:02d}:{whole % 60:02d}"


def position_at(samples: list[dict[str, Any]], second: int, max_age: int = 4) -> dict[str, Any] | None:
    """Last known sample, refusing stale positions or future interpolation."""
    if not samples:
        return None
    times = [int(p["time_s"]) for p in samples]
    index = bisect_right(times, second) - 1
    if index < 0 or second - times[index] > max_age:
        return None
    return samples[index]


def active_wards(wards: list[dict[str, Any]], second: int) -> list[dict[str, Any]]:
    return [w for w in wards if w["time_s"] <= second and (w.get("end_s") is None or second < w["end_s"])]


def companionship(players: list[dict[str, Any]], positions: dict[str, list[dict[str, Any]]],
                  duration: int, thresholds: Thresholds = Thresholds()) -> list[dict[str, Any]]:
    """Find contiguous 2+4, 2+5, and 2+4+5 proximity intervals per team."""
    groups = ((2, 4), (2, 5), (2, 4, 5))
    result: list[dict[str, Any]] = []
    # Expand once per player; rebuilding a bisect key list per second is quadratic.
    duration = max(0, duration)
    sampled: dict[str, list[dict[str, Any] | None]] = {}
    for player in players:
        slot = str(player["slot"])
        rows = positions.get(slot, [])
        index = 0
        current = None
        timeline = []
        for second in range(duration + 1):
            while index < len(rows) and rows[index]["time_s"] <= second:
                current = rows[index]
                index += 1
            timeline.append(current if current and second - current["time_s"] <= thresholds.position_max_age else None)
        sampled[slot] = timeline
    for team in (2, 3):
        by_role = {p.get("role"): p for p in players if p.get("team") == team}
        for roles in groups:
            members = [by_role.get(role) for role in roles]
            if any(member is None for member in members):
                continue
            start: int | None = None
            for second in range(duration + 1):
                points = [sampled[str(p["slot"])][second] for p in members]
                valid = all(point is not None for point in points)
                together = valid and all(
                    hypot(points[i]["x"] - points[j]["x"], points[i]["y"] - points[j]["y"])
                    <= thresholds.together_radius
                    for i in range(len(points)) for j in range(i + 1, len(points))
                )
                if together and start is None:
                    start = second
                if not together and start is not None:
                    if second - start >= thresholds.together_min_seconds:
                        result.append({"team": team, "roles": "+".join(map(str, roles)),
                                       "time_s": start, "end_s": second,
                                       "duration_s": second - start,
                                       "evidence": "sampled_positions"})
                    start = None
            if start is not None and duration + 1 - start >= thresholds.together_min_seconds:
                result.append({"team": team, "roles": "+".join(map(str, roles)),
                               "time_s": start, "end_s": duration + 1,
                               "duration_s": duration + 1 - start,
                               "evidence": "sampled_positions"})
    return result


def rune_windows(events: list[dict[str, Any]], positions: dict[str, list[dict[str, Any]]],
                 players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize observable attendance and pickup without fabricating spawn coordinates."""
    windows = [("开局首次赏金", -60, 30, "bounty"),
               ("6:00 强化神符", 330, 390, "power"),
               ("7:00 经验神符", 390, 450, "wisdom")]
    power_kinds = {"double_damage", "haste", "illusion", "invisibility", "regeneration", "arcane", "shield"}
    result = []
    for label, start, end, kind in windows:
        pickups = [e for e in events if e.get("type") == "rune" and start <= e["time_s"] <= end
                   and e.get("raw", {}).get("rune_action") != "denied"
                   and (str(e.get("subtype", "")).lower() == kind or
                        (kind == "power" and str(e.get("subtype", "")).lower() in power_kinds))]
        samples = []
        for p in players:
            for sample in positions.get(str(p["slot"]), []):
                if start <= sample["time_s"] <= end:
                    samples.append({"slot": p["slot"], "team": p["team"], **sample})
        attendance = []
        for pickup in pickups:
            if pickup.get("x") is None or pickup.get("y") is None:
                continue
            for player in players:
                track = positions.get(str(player["slot"]), [])
                for offset in (-15, -5, 0):
                    sample = position_at(track, pickup["time_s"] + offset)
                    if sample and hypot(sample["x"] - pickup["x"], sample["y"] - pickup["y"]) <= 1200:
                        attendance.append({"pickup_time_s": pickup["time_s"], "slot": player["slot"],
                                           "team": player["team"], "arrived_by_s": sample["time_s"],
                                           "distance": round(hypot(sample["x"] - pickup["x"], sample["y"] - pickup["y"]))})
                        break
        followups = [e for e in events if e.get("type") in ("kill", "objective") and
                     any(p["time_s"] < e["time_s"] <= p["time_s"] + 90 for p in pickups)]
        result.append({"label": label, "start_s": start, "end_s": end,
                       "pickups": pickups, "positions": samples, "attendance": attendance,
                       "followups": followups,
                       "evidence": "confirmed_pickup" if pickups else "insufficient"})
    return result


def smoke_followups(smokes: list[dict[str, Any]], events: list[dict[str, Any]],
                    window_s: int = 90) -> list[dict[str, Any]]:
    result = []
    for smoke in smokes:
        start = smoke["time_s"]
        nearby = [event for event in events if start <= event["time_s"] <= start + window_s
                  and event.get("type") in ("kill", "objective")]
        members = smoke.get("members") or [p.get("player_id") for p in smoke.get("raw", {}).get("participants", [])
                                          if p.get("player_id") is not None]
        result.append({**smoke, "followups": nearby,
                       "preparation": "有开雾时间与参与者" if members else "参与者证据不足",
                       "movement": "可按时间轴核查轨迹" if smoke.get("x") is not None else "位置证据不足",
                       "outcome": f"窗口内 {len(nearby)} 项后续事件（非因果归属）"})
    return result


def economy_at(series: list[dict[str, Any]], second: int) -> dict[str, Any] | None:
    return position_at(series, second, 90)
