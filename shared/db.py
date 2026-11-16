"""Postgres helpers shared by the api and worker. Tables are created on startup (idempotent)."""
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    correlation_id TEXT,
    request JSONB NOT NULL,
    result JSONB,
    trace JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS quarantine (
    id SERIAL PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    correlation_id TEXT,
    payload JSONB,
    errors JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    satisfactory BOOLEAN NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

Json = psycopg2.extras.Json


@contextmanager
def cursor():
    conn = psycopg2.connect(get_settings().database_url)
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


def init_db() -> None:
    with cursor() as cur:
        cur.execute(SCHEMA)


def create_run(run_id: str, request: dict, correlation_id: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO runs (run_id, status, correlation_id, request) VALUES (%s, 'queued', %s, %s)",
            (run_id, correlation_id, Json(request)),
        )


def set_status(run_id: str, status: str) -> None:
    with cursor() as cur:
        cur.execute("UPDATE runs SET status=%s, updated_at=now() WHERE run_id=%s", (status, run_id))


def save_result(run_id: str, status: str, result: dict) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE runs SET status=%s, result=%s, updated_at=now() WHERE run_id=%s",
            (status, Json(result), run_id),
        )


def append_trace(run_id: str, step: dict) -> None:
    """Record one step (tool call, query, passages, prompt version, latency, tokens, cost, verdict). D5."""
    with cursor() as cur:
        cur.execute(
            """UPDATE runs
               SET trace = trace || jsonb_build_array(
                   jsonb_build_object('run_id', run_id, 'correlation_id', correlation_id,
                                      'recorded_at', now()) || %s::jsonb
               ), updated_at=now()
               WHERE run_id=%s""",
            (Json(step), run_id),
        )


def get_run(run_id: str):
    with cursor() as cur:
        cur.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,))
        return cur.fetchone()


def add_quarantine(payload, errors: list, correlation_id: str) -> int:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO quarantine (correlation_id, payload, errors) VALUES (%s, %s, %s) RETURNING id",
            (correlation_id, Json(payload), Json(errors)),
        )
        return cur.fetchone()["id"]


def add_feedback(run_id: str, satisfactory: bool, reason) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO feedback (run_id, satisfactory, reason) VALUES (%s, %s, %s)",
            (run_id, satisfactory, reason),
        )


def get_feedback_score() -> dict:
    """Return a live usefulness score using only each run's latest feedback."""
    with cursor() as cur:
        cur.execute(
            """WITH latest AS (
                   SELECT DISTINCT ON (f.run_id)
                          f.run_id, f.satisfactory,
                          COALESCE(r.result #>> '{routing,route}', 'unknown') AS route
                     FROM feedback f
                     JOIN runs r ON r.run_id = f.run_id
                    ORDER BY f.run_id, f.created_at DESC, f.id DESC
               )
               SELECT COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE satisfactory) AS satisfactory
                 FROM latest"""
        )
        overall = cur.fetchone()
        cur.execute(
            """WITH latest AS (
                   SELECT DISTINCT ON (f.run_id)
                          f.run_id, f.satisfactory,
                          COALESCE(r.result #>> '{routing,route}', 'unknown') AS route
                     FROM feedback f
                     JOIN runs r ON r.run_id = f.run_id
                    ORDER BY f.run_id, f.created_at DESC, f.id DESC
               )
               SELECT route, COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE satisfactory) AS satisfactory
                 FROM latest
                GROUP BY route
                ORDER BY route"""
        )
        by_route = cur.fetchall()

    total = int(overall["total"] or 0)
    satisfactory = int(overall["satisfactory"] or 0)

    def percentage(count: int, denominator: int):
        return round(100 * count / denominator, 1) if denominator else None

    return {
        "total_feedback": total,
        "satisfactory_count": satisfactory,
        "needs_improvement_count": total - satisfactory,
        "satisfactory_pct": percentage(satisfactory, total),
        "by_route": [
            {
                "route": row["route"],
                "total_feedback": int(row["total"]),
                "satisfactory_count": int(row["satisfactory"]),
                "satisfactory_pct": percentage(int(row["satisfactory"]), int(row["total"])),
            }
            for row in by_route
        ],
    }
