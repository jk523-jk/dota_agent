"""Desktop and command-line entry points."""

from __future__ import annotations

import argparse
from pathlib import Path

from .app import run
from .bp import prediction_for_match
from .export import write_csv, write_html
from .ingest import import_managed_replay, parse_replay
from .paths import DATA_ROOT, default_db, match_dir
from .storage import Store
from .teams import allowed_match


def main() -> None:
    parser = argparse.ArgumentParser(description="Dota 2 replay review")
    sub = parser.add_subparsers(dest="command")
    importer = sub.add_parser("import", help="import local .dem files")
    importer.add_argument("replays", nargs="+", type=Path)
    importer.add_argument("--db", type=Path, default=default_db())
    exporter = sub.add_parser("export", help="write offline report and CSV files")
    exporter.add_argument("match_key")
    exporter.add_argument("--db", type=Path, default=default_db())
    refresher = sub.add_parser("refresh-map", help="refresh derived map events and features without replacing match data")
    refresher.add_argument("replays", nargs="+", type=Path)
    refresher.add_argument("--db", type=Path, default=default_db())
    exporter.add_argument("--out", type=Path, help="output folder inside dota_data")
    args = parser.parse_args()
    if args.command == "import":
        with Store(args.db) as store:
            for replay in args.replays:
                match = import_managed_replay(replay)
                print(f"{match['id']}: {'imported' if store.save(match) else 'already imported'}")
    elif args.command == "refresh-map":
        with Store(args.db) as store:
            for replay in args.replays:
                data = parse_replay(replay)
                refreshed = store.refresh_map_data(data)
                print(f"{data['match_id'] or data['id']}: {'map data refreshed' if refreshed else 'skipped (match SHA-256 does not match database)'}")
    elif args.command == "export":
        with Store(args.db) as store:
            match = store.load(args.match_key)
            if not allowed_match(match):
                parser.error("this match is outside the selected team scope")
            match["bp_prediction"] = prediction_for_match(store, match)
        output = args.out or match_dir(match)
        if not output.resolve().is_relative_to(DATA_ROOT.resolve()):
            parser.error("reports must be saved inside dota_data")
        print(write_html(match, output / "report.html"))
        for path in write_csv(match, output):
            print(path)
    else:
        run()


if __name__ == "__main__":
    main()
