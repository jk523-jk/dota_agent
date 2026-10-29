"""Exercise the live inventory and role-write API, restoring original roles."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota_review.api import RoleUpdate, get_inventory, get_match, list_matches, update_player_role
from dota_review.mysql import mysql_connection


def main() -> None:
    match = list_matches()["matches"][0]
    key = match["id"]
    detail = get_match(key)
    player = detail["players"][0]
    inventory = get_inventory(key, int(player["slot"]), 600)
    if inventory["coverage"] != "available":
        raise RuntimeError("Live inventory query did not find a snapshot")
    print(f"inventory API: match={key}, slot={player['slot']}, "
          f"snapshot={inventory['snapshot_time_s']}s, items={len(inventory['items'])}")
    print(f"map data API: targets={len(detail['map_features'])}, "
          f"observations={len(detail['map_observations'])}, wards={len(detail['wards'])}")

    with mysql_connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT slot,role,role_source FROM dota_players WHERE match_key=%s ORDER BY slot", (key,))
        original = cursor.fetchall()
    if not original:
        raise RuntimeError("Selected match has no players")
    target = original[0]
    requested_role = (int(target["role"]) % 5 + 1) if target["role"] else 1
    try:
        result = update_player_role(key, int(target["slot"]), RoleUpdate(role=requested_role))
        saved = get_match(key)["players"]
        actual = next(row["role"] for row in saved if row["slot"] == target["slot"])
        if actual != requested_role:
            raise RuntimeError("Role update did not persist")
        print(f"role API: slot={target['slot']} saved as {actual}; teammate role swap verified")
    finally:
        # Restore the exact pre-test player rows. UPDATE privilege is limited to
        # these two columns and the selected local match remains unchanged.
        with mysql_connection() as db, db.cursor() as cursor:
            cursor.executemany("UPDATE dota_players SET role=%s,role_source=%s "
                "WHERE match_key=%s AND slot=%s", [
                    (row["role"], row["role_source"], key, row["slot"]) for row in original])
            db.commit()


if __name__ == "__main__":
    main()
