import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from vans_signals.slack import slack_message
from vans_signals.store import Signal, SignalStore

_TOKEN_HASH = re.compile(r"^[0-9a-f]{64}$")


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


def load_signals_tokens(raw: str | None) -> dict[str, str]:
    if raw is None or raw.strip() == "":
        raise ValueError("SIGNALS_TOKENS is missing")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("SIGNALS_TOKENS is not valid JSON") from exc
    if not isinstance(parsed, dict) or len(parsed) == 0:
        raise ValueError("SIGNALS_TOKENS must be a non-empty JSON object")
    tokens: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or _TOKEN_HASH.fullmatch(key) is None:
            raise ValueError("SIGNALS_TOKENS keys must be 64 lowercase hex")
        if not isinstance(value, str) or value == "":
            raise ValueError("SIGNALS_TOKENS values must be non-empty strings")
        tokens[key] = value
    return tokens


def service_named_by_token(presented: str, tokens: Mapping[str, str]) -> str | None:
    presented_digest = hashlib.sha256(presented.encode("utf-8")).digest()
    named = None
    for stored_hex, service in tokens.items():
        try:
            expected = bytes.fromhex(stored_hex)
        except ValueError:
            continue
        if len(expected) != 32:
            continue
        if hmac.compare_digest(presented_digest, expected):
            named = service
    return named


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
                await slack.post(slack_message(signal))
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
