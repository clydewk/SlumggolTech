from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

import clickhouse_connect

from slumggol_bot.config import AppSettings

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SQL = ROOT / "sql" / "clickhouse_bot_analytics.sql"
MIGRATE_V2_SQL = ROOT / "sql" / "clickhouse_bot_analytics_migrate_v2.sql"


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Slumggol ClickHouse analytics schema.")
    parser.add_argument(
        "command",
        choices=("ping", "bootstrap", "migrate_v2", "smoke"),
        help="Admin operation to run against ClickHouse.",
    )
    args = parser.parse_args()

    settings = AppSettings()
    if not settings.clickhouse_url:
        raise SystemExit("CLICKHOUSE_URL must be set.")

    if args.command == "ping":
        ping(settings)
        return
    if args.command == "bootstrap":
        apply_sql(settings, BOOTSTRAP_SQL)
        return
    if args.command == "migrate_v2":
        apply_sql(settings, MIGRATE_V2_SQL)
        return
    smoke(settings)


def ping(settings: AppSettings) -> None:
    client = _get_client(settings, database="default")
    result = client.query(
        "SELECT version(), %(database)s IN (SELECT name FROM system.databases) FORMAT TSV",
        parameters={"database": settings.clickhouse_database},
    )
    version, database_exists = result.result_rows[0]
    print(f"version={version}")
    print(f"database_exists={int(database_exists)}")


def apply_sql(settings: AppSettings, path: Path) -> None:
    client = _get_client(settings, database="default")
    sql = path.read_text()
    for statement in _split_statements(sql):
        client.command(statement)
    print(f"applied={path.name}")


def smoke(settings: AppSettings) -> None:
    client = _get_client(settings, database=settings.clickhouse_database)
    required_tables = {
        "message_events",
        "claim_events",
        "factcheck_events",
        "reply_events",
        "usage_events",
        "claim_intel_5m",
        "factcheck_intel_daily",
        "reply_outcomes_daily",
        "dashboard_summary_24h",
        "dashboard_trending_claims_24h",
        "dashboard_claim_group_spread_24h",
        "dashboard_high_risk_scams_24h",
    }
    result = client.query(
        """
        SELECT name
        FROM system.tables
        WHERE database = %(database)s
          AND name IN %(names)s
        ORDER BY name
        """,
        parameters={
            "database": settings.clickhouse_database,
            "names": tuple(sorted(required_tables)),
        },
    )
    existing = {str(row[0]) for row in result.result_rows}
    missing = sorted(required_tables - existing)
    if missing:
        raise SystemExit(f"missing_objects={','.join(missing)}")

    summary_rows = client.query("SELECT * FROM dashboard_summary_24h LIMIT 1").result_rows
    print(f"objects={len(existing)}")
    print(f"summary_rows={len(summary_rows)}")


def _get_client(settings: AppSettings, *, database: str):
    parsed = urlparse(settings.clickhouse_url)
    return clickhouse_connect.get_client(
        host=parsed.hostname or "",
        port=parsed.port or 8443,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=database,
        secure=parsed.scheme == "https",
        interface="https" if parsed.scheme == "https" else "http",
    )


def _split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    for char in sql:
        if char == "'":
            in_single_quote = not in_single_quote
        if char == ";" and not in_single_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


if __name__ == "__main__":
    main()
