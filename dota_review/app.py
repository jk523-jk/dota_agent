"""Tkinter desktop shell for local replay review."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any
from PIL import Image, ImageTk

from .bp import draft_descriptors, fetch_pro_samples, prediction_for_match, train_model, verify_match_patch
from .core import Thresholds, active_wards, clock_label, companionship, position_at, rune_windows
from .entities import observations_from_creeps
from .export import write_csv, write_html
from .ingest import import_managed_replay
from .paths import default_db, match_dir, match_time_text, report_dir
from .patchnotes import PatchKnowledgeBase
from .storage import Store
from .teams import allowed_match
from .trends import build_trends, write_trends_csv


def day_night_at(second: int) -> tuple[str, int]:
    """Return the in-game phase and seconds until its next change."""
    if second < 0:
        return "night", -second
    phase = "day" if second % 600 < 300 else "night"
    next_change = (second // 300 + 1) * 300
    return phase, next_change - second


class ReviewApp(tk.Tk):
    def __init__(self, db_path: str | Path | None = None):
        super().__init__()
        self.title("Dota 2 录像复盘")
        self.geometry("1280x800")
        self.minsize(950, 650)
        self.store = Store(db_path or default_db())
        self.patch_kb = PatchKnowledgeBase(self.store.db)
        self.data: dict[str, Any] | None = None
        self.creep_at_time: dict[int, list[dict[str, Any]]] = {}
        self._map_image_source = Image.open(Path(__file__).parent / "assets" / "dota2_map_7_41.png").convert("RGB")
        self._map_photo: ImageTk.PhotoImage | None = None
        self._map_photo_size = 0
        self._map_photo_night: bool | None = None
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._ability_fetching: set[int] = set()
        self._ability_attempted: set[int] = set()
        self.thresholds = Thresholds(**{**asdict(Thresholds()), **self.store.get_thresholds()})
        self._build()
        self._refresh_matches()
        self._refresh_hero_picker()
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        for label, command in (("导入 .dem", self._import), ("校验版本", self._verify_patch),
                               ("导出 HTML + CSV", self._export),
                               ("修正位置", self._edit_role), ("分析阈值", self._edit_thresholds),
                               ("选手映射", self._edit_mapping),
                               ("多场趋势", self._trends), ("训练 BP", self._train_bp),
                               ("同步英雄日志", self._sync_hero_notes)):
            button = ttk.Button(bar, text=label, command=command)
            button.pack(side="left", padx=3)
            if label == "同步英雄日志":
                self.kb_sync_button = button
        self.status = ttk.Label(bar, text="就绪")
        self.status.pack(side="right")
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=8)
        body.add(left, weight=1)
        ttk.Label(left, text="已导入比赛").pack(anchor="w")
        self.matches = ttk.Treeview(left, columns=("teams", "duration", "status"), show="headings", height=13)
        for key, label, width in (("teams", "对阵", 215), ("duration", "时长", 65), ("status", "状态", 85)):
            self.matches.heading(key, text=label)
            self.matches.column(key, width=width)
        self.matches.pack(fill="both", expand=True)
        self.matches.bind("<<TreeviewSelect>>", self._select_match)
        ttk.Label(left, text="位置推断可逐场修正；跨场选手身份由映射表确定。", wraplength=260).pack(anchor="w", pady=8)
        right = ttk.Frame(body, padding=8)
        body.add(right, weight=4)
        top = ttk.Frame(right)
        top.pack(fill="x")
        self.heading = ttk.Label(top, text="选择比赛", font=("Microsoft YaHei UI", 15, "bold"))
        self.heading.pack(side="left")
        self.patch_label = ttk.Label(top, text="")
        self.patch_label.pack(side="right")
        self.match_meta = ttk.Label(right, text="", foreground="#65777a")
        self.match_meta.pack(anchor="w")
        tabs = ttk.Notebook(right)
        tabs.pack(fill="both", expand=True, pady=8)
        review = ttk.Frame(tabs)
        tabs.add(review, text="地图与事件")
        layout = ttk.PanedWindow(review, orient="horizontal")
        layout.pack(fill="both", expand=True)
        map_panel = ttk.Frame(layout)
        layout.add(map_panel, weight=3)
        self.canvas = tk.Canvas(map_panel, bg="#101b1c", highlightthickness=0, width=580, height=580)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self._draw())
        controls = ttk.Frame(map_panel)
        controls.pack(fill="x", pady=8)
        self.time_label = ttk.Label(controls, text="00:00")
        self.time_label.pack(side="left")
        self.time_var = tk.IntVar(value=0)
        self.timeline = ttk.Scale(controls, from_=-90, to=3600, variable=self.time_var,
                                  command=lambda _: self._time_changed())
        self.timeline.pack(side="left", fill="x", expand=True, padx=8)
        self.side = tk.StringVar(value="both")
        view = ttk.Combobox(controls, textvariable=self.side, values=("both", "radiant", "dire"),
                            state="readonly", width=10)
        view.pack(side="right")
        view.bind("<<ComboboxSelected>>", lambda _: self._update_view())
        self.layer_vars = {key: tk.BooleanVar(value=True) for key in ("wards", "routes", "heroes", "camps")}
        layer_bar = ttk.Frame(map_panel)
        layer_bar.pack(fill="x", pady=(4, 0))
        for key, label in (("wards", "眼位"), ("routes", "刷钱路线"), ("heroes", "英雄"), ("camps", "野区")):
            ttk.Checkbutton(layer_bar, text=label, variable=self.layer_vars[key],
                            command=self._draw).pack(side="left", padx=(0, 10))
        ttk.Label(map_panel, text="7.41 游戏地图 · 眼位与英雄轨迹按录像坐标叠加",
                  foreground="#788b80").pack(anchor="w", pady=(3, 0))
        event_panel = ttk.Frame(layout, padding=(8, 2, 0, 0))
        layout.add(event_panel, weight=2)
        ttk.Label(event_panel, text="复盘图层", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(0, 6))
        self.info_tabs = ttk.Notebook(event_panel)
        self.info_tabs.pack(fill="both", expand=True)
        ward_tab = ttk.Frame(self.info_tabs, padding=5)
        farm_tab = ttk.Frame(self.info_tabs, padding=5)
        event_tab = ttk.Frame(self.info_tabs, padding=5)
        self.info_tabs.add(ward_tab, text="眼位")
        self.info_tabs.add(farm_tab, text="刷钱路线")
        self.info_tabs.add(event_tab, text="事件")
        list_style = {"activestyle": "none", "borderwidth": 0, "highlightthickness": 0,
                      "background": "#172222", "foreground": "#dbe5d9",
                      "selectbackground": "#31594f", "selectforeground": "#ffffff",
                      "font": ("Microsoft YaHei UI", 9)}
        self.wards_list = tk.Listbox(ward_tab, **list_style)
        self.wards_list.pack(fill="both", expand=True)
        self.wards_list.bind("<<ListboxSelect>>", self._jump_ward)
        self.farm_list = tk.Listbox(farm_tab, **list_style)
        self.farm_list.pack(fill="both", expand=True)
        self.events = ttk.Treeview(event_tab, columns=("time", "team", "event"), show="headings")
        for key, label, width in (("time", "时间", 58), ("team", "队伍", 48), ("event", "事件", 180)):
            self.events.heading(key, text=label)
            self.events.column(key, width=width)
        self.events.pack(fill="both", expand=True)
        self.events.bind("<<TreeviewSelect>>", self._jump_event)
        details = ttk.Notebook(tabs)
        tabs.add(details, text="分析")
        self.analysis_text = tk.Text(details, wrap="word", bg="#f7f8f8", relief="flat", padx=16, pady=12)
        details.add(self.analysis_text, text="重点与联动")
        self.bp_text = tk.Text(details, wrap="word", bg="#f7f8f8", relief="flat", padx=16, pady=12)
        details.add(self.bp_text, text="BP")
        hero_page = ttk.Frame(tabs, padding=12)
        tabs.add(hero_page, text="英雄改动")
        hero_controls = ttk.Frame(hero_page)
        hero_controls.pack(fill="x", pady=(0, 8))
        ttk.Label(hero_controls, text="英雄").pack(side="left")
        self.hero_choice_var = tk.StringVar()
        self.hero_choice = ttk.Combobox(hero_controls, textvariable=self.hero_choice_var,
                                        state="readonly", width=28)
        self.hero_choice.pack(side="left", padx=(6, 12))
        self.hero_choice.bind("<<ComboboxSelected>>", lambda _: self._show_hero_changes())
        ttk.Label(hero_controls, text="检索改动").pack(side="left")
        self.hero_query_var = tk.StringVar()
        query_box = ttk.Entry(hero_controls, textvariable=self.hero_query_var, width=24)
        query_box.pack(side="left", padx=6)
        query_box.bind("<Return>", lambda _: self._show_hero_changes())
        ttk.Button(hero_controls, text="搜索", command=self._show_hero_changes).pack(side="left")
        self.hero_kb_status = ttk.Label(hero_page, text="知识库尚未同步。点击“同步英雄日志”获取 Valve 官方近期补丁说明。",
                                        foreground="#65777a")
        self.hero_kb_status.pack(anchor="w", pady=(0, 6))
        self.hero_changes = tk.Text(hero_page, wrap="word", bg="#f7f8f8", relief="flat", padx=16, pady=12)
        self.hero_changes.pack(fill="both", expand=True)
        self.hero_changes.tag_configure("patch", font=("Microsoft YaHei UI", 11, "bold"), foreground="#1f5149")
        self.hero_changes.tag_configure("source", foreground="#386b91", underline=True)
        self.hero_changes.insert("1.0", "同步官方英雄日志后，选择英雄即可查看近期改动。\n\n改动按英雄、技能和天赋切分索引，点击版本来源可查看 Valve 原始数据。")
        self.hero_changes.configure(state="disabled")
        self._draw()

    def _refresh_matches(self) -> None:
        self.matches.delete(*self.matches.get_children())
        for row in self.store.list_matches():
            if not allowed_match(row):
                continue
            self.matches.insert("", "end", iid=row["id"], values=(
                f"{row['radiant_name']} vs {row['dire_name']}", clock_label(row["duration_s"]),
                "截断" if row["parse_status"] == "partial" else "完整"))

    def _refresh_hero_picker(self) -> None:
        previous = self.hero_choice_var.get()
        self.hero_label_to_id: dict[str, int] = {}
        labels = []
        for hero in self.patch_kb.list_heroes():
            label = hero["name"]
            if hero["english_name"] and hero["english_name"] != label:
                label = f"{label}（{hero['english_name']}）"
            if label in self.hero_label_to_id:
                label = f"{label} · {hero['hero_id']}"
            self.hero_label_to_id[label] = hero["hero_id"]
            labels.append(label)
        self.hero_choice.configure(values=labels)
        if previous in self.hero_label_to_id:
            self.hero_choice_var.set(previous)
        elif labels:
            self.hero_choice_var.set(labels[0])
            self._show_hero_changes()
        else:
            self.hero_choice_var.set("")

    def _show_hero_changes(self) -> None:
        label = self.hero_choice_var.get()
        hero_id = getattr(self, "hero_label_to_id", {}).get(label)
        if hero_id is None:
            return
        query = self.hero_query_var.get().strip()
        notes = self.patch_kb.recent_hero_notes(hero_id, limit=50, query=query)
        latest = self.patch_kb.latest_patch()
        cached_versions = self.store.db.execute("SELECT COUNT(*) FROM game_patches").fetchone()[0]
        if query and not notes:
            status = f"{label}：在已缓存的 {cached_versions} 个近期版本中，没有匹配“{query}”的改动。"
        elif notes:
            versions = len({row["patch_number"] for row in notes})
            status = f"{label}：检索到 {len(notes)} 条改动，涉及 {versions} 个版本；知识库最新版本 {latest or '未知'}。"
        else:
            status = f"{label}：已缓存 {cached_versions} 个版本，但没有找到该英雄的改动。"
        if cached_versions == 0:
            status = "知识库尚未同步。点击“同步英雄日志”获取 Valve 官方近期补丁说明。"
        missing_abilities = self.patch_kb.unresolved_ability_ids(hero_id) if notes else []
        if missing_abilities and hero_id not in self._ability_attempted:
            self._ability_attempted.add(hero_id)
            self._ability_fetching.add(hero_id)
            self._fetch_hero_ability_names(hero_id)
            status += " 正在从 Valve 加载技能名称…"
        self.hero_kb_status.configure(text=status)
        self.hero_changes.configure(state="normal")
        self.hero_changes.delete("1.0", "end")
        if not notes:
            if cached_versions == 0:
                self.hero_changes.insert("1.0", "知识库为空。同步完成后，可按英雄、技能或改动文本检索近期原文。")
            elif query:
                self.hero_changes.insert("1.0", "没有匹配的改动。清空检索框即可重新显示该英雄的近期改动。")
            else:
                self.hero_changes.insert("1.0", "近期官方补丁中没有该英雄的改动。")
            self.hero_changes.configure(state="disabled")
            return
        summary = f"检索结果：{len(notes)} 条英雄改动。内容来自 Valve 官方结构化补丁日志，按英雄、技能和天赋检索。\n\n"
        self.hero_changes.insert("end", summary)
        last_patch = None
        for row in notes:
            if row["patch_number"] != last_patch:
                self.hero_changes.insert("end", f"{row['patch_number']}  ·  ", "patch")
                stamp = row.get("patch_timestamp")
                if stamp:
                    date = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")
                    self.hero_changes.insert("end", date + "  ", "patch")
                tag = f"source_{row['patch_number']}"
                self.hero_changes.insert("end", "查看 Valve 来源\n", ("source", tag))
                self.hero_changes.tag_bind(tag, "<Button-1>",
                    lambda _event, url=row["source_url"]: webbrowser.open(url))
                last_patch = row["patch_number"]
            subject = row["subject"]
            if subject.startswith("技能 ID "):
                try:
                    ability_name = self.patch_kb.ability_name(int(subject.removeprefix("技能 ID ")))
                    if ability_name:
                        subject = f"{subject} · {ability_name}"
                except ValueError:
                    pass
            self.hero_changes.insert("end", f"  {row['section']} · {subject}\n", "patch")
            self.hero_changes.insert("end", f"  {row['note']}\n\n")
        self.hero_changes.configure(state="disabled")

    def _fetch_hero_ability_names(self, hero_id: int) -> None:
        path = self.store.path

        def worker() -> None:
            try:
                with Store(path) as store:
                    count = PatchKnowledgeBase(store.db).fetch_hero_abilities(hero_id)
                self.messages.put(("hero_abilities_ready", (hero_id, count)))
            except Exception as exc:
                self.messages.put(("hero_abilities_error", (hero_id, str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _sync_hero_notes(self) -> None:
        self.kb_sync_button.state(["disabled"])
        self.status.configure(text="正在同步 Valve 英雄更新日志…")
        path = self.store.path

        def worker() -> None:
            try:
                with Store(path) as store:
                    kb = PatchKnowledgeBase(store.db)
                    result = kb.sync(lambda line: self.messages.put(("knowledge_progress", line)))
                self.messages.put(("knowledge_synced", result))
            except Exception as exc:
                self.messages.put(("knowledge_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _select_match(self, _: object = None) -> None:
        selected = self.matches.selection()
        if not selected:
            return
        self.data = self.store.load(selected[0])
        d = self.data
        self.creep_at_time = {}
        for creep in d.get("creeps", []):
            self.creep_at_time.setdefault(creep["time_s"], []).append(creep)
        self.heading.configure(text=f"{d['radiant_name']} vs {d['dire_name']}")
        self.match_meta.configure(text=f"{match_time_text(d)} · {d['metadata'].get('league_name') or '赛事名称待核实'} · 比赛 ID {d.get('match_id') or '未知'}")
        self.patch_label.configure(text="7.41f 时间窗口推定" if d["metadata"].get("patch_method") == "major_patch_and_release_window"
                                   else "版本未验证")
        self.timeline.configure(from_=-90, to=max(0, d["duration_s"]))
        self.time_var.set(0)
        self._update_view()

    def _update_view(self) -> None:
        if not self.data:
            return
        d = self.data
        side = self.side.get()
        self.events.delete(*self.events.get_children())
        self.timeline_rows = sorted(d["events"] + [
            {"time_s": o["time_s"], "team": o["team"],
             "description": f"拉野候选 · 野区 {o['details'].get('camp_id', '?')} · {o['evidence']}"}
            for o in d["observations"] if o["type"] == "pull" and o["time_s"] is not None
        ], key=lambda row: row["time_s"])
        for index, e in enumerate(self.timeline_rows):
            if side != "both" and e.get("team") not in (None, 2 if side == "radiant" else 3):
                continue
            self.events.insert("", "end", iid=str(index), values=(clock_label(e["time_s"]),
                               "天辉" if e.get("team") == 2 else "夜魇" if e.get("team") == 3 else "",
                               e.get("description") or e.get("type")))
        self._analysis()
        self._refresh_map_lists()
        self._update_clock_display()
        self._draw()

    def _time_changed(self) -> None:
        self._update_clock_display()
        self._refresh_map_lists()
        self._draw()

    def _update_clock_display(self) -> None:
        second = int(float(self.time_var.get()))
        phase, remaining = day_night_at(second)
        phase_label = "白昼" if phase == "day" else "夜间"
        transition = "入夜" if phase == "day" else "天亮"
        self.time_label.configure(text=f"{clock_label(second)}  ·  {phase_label}  ·  {transition} {clock_label(remaining)}")

    def _jump_event(self, _: object) -> None:
        if self.data and self.events.selection():
            event = self.timeline_rows[int(self.events.selection()[0])]
            self.time_var.set(event["time_s"])
            self._time_changed()

    def _jump_ward(self, _: object) -> None:
        if not self.data or not self.wards_list.curselection():
            return
        side = self.side.get()
        team_filter = None if side == "both" else 2 if side == "radiant" else 3
        wards = sorted((w for w in self.data["wards"] if team_filter is None or w.get("team") == team_filter),
                       key=lambda w: w["time_s"])
        index = self.wards_list.curselection()[0]
        if index < len(wards):
            self.time_var.set(wards[index]["time_s"])
            self._time_changed()

    def _refresh_map_lists(self) -> None:
        if not self.data:
            return
        second = int(self.time_var.get())
        side = self.side.get()
        team_filter = None if side == "both" else 2 if side == "radiant" else 3
        self.wards_list.delete(0, "end")
        wards = [w for w in self.data["wards"] if team_filter is None or w.get("team") == team_filter]
        for ward in sorted(wards, key=lambda w: w["time_s"]):
            live = ward["time_s"] <= second and (ward.get("end_s") is None or second < ward["end_s"])
            if ward["time_s"] > second:
                state = f"将于 {clock_label(ward['time_s'])} 放置"
            elif live:
                state = "存活中" if ward.get("end_s") is None else f"存活 · 至 {clock_label(ward['end_s'])}"
            else:
                state = "已失效"
            team = "天辉" if ward.get("team") == 2 else "夜魇"
            kind = "侦查守卫" if ward.get("kind") == "observer" else "岗哨守卫"
            self.wards_list.insert("end", f"{clock_label(ward['time_s'])}  {team} · {kind} · {state}")
        self.farm_list.delete(0, "end")
        self.farm_players = []
        for player in self.data["players"]:
            if team_filter is not None and player["team"] != team_filter:
                continue
            samples = self.data["positions"].get(str(player["slot"]), [])
            past = [p for p in samples if second - 180 <= p["time_s"] <= second]
            if not past:
                continue
            self.farm_players.append((player, past))
            team = "天辉" if player["team"] == 2 else "夜魇"
            role = player.get("role") or "位置待定"
            self.farm_list.insert("end", f"{role} · {player.get('name') or '未知选手'}  ({team})\n  最近3分钟 {len(past)} 个位置样本")
        if not self.farm_players:
            self.farm_list.insert("end", "当前时间之前没有可用的英雄位置样本")

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        width = max(c.winfo_width(), 100)
        height = max(c.winfo_height(), 100)
        size = max(80, min(width, height) - 28)
        ox, oy = (width - size) / 2, (height - size) / 2
        phase, _ = day_night_at(int(float(self.time_var.get())))
        is_night = phase == "night"
        if self._map_photo_size != size or self._map_photo_night != is_night:
            source = self._map_image_source
            if is_night:
                night_tint = Image.new("RGB", source.size, (17, 29, 55))
                source = Image.blend(source, night_tint, 0.48)
            source = source.resize((size, size), Image.Resampling.LANCZOS)
            self._map_photo = ImageTk.PhotoImage(source)
            self._map_photo_size = size
            self._map_photo_night = is_night
        c.create_image(ox, oy, image=self._map_photo, anchor="nw")
        c.create_rectangle(ox, oy, ox+size, oy+size, outline="#a6ae91", width=1)
        if not self.data:
            return
        second = self.time_var.get()
        side = self.side.get()
        def pos(x: float, y: float) -> tuple[float, float]:
            map_x = max(0, min(1, (x - 7563) / (25900 - 7563)))
            map_y = 1 - max(0, min(1, (y - 7800) / (25600 - 7800)))
            # The game asset includes a narrow black frame around the playable map.
            return ox + size * (.058 + .884 * map_x), oy + size * (.058 + .884 * map_y)
        if self.layer_vars["wards"].get():
            for ward in active_wards(self.data["wards"], second):
                if ward.get("x") is None or ward.get("y") is None:
                    continue
                if side != "both" and ward.get("team") != (2 if side == "radiant" else 3):
                    continue
                x, y = pos(ward["x"], ward["y"])
                radius = 8 if ward.get("kind") == "observer" else 5
                color = "#75cfad" if ward.get("team") == 2 else "#f0ad92"
                c.create_oval(x-radius, y-radius, x+radius, y+radius, outline=color, width=2, fill="#182322")
                c.create_text(x, y, text="◉" if ward.get("kind") == "observer" else "+", fill=color, font=("Arial", 8, "bold"))
        if self.layer_vars["camps"].get():
            for creep in self.creep_at_time.get(second - second % 2, []):
                if creep.get("kind") != "neutral":
                    continue
                x, y = pos(creep["x"], creep["y"])
                c.create_oval(x-2, y-2, x+2, y+2, fill="#f4d88a", outline="")
        if self.layer_vars["routes"].get():
            for player, samples in getattr(self, "farm_players", []):
                usable = [p for p in samples if p.get("x") is not None and p.get("y") is not None]
                if len(usable) > 1:
                    coords = [coord for p in usable for coord in pos(p["x"], p["y"])]
                    color = "#75cfad" if player["team"] == 2 else "#f0ad92"
                    c.create_line(*coords, fill=color, width=2, smooth=True, stipple="gray50")
        for observation in self.data["observations"] if self.layer_vars["routes"].get() else []:
            if observation["type"] != "pull" or observation["time_s"] is None or observation["end_s"] is None:
                continue
            if not observation["time_s"] <= second < observation["end_s"]:
                continue
            details = observation["details"]
            if details.get("x") is None or details.get("y") is None:
                continue
            x, y = pos(details["x"], details["y"])
            c.create_rectangle(x-8, y-8, x+8, y+8, outline="#e8c76a", width=2)
        for player in self.data["players"] if self.layer_vars["heroes"].get() else []:
            if side != "both" and player["team"] != (2 if side == "radiant" else 3):
                continue
            sample = position_at(self.data["positions"].get(str(player["slot"]), []), second)
            if sample is None:
                continue
            x, y = pos(sample["x"], sample["y"])
            color = "#75cfad" if player["team"] == 2 else "#f0ad92"
            c.create_oval(x-11, y-11, x+11, y+11, fill=color, outline="#10171b")
            c.create_text(x, y, text=str(player.get("role") or "?"), fill="#102019", font=("Arial", 9, "bold"))

    def _analysis(self) -> None:
        if not self.data:
            return
        d = self.data
        lines = ["重点神符窗口"]
        for window in rune_windows(d["events"], d["positions"], d["players"]):
            lines.append(f"{window['label']}：{len(window['pickups'])} 次拾取，{len(window['positions'])} 个站位样本，到场证据 {len(window['attendance'])}，后续事件 {len(window['followups'])}（非因果）；{window['evidence']}")
        lines += ["", "2+4 / 2+5 / 2+4+5 联动"]
        groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for item in companionship(d["players"], d["positions"], d["duration_s"], self.thresholds):
            groups.setdefault((item["team"], item["roles"]), []).append(item)
        for (team, roles), items in sorted(groups.items()):
            lines.append(f"{'天辉' if team == 2 else '夜魇'} {roles}: {len(items)} 次，累计 {sum(i['duration_s'] for i in items)} 秒")
        creep_obs = [o for o in d["observations"] if o["evidence"] != "insufficient"]
        lines += ["", f"兵线与拉野：{len(creep_obs)} 个有实体证据的片段；拉野仅为候选，需人工核查。" if creep_obs else
                  "兵线与拉野：证据不足；没有足够的实体轨迹。",
                  "开雾行动的后续事件为时间关联，不构成决策原因证明。"]
        self.analysis_text.delete("1.0", "end")
        self.analysis_text.insert("1.0", "\n".join(lines))
        descriptors = draft_descriptors(d["draft"])
        prediction = prediction_for_match(self.store, d)
        bp_lines = [f"天辉胜率预测：{prediction['radiant_probability']:.1%}（7.41f 时间窗口推定；验证 Brier {prediction['brier']:.3f}，ECE {prediction['ece']:.3f}）" if prediction else "胜率预测：预测数据不足",
                    "", "阵容特征（仅列出内置、可核查的英雄标签）"]
        for team in (2, 3):
            row = descriptors[team]
            bp_lines.append(f"{'天辉' if team == 2 else '夜魇'}：开团工具 {', '.join(row['initiation_tools']) or '未标注'}；推进/终结工具 {', '.join(row['siege_tools']) or '未标注'}；标签覆盖 {row['coverage']}")
        bp_lines += ["", "禁选顺序"]
        bp_lines += [f"{i+1:02d} {'天辉' if p['team']==2 else '夜魇' if p['team']==3 else '?'} {p['action']} {p['hero']}"
                     for i, p in enumerate(d["draft"])]
        self.bp_text.delete("1.0", "end")
        self.bp_text.insert("1.0", "\n".join(bp_lines))

    def _import(self) -> None:
        paths = filedialog.askopenfilenames(title="选择 Dota 2 录像", filetypes=[("Dota 2 replay", "*.dem")])
        if not paths:
            return
        self.status.configure(text=f"解析中：0/{len(paths)}")
        def worker() -> None:
            for index, path in enumerate(paths, 1):
                try:
                    self.messages.put(("imported", import_managed_replay(path)))
                except Exception as exc:
                    self.messages.put(("error", f"{Path(path).name}: {exc}"))
                self.messages.put(("progress", f"解析中：{index}/{len(paths)}"))
            self.messages.put(("progress", "导入完成"))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "imported":
                    if self.store.save(value):
                        self._refresh_matches()
                        self.matches.selection_set(value["id"])
                        self._select_match()
                    else:
                        self.status.configure(text="重复录像，已跳过")
                elif kind == "error":
                    messagebox.showerror("导入失败", str(value))
                elif kind == "knowledge_progress":
                    self.status.configure(text=str(value))
                elif kind == "knowledge_synced":
                    self.kb_sync_button.state(["!disabled"])
                    self._ability_attempted.clear()
                    self._refresh_hero_picker()
                    self._show_hero_changes()
                    errors = value.get("errors", [])
                    message = (f"英雄日志已同步：{value['heroes']} 位英雄、{value['patches']} 个补丁、"
                               f"{value['notes']} 条改动")
                    if errors:
                        message += f"；{len(errors)} 个版本未能读取"
                    self.status.configure(text=message)
                elif kind == "hero_abilities_ready":
                    hero_id, _ = value
                    self._ability_fetching.discard(hero_id)
                    if self.hero_label_to_id.get(self.hero_choice_var.get()) == hero_id:
                        self._show_hero_changes()
                elif kind == "hero_abilities_error":
                    hero_id, error = value
                    self._ability_fetching.discard(hero_id)
                    if self.hero_label_to_id.get(self.hero_choice_var.get()) == hero_id:
                        self.hero_kb_status.configure(text=f"技能名称暂时无法读取，日志仍以 Valve 技能 ID 展示：{error}")
                elif kind == "knowledge_error":
                    self.kb_sync_button.state(["!disabled"])
                    self.status.configure(text="英雄日志同步失败")
                    messagebox.showerror("英雄日志同步失败", str(value))
                elif kind == "verified":
                    key, patch = value
                    self.status.configure(text=f"版本校验：{patch}")
                    if self.data and self.data["id"] == key:
                        self._select_match()
                elif kind == "progress":
                    self.status.configure(text=str(value))
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _export(self) -> None:
        if not self.data:
            return
        root = match_dir(self.data)
        report_data = {**self.data, "bp_prediction": prediction_for_match(self.store, self.data)}
        report = write_html(report_data, root / "report.html", self.thresholds)
        write_csv(self.data, root)
        self.status.configure(text=f"已导出：{root}")
        if messagebox.askyesno("导出完成", "已导出离线 HTML 和 CSV。现在打开报告？"):
            webbrowser.open(report.as_uri())

    def _edit_role(self) -> None:
        if not self.data:
            return
        choices = "\n".join(f"{p['slot']}: {p['name'] or p['hero']} → {p['role'] or '?'}" for p in self.data["players"])
        slot = simpledialog.askinteger("修正位置", f"输入选手 slot：\n{choices}", parent=self)
        if slot is None:
            return
        role = simpledialog.askinteger("修正位置", "输入位置 1–5：", minvalue=1, maxvalue=5, parent=self)
        if role is None:
            return
        try:
            self.store.set_role(self.data["id"], slot, role)
            self.data = self.store.load(self.data["id"])
            self._update_view()
        except KeyError:
            messagebox.showerror("未知选手", f"slot {slot} 不在此场比赛中")

    def _edit_mapping(self) -> None:
        if not self.data:
            return
        choices = "\n".join(f"{p['account_id'] or '无 ID'}: {p['name']}" for p in self.data["players"])
        account = simpledialog.askstring("选手映射", f"输入账号 ID：\n{choices}", parent=self)
        if not account:
            return
        candidates = self.store.mapping_candidates(account)
        hint = "、".join(candidates[:8]) or "无"
        name = simpledialog.askstring("选手映射", f"输入跨场统一名称：\n同一账号的历史名称候选：{hint}", parent=self)
        if name:
            self.store.map_player(account, name)
            self.status.configure(text=f"已映射 {account} → {name}")

    def _edit_thresholds(self) -> None:
        old = self.thresholds
        radius = simpledialog.askfloat("联动阈值", "同行最大距离（世界单位）：",
                                       initialvalue=old.together_radius, minvalue=100, maxvalue=5000, parent=self)
        if radius is None:
            return
        seconds = simpledialog.askinteger("联动阈值", "同行最短秒数：",
                                          initialvalue=old.together_min_seconds, minvalue=1, maxvalue=120, parent=self)
        if seconds is None:
            return
        pull_radius = simpledialog.askfloat("拉野阈值", "兵线小兵与野怪最大距离：",
                                            initialvalue=old.pull_encounter_radius,
                                            minvalue=100, maxvalue=1500, parent=self)
        if pull_radius is None:
            return
        pull_seconds = simpledialog.askinteger("拉野阈值", "接触最短秒数：",
                                               initialvalue=old.pull_min_seconds, minvalue=2, maxvalue=60, parent=self)
        if pull_seconds is None:
            return
        self.thresholds = Thresholds(together_radius=radius, together_min_seconds=seconds,
                                     position_max_age=old.position_max_age,
                                     pull_encounter_radius=pull_radius,
                                     pull_camp_radius=old.pull_camp_radius,
                                     pull_min_seconds=pull_seconds)
        self.store.set_thresholds(asdict(self.thresholds))
        if self.data:
            observations = observations_from_creeps(self.data.get("creeps", []),
                pull_radius=pull_radius, camp_radius=old.pull_camp_radius,
                pull_min_seconds=pull_seconds)
            self.store.replace_observations(self.data["id"], observations)
            self.data = self.store.load(self.data["id"])
            self._update_view()
        self.status.configure(text="分析阈值已更新")

    def _trends(self) -> None:
        rows = build_trends(self.store)
        visible_count = sum(allowed_match(match) for match in self.store.list_matches())
        lines = [f"已导入 {visible_count} 场；跨场身份需至少 3 名已映射选手重合。", ""]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(row["team_identity"], []).append(row)
        for identity, items in groups.items():
            lines.append(identity)
            for row in items:
                lines.append(f"  {row['match_id'] or row['match_key']} · 胜 {row['won']} · 赏金 {row['first_bounty']} · 眼 {row['observer_wards']}/{row['sentry_wards']} · 开雾 {row['smokes']} · 拉野候选 {row['pull_candidates']} · Carry 10/20分经济 {row['carry_nw_10']}/{row['carry_nw_20']} · 2+4 {row['link_2+4_seconds']}秒")
            lines.append("")
        window = tk.Toplevel(self)
        window.title("多场趋势")
        ttk.Button(window, text="导出趋势 CSV", command=lambda: self._export_trends(rows)).pack(anchor="e", padx=10, pady=8)
        text = tk.Text(window, width=75, height=28, padx=15, pady=15)
        text.pack(fill="both", expand=True)
        text.insert("1.0", "\n".join(lines))

    def _export_trends(self, rows: list[dict[str, Any]]) -> None:
        path = write_trends_csv(rows, report_dir() / "trends.csv")
        self.status.configure(text=f"趋势已导出：{path}")

    def _train_bp(self) -> None:
        self.status.configure(text="获取公开职业赛并训练 BP…")
        path = self.store.path
        def worker() -> None:
            try:
                with Store(path) as store:
                    count = fetch_pro_samples(store, pages=3)
                    result = train_model(store)
                self.messages.put(("progress", f"BP：{count} 场本次缓存；{result}"))
            except Exception as exc:
                self.messages.put(("error", f"BP 训练失败：{exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def _verify_patch(self) -> None:
        if not self.data:
            return
        key = self.data["id"]
        path = self.store.path
        self.status.configure(text="正在通过 OpenDota 校验版本…")
        def worker() -> None:
            try:
                with Store(path) as store:
                    patch = verify_match_patch(store, key)
                self.messages.put(("verified", (key, patch)))
            except Exception as exc:
                self.messages.put(("error", f"版本校验失败：{exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def _close(self) -> None:
        self.store.close()
        self.destroy()


def run(db_path: str | Path | None = None) -> None:
    ReviewApp(db_path).mainloop()
