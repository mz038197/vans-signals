import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from vans_signals.store import Signal, SignalStore


class SignalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    log_time: datetime
    logger_name: str
    level: str
    message: str
    source: str

    @field_validator("log_time")
    @classmethod
    def log_time_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("log time needs a timezone")
        return value


def bearer_token(header: str | None) -> str | None:
    if header is None:
        return None
    scheme, separator, token = header.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or token == "" or " " in token:
        return None
    return token


def service_named_by_token(presented: str, tokens: Mapping[str, str]) -> str | None:
    presented_hash = hashlib.sha256(presented.encode("utf-8")).digest()
    named = None
    for token, service in tokens.items():
        expected_hash = hashlib.sha256(token.encode("utf-8")).digest()
        if hmac.compare_digest(presented_hash, expected_hash):
            named = service
    return named


def slack_text(signal: Signal) -> str:
    when = signal.log_time.isoformat(
        timespec="seconds" if signal.log_time.microsecond == 0 else "microseconds"
    )
    return f"{when} {signal.logger_name} {signal.level} {signal.message}"


def create_app(
    *,
    database_url: str,
    tokens: Mapping[str, str],
    slack,
    sleep: Callable[[float], Awaitable[None]],
) -> FastAPI:
    store = SignalStore(database_url)
    deliveries: set[asyncio.Task] = set()

    async def deliver(signal: Signal) -> None:
        for delay in (1, 2, 4):
            await sleep(delay)
            try:
                await slack.post(slack_text(signal))
            except Exception:
                continue
            return

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await store.open()
        try:
            yield
        finally:
            pending = list(deliveries)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            close = getattr(slack, "aclose", None)
            if close is not None:
                await close()

    app = FastAPI(title="vans-signals", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/signals")
    async def post_signal(
        body: SignalBody,
        authorization: str | None = Header(default=None),
    ):
        presented = bearer_token(authorization)
        service = None if presented is None else service_named_by_token(presented, tokens)
        if service is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if body.source != service:
            raise HTTPException(status_code=403, detail="Forbidden")
        signal = Signal(
            log_time=body.log_time,
            logger_name=body.logger_name,
            level=body.level,
            message=body.message,
            source=service,
        )
        await store.insert(signal)
        task = asyncio.create_task(deliver(signal))
        deliveries.add(task)
        task.add_done_callback(deliveries.discard)
        return {}

    return app
