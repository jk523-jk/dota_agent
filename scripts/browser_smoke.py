"""Black-box smoke check for the interactive Vue review screen."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from playwright.sync_api import sync_playwright


MATCH_KEY = "a" * 16
ROOT = Path(__file__).resolve().parents[1]
detail = {
    "match": {"id": MATCH_KEY, "match_id": "12345", "radiant_name": "OG", "dire_name": "Team Liquid",
              "duration_s": 120, "patch": "7.41", "parse_status": "complete"},
    "players": [{"slot": 0, "team": 2, "hero": "axe", "player_name": "Axe Player", "role": 2, "role_source": "manual"}],
    "positions": {"0": [{"time_s": 0, "x": 15000, "y": 15000},
                          {"time_s": 42, "x": 17000, "y": 16000}]}, "wards": [], "events": [],
    "map_features": [], "map_observations": [], "map_feature_coverage": "complete",
    "tower_identity_coverage": "complete", "map_objective_coverage": "complete",
    "creep_coverage": "complete", "ability_event_coverage": {"glyph": "complete", "scan": "complete"},
}


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.set_default_timeout(5000)
        calls: list[tuple[str, dict]] = []

        def route_api(route):
            request = route.request
            if request.method == "GET" and request.url.endswith("/api/matches"):
                route.fulfill(json={"matches": [detail["match"]]})
            elif request.method == "GET" and f"/api/matches/{MATCH_KEY}/inventory" in request.url:
                query = parse_qs(urlparse(request.url).query)
                second = int(query["time_s"][0])
                route.fulfill(json={"slot": 0, "requested_time_s": second, "snapshot_time_s": second,
                                    "tick": second * 30, "items": {"0": "item_black_king_bar"} if second >= 40 else {"0": "item_blink", "1": "item_boots"},
                                    "coverage": "available"})
            elif request.method == "PATCH" and "/role" in request.url:
                body = request.post_data_json
                calls.append((request.method, body))
                route.fulfill(json={"slot": 0, "role": body["role"], "role_source": "manual"})
            else:
                route.fulfill(json=detail)

        page.route("**/api/**", route_api)
        def route_game_images(route):
            if "/items/" in route.request.url:
                route.continue_()
            else:
                route.fulfill(content_type="image/svg+xml",
                              body='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1" fill="#888"/></svg>')

        page.route("https://cdn.cloudflare.steamstatic.com/**", route_game_images)
        page.on("pageerror", lambda error: print("pageerror:", error))
        page.on("console", lambda message: print("console:", message.type, message.text) if message.type == "error" else None)
        page.on("requestfailed", lambda request: print("requestfailed:", request.url, request.failure))
        page.on("response", lambda response: print("response:", response.status, response.url) if response.status >= 400 else None)
        page.goto("http://127.0.0.1:5173/", wait_until="networkidle")
        page.wait_for_timeout(700)
        game_icon = page.locator(".brand-mark")
        assert game_icon.get_attribute("src") == "/assets/dota2-game-icon.jpg"
        assert page.locator('[data-feature-id="radiant-base:tower3_top"]').get_attribute("data-world-x") == "9887"
        assert page.locator('[data-feature-id="radiant-base:tower4_top"]').get_attribute("data-world-y") == "11797"
        page.locator(".team-roster button").click()
        page.locator(".hero-portrait").wait_for(state="visible")
        page.get_by_text("blink", exact=False).wait_for()
        assert page.get_by_text("当前装备").is_visible()
        assert page.locator(".item-slot .item-icon").count() == 2
        assert "/items/blink.png" in page.locator(".item-slot .item-icon").first.get_attribute("src")
        page.wait_for_function("() => [...document.querySelectorAll('.item-slot .item-icon')].every(img => img.complete && img.naturalWidth > 0)", timeout=15000)
        range_input = page.get_by_role("slider", name="选择游戏时间")
        range_input.evaluate("element => { element.value = '42'; element.dispatchEvent(new Event('input', {bubbles:true})); element.dispatchEvent(new Event('change', {bubbles:true})); }")
        page.locator(".inventory-title > span").filter(has_text="00:42").wait_for()
        page.locator(".item-slot.occupied").get_by_text("black king bar", exact=True).wait_for()
        page.get_by_label("编辑英雄分路").select_option("3")
        page.get_by_role("button", name="保存分路").click()
        page.get_by_text("分路已保存").wait_for()
        assert calls == [("PATCH", {"role": 3})], calls

        page.get_by_role("button", name="画线").click()
        map_box = page.locator(".tactical-map").bounding_box()
        assert map_box
        page.mouse.move(map_box["x"] + map_box["width"] * .3, map_box["y"] + map_box["height"] * .35)
        page.mouse.down()
        page.mouse.move(map_box["x"] + map_box["width"] * .48, map_box["y"] + map_box["height"] * .47, steps=8)
        page.mouse.up()
        assert page.locator(".user-drawing-layer polyline").count() == 1
        assert page.evaluate("matchKey => JSON.parse(localStorage.getItem('dota-review-map-lines:' + matchKey)).length", MATCH_KEY) == 1
        artifacts = ROOT / "artifacts"
        artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts / "interactive-demo.png"), full_page=True)
        page.reload(wait_until="networkidle")
        page.locator(".user-drawing-layer polyline").wait_for(state="visible")
        assert page.locator(".user-drawing-layer polyline").count() == 1
        print("browser smoke: game icon, item icons, time-synced inventory, role save, map drawing and persistence passed")
        browser.close()


if __name__ == "__main__":
    main()
