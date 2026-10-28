"""Least-privilege connection to the migrated Dota review database."""
from __future__ import annotations

import os
import ssl
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pymysql
from dotenv import dotenv_values
from pymysql.cursors import DictCursor

_ROOT = Path(__file__).resolve().parents[1]


def mysql_config() -> dict[str, Any]:
    # Environment variables override the local .env file, allowing deployment
    # platforms to inject secrets without writing them to disk.
    values = {**dotenv_values(_ROOT / ".env"), **os.environ}

    def value(key: str, default: str = "") -> str:
        return str(values.get(key) or default)

    required = ("DOTA_MYSQL_HOST", "DOTA_MYSQL_USER", "DOTA_MYSQL_PASSWORD")
    missing = [key for key in required if not value(key)]
    if missing:
        raise RuntimeError("Missing MySQL settings: " + ", ".join(missing))

    ssl_ca = value("DOTA_MYSQL_SSL_CA")
    ssl_enabled = value("DOTA_MYSQL_SSL", "false").lower() in {"1", "true", "yes", "on"}
    ssl_args = {"ssl": {"ca": ssl_ca}} if ssl_ca else (
        {"ssl": {"check_hostname": False, "verify_mode": ssl.CERT_NONE}}
        if ssl_enabled else {}
    )
    return {
        "host": value("DOTA_MYSQL_HOST"),
        "port": int(value("DOTA_MYSQL_PORT", "3306")),
        "database": value("DOTA_MYSQL_DATABASE", "dota_review"),
        "user": value("DOTA_MYSQL_USER"),
        "password": value("DOTA_MYSQL_PASSWORD"),
        "charset": "utf8mb4",
        "connect_timeout": int(value("DOTA_MYSQL_CONNECT_TIMEOUT", "10")),
        "cursorclass": DictCursor,
        "autocommit": True,
        **ssl_args,
    }


@contextmanager
def mysql_connection():
    connection = pymysql.connect(**mysql_config())
    try:
        yield connection
    finally:
        connection.close()
