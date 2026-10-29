"""Copy the Dota review SQLite store into isolated dota_* MySQL tables.

The default mode is a read-only preview. Apply mode writes to temporary
dota_stage_* tables, verifies row counts, then renames them to dota_* tables.
Existing tables with either prefix cause the migration to stop.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pymysql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "dota_data" / "review.sqlite3"

# Parent tables precede their children. This order is also used for staging
# table creation and the final atomic rename.
TABLES = (
    "matches", "players", "positions", "inventory_snapshots", "creeps", "map_features", "economy",
    "wards", "events", "draft", "observations", "player_map", "thresholds",
    "api_cache", "bp_models",
)

DDL = {
    "matches": """(
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        sha256 CHAR(64) NOT NULL UNIQUE,
        filename TEXT NOT NULL,
        match_id VARCHAR(32), patch VARCHAR(32),
        patch_verified TINYINT NOT NULL DEFAULT 0,
        duration_s INT NOT NULL DEFAULT 0,
        radiant_name VARCHAR(255), dire_name VARCHAR(255),
        radiant_win TINYINT, parse_status VARCHAR(32) NOT NULL,
        imported_at VARCHAR(40) NOT NULL,
        metadata_json LONGTEXT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "players": """(
        match_key VARCHAR(64) NOT NULL, slot INT NOT NULL, team INT NOT NULL,
        hero VARCHAR(128), account_id VARCHAR(64), player_name VARCHAR(255),
        role INT, role_source VARCHAR(64),
        PRIMARY KEY(match_key, slot),
        CONSTRAINT fk_dota_players_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "positions": """(
        match_key VARCHAR(64) NOT NULL, slot INT NOT NULL, time_s INT NOT NULL,
        tick BIGINT, x DOUBLE NOT NULL, y DOUBLE NOT NULL,
        PRIMARY KEY(match_key, slot, time_s),
        KEY idx_dota_positions_time(match_key, time_s),
        CONSTRAINT fk_dota_positions_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "inventory_snapshots": """(
        match_key VARCHAR(64) NOT NULL, slot INT NOT NULL, time_s INT NOT NULL,
        tick BIGINT NOT NULL, items_json LONGTEXT NOT NULL,
        PRIMARY KEY(match_key, slot, tick),
        KEY idx_dota_inventory_time(match_key, slot, time_s, tick),
        CONSTRAINT fk_dota_inventory_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "creeps": """(
        match_key VARCHAR(64) NOT NULL, entity_id VARCHAR(191) NOT NULL,
        kind VARCHAR(64) NOT NULL, team INT, time_s INT NOT NULL,
        x DOUBLE NOT NULL, y DOUBLE NOT NULL,
        PRIMARY KEY(match_key, entity_id, time_s),
        KEY idx_dota_creeps_time(match_key, time_s),
        CONSTRAINT fk_dota_creeps_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "map_features": """(
        match_key VARCHAR(64) NOT NULL, feature_id VARCHAR(191) NOT NULL,
        kind VARCHAR(64) NOT NULL, team INT, name VARCHAR(255),
        x DOUBLE NOT NULL, y DOUBLE NOT NULL, evidence VARCHAR(128) NOT NULL,
        PRIMARY KEY(match_key, feature_id),
        CONSTRAINT fk_dota_map_features_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "economy": """(
        match_key VARCHAR(64) NOT NULL, slot INT NOT NULL, time_s INT NOT NULL,
        net_worth DOUBLE, gold DOUBLE, xp DOUBLE, last_hits INT,
        PRIMARY KEY(match_key, slot, time_s),
        CONSTRAINT fk_dota_economy_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "wards": """(
        match_key VARCHAR(64) NOT NULL, ordinal INT NOT NULL, team INT,
        kind VARCHAR(64), time_s INT NOT NULL, end_s INT, x DOUBLE, y DOUBLE,
        placer_slot INT, end_reason VARCHAR(128), evidence VARCHAR(128) NOT NULL,
        raw_json LONGTEXT NOT NULL,
        PRIMARY KEY(match_key, ordinal),
        CONSTRAINT fk_dota_wards_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "events": """(
        match_key VARCHAR(64) NOT NULL, ordinal INT NOT NULL,
        time_s INT NOT NULL, type VARCHAR(64) NOT NULL, subtype VARCHAR(128),
        team INT, slot INT, x DOUBLE, y DOUBLE, description TEXT,
        evidence VARCHAR(128) NOT NULL, raw_json LONGTEXT NOT NULL,
        PRIMARY KEY(match_key, ordinal), KEY idx_dota_events_time(match_key, time_s),
        CONSTRAINT fk_dota_events_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "draft": """(
        match_key VARCHAR(64) NOT NULL, ordinal INT NOT NULL, team INT,
        action VARCHAR(32), hero VARCHAR(128), game_time_s INT,
        raw_json LONGTEXT NOT NULL,
        PRIMARY KEY(match_key, ordinal),
        CONSTRAINT fk_dota_draft_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "observations": """(
        match_key VARCHAR(64) NOT NULL, ordinal INT NOT NULL,
        type VARCHAR(64) NOT NULL, time_s INT, end_s INT, team INT,
        evidence VARCHAR(128) NOT NULL, details_json LONGTEXT NOT NULL,
        PRIMARY KEY(match_key, ordinal),
        CONSTRAINT fk_dota_observations_match FOREIGN KEY(match_key)
            REFERENCES dota_stage_matches(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "player_map": """(
        account_id VARCHAR(64) NOT NULL PRIMARY KEY,
        canonical_name VARCHAR(255) NOT NULL, updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "thresholds": """(
        name VARCHAR(255) NOT NULL PRIMARY KEY, value DOUBLE NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "api_cache": """(
        cache_key VARCHAR(512) NOT NULL PRIMARY KEY,
        fetched_at VARCHAR(40) NOT NULL, payload_json LONGTEXT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "bp_models": """(
        id BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
        patch VARCHAR(32) NOT NULL, trained_at VARCHAR(40) NOT NULL,
        cutoff BIGINT NOT NULL, validation_end BIGINT,
        train_n INT NOT NULL, valid_n INT NOT NULL,
        brier DOUBLE NOT NULL, baseline_brier DOUBLE NOT NULL, ece DOUBLE NOT NULL,
        accepted TINYINT NOT NULL, model_json LONGTEXT NOT NULL,
        sample_ids_json LONGTEXT NOT NULL, train_start BIGINT, train_end BIGINT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
}


def mysql_config(env_file: Path, database_name: str | None = None) -> dict[str, Any]:
    if not env_file.is_file():
        raise FileNotFoundError(f"环境文件不存在：{env_file}")
    values = {**dotenv_values(env_file), **os.environ}

    def value(name: str, default: str = "") -> str:
        return str(values.get(name, default) or default)

    user, password = value("APP_MYSQL_USER"), value("APP_MYSQL_PASSWORD")
    if not user or not password:
        raise RuntimeError("需要配置 APP_MYSQL_USER / APP_MYSQL_PASSWORD；不会使用业务只读账号")

    ca = value("APP_MYSQL_SSL_CA") or value("MYSQL_SSL_CA")
    app_ssl = value("APP_MYSQL_SSL")
    ssl_enabled = (app_ssl.lower() in {"1", "true", "yes", "on"}) if app_ssl else (
        value("MYSQL_SSL").lower() in {"1", "true", "yes", "on"})
    ssl: dict[str, Any] = {}
    if ca:
        ssl = {"ca": ca}
    elif ssl_enabled:
        import ssl as ssl_module
        ssl = {"check_hostname": False, "verify_mode": ssl_module.CERT_NONE}

    return {
        "host": value("APP_MYSQL_HOST") or value("MYSQL_HOST", "127.0.0.1"),
        "port": int(value("APP_MYSQL_PORT") or value("MYSQL_PORT", "3306")),
        "user": user,
        "password": password,
        "database": database_name or value("APP_MYSQL_DB", "lab_hazmat_app"),
        "charset": value("MYSQL_CHARSET", "utf8mb4"),
        "connect_timeout": int(value("APP_MYSQL_CONNECT_TIMEOUT", "10")),
        "ssl": ssl or None,
    }


def source_counts(source: sqlite3.Connection) -> dict[str, int]:
    available = {row[0] for row in source.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = set(TABLES) - available
    missing_required = missing - {"inventory_snapshots"}
    if missing_required:
        raise RuntimeError("SQLite 源库缺少表：" + ", ".join(sorted(missing_required)))
    return {table: (source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    if table in available else 0) for table in TABLES}


def connect(config: dict[str, Any]):
    return pymysql.connect(**config, autocommit=False, cursorclass=pymysql.cursors.DictCursor)


def preflight(source_path: Path, env_file: Path, database_name: str) -> tuple[sqlite3.Connection, Any, dict[str, int]]:
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite 源库不存在：{source_path}")
    source = sqlite3.connect(f"file:{source_path.resolve().as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("BEGIN")
    counts = source_counts(source)
    target = connect(mysql_config(env_file, database_name))
    with target.cursor() as cursor:
        cursor.execute("SELECT DATABASE() AS db, VERSION() AS version, @@read_only AS read_only")
        destination = cursor.fetchone()
        cursor.execute("SHOW TABLES")
        names = [next(iter(row.values())) for row in cursor.fetchall()]
    print(f"SQLite source: {source_path} ({source_path.stat().st_size:,} bytes)")
    print(f"MySQL target database: {destination['db']}")
    print(f"MySQL server: {destination['version']}; read_only={destination['read_only']}")
    for table, count in counts.items():
        print(f"  {table}: {count:,}")
    collisions = [name for name in names if name.startswith(("dota_", "dota_stage_"))]
    print(f"Existing tables with reserved Dota prefixes: {', '.join(collisions) if collisions else 'none'}")
    if destination["read_only"]:
        raise RuntimeError("MySQL 服务处于 read_only 模式，停止迁移")
    if collisions:
        raise RuntimeError("发现现存 Dota 前缀表；为避免覆盖，停止迁移")
    return source, target, counts


def stage_table_name(table: str) -> str:
    return f"dota_stage_{table}"


def create_staging_tables(target, counts: dict[str, int]) -> None:
    with target.cursor() as cursor:
        for table in TABLES:
            ddl = DDL[table]
            cursor.execute(f"CREATE TABLE `{stage_table_name(table)}` {ddl}")
    target.commit()


def copy_table(source: sqlite3.Connection, target, table: str, batch_size: int = 5000) -> int:
    exists = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if exists is None:
        return 0
    cursor = source.execute(f'SELECT * FROM "{table}"')
    columns = [description[0] for description in cursor.description]
    if table == "api_cache":
        columns = ["cache_key" if column == "key" else column for column in columns]
    col_sql = ", ".join(f"`{column}`" for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO `{stage_table_name(table)}` ({col_sql}) VALUES ({placeholders})"
    total = 0
    with target.cursor() as mysql_cursor:
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            mysql_cursor.executemany(sql, [tuple(row) for row in rows])
            total += len(rows)
    return total


def apply_migration(source: sqlite3.Connection, target, counts: dict[str, int]) -> None:
    # Preflight has confirmed these names do not exist, so they are safe to clean
    # if any CREATE TABLE statement fails partway through.
    created = True
    try:
        create_staging_tables(target, counts)
        for table in TABLES:
            count = copy_table(source, target, table)
            target.commit()
            if count != counts[table]:
                raise RuntimeError(f"{table} 行数不一致：SQLite={counts[table]} MySQL={count}")
            print(f"Copied {table}: {count:,}")
        with target.cursor() as cursor:
            for table in TABLES:
                cursor.execute(f"SELECT COUNT(*) AS n FROM `{stage_table_name(table)}`")
                actual = cursor.fetchone()["n"]
                if actual != counts[table]:
                    raise RuntimeError(f"{table} 验证失败：期望 {counts[table]} 行，实际 {actual} 行")
            rename = ", ".join(
                f"`{stage_table_name(table)}` TO `dota_{table}`" for table in TABLES)
            cursor.execute(f"RENAME TABLE {rename}")
        target.commit()
        print("Migration complete. SQLite source remains unchanged.")
    except BaseException:
        target.rollback()
        # All staging names were confirmed absent before creation. Clean up only
        # those names if this run reached table creation; never touch dota_*.
        if created:
            try:
                with target.cursor() as cursor:
                    names = ", ".join(f"`{stage_table_name(table)}`" for table in reversed(TABLES))
                    cursor.execute(f"DROP TABLE IF EXISTS {names}")
                target.commit()
            except Exception:
                target.rollback()
                print("Cleanup of temporary dota_stage_* tables failed; inspect the database before retrying.")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="将 Dota SQLite 数据迁移到 lab_agent 配置的应用 MySQL 库")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--env-file", type=Path, required=True,
                        help="lab_agent 的 .env 文件；仅读取 APP_MYSQL_* 写账号")
    parser.add_argument("--database", default="dota_review",
                        help="MySQL 目标 database，默认 dota_review")
    parser.add_argument("--apply", action="store_true", help="执行迁移；缺省只做只读预览")
    args = parser.parse_args()
    if not args.database or not args.database.replace("_", "").isalnum() or not args.database[0].isalpha():
        parser.error("--database 只能使用字母、数字和下划线，并以字母开头")
    source = target = None
    try:
        source, target, counts = preflight(args.source, args.env_file, args.database)
        if args.apply:
            apply_migration(source, target, counts)
        else:
            print("Preview only: no MySQL data or schema was changed. Add --apply to migrate.")
        return 0
    except Exception as exc:
        # Avoid printing connection arguments, environment values, or SQL payloads.
        print(f"Migration stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


if __name__ == "__main__":
    raise SystemExit(main())
