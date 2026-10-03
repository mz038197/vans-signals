import asyncio
from dataclasses import dataclass
from datetime import datetime

import psycopg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    log_time TIMESTAMPTZ NOT NULL,
    logger_name TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT NOT NULL
)
"""


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


@dataclass(frozen=True)
class Signal:
    log_time: datetime
    logger_name: str
    level: str
    message: str
    source: str


class SignalStore:
    def __init__(self, database_url: str):
        self.database_url = normalize_database_url(database_url)

    async def open(self) -> None:
        await asyncio.to_thread(self._open)

    async def insert(self, signal: Signal) -> None:
        await asyncio.to_thread(self._insert, signal)

    def _open(self) -> None:
        with psycopg.connect(self.database_url) as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    def _insert(self, signal: Signal) -> None:
        with psycopg.connect(self.database_url) as conn:
            conn.execute(
                """
                INSERT INTO signals (log_time, logger_name, level, message, source)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    signal.log_time,
                    signal.logger_name,
                    signal.level,
                    signal.message,
                    signal.source,
                ),
            )
            conn.commit()
