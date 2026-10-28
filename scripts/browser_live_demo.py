"""Open the live MySQL-backed UI and verify an actual inventory snapshot."""
from __future__ import annotations

from pathlib import Path
import sys

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota_review.api import get_match, list_matches
from dota_review.mysql import mysql_connection

MAP_WORLD = {"xmin": 7563, "xmax": 25900, "ymin": 7800, "ymax": 25600}
MAP_ART = {"left": 59.57, "right": 939.45, "top": 59.57, "bottom": 935.55}
RADIANT_BASE = {
    "radiant-base:tower3_top": (9792, 12976), "radiant-base:tower3_mid": (11744, 12240),
    "radiant-base:tower3_bot": (12432, 10272), "radiant-base:barracks_melee_top": (10048, 12626),
    "radiant-base:barracks_range_top": (9540, 12625), "radiant-base:barracks_melee_mid": (11712, 11832),
    "radiant-base:barracks_range_mid": (11324, 12185), "radiant-base:barracks_melee_bot": (12104, 10024),
    "radiant-base:barracks_range_bot": (12105, 10531), "radiant-base:tower4_top": (10672, 11520),
    "radiant-base:tower4_bot": (10992, 11192), "radiant-base:ancient": (10464, 11032),
}


def project(x: float, y: float) -> tuple[float, float]:
    return (
        MAP_ART["left"] + (MAP_ART["right"] - MAP_ART["left"]) * (x - MAP_WORLD["xmin"]) / (MAP_WORLD["xmax"] - MAP_WORLD["xmin"]),
        MAP_ART["top"] + (MAP_ART["bottom"] - MAP_ART["top"]) * (MAP_WORLD["ymax"] - y) / (MAP_WORLD["ymax"] - MAP_WORLD["ymin"]),
    )


def assert_transform(transform: str, expected: tuple[float, float]) -> None:
    values = transform.removeprefix("translate(").removesuffix(")").split()
    actual = float(values[0]), float(values[1])
    if abs(actual[0] - expected[0]) > 0.001 or abs(actual[1] - expected[1]) > 0.001:
        raise AssertionError(f"map projection mismatch: {actual} != {expected}")


