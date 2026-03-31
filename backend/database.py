"""
SQLite persistence layer for query logs.

The DB file path is controlled by the DB_PATH env var (default: data/queries.db).
Call init_db() once at startup. log_query() is safe to call from async code
(it uses synchronous sqlite3 but writes are fast and infrequent).
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = os.environ.get("DB_PATH", "data/queries.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS query_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                query           TEXT    NOT NULL,
                provider        TEXT,
                models_tried    TEXT,
                elapsed_ms      INTEGER,
                input_tokens    INTEGER DEFAULT 0,
                output_tokens   INTEGER DEFAULT 0,
                total_tokens    INTEGER DEFAULT 0,
                tool_calls_count INTEGER DEFAULT 0,
                tool_names      TEXT,
                status          TEXT    NOT NULL,
                error_message   TEXT
            )
        """)
        conn.commit()
    purged = purge_old_queries()
    if purged:
        logger.info("Purged %d query log entries older than 30 days", purged)
    logger.info("Database ready at %s", _DB_PATH)


def purge_old_queries(days: int = 30) -> int:
    """Delete query log entries older than the given number of days."""
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "DELETE FROM query_logs WHERE timestamp < datetime('now', ?)",
                (f"-{days} days",),
            )
            conn.commit()
            return cursor.rowcount
    except Exception as exc:
        logger.error("Failed to purge old queries: %s", exc)
        return 0


def log_query(
    *,
    query: str,
    provider: str | None,
    models_tried: list[str],
    elapsed_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    tool_calls_count: int = 0,
    tool_names: list[str],
    status: str,
    error_message: str | None = None,
) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO query_logs
                    (timestamp, query, provider, models_tried, elapsed_ms,
                     input_tokens, output_tokens, total_tokens,
                     tool_calls_count, tool_names, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    query,
                    provider,
                    json.dumps(models_tried),
                    elapsed_ms,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    tool_calls_count,
                    json.dumps(tool_names),
                    status,
                    error_message,
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.error("Failed to log query to DB: %s", exc)


def get_queries(page: int = 1, limit: int = 50, search: str = "") -> dict:
    offset = (page - 1) * limit
    with _connect() as conn:
        if search:
            like = f"%{search}%"
            total = conn.execute(
                "SELECT COUNT(*) FROM query_logs WHERE query LIKE ?", (like,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM query_logs WHERE query LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (like, limit, offset),
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) FROM query_logs").fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM query_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["models_tried"] = json.loads(item["models_tried"] or "[]")
        item["tool_names"] = json.loads(item["tool_names"] or "[]")
        items.append(item)

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "items": items,
    }


def get_stats() -> dict:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM query_logs").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM query_logs WHERE date(timestamp) = date('now')"
        ).fetchone()[0]
        avg_tokens = (
            conn.execute(
                "SELECT AVG(total_tokens) FROM query_logs WHERE status = 'done'"
            ).fetchone()[0]
            or 0
        )
        avg_elapsed = (
            conn.execute(
                "SELECT AVG(elapsed_ms) FROM query_logs WHERE status = 'done'"
            ).fetchone()[0]
            or 0
        )
        error_count = conn.execute(
            "SELECT COUNT(*) FROM query_logs WHERE status = 'error'"
        ).fetchone()[0]
        tool_rows = conn.execute(
            "SELECT tool_names FROM query_logs WHERE tool_names NOT IN ('[]', 'null') AND tool_names IS NOT NULL"
        ).fetchall()

    tool_counts: dict[str, int] = {}
    for row in tool_rows:
        for name in json.loads(row[0] or "[]"):
            tool_counts[name] = tool_counts.get(name, 0) + 1
    top_tools = sorted(tool_counts.items(), key=lambda x: -x[1])[:5]

    return {
        "total_queries": total,
        "today_queries": today,
        "avg_tokens": round(avg_tokens),
        "avg_elapsed_ms": round(avg_elapsed),
        "error_count": error_count,
        "top_tools": [{"name": n, "count": c} for n, c in top_tools],
    }
