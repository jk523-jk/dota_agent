`dota2_map_7_41.png` is the Dota 2 7.41 in-game minimap asset, sourced from
[Liquipedia Commons' minimap category](https://liquipedia.net/commons/Category:Dota_2_minimap_images).

Dota 2 and its game assets belong to Valve Corporation. This file is included
as the local replay viewer's map background; the app overlays replay-derived
positions and does not modify the source image.

The `icons/` folder contains Dota 2 minimap icons mirrored by the Dota 2 Wiki:

- `tower-45-minimap.png` and `tower-90-minimap.png`: tower orientations.
- `barracks-45-minimap.png` and `barracks-90-minimap.png`: barracks orientations.
- `ancient-minimap.png`: the Ancient (遗迹).
- `observer-ward-minimap.png` and `sentry-ward-minimap.png`: True Sight and Observer ward markers.
- `roshan-minimap.png`: Roshan.
- `tormentor-minimap.png`: Tormentor (魔方).
- `twin-gate-minimap.png`: Fiend's Gate.
- `lotus-pool-minimap.png`, `main-shop-minimap.png`, `side-shop-minimap.png`, and `secret-shop-minimap.png`: Lotus Pool and shop icons.
- `neutral-camp-{small,medium,large,ancient}-minimap.png`: neutral camp size icons.
- `watcher-minimap.png`: Watcher (侦察守卫).
- `hero-{own,ally,enemy}-minimap.png`: in-game team-specific hero minimap markers.

Sources: [unit minimap icons](https://dota2.fandom.com/wiki/Category:Unit_minimap_icons)
and [minimap icons](https://dota2.fandom.com/wiki/Category:Minimap_icons).
Original game artwork belongs to Valve Corporation.

The frontend projects replay world coordinates using gem's 7.41 world bounds
(`x: 7563–25900`, `y: 7800–25600`) onto the visible map artwork bounds in this
1024 × 1024 image (approximately `x: 61–962`, `y: 61–958` pixels). This accounts
for the black padding around the map and keeps towers, objectives, camps, wards,
heroes, lanes, and vision estimates on the same projection.
