"""Read-only smoke check against the configured MySQL-backed FastAPI service."""
from __future__ import annotations

import json
from urllib.request import urlopen


def main() -> None:
    with urlopen("http://127.0.0.1:8765/api/matches", timeout=20) as response:
        payload = json.load(response)
    matches = payload["matches"]
    if not matches:
        raise SystemExit("No matches returned from MySQL")
    key = matches[0]["id"]
    with urlopen(f"http://127.0.0.1:8765/api/matches/{key}", timeout=20) as response:
        detail = json.load(response)
    assert detail["match"]["id"] == key
    assert len(detail["players"]) > 0
    print(f"live API smoke: {len(matches)} matches listed; detail for {key} returned {len(detail['players'])} players")


if __name__ == "__main__":
    main()
