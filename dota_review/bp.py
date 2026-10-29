"""Transparent draft descriptors and a strictly time-split pro-match model."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .storage import Store
from .paths import relocate_match_folder


# Deliberately inspectable, conservative tags. Unlisted heroes are reported as unknown.
INITIATORS = {"axe", "centaur", "earthshaker", "enigma", "magnus", "mars", "tidehunter",
              "tiny", "clockwerk", "beastmaster", "primal_beast", "spirit_breaker", "bat rider",
              "batrider", "sand_king", "slardar", "puck", "faceless_void"}
SIEGE = {"death_prophet", "dragon_knight", "leshrac", "lone_druid", "lycan", "nature's_prophet",
         "natures_prophet", "pugna", "shadow_shaman", "terrorblade", "troll_warlord", "luna",
         "drow_ranger", "medusa", "sniper", "visage", "broodmother"}

# OpenDota's patch constant is major-only (7.41, ID 60). Valve published
# 7.41f on 2026-09-15. This conservative window begins the following UTC day
# and ends at the current release snapshot; update it after checking later patches.
# https://www.dota2.com/newsentry/677383425371407609
PATCH_741F_START = 1789516800  # 2026-09-16 00:00 UTC
PATCH_741F_END = 1793059200    # 2026-10-27 00:00 UTC, exclusive


def hero_key(name: str | None) -> str:
    return (name or "").lower().removeprefix("npc_dota_hero_").replace(" ", "_")


def draft_descriptors(draft: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result = {}
    for team in (2, 3):
        heroes = [hero_key(row.get("hero")) for row in draft
                  if row.get("team") == team and row.get("action") == "pick"]
        initiate = [h for h in heroes if h in INITIATORS]
        siege = [h for h in heroes if h in SIEGE]
        unknown = [h for h in heroes if h not in INITIATORS and h not in SIEGE]
        result[team] = {"initiation_tools": initiate, "siege_tools": siege,
                        "untagged": unknown, "coverage": f"{len(heroes)-len(unknown)}/{len(heroes)}"}
    return result


def _download_api(path: str) -> Any:
    request = urllib.request.Request("https://api.opendota.com/api/" + path,
                                     headers={"User-Agent": "DotaReview/0.1", "Accept": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 502, 503, 504) or attempt == 4:
                raise
            retry_after = exc.headers.get("Retry-After")
            time.sleep(min(60, int(retry_after)) if retry_after and retry_after.isdigit() else
                       (10 * (attempt + 1) if exc.code == 429 else 2 ** attempt))
    return payload


def _api(store: Store, path: str) -> Any:
    cached = store.db.execute("SELECT payload_json FROM api_cache WHERE key=?", (path,)).fetchone()
    if cached:
        return json.loads(cached[0])
    payload = _download_api(path)
    with store.db:
        store.db.execute("INSERT OR REPLACE INTO api_cache(key,payload_json) VALUES (?,?)",
                         (path, json.dumps(payload)))
    return payload


def patch_id(store: Store, name: str = "7.41f") -> int | None:
    lookup_name = "7.41" if name == "7.41f" else name
    patches = _api(store, "constants/patch")
    for patch in patches.values() if isinstance(patches, dict) else patches:
        if patch.get("name") == lookup_name:
            return int(patch["id"])
    return None


def in_741f_window(detail: dict[str, Any], major_patch_id: int) -> bool:
    start = detail.get("start_time")
    return (detail.get("patch") == major_patch_id and isinstance(start, int)
            and PATCH_741F_START <= start < PATCH_741F_END)


def verify_match_patch(store: Store, key: str) -> str:
    match = store.load(key)
    if not match.get("match_id"):
        raise ValueError("录像没有比赛 ID，无法通过 OpenDota 校验版本")
    detail = _api(store, f"matches/{match['match_id']}")
    patches = _api(store, "constants/patch")
    patch_rows = patches.values() if isinstance(patches, dict) else patches
    names = {int(p["id"]): p["name"] for p in patch_rows}
    name = names.get(detail.get("patch"), "unknown")
    candidate = name == "7.41" and in_741f_window(detail, int(detail["patch"]))
    if candidate:
        name = "7.41f"
    metadata = match["metadata"]
    metadata["start_time"] = detail.get("start_time")
    metadata["start_time_source"] = "OpenDota"
    league = detail.get("league") or {}
    if league.get("name"):
        metadata["league_name"] = league["name"]
    metadata["opendota_patch_id"] = detail.get("patch")
    metadata["patch_method"] = "major_patch_and_release_window" if candidate else "opendota_major_patch"
    folder = relocate_match_folder(match)
    (folder / "opendota.json").write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
    with store.db:
        store.db.execute("UPDATE matches SET patch=?,patch_verified=?,metadata_json=? WHERE id=?",
                         (name, 0, json.dumps(metadata, ensure_ascii=False), key))
    return name


def fetch_pro_samples(store: Store, pages: int = 3, patch: str = "7.41f") -> int:
    """Cache public professional matches and exact-patch match details."""
    target = patch_id(store, patch)
    if target is None:
        raise RuntimeError(f"OpenDota has no patch id for {patch}")
    cursor: int | None = None
    added = 0
    for _ in range(pages):
        path = "proMatches" + (f"?less_than_match_id={cursor}" if cursor else "")
        page = _api(store, path)
        if not page:
            break
        paths = [f"matches/{item['match_id']}" for item in page if item.get("match_id")]
        missing = [path for path in paths if not store.db.execute(
            "SELECT 1 FROM api_cache WHERE key=?", (path,)).fetchone()]
        # Fetch concurrently, then write on this thread (sqlite connections are
        # thread-bound). The bounded pool avoids hundreds of open requests.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(_download_api, path): path for path in missing}
            for future in as_completed(futures):
                detail = future.result()
                with store.db:
                    store.db.execute("INSERT OR REPLACE INTO api_cache(key,payload_json) VALUES (?,?)",
                                     (futures[future], json.dumps(detail)))
        for path in paths:
            detail = _api(store, path)
            if in_741f_window(detail, target) and detail.get("picks_bans"):
                added += 1
        cursor = min(int(item["match_id"]) for item in page)
    return added


def cached_pro_samples(store: Store, patch: str = "7.41f") -> list[dict[str, Any]]:
    target = patch_id(store, patch)
    if target is None:
        return []
    rows = store.db.execute("SELECT payload_json FROM api_cache WHERE key LIKE 'matches/%'")
    samples = []
    for row in rows:
        detail = json.loads(row[0])
        if not in_741f_window(detail, target) or not detail.get("leagueid"):
            continue
        picks = detail.get("picks_bans") or []
        radiant = [int(x["hero_id"]) for x in picks if x.get("is_pick") and x.get("team") == 0]
        dire = [int(x["hero_id"]) for x in picks if x.get("is_pick") and x.get("team") == 1]
        if len(radiant) != 5 or len(dire) != 5 or detail.get("radiant_win") is None:
            continue
        samples.append({"match_id": int(detail["match_id"]), "start_time": int(detail["start_time"]),
                        "radiant": radiant, "dire": dire, "win": int(bool(detail["radiant_win"]))})
    return sorted(samples, key=lambda x: (x["start_time"], x["match_id"]))


def _sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-max(-30, min(30, value))))


def _features(sample: dict[str, Any]) -> dict[int, int]:
    result = defaultdict(int)
    for hero in sample["radiant"]:
        result[hero] += 1
    for hero in sample["dire"]:
        result[hero] -= 1
    return dict(result)


def _predict(weights: dict[int, float], bias: float, sample: dict[str, Any]) -> float:
    return _sigmoid(bias + sum(weights.get(hero, 0.0) * value for hero, value in _features(sample).items()))


def train_model(store: Store, patch: str = "7.41f", min_train: int = 200,
                min_valid: int = 50) -> dict[str, Any]:
    samples = cached_pro_samples(store, patch)
    if len(samples) < min_train + min_valid:
        return {"accepted": False, "reason": f"职业赛样本不足：{len(samples)}/{min_train+min_valid}"}
    split = int(len(samples) * .8)
    if split < min_train or len(samples) - split < min_valid:
        return {"accepted": False, "reason": "时间验证集样本不足"}
    train, valid = samples[:split], samples[split:]
    cutoff = valid[0]["start_time"]
    weights: dict[int, float] = defaultdict(float)
    wins = sum(s["win"] for s in train)
    bias = math.log((wins + 1) / (len(train) - wins + 1))
    # Full-batch gradient with L2 regularization keeps training deterministic.
    for _ in range(350):
        grad: dict[int, float] = defaultdict(float)
        grad_bias = 0.0
        for sample in train:
            error = _predict(weights, bias, sample) - sample["win"]
            grad_bias += error
            for hero, value in _features(sample).items():
                grad[hero] += error * value
        n = len(train)
        for hero in set(weights) | set(grad):
            weights[hero] -= .45 * (grad[hero] / n + .01 * weights[hero])
        bias -= .45 * grad_bias / n
    probabilities = [_predict(weights, bias, s) for s in valid]
    brier = sum((p - s["win"]) ** 2 for p, s in zip(probabilities, valid)) / len(valid)
    base_p = wins / len(train)
    baseline = sum((base_p - s["win"]) ** 2 for s in valid) / len(valid)
    bins = [[] for _ in range(10)]
    for probability, sample in zip(probabilities, valid):
        bins[min(9, int(probability * 10))].append((probability, sample["win"]))
    ece = sum(len(bucket) / len(valid) * abs(sum(p for p, _ in bucket) / len(bucket) -
              sum(y for _, y in bucket) / len(bucket)) for bucket in bins if bucket)
    accepted = brier < baseline and ece <= .10
    model = {"weights": weights, "bias": bias, "patch": patch}
    with store.db:
        store.db.execute("""INSERT INTO bp_models
            (patch,cutoff,train_start,train_end,validation_end,train_n,valid_n,brier,baseline_brier,ece,accepted,model_json,sample_ids_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (patch, cutoff, train[0]["start_time"],
            train[-1]["start_time"], valid[-1]["start_time"], len(train), len(valid), brier, baseline,
            ece, int(accepted), json.dumps(model), json.dumps([s["match_id"] for s in samples])))
    return {"accepted": accepted, "cutoff": cutoff, "train_start": train[0]["start_time"],
            "train_end": train[-1]["start_time"], "validation_end": valid[-1]["start_time"],
            "train_n": len(train), "valid_n": len(valid),
            "brier": brier, "baseline_brier": baseline, "ece": ece}


