import asyncio
import hashlib
import os
from datetime import timezone

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


ROUTER_TOKEN = "router-production-token"
ROUTER_ROTATED_TOKEN = "router-rotated-token"
MCP_TOKEN = "mcp-production-token"
ROUTER_SOURCE = "vans-coding-router"
MCP_SOURCE = "vans-mcp-server"
SIGNALS_TOKENS = {
    token_hash(ROUTER_TOKEN): ROUTER_SOURCE,
    token_hash(ROUTER_ROTATED_TOKEN): ROUTER_SOURCE,
    token_hash(MCP_TOKEN): MCP_SOURCE,
}
DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or (
    "postgresql://signals:signals@127.0.0.1:55432/signals"
)


class RecordingSlack:
    def __init__(self, *, fail: bool = True):
        self.messages: list[dict] = []
        self.fail = fail

    async def post(self, message: dict) -> None:
        self.messages.append(message)
        if self.fail:
            raise RuntimeError("slack rejected the webhook")


class RecordingSleeper:
    def __init__(self, *, pause: bool = False):
        self.delays: list[float] = []
        self.pause = pause

    async def __call__(self, seconds: float) -> None:
        self.delays.append(float(seconds))
        if self.pause:
            await asyncio.Event().wait()
            return
        await asyncio.sleep(0)


def signal_body(**overrides) -> dict:
    body = {
        "log_time": "2026-10-03T03:37:00Z",
        "logger_name": "src.jobs.catalog",
        "level": "ERROR",
        "message": "upstream openrouter did not update",
        "source": ROUTER_SOURCE,
    }
    body.update(overrides)
    return body


def read_signals(database_url: str) -> list[dict]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT log_time, logger_name, level, message, source
            FROM signals
            ORDER BY id
            """
        ).fetchall()
    stored = []
    for row in rows:
        log_time = row["log_time"].astimezone(timezone.utc).isoformat(timespec="seconds")
        stored.append(
            {
                "log_time": log_time,
                "logger_name": row["logger_name"],
                "level": row["level"],
                "message": row["message"],
                "source": row["source"],
            }
        )
    return stored


def signal_columns(database_url: str) -> set[str]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'signals'
            """
        ).fetchall()
    return {row["column_name"] for row in rows}


@pytest.fixture
def database_url() -> str:
    return DATABASE_URL


@pytest.fixture
def slack() -> RecordingSlack:
    return RecordingSlack()


@pytest.fixture
def sleeper() -> RecordingSleeper:
    return RecordingSleeper()


@pytest.fixture
async def client(database_url, slack, sleeper):
    from vans_signals.app import create_app

    app = create_app(
        database_url=database_url,
        tokens=SIGNALS_TOKENS,
        slack=slack,
        sleep=sleeper,
    )
    async with app.router.lifespan_context(app):
        await asyncio.to_thread(truncate_signals, database_url)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://signals", timeout=1.0) as http:
            yield http


def truncate_signals(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute("TRUNCATE signals RESTART IDENTITY")
        conn.commit()
