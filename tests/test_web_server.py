import http.client
from contextlib import closing
import json
import os
import shutil
import subprocess
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dota_review.storage import SCHEMA
from dota_review import web_server
from dota_review.core import active_wards


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / ("review # data.sqlite3" if os.name == "nt" else "review ?# data.sqlite3")
        db = sqlite3.connect(self.db_path)
        db.executescript(SCHEMA)
        for key, radiant, dire in [("a" * 16, "OG", "Other"),
                                   ("b" * 16, "Other", "team liquid"),
                                   ("c" * 16, "Other", "Unknown")]:
            db.execute("""INSERT INTO matches
                (id, sha256, filename, match_id, radiant_name, dire_name, duration_s,
                 patch, parse_status, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (key, key, "/secret/local/file", "123", radiant, dire, 1200,
                 "7.41", "complete", '{"secret":"raw-secret"}'))
        db.execute("INSERT INTO players (match_key,slot,team,hero,account_id,player_name) VALUES (?,?,?,?,?,?)",
                   ("a" * 16, 0, 2, "axe", "secret-account", "Player"))
        db.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?)", [
            ("a" * 16, 0, 20, 999, 1, 2), ("a" * 16, 0, 10, 888, 3, 4)])
        db.executemany("INSERT INTO wards VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("a" * 16, 0, 2, "observer", 20, None, 1, 2, 0, "recording_end", "secret-evidence", "raw-secret"),
            ("a" * 16, 1, 3, "sentry", 10, 30, 3, 4, None, "expired", "secret-evidence", "raw-secret")])
        db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("a" * 16, 0, 20, "kill", None, 2, 0, 1, 2, "later", "secret-evidence", "raw-secret"),
            ("a" * 16, 1, 10, "rune", None, 3, None, 3, 4, "earlier", "secret-evidence", "raw-secret")])
        db.execute("INSERT INTO creeps VALUES (?,?,?,?,?,?,?)", ("a" * 16, "hidden", "lane", 2, 10, 1, 2))
        db.execute("INSERT INTO map_features VALUES (?,?,?,?,?,?,?,?)",
                   ("a" * 16, "1:2", "tower", 2, "npc_dota_goodguys_tower1_mid", 10000, 12000, "replay_entity"))
        db.commit()
        db.close()
        self.server = web_server.ReviewServer(0, self.db_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_list_filter_and_fields(self):
        status, headers, body = self.request("/api/matches")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        matches = json.loads(body)["matches"]
        self.assertEqual({m["id"] for m in matches}, {"a" * 16, "b" * 16})
        self.assertEqual(set(matches[0]), {"id", "match_id", "radiant_name", "dire_name", "duration_s", "patch", "parse_status"})
        self.assertNotIn(b"secret", body)

    def test_detail_sorted_and_minimal(self):
        status, _, body = self.request("/api/matches/" + "a" * 16)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(set(data), {"match", "players", "positions", "wards", "events", "map_features", "map_observations", "map_feature_coverage", "tower_identity_coverage", "map_objective_coverage", "creep_coverage", "ability_event_coverage"})
        self.assertEqual(data["map_features"], [{"feature_id":"1:2", "kind":"tower", "team":2, "name":"npc_dota_goodguys_tower1_mid", "x":10000.0, "y":12000.0}])
        self.assertEqual(data["map_observations"], [])
        self.assertIsNone(data["ability_event_coverage"])
        self.assertIsNone(data["tower_identity_coverage"])
        self.assertEqual(data["players"], [{"slot": 0, "team": 2, "hero": "axe", "player_name": "Player"}])
        self.assertEqual([p["time_s"] for p in data["positions"]["0"]], [10, 20])
        self.assertEqual(set(data["positions"]["0"][0]), {"time_s", "x", "y"})
        self.assertEqual([p["time_s"] for p in data["wards"]], [10, 20])
        self.assertEqual([p["time_s"] for p in data["events"]], [10, 20])
        self.assertEqual(set(data["wards"][0]), {"team", "kind", "time_s", "end_s", "end_reason", "x", "y"})
        self.assertEqual([w["end_reason"] for w in data["wards"]], ["expired", "recording_end"])
        self.assertIsNone(data["wards"][1]["end_s"])
        self.assertEqual([w["kind"] for w in active_wards(data["wards"], 19)], ["sentry"])
        self.assertEqual([w["kind"] for w in active_wards(data["wards"], 20)], ["sentry", "observer"])
        self.assertEqual([w["kind"] for w in active_wards(data["wards"], 30)], ["observer"])
        self.assertEqual([w["kind"] for w in active_wards(data["wards"], 1200)], ["observer"])
        self.assertEqual(set(data["events"][0]), {"time_s", "type", "subtype", "team", "slot", "description"})
        for secret in (b"secret", b"account_id", b"raw_json", b"creeps", b"/secret/local/file"):
            self.assertNotIn(secret, body)

    def test_unknown_disallowed_and_paths(self):
        for path in ("/api/matches/" + "c" * 16, "/api/matches/" + "d" * 16,
                     "/api/matches/../x", "/api/matches/%61" + "a" * 15,
                     "/api/matches/" + "a" * 16 + "?x=1", "/api/matches/", "/api/matches?x=1",
                     "/dota_review/storage.py", "/favicon.ico"):
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 404)
                self.assertEqual(headers["Cache-Control"], "no-store")

    def test_host_and_methods(self):
        for host in ("evil.example", "localhost.evil", "127.0.0.1.evil", "[::1]", "localhost:99999"):
            with self.subTest(host=host):
                self.assertEqual(self.request("/api/matches", headers={"Host": host})[0], 400)
        self.assertEqual(self.request("/api/matches", headers={"Host": "localhost:1234"})[0], 200)
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"):
            with self.subTest(method=method):
                status, headers, _ = self.request("/api/matches", method=method)
                self.assertEqual(status, 405)
                self.assertEqual(headers["Cache-Control"], "no-store")

    def test_static_exact_file_only(self):
        html = self.root / "web_ui.html"
        html.write_text("<!doctype html><title>review</title>", encoding="utf-8")
        with patch.object(web_server, "_HTML", html):
            for path in ("/", "/web_ui.html"):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith("text/html"))
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertIn(b"review", body)
            self.assertEqual(self.request("/web_ui.html?x=1")[0], 404)
        with patch.object(web_server, "_HTML", self.root / "missing.html"):
            self.assertEqual(self.request("/")[0], 404)

    def test_map_asset_exact_path(self):
        image = self.root / "map.png"
        image.write_bytes(b"test image")
        with patch.object(web_server, "_MAP", image):
            status, headers, body = self.request("/assets/dota2_map_7_41.png")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertEqual(body, b"test image")
            for path in ("/assets/dota2_map_7_41.png?x=1", "/assets/../web_ui.html",
                         "/assets/%64ota2_map_7_41.png", "/assets/other.png"):
                self.assertEqual(self.request(path)[0], 404)
        with patch.object(web_server, "_MAP", self.root / "missing.png"):
            self.assertEqual(self.request("/assets/dota2_map_7_41.png")[0], 404)

    def test_database_read_only_and_missing(self):
        with closing(web_server._connect(self.db_path)) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM matches")
        missing = self.root / "not-created.sqlite3"
        self.server.db_path = missing
        status, headers, body = self.request("/api/matches")
        self.assertEqual(status, 500)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn(str(missing).encode(), body)
        self.assertFalse(missing.exists())


@unittest.skipUnless(shutil.which("node"), "Node.js is required for Web UI behavior tests")
class WebUiBehaviorTests(unittest.TestCase):
    def test_timeline_wards_and_clamped_coordinates(self):
        # Run the actual page script with a minimal DOM, exercising public controls.
        html = (Path(web_server.__file__).with_name("web_ui.html")).read_text(encoding="utf-8")
        page_script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r'''
const assert = require('assert');
class Element {
  constructor(name) { this.name = name; this.children = []; this.attrs = {}; this.listeners = {}; this.value = ''; this.textContent = ''; this.disabled = false; this.dataset = {}; this.classList = {add(){}, remove(){}, toggle(){return false;}}; }
  setAttribute(k,v) { this.attrs[k] = String(v); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  trigger(type) { if (this.listeners[type]) this.listeners[type]({target:this, currentTarget:this}); }
  focus() {}
  remove() { this.removed = true; }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
const trails = get('trail-toggle'); trails.dataset.layer = 'trails';
const objectives = get('objective-toggle'); objectives.dataset.layer = 'objectives';
global.document = {getElementById:get, createElement:name => new Element(name), createElementNS:(_,name) => new Element(name), querySelectorAll:() => [trails, objectives]};
global.requestAnimationFrame = () => 1; global.cancelAnimationFrame = () => {};
const fixture = {
  match:{id:'a', duration_s:100, patch:'7.41f', parse_status:'complete'}, players:[{slot:0, team:2, hero:'axe'}, {slot:5, team:3, hero:'pudge'}],
  positions:{0:[{time_s:-120,x:0,y:30000}, {time_s:-119,x:7563,y:7800}, {time_s:-118,x:16731.5,y:16700}, {time_s:'Infinity',x:0,y:0}], 5:[{time_s:-119,x:8000,y:8000}, {time_s:-118,x:9000,y:9000}]},
  wards:[{kind:'observer',team:2,time_s:-120,end_s:null,x:30000,y:0}, {kind:'sentry',team:3,time_s:-119,end_s:-118,x:7563,y:7800}, {kind:'observer',team:2,time_s:-118,end_s:-117,x:16731.5,y:16700}, {time_s:-120,end_s:'Infinity',x:10000,y:10000}, {time_s:-120,end_s:null,x:' ',y:false}],
  events:[{time_s:-120,type:'early'}, {time_s:-89,type:'pre'}, {time_s:100,type:'end'}, {time_s:'-Infinity',type:'bad'}, {time_s:-120,type:'glyph',team:2}, {time_s:-120,type:'scan',team:3}, {time_s:0,type:'tower',subtype:'npc_dota_goodguys_tower1_mid'}],
  map_features:[{feature_id:'tower:1',kind:'tower',team:2,name:'npc_dota_goodguys_tower1_mid',x:10000,y:12000},{feature_id:'shop:1',kind:'shop',team:5,name:null,x:21270,y:15176}], map_observations:[],
  map_feature_coverage:'complete', tower_identity_coverage:'complete', map_objective_coverage:'complete', creep_coverage:'partial', ability_event_coverage:{glyph:'complete',scan:'partial'}
};
global.fetch = async url => ({ok:true, json:async () => url === '/api/matches' ? {matches:[{id:'a'}]} : fixture});
''' + page_script + r'''
setImmediate(() => {
  try {
    const objectiveText = get('objective-state').textContent;
    assert.equal(get('objective-layer').children.length, 24); assert.ok(objectiveText.includes('防御塔 22 座')); assert.ok(objectiveText.includes('录像塔防次数天辉 1 / 夜魇 0')); assert.ok(objectiveText.includes('录像扫描次数天辉 已记录 0（不完整） / 夜魇 已记录 1（不完整）'));
    assert.match(get('objective-layer').children[0].children[0].textContent, /池内数量未知/);
    assert.ok(get('objective-layer').children.some(e => e.attrs['data-kind'] === 'shop'));
    const killedTower = () => get('objective-layer').children.find(e => e.attrs['data-feature-id'] === 'radiant-tower:tier1-mid');
    get('range').value = '-1'; get('range').trigger('input'); assert.equal(killedTower().attrs['data-state'], 'alive');
    get('range').value = '0'; get('range').trigger('input'); assert.equal(killedTower(), undefined); assert.equal(get('objective-layer').children.length, 24);
    get('range').value = '-120'; get('range').trigger('input');
    objectives.trigger('click'); assert.equal(get('objective-layer').children.length, 0); objectives.trigger('click'); assert.equal(get('objective-layer').children.length, 25);
    assert.equal(get('range').min, '-120'); assert.equal(get('start-tick').textContent, '-02:00');
    const events = get('events').children;
    const early = events.find(e => e.children[1].textContent === 'early');
    assert.equal(early.children[0].textContent, '-02:00'); assert.equal(early.disabled, false); early.trigger('click');
    assert.equal(get('time').textContent, '-02:00');
    assert.equal(get('hero-count').textContent, '1'); assert.equal(get('ward-count').textContent, '1');
    const hero = get('hero-layer').children[0], ward = get('ward-layer').children[0];
    assert.equal(hero.attrs.transform, 'translate(59.57 59.57)'); assert.match(hero.children[0].textContent, /坐标被裁至地图边界/);
    assert.match(hero.children[3].attrs.href, /\/heroes\/axe\.png$/);
    const portrait = get('radiant-roster').children[1];
    assert.match(portrait.children[0].src, /\/heroes\/axe\.png$/);
    portrait.children[0].onerror(); assert.equal(portrait.children[0].removed, true); assert.equal(portrait.textContent, '?');
    assert.equal(ward.attrs.transform, 'translate(939.45 935.55)'); assert.match(ward.children[0].textContent, /结束时间未知/); assert.match(ward.children[0].textContent, /坐标被裁至地图边界/);
    trails.trigger('click'); get('range').value = '-119'; get('range').trigger('input');
    assert.equal(get('time').textContent, '-01:59'); assert.equal(get('ward-count').textContent, '2');
    assert.equal(get('trail-layer').children[0].attrs.points, '59.57,59.57 59.57,935.55'); assert.match(get('trail-layer').children[0].children[0].textContent, /裁至地图边界/);
    get('range').value = '-118'; get('range').trigger('input'); assert.equal(get('ward-count').textContent, '2');
    assert.equal(get('hero-layer').children[0].attrs.transform, 'translate(499.51 497.55999999999995)');
    assert.equal(get('ward-layer').children[1].attrs.transform, 'translate(499.51 497.55999999999995)');
    assert.equal(get('trail-layer').children.length, 2);
    const radiant = get('radiant-roster').children[1], dire = get('dire-roster').children[1];
    radiant.trigger('click'); assert.equal(radiant.attrs['aria-pressed'], 'true'); assert.equal(get('trail-layer').children.length, 1);
    assert.match(get('trail-layer').children[0].attrs.points, /^59.57,59.57/);
    dire.trigger('click'); assert.equal(radiant.attrs['aria-pressed'], 'false'); assert.equal(dire.attrs['aria-pressed'], 'true'); assert.equal(get('trail-layer').children.length, 1);
    dire.trigger('click'); assert.equal(dire.attrs['aria-pressed'], 'false'); assert.equal(get('trail-layer').children.length, 2);
    trails.trigger('click'); assert.equal(get('trail-layer').children.length, 0);
    radiant.trigger('click'); assert.equal(trails.attrs['aria-pressed'], 'true'); assert.equal(get('trail-layer').children.length, 1);
    const end = events.find(e => e.children[1].textContent === 'end'); end.trigger('click');
    assert.equal(get('ward-count').textContent, '1'); get('play').trigger('click'); assert.equal(get('time').textContent, '-02:00');
    fixture.match.duration_s = null; fixture.match.patch = '7.40'; fixture.positions = {}; fixture.wards = []; fixture.events = [{time_s:-1,type:'pre'}];
    get('matches').children[0].trigger('click');
    setImmediate(() => { try { assert.equal(get('objective-layer').children.length, 0); assert.match(get('objective-state').textContent, /地图版本不匹配/); assert.equal(get('radiant-roster').children[1].attrs['aria-pressed'], 'false'); assert.equal(get('range').min, '-90'); assert.equal(get('start-tick').textContent, '-01:30'); assert.equal(get('range').disabled, false); get('range').value = '-1'; get('range').trigger('input'); assert.equal(get('time').textContent, '-00:01'); } catch(e) { console.error(e); process.exitCode = 1; } });
  } catch(e) { console.error(e); process.exitCode = 1; }
});
'''
        result = subprocess.run([shutil.which("node"), "-"], input=harness, text=True,
                                encoding="utf-8", capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