def predict(store: Store, match_start: int, radiant: list[int], dire: list[int],
            patch: str = "7.41f") -> float | None:
    """No model trained with or after this match can be used for its prediction."""
    row = store.db.execute("""SELECT * FROM bp_models WHERE patch=? AND accepted=1
        AND validation_end<? ORDER BY trained_at DESC,id DESC LIMIT 1""", (patch, match_start)).fetchone()
    if row is None or len(radiant) != 5 or len(dire) != 5:
        return None
    model = json.loads(row["model_json"])
    weights = {int(k): v for k, v in model["weights"].items()}
    return _predict(weights, model["bias"], {"radiant": radiant, "dire": dire})


def prediction_for_match(store: Store, match: dict[str, Any]) -> dict[str, Any] | None:
    if match.get("patch") != "7.41f" or match.get("metadata", {}).get("patch_method") != "major_patch_and_release_window":
        return None
    start = match["metadata"].get("start_time")
    if not isinstance(start, int):
        return None
    def picks(team: int) -> list[int]:
        return [int(row["raw"]["opendota_hero_id"]) for row in match["draft"]
                if row["team"] == team and row["action"] == "pick" and row["raw"].get("opendota_hero_id")]
    probability = predict(store, start, picks(2), picks(3))
    if probability is None:
        return None
    row = store.db.execute("""SELECT id,cutoff,train_start,train_end,validation_end,train_n,valid_n,brier,baseline_brier,ece
        FROM bp_models WHERE patch='7.41f' AND accepted=1 AND validation_end<?
        ORDER BY trained_at DESC,id DESC LIMIT 1""", (start,)).fetchone()
    return {"radiant_probability": probability, **dict(row)} if row else None
