"""Deterministic replay analysis that works without LangGraph or an LLM."""
from __future__ import annotations

import re
import uuid
from typing import Any

from .agent_analytics import player_analysis, team_style, ward_hotspots
from .vision_knowledge import retrieve_vision_knowledge

_MODES = {"player", "ward_hotspots", "team_style"}


def classify_request(message: str, requested_mode: str | None = None,
                     selected_slot: int | None = None) -> str:
    if requested_mode in _MODES:
        return requested_mode
    text = message.casefold()
    if re.search(r"眼位|守卫|视野|热区|ward|vision|hotspot", text):
        return "ward_hotspots"
    if re.search(r"战队|团队|风格|打法|习惯|team|style", text):
        return "team_style"
    if re.search(r"选手|玩家|个人|表现|补刀|经济|装备|player|performance|farm", text):
        return "player"
    return "player" if selected_slot is not None else "help"


def fallback_answer(intent: str, analysis: dict[str, Any]) -> str:
    if analysis.get("kind") == "error":
        return str(analysis.get("message"))
    if intent == "help":
        return "我可以分析选手经济与补刀、绘制眼位热区，或概括战队跨场打法。点击右侧快捷分析，或先选择一名英雄后提问。"
    if intent == "player":
        player = analysis.get("player", {})
        name = player.get("player_name") or player.get("hero") or f"位置 {player.get('slot')}"
        rows = analysis.get("checkpoints", [])
        details = "；".join(
            f"{row['target_time_s'] // 60} 分钟：经济 {row['net_worth']:,}，补刀 {row['last_hits']}，"
            f"队内经济第 {row['team_net_worth_rank']}/{row['team_size']}"
            for row in rows)
        if not details:
            details = "这场比赛没有可用的经济快照。"
        text = f"{name}（{player.get('hero') or '英雄未知'}）的录像数据：{details}。"
        text += f"记录到个人放眼 {analysis.get('observer_wards', 0)} 次、岗哨 {analysis.get('sentry_wards', 0)} 次。"
        return text + "当前数据缺少可靠的个人击杀/死亡 slot 关联，因此不估算 K/D/A。"
    if intent == "ward_hotspots":
        counts = analysis.get("by_team_and_kind", {})
        top = analysis.get("hotspots", [])[:4]
        spots = "；".join(
            f"{'天辉' if row['team'] == 2 else '夜魇' if row['team'] == 3 else '未知队伍'}"
            f"({row['x']}, {row['y']}) {row['placements']} 次"
            for row in top)
        return (f"共找到 {analysis.get('placement_count', 0)} 个带坐标的眼位记录。"
                f"天辉 {counts.get('radiant', {})}，夜魇 {counts.get('dire', {})}。"
                f"高频位置：{spots or '没有可聚合的热点'}。热区只表示重复放置坐标，不等同于实际视野。"
                + (" 知识库：" + " ".join(item["text"] + " 来源：" + item["source"] for item in analysis.get("knowledge", []))))
    if intent == "team_style":
        metrics = analysis.get("metrics", {})
        sample = metrics.get("matches", 0)
        if not sample:
            return f"没有找到 {analysis.get('team', '该队伍')} 的可分析比赛。"
        win_rate = metrics.get("win_rate")
        win_text = f"胜率 {win_rate:.0%}" if win_rate is not None else "胜负记录不可用"
        return (f"{analysis.get('team')} 当前可查 {sample} 场：{win_text}，平均时长 "
                f"{int(metrics.get('average_duration_s') or 0) // 60} 分钟；前 15 分钟平均击杀 "
                f"{metrics.get('average_early_kills_0_15m', 0)}，平均烟雾 {metrics.get('average_smokes', 0)} 次，"
                f"平均眼位 {metrics.get('average_wards', 0)} 个，平均拆塔 {metrics.get('average_towers_taken', 0)} 座。"
                f"样本等级：{metrics.get('sample_quality')}。这只是已导入比赛的描述性统计，不代表稳定风格或因果关系。")
    return "已完成分析，但目前没有可展示的结果。"


def run_evidence_only(match_key: str, message: str, *, selected_slot: int | None = None,
                      side: str | None = None, requested_mode: str | None = None) -> dict[str, Any]:
    """Return replay analytics without importing the graph, checkpointer, or an LLM."""
    intent = classify_request(message, requested_mode, selected_slot)
    if intent == "player":
        if selected_slot is None:
            analysis = {"kind": "error", "message": "请先点击一名英雄头像，再运行选手分析。", "citations": []}
        else:
            analysis = player_analysis(match_key, int(selected_slot))
    elif intent == "ward_hotspots":
        analysis = ward_hotspots(match_key)
        analysis["knowledge"] = retrieve_vision_knowledge(message or "眼位视野")
    elif intent == "team_style":
        analysis = team_style(match_key, side or "radiant")
    else:
        analysis = {"kind": "help", "citations": [], "limitations": []}
    return {"thread_id": str(uuid.uuid4()), "intent": intent,
            "answer": fallback_answer(intent, analysis), "analysis": analysis,
            "citations": analysis.get("citations", []), "model": "evidence_only"}
