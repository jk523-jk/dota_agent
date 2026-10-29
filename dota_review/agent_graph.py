"""LangGraph orchestration for evidence-backed replay questions."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from .agent_analytics import player_analysis, team_style, ward_hotspots

_ROOT = Path(__file__).resolve().parents[1]
_MODELS = {"player", "ward_hotspots", "team_style"}
_GRAPH_LOCK = threading.RLock()
_CONNECTION: sqlite3.Connection | None = None
_CHECKPOINTER: SqliteSaver | None = None
_GRAPH = None
logger = logging.getLogger(__name__)


class AgentState(MessagesState, total=False):
    match_key: str
    selected_slot: int | None
    side: Literal["radiant", "dire"] | None
    requested_mode: str | None
    intent: str
    analysis: dict[str, Any]
    answer: str
    citations: list[dict[str, Any]]
    model_error: str | None


def _settings() -> dict[str, str]:
    return {**dotenv_values(_ROOT / ".env"), **os.environ}


def _model_name() -> str:
    return str(_settings().get("DOTA_AGENT_MODEL") or "").strip()


def _checkpoint_path() -> Path:
    settings = _settings()
    value = str(settings.get("DOTA_AGENT_STATE_DB") or "dota_data/agent-state.sqlite3")
    path = Path(value)
    return path if path.is_absolute() else _ROOT / path


@lru_cache(maxsize=1)
def _chat_model():
    settings = _settings()
    model_name = str(settings.get("DOTA_AGENT_MODEL") or "").strip()
    if not model_name:
        return None
    kwargs: dict[str, Any] = {"temperature": 0.2, "timeout": 40, "max_retries": 2}
    if settings.get("DOTA_AGENT_API_KEY"):
        kwargs["api_key"] = settings["DOTA_AGENT_API_KEY"]
    if settings.get("DOTA_AGENT_BASE_URL"):
        kwargs["base_url"] = settings["DOTA_AGENT_BASE_URL"]
    return init_chat_model(model_name, **kwargs)


def agent_status() -> dict[str, Any]:
    configured = bool(_model_name())
    if not configured:
        return {"ready": True, "mode": "evidence_only", "model_configured": False,
                "message": "分析工具已就绪；配置模型后可启用自然语言综合解读。"}
    try:
        _chat_model()
        return {"ready": True, "mode": "llm", "model_configured": True,
                "model": _model_name(), "message": "模型配置已加载；若语言模型流程失败，会自动回落到录像证据分析。"}
    except Exception:
        return {"ready": True, "mode": "evidence_only", "model_configured": False,
                "message": "模型配置无法加载；确定性分析仍可使用，请检查模型标识和对应 provider 包。"}


def classify_request(message: str, requested_mode: str | None = None,
                     selected_slot: int | None = None) -> str:
    if requested_mode in _MODELS:
        return requested_mode
    text = message.casefold()
    if re.search(r"眼位|守卫|视野|热区|ward|vision|hotspot", text):
        return "ward_hotspots"
    if re.search(r"战队|团队|风格|打法|习惯|team|style", text):
        return "team_style"
    if re.search(r"选手|玩家|个人|表现|补刀|经济|装备|player|performance|farm", text):
        return "player"
    return "player" if selected_slot is not None else "help"


def _route(state: AgentState) -> dict[str, Any]:
    last_user = next((message for message in reversed(state.get("messages", []))
                      if isinstance(message, HumanMessage)), None)
    question = str(last_user.content) if last_user else ""
    return {"intent": classify_request(question, state.get("requested_mode"),
                                         state.get("selected_slot"))}


def _gather(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent", "help")
    try:
        if intent == "player":
            slot = state.get("selected_slot")
            if slot is None:
                raise LookupError("请先点击一名英雄头像，再运行选手分析。")
            analysis = player_analysis(state["match_key"], int(slot))
        elif intent == "ward_hotspots":
            analysis = ward_hotspots(state["match_key"])
        elif intent == "team_style":
            analysis = team_style(state["match_key"], state.get("side") or "radiant")
        else:
            analysis = {"kind": "help", "citations": [], "limitations": []}
        return {"analysis": analysis, "citations": analysis.get("citations", [])}
    except (LookupError, ValueError) as exc:
        return {"analysis": {"kind": "error", "message": str(exc), "citations": [], "limitations": []},
                "citations": []}


def _fallback_answer(intent: str, analysis: dict[str, Any]) -> str:
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
                f"高频位置：{spots or '没有可聚合的热点'}。热区只表示重复放置坐标，不等同于实际视野。")
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


def _respond(state: AgentState) -> dict[str, Any]:
    analysis = state.get("analysis", {})
    if analysis.get("kind") == "error" or analysis.get("kind") == "help":
        answer = _fallback_answer(state.get("intent", "help"), analysis)
        return {"answer": answer, "messages": [{"role": "assistant", "content": answer}]}
    try:
        model = _chat_model()
    except Exception:
        model = None
    if model is None:
        answer = _fallback_answer(state.get("intent", "help"), analysis)
        return {"answer": answer, "messages": [{"role": "assistant", "content": answer}]}
    system = SystemMessage(content=(
        "你是 Dota 2 录像复盘教练。只根据提供的结构化比赛证据分析，不得补造击杀、视野、因果或战术。"
        "明确区分事实和推测；样本少时降低结论强度；指出分析限制。用简体中文回答，简洁且具体。"
        "引用证据时写出比赛时间（如 10:00）或坐标；可点击的证据引用由界面单独展示。"
        "当前可用的分析数据如下：\n" + json.dumps(analysis, ensure_ascii=False, default=str)[:18000]
    ))
    try:
        messages = [system, *state.get("messages", [])[-8:]]
        response = model.invoke(messages)
        answer = str(getattr(response, "content", response)).strip()
        if not answer:
            answer = _fallback_answer(state.get("intent", "help"), analysis)
    except Exception:
        logger.exception("LLM response generation failed; returning replay evidence summary")
        answer = _fallback_answer(state.get("intent", "help"), analysis)
        return {"answer": answer, "model_error": "model_call_failed",
                "messages": [{"role": "assistant", "content": answer}]}
    return {"answer": answer, "messages": [{"role": "assistant", "content": answer}]}


def _get_graph():
    global _CONNECTION, _CHECKPOINTER, _GRAPH
    if _GRAPH is not None:
        return _GRAPH
    path = _checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _CONNECTION = sqlite3.connect(path, check_same_thread=False)
    _CHECKPOINTER = SqliteSaver(_CONNECTION)
    _CHECKPOINTER.setup()
    builder = StateGraph(AgentState)
    builder.add_node("route", _route)
    builder.add_node("gather_evidence", _gather)
    builder.add_node("answer", _respond)
    builder.add_edge(START, "route")
    builder.add_edge("route", "gather_evidence")
    builder.add_edge("gather_evidence", "answer")
    builder.add_edge("answer", END)
    _GRAPH = builder.compile(checkpointer=_CHECKPOINTER)
    return _GRAPH


def run_agent(match_key: str, message: str, *, selected_slot: int | None = None,
              side: Literal["radiant", "dire"] | None = None,
              requested_mode: str | None = None, thread_id: str | None = None) -> dict[str, Any]:
    thread_id = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    with _GRAPH_LOCK:
        result = _get_graph().invoke({
            "match_key": match_key, "selected_slot": selected_slot, "side": side,
            "requested_mode": requested_mode, "messages": [HumanMessage(content=message)],
            "analysis": {}, "citations": [], "answer": "", "model_error": None,
        }, config=config)
    response = {
        "thread_id": thread_id,
        "intent": result.get("intent", "help"),
        "answer": result.get("answer", ""),
        "analysis": result.get("analysis", {}),
        "citations": result.get("citations", []),
        "model": agent_status()["mode"],
    }
    if result.get("model_error"):
        response["notice"] = "模型请求失败，已回落到录像证据分析。"
    return response


def run_evidence_only(match_key: str, message: str, *, selected_slot: int | None = None,
                      side: Literal["radiant", "dire"] | None = None,
                      requested_mode: str | None = None) -> dict[str, Any]:
    """Return replay analytics without loading the graph/checkpointer or an LLM."""
    intent = classify_request(message, requested_mode, selected_slot)
    if intent == "player":
        if selected_slot is None:
            analysis = {"kind": "error", "message": "请先点击一名英雄头像，再运行选手分析。", "citations": []}
        else:
            analysis = player_analysis(match_key, int(selected_slot))
    elif intent == "ward_hotspots":
        analysis = ward_hotspots(match_key)
    elif intent == "team_style":
        analysis = team_style(match_key, side or "radiant")
    else:
        analysis = {"kind": "help", "citations": [], "limitations": []}
    return {"thread_id": str(uuid.uuid4()), "intent": intent,
            "answer": _fallback_answer(intent, analysis), "analysis": analysis,
            "citations": analysis.get("citations", []), "model": "evidence_only"}
