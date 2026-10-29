"""Small source-attributed retrieval corpus for 7.41 vision questions."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _corpus():
    return json.loads(Path(__file__).with_name("vision_knowledge.json").read_text(encoding="utf-8"))


def retrieve_vision_knowledge(query: str, limit: int = 4) -> list[dict]:
    query = query.casefold()
    ranked = [(sum(1 for word in row["keywords"] if word.casefold() in query), row) for row in _corpus()]
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [{k: v for k, v in row.items() if k != "keywords"} for score, row in ranked if score][:limit]
