"""The user-selected professional team scope and conservative name aliases."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


TEAM_ALIASES = {
    "OG": {"og", "ogesports"},
    "Team Yandex": {"teamyandex", "yandex"},
    "PARIVISION": {"parivision", "pari", "parivisionteam"},
    "Team Spirit": {"teamspirit", "spirit"},
    "Xtreme Gaming": {"xtremegaming", "xg"},
    "1win": {"1win", "1w", "1winteam"},
    "Team Liquid": {"teamliquid", "liquid"},
    "LGD": {"lgd", "lgdgaming", "psglgd", "teamlgd"},
    "Tidebound": {"tidebound", "teamtidebound", "tideboundesports"},
}


def _key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", value or "").casefold())


def canonical_team(name: str | None) -> str | None:
    key = _key(name)
    return next((canonical for canonical, aliases in TEAM_ALIASES.items() if key in aliases), None)


def allowed_match(data: dict[str, Any]) -> bool:
    return bool(canonical_team(data.get("radiant_name")) or canonical_team(data.get("dire_name")))
