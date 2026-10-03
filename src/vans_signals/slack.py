import re
from collections.abc import Mapping
from datetime import datetime

import httpx

from vans_signals.store import Signal

_OFFSET = re.compile(r"([+-]\d{2}:\d{2}(?::\d{2})?)$")


def _mrkdwn(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_log_time(value: datetime) -> str:
    timespec = "seconds" if value.microsecond == 0 else "microseconds"
    rendered = value.isoformat(sep=" ", timespec=timespec)
    return _OFFSET.sub(r" \1", rendered)


def _preview(level: str, logger_name: str, message: str) -> str:
    flat_message = " ".join(message.split())
    return _clip(_mrkdwn(f"{level} · {logger_name} · {flat_message}"), 3000)


def _section(message: str) -> str:
    escaped = _mrkdwn(message)
    if escaped.strip() == "":
        return "_no message_"
    return _clip(escaped, 3000)


def _field(label: str, value: str) -> dict[str, str]:
    return {"type": "mrkdwn", "text": _clip(f"*{label}*\n{_mrkdwn(value)}", 2000)}


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def slack_message(signal: Signal) -> dict[str, object]:
    level = " ".join(signal.level.split()) or "signal"
    when = _format_log_time(signal.log_time)
    return {
        "text": _preview(level, signal.logger_name, signal.message),
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": level[:150], "emoji": False},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _section(signal.message)},
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    _field("Time", when),
                    _field("Logger", signal.logger_name),
                    _field("Source", signal.source),
                ],
            },
        ],
    }


class SlackWebhook:
    def __init__(self, webhook_url: str, *, client: httpx.AsyncClient | None = None):
        self._webhook_url = webhook_url
        self._client = client if client is not None else httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    async def post(self, message: Mapping[str, object]) -> None:
        response = await self._client.post(self._webhook_url, json=dict(message))
        response.raise_for_status()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
