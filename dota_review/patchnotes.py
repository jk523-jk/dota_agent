"""Local, searchable hero patch-note knowledge base backed by Valve's datafeed."""

from __future__ import annotations

import html
import json
import re
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any, Callable


DATAFEED = "https://www.dota2.com/datafeed"
PATCH_WINDOW = 12


class PatchKnowledgeBase:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS game_heroes (
                hero_id INTEGER PRIMARY KEY,
                hero_key TEXT NOT NULL,
                name TEXT NOT NULL,
                english_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS game_abilities (
                ability_id INTEGER PRIMARY KEY,
                hero_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                english_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS game_patches (
                patch_number TEXT PRIMARY KEY,
                patch_timestamp INTEGER NOT NULL,
                source_url TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hero_patch_notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                patch_number TEXT NOT NULL,
                patch_timestamp INTEGER NOT NULL,
                hero_id INTEGER NOT NULL,
                hero_name TEXT NOT NULL,
                section TEXT NOT NULL,
                subject TEXT NOT NULL,
                note TEXT NOT NULL,
                source_url TEXT NOT NULL,
                UNIQUE(patch_number, hero_id, section, subject, note)
            );
            CREATE INDEX IF NOT EXISTS idx_hero_patch_notes_recent
                ON hero_patch_notes(hero_id, patch_timestamp DESC);
        """)
        self.fts_available = True
        try:
            self.db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS hero_patch_notes_fts
                USING fts5(hero_id UNINDEXED, patch_number UNINDEXED, hero_name,
                           section, subject, note, tokenize='unicode61')""")
        except sqlite3.OperationalError:
            self.fts_available = False

    @staticmethod
    def _get_json(path: str, query: dict[str, str]) -> dict[str, Any]:
        url = f"{DATAFEED}/{path}?{urlencode(query)}"
        request = Request(url, headers={
            "User-Agent": "DotaReplayReview/1.0 (local patch notes cache)",
            "Accept": "application/json",
        })
        try:
            with urlopen(request, timeout=25) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"Valve 更新日志请求失败：{exc}") from exc

    @staticmethod
    def _clean_note(value: Any) -> str:
        text = html.unescape(str(value or ""))
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]*>", " ", text)
        return " ".join(text.split())

    @classmethod
    def _notes_for_hero(cls, hero: dict[str, Any]) -> list[tuple[str, str, str]]:
        chunks: list[tuple[str, str, str]] = []

        def add(section: str, subject: str, entries: Any) -> None:
            if isinstance(entries, (str, dict)):
                entries = [entries]
            if not isinstance(entries, list):
                return
            for entry in entries:
                if isinstance(entry, dict):
                    if entry.get("hide_dot"):
                        continue
                    text = cls._clean_note(entry.get("note"))
                else:
                    text = cls._clean_note(entry)
                if text and text not in (".", "•"):
                    chunks.append((section, subject, text))

        add("英雄属性", "基础属性", hero.get("hero_notes"))
        add("天赋", "天赋改动", hero.get("talent_notes"))
        for ability in hero.get("abilities", []):
            ability_id = ability.get("ability_id", "?")
            add("技能", f"技能 ID {ability_id}", ability.get("ability_notes"))
        return chunks

    def sync(self, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        report = progress or (lambda _: None)
        report("读取 Valve 英雄列表…")
        hero_response = self._get_json("herolist", {"language": "schinese"})
        hero_rows = hero_response.get("result", {}).get("data", {}).get("heroes", [])
        if not hero_rows:
            raise RuntimeError("Valve 英雄列表为空，未更新知识库")
        heroes = [{
            "hero_id": int(row["id"]),
            "hero_key": row.get("name", ""),
            "name": row.get("name_loc") or row.get("name_english_loc") or row.get("name", ""),
            "english_name": row.get("name_english_loc") or row.get("name_loc") or row.get("name", ""),
        } for row in hero_rows]
        hero_names = {hero["hero_id"]: hero["name"] for hero in heroes}

        report("读取近期补丁列表…")
        patch_response = self._get_json("patchnoteslist", {"language": "schinese"})
        patches = sorted(patch_response.get("patches", []),
                         key=lambda row: int(row.get("patch_timestamp") or 0))[-PATCH_WINDOW:]
        if not patches:
            raise RuntimeError("Valve 补丁列表为空，未更新知识库")

        fetched: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        errors: list[str] = []
        for index, patch in enumerate(reversed(patches), 1):
            version = str(patch.get("patch_number") or patch.get("patch_name") or "")
            if not version:
                continue
            report(f"读取补丁 {version}（{index}/{len(patches)}）…")
            try:
                note_data = self._get_json("patchnotes", {"version": version, "language": "schinese"})
                if note_data.get("success") is False:
                    raise RuntimeError("Valve 未返回此版本的结构化说明")
                note_data.setdefault("patch_number", version)
                note_data.setdefault("patch_timestamp", patch.get("patch_timestamp", 0))
                fetched.append((patch, note_data.get("heroes", [])))
            except Exception as exc:
                errors.append(f"{version}: {exc}")

        with self.db:
            self.db.executemany("""INSERT INTO game_heroes(hero_id,hero_key,name,english_name)
                VALUES (:hero_id,:hero_key,:name,:english_name)
                ON CONFLICT(hero_id) DO UPDATE SET hero_key=excluded.hero_key,
                  name=excluded.name,english_name=excluded.english_name""", heroes)
            for patch, hero_entries in fetched:
                version = str(patch.get("patch_number") or patch.get("patch_name"))
                timestamp = int(patch.get("patch_timestamp") or 0)
                self.db.execute("DELETE FROM hero_patch_notes WHERE patch_number=?", (version,))
                if self.fts_available:
                    self.db.execute("DELETE FROM hero_patch_notes_fts WHERE patch_number=?", (version,))
                source_url = f"{DATAFEED}/patchnotes?{urlencode({'version': version, 'language': 'schinese'})}"
                self.db.execute("""INSERT OR REPLACE INTO game_patches
                    (patch_number,patch_timestamp,source_url) VALUES (?,?,?)""",
                    (version, timestamp, source_url))
                for hero in hero_entries:
                    try:
                        hero_id = int(hero["hero_id"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    hero_name = hero_names.get(hero_id, f"英雄 {hero_id}")
                    for section, subject, note in self._notes_for_hero(hero):
                        cursor = self.db.execute("""INSERT OR IGNORE INTO hero_patch_notes
                            (patch_number,patch_timestamp,hero_id,hero_name,section,subject,note,source_url)
                            VALUES (?,?,?,?,?,?,?,?)""",
                            (version, timestamp, hero_id, hero_name, section, subject, note, source_url))
                        if cursor.rowcount:
                            note_id = cursor.lastrowid
                            if self.fts_available:
                                self.db.execute("""INSERT INTO hero_patch_notes_fts
                                    (rowid,hero_id,patch_number,hero_name,section,subject,note)
                                    VALUES (?,?,?,?,?,?,?)""",
                                    (note_id, str(hero_id), version, hero_name, section, subject, note))

        return {"heroes": len(heroes), "patches": len(fetched),
                "notes": self.db.execute("SELECT COUNT(*) FROM hero_patch_notes").fetchone()[0],
                "errors": errors}

    def list_heroes(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute(
            "SELECT hero_id,hero_key,name,english_name FROM game_heroes ORDER BY name COLLATE NOCASE")]

    def latest_patch(self) -> str | None:
        row = self.db.execute("SELECT patch_number FROM game_patches ORDER BY patch_timestamp DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def unresolved_ability_ids(self, hero_id: int) -> list[int]:
        subjects = self.db.execute("""SELECT DISTINCT subject FROM hero_patch_notes
            WHERE hero_id=? AND section='技能'""", (hero_id,))
        ability_ids = {int(match.group(0)) for row in subjects
                       if (match := re.search(r"\d+", row[0] or ""))}
        if not ability_ids:
            return []
        known = {row[0] for row in self.db.execute("SELECT ability_id FROM game_abilities WHERE ability_id IN ({})".format(
            ",".join("?" for _ in ability_ids)), tuple(ability_ids))}
        return sorted(ability_ids - known)

    def fetch_hero_abilities(self, hero_id: int) -> int:
        response = self._get_json("herodata", {"hero_id": str(hero_id), "language": "schinese"})
        heroes = response.get("result", {}).get("data", {}).get("heroes", [])
        if not heroes:
            raise RuntimeError(f"Valve 未返回英雄 {hero_id} 的技能列表")
        rows = []
        for ability in heroes[0].get("abilities", []):
            try:
                ability_id = int(ability["id"])
            except (KeyError, TypeError, ValueError):
                continue
            name = ability.get("name_loc") or ability.get("name_english_loc") or ability.get("name", "")
            english_name = ability.get("name_english_loc") or name
            if name:
                rows.append((ability_id, hero_id, name, english_name))
        with self.db:
            self.db.executemany("""INSERT INTO game_abilities(ability_id,hero_id,name,english_name)
                VALUES (?,?,?,?) ON CONFLICT(ability_id) DO UPDATE SET
                hero_id=excluded.hero_id,name=excluded.name,english_name=excluded.english_name""", rows)
        return len(rows)

    def ability_name(self, ability_id: int) -> str | None:
        row = self.db.execute("SELECT name FROM game_abilities WHERE ability_id=?", (ability_id,)).fetchone()
        return row[0] if row else None

    def recent_hero_notes(self, hero_id: int, limit: int = 50,
                          query: str = "") -> list[dict[str, Any]]:
        tokens = re.findall(r"[\w\u3400-\u9fff]+", query, flags=re.UNICODE)
        if tokens and self.fts_available:
            match = " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)
            try:
                rows = self.db.execute("""SELECT n.* FROM hero_patch_notes_fts f
                    JOIN hero_patch_notes n ON n.note_id=f.rowid
                    WHERE hero_patch_notes_fts MATCH ? AND n.hero_id=?
                    ORDER BY bm25(hero_patch_notes_fts),n.patch_timestamp DESC LIMIT ?""",
                    (match, hero_id, limit))
                found = [dict(row) for row in rows]
                if found:
                    return found
            except sqlite3.OperationalError:
                pass
        if tokens:
            where = " AND ".join("(note LIKE ? OR subject LIKE ?)" for _ in tokens)
            values: list[Any] = [hero_id]
            for token in tokens:
                values.extend((f"%{token}%", f"%{token}%"))
            values.append(limit)
            rows = self.db.execute(f"""SELECT * FROM hero_patch_notes WHERE hero_id=? AND {where}
                ORDER BY patch_timestamp DESC,note_id DESC LIMIT ?""", values)
        else:
            rows = self.db.execute("""SELECT * FROM hero_patch_notes WHERE hero_id=?
                ORDER BY patch_timestamp DESC,note_id DESC LIMIT ?""", (hero_id, limit))
        return [dict(row) for row in rows]