def main() -> None:
    key = list_matches()["matches"][0]["id"]
    detail = get_match(key)
    original = detail["players"]
    tested_player = original[0]
    old_role = int(tested_player["role"])
    new_role = old_role % 5 + 1
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.set_default_timeout(15_000)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(f"console.error {message.location.get('url', '')}: {message.text}") if message.type == "error" else None)
        page.on("response", lambda response: errors.append(f"HTTP {response.status} {response.url}") if response.status >= 400 else None)
        try:
            page.goto("http://127.0.0.1:5173/", wait_until="networkidle")
            selected_match = page.locator(".match-row.selected")
            if not selected_match.count():
                raise AssertionError(f"no match loaded: {page.locator('body').inner_text()[:500]}; errors={errors}")
            selected_match.wait_for()
            match_name = selected_match.locator(".match-pair").inner_text()
            range_input = page.get_by_role("slider", name="选择游戏时间")
            range_input.evaluate("element => { element.value = '600'; element.dispatchEvent(new Event('input', {bubbles:true})); }")
            page.locator(".team-roster button").first.click()
            page.locator(".item-slot.occupied").first.wait_for()
            hero = page.locator(".inventory-title .eyebrow").inner_text()
            items = page.locator(".item-slot.occupied").all_inner_texts()
            if not items or errors:
                raise AssertionError(f"live UI failed: items={items}, page_errors={errors}")

            agent_actions = page.locator(".agent-shortcuts button")
            if agent_actions.count() != 3:
                raise AssertionError("agent analysis shortcuts are missing")
            agent_actions.nth(0).click()
            page.wait_for_function("document.querySelectorAll('.agent-turn.assistant').length >= 1")
            if page.locator(".agent-turn.assistant .analysis-card").count() < 4:
                raise AssertionError("player analysis did not render its economy evidence")
            page.locator(".agent-citations button").nth(1).click()
            if range_input.input_value() != "900":
                raise AssertionError("agent evidence citation did not jump the match timeline")
            range_input.evaluate("element => { element.value = '600'; element.dispatchEvent(new Event('input', {bubbles:true})); }")
            agent_actions.nth(1).click()
            page.wait_for_function("document.querySelectorAll('.agent-turn.assistant').length >= 2")
            page.locator(".ward-heat-layer g").first.wait_for()
            if not page.locator(".agent-turn.assistant .hotspot-card").count():
                raise AssertionError("ward hotspot analysis did not render")
            agent_actions.nth(2).click()
            page.wait_for_function("document.querySelectorAll('.agent-turn.assistant').length >= 3")
            if not page.locator(".team-style-metrics").count():
                raise AssertionError("team style analysis did not render its sample metrics")
            if errors:
                raise AssertionError(f"agent interaction produced browser errors: {errors}")

            feature_elements = page.locator(".feature-layer g.feature-marker")
            feature_count = feature_elements.count()
            dynamic_features = [feature for feature in detail["map_features"] if not (
                feature["team"] == 2 and (feature["kind"] in ("ancient", "barracks") or
                    feature["kind"] == "tower" and ("goodguys_tower3_" in (feature["name"] or "") or
                    "goodguys_tower4" in (feature["name"] or ""))))]
            expected_count = len(dynamic_features) + len(RADIANT_BASE)
            if feature_count != expected_count:
                raise AssertionError(f"map target count mismatch: {feature_count} != {expected_count}")
            if page.locator(".feature-layer g.feature-marker > image").count() != feature_count:
                raise AssertionError("a map target is missing its Dota 2 minimap icon")
            feature_dom = feature_elements.evaluate_all("nodes => nodes.map(n => ({id:n.dataset.featureId, kind:n.dataset.kind, x:Number(n.dataset.worldX), y:Number(n.dataset.worldY), transform:n.getAttribute('transform')}))")
            source_features = {str(feature["feature_id"]): feature for feature in detail["map_features"]}
            for marker in feature_dom:
                if marker["id"] in RADIANT_BASE:
                    expected_world = RADIANT_BASE[marker["id"]]
                    if (marker["x"], marker["y"]) != expected_world:
                        raise AssertionError(f"Radiant static anchor mismatch: {marker['id']} {marker['x'], marker['y']} != {expected_world}")
                else:
                    feature = source_features[marker["id"]]
                    if (marker["x"], marker["y"]) != (feature["x"], feature["y"]):
                        raise AssertionError(f"replay map coordinates changed unexpectedly for {marker['id']}")
                x, y = project(marker["x"], marker["y"])
                if marker["kind"] == "roshan": x, y = x + 40, y - 8
                if marker["kind"] == "tormentor": x, y = x + 26, y - 18
                assert_transform(marker["transform"], (x, y))

            ward_dom = page.locator(".ward-layer g.ward-marker").evaluate_all("nodes => nodes.map(n => ({x:Number(n.dataset.worldX), y:Number(n.dataset.worldY), transform:n.getAttribute('transform')}))")
            if len(ward_dom) != page.locator(".ward-layer image").count() or not ward_dom:
                raise AssertionError("wards are missing Dota 2 minimap icons")
            for marker in ward_dom:
                assert_transform(marker["transform"], project(marker["x"], marker["y"]))

            hero_dom = page.locator(".hero-layer g.hero-marker").evaluate_all("nodes => nodes.map(n => ({slot:Number(n.dataset.slot), x:Number(n.dataset.worldX), y:Number(n.dataset.worldY), transform:n.getAttribute('transform')}))")
            if len(hero_dom) != page.locator(".hero-layer image").count() or not hero_dom:
                raise AssertionError("heroes are missing Dota 2 portraits on the map")
            for marker in hero_dom:
                assert_transform(marker["transform"], project(marker["x"], marker["y"]))

            role_select = page.get_by_label("编辑英雄分路")
            role_save = page.get_by_role("button", name="保存分路")
            role_select.select_option(str(new_role))
            role_save.click()
            page.get_by_text("分路已保存").wait_for()
            page.wait_for_function("document.querySelector('.inventory-tools button')?.disabled === true")
            with mysql_connection() as db, db.cursor() as cursor:
                cursor.execute("SELECT role FROM dota_players WHERE match_key=%s AND slot=%s",
                               (key, tested_player["slot"]))
                if int(cursor.fetchone()["role"]) != new_role:
                    raise AssertionError("browser role save did not persist")
            role_select.select_option(str(old_role))
            role_save.click()
            page.get_by_text("分路已保存").wait_for()
            page.wait_for_function("document.querySelector('.inventory-tools button')?.disabled === true")
            output = ROOT / "artifacts" / "live-db-demo.png"
            output.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(output), full_page=True)
            print(f"live browser demo: {match_name.strip()} · {hero} · 10:00 · {', '.join(items)}")
            print("live browser agent: player economy · ward heatmap · team style · evidence links")
            print(f"live UI role save: {old_role} → {new_role} → restored to {old_role}")
            print(f"screenshot: {output}")
        finally:
            with mysql_connection() as db, db.cursor() as cursor:
                cursor.executemany("UPDATE dota_players SET role=%s,role_source=%s "
                    "WHERE match_key=%s AND slot=%s", [
                        (row["role"], row["role_source"], key, row["slot"]) for row in original])
                db.commit()
            browser.close()


if __name__ == "__main__":
    main()
