import asyncio
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from tests.conftest import (
    MCP_SOURCE,
    MCP_TOKEN,
    ROUTER_ROTATED_TOKEN,
    ROUTER_TOKEN,
    SIGNALS_TOKENS,
    RecordingSlack,
    RecordingSleeper,
    read_signals,
    signal_body,
    signal_columns,
    truncate_signals,
)
from vans_signals.app import create_app, load_signals_tokens
from vans_signals.slack import slack_message
from vans_signals.store import Signal

STORED_SIGNAL = {
    "log_time": "2026-10-03T03:37:00+00:00",
    "logger_name": "src.jobs.catalog",
    "level": "ERROR",
    "message": "upstream openrouter did not update",
    "source": "vans-coding-router",
}
SLACK_MESSAGE = {
    "text": "ERROR · src.jobs.catalog · upstream openrouter did not update",
    "blocks": [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "ERROR", "emoji": False},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "upstream openrouter did not update"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "*Time*\n2026-10-03 03:37:00 +00:00"},
                {"type": "mrkdwn", "text": "*Logger*\nsrc.jobs.catalog"},
                {"type": "mrkdwn", "text": "*Source*\nvans-coding-router"},
            ],
        },
    ],
}


async def _settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_health_is_available(client):
    response = await client.get("/health")

    assert response.status_code == 200


async def test_signals_cannot_be_read(client):
    response = await client.get("/signals", headers={"Authorization": f"Bearer {ROUTER_TOKEN}"})

    assert response.status_code == 405


async def test_log_time_without_a_timezone_is_rejected(client, database_url, slack, sleeper):
    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {ROUTER_TOKEN}"},
        json=signal_body(log_time="2026-10-03T03:37:00"),
    )
    await _settle()

    assert response.status_code == 422
    assert read_signals(database_url) == []
    assert slack.messages == []
    assert sleeper.delays == []


async def test_missing_token_is_rejected_and_stores_nothing(client, database_url, slack, sleeper):
    response = await client.post("/signals", json=signal_body())
    await _settle()

    assert response.status_code == 401
    assert read_signals(database_url) == []
    assert slack.messages == []
    assert sleeper.delays == []


async def test_unknown_token_is_rejected_and_stores_nothing(client, database_url, slack, sleeper):
    response = await client.post(
        "/signals",
        headers={"Authorization": "Bearer not-the-router-token"},
        json=signal_body(),
    )
    await _settle()

    assert response.status_code == 401
    assert read_signals(database_url) == []
    assert slack.messages == []
    assert sleeper.delays == []


async def test_mismatched_source_is_rejected_and_stores_nothing(client, database_url, slack, sleeper):
    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {ROUTER_TOKEN}"},
        json=signal_body(source="someone-else"),
    )
    await _settle()

    assert response.status_code == 403
    assert read_signals(database_url) == []
    assert slack.messages == []
    assert sleeper.delays == []


async def test_router_token_stores_the_signal_and_returns_before_slack(
    client, database_url, slack, sleeper
):
    sleeper.pause = True

    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {ROUTER_TOKEN}"},
        json=signal_body(),
    )
    for _ in range(20):
        if sleeper.delays:
            break
        await asyncio.sleep(0)

    assert response.status_code == 200
    assert sleeper.delays == [1]
    assert slack.messages == []
    assert read_signals(database_url) == [STORED_SIGNAL]
    assert signal_columns(database_url) == {
        "id",
        "log_time",
        "logger_name",
        "level",
        "message",
        "source",
    }


async def _wait_until(predicate) -> None:
    for _ in range(30):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("timed out waiting for Slack delivery")


async def test_slack_failures_are_spaced_one_then_two_then_four_seconds_and_leave_the_row(
    client, database_url, slack, sleeper
):
    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {ROUTER_TOKEN}"},
        json=signal_body(),
    )
    stored_at_response = read_signals(database_url)
    await _wait_until(lambda: len(slack.messages) >= 3)

    assert response.status_code == 200
    assert sleeper.delays == [1.0, 2.0, 4.0]
    assert slack.messages == [SLACK_MESSAGE, SLACK_MESSAGE, SLACK_MESSAGE]
    assert stored_at_response == [STORED_SIGNAL]
    assert read_signals(database_url) == [STORED_SIGNAL]


async def test_slack_success_does_not_post_again(client, slack, sleeper):
    slack.fail = False

    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {ROUTER_TOKEN}"},
        json=signal_body(),
    )
    await _wait_until(lambda: len(slack.messages) >= 1)
    await _settle()

    assert response.status_code == 200
    assert sleeper.delays == [1.0]
    assert slack.messages == [SLACK_MESSAGE]


async def test_restart_during_slack_tries_does_not_send_the_stored_signal(database_url):
    sleeper = RecordingSleeper(pause=True)
    slack = RecordingSlack()
    app = create_app(
        database_url=database_url,
        tokens=load_signals_tokens(json.dumps(SIGNALS_TOKENS)),
        slack=slack,
        sleep=sleeper,
    )
    async with app.router.lifespan_context(app):
        await asyncio.to_thread(truncate_signals, database_url)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://signals", timeout=1.0) as http:
            response = await http.post(
                "/signals",
                headers={"Authorization": f"Bearer {ROUTER_TOKEN}"},
                json=signal_body(),
            )
            await _wait_until(lambda: sleeper.delays == [1.0])
            assert response.status_code == 200
            assert slack.messages == []

    restarted_slack = RecordingSlack(fail=False)
    restarted_sleeper = RecordingSleeper()
    restarted = create_app(
        database_url=database_url,
        tokens=load_signals_tokens(json.dumps(SIGNALS_TOKENS)),
        slack=restarted_slack,
        sleep=restarted_sleeper,
    )
    async with restarted.router.lifespan_context(restarted):
        await _settle()
        assert restarted_sleeper.delays == []
        assert restarted_slack.messages == []
        assert read_signals(database_url) == [STORED_SIGNAL]


EXAMPLE_BODY = Path(__file__).resolve().parents[1] / "docs" / "signal-body.example.json"


def _import_process_app(monkeypatch, database_url: str, tokens_json: str | None):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    monkeypatch.delenv("SIGNALS_ROUTER_TOKEN", raising=False)
    monkeypatch.delenv("SIGNALS_ROUTER_SOURCE", raising=False)
    if tokens_json is None:
        monkeypatch.delenv("SIGNALS_TOKENS", raising=False)
    else:
        monkeypatch.setenv("SIGNALS_TOKENS", tokens_json)
    sys.modules.pop("app", None)
    return importlib.import_module("app")


def test_missing_tokens_secret_refuses_to_start(monkeypatch, database_url):
    with pytest.raises(ValueError, match="SIGNALS_TOKENS"):
        _import_process_app(monkeypatch, database_url, None)


def test_invalid_tokens_pair_refuses_to_start(monkeypatch, database_url):
    with pytest.raises(ValueError, match="SIGNALS_TOKENS"):
        _import_process_app(monkeypatch, database_url, '{"not-a-hash": "vans-coding-router"}')
    sys.modules.pop("app", None)
    with pytest.raises(ValueError, match="SIGNALS_TOKENS"):
        _import_process_app(monkeypatch, database_url, "{}")


def test_one_valid_pair_is_enough_to_start(monkeypatch, database_url):
    one_hash, service = next(iter(SIGNALS_TOKENS.items()))
    module = _import_process_app(monkeypatch, database_url, json.dumps({one_hash: service}))
    assert module.app is not None


async def test_second_service_token_stores_that_source(client, database_url, slack, sleeper):
    slack.fail = False
    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {MCP_TOKEN}"},
        json=signal_body(source=MCP_SOURCE),
    )
    await _wait_until(lambda: len(slack.messages) >= 1)

    assert response.status_code == 200
    stored = read_signals(database_url)
    assert stored == [
        {
            **STORED_SIGNAL,
            "source": MCP_SOURCE,
        }
    ]
    assert slack.messages == [
        slack_message(
            Signal(
                log_time=datetime.fromisoformat(STORED_SIGNAL["log_time"]),
                logger_name=STORED_SIGNAL["logger_name"],
                level=STORED_SIGNAL["level"],
                message=STORED_SIGNAL["message"],
                source=MCP_SOURCE,
            )
        )
    ]
    assert sleeper.delays == [1.0]


async def test_rotated_hash_for_the_same_service_is_accepted(client, database_url, slack):
    slack.fail = False
    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {ROUTER_ROTATED_TOKEN}"},
        json=signal_body(),
    )
    await _wait_until(lambda: len(slack.messages) >= 1)

    assert response.status_code == 200
    assert read_signals(database_url) == [STORED_SIGNAL]


async def test_level_is_stored_as_posted(client, database_url, slack):
    slack.fail = False
    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {ROUTER_TOKEN}"},
        json=signal_body(level="WARNING"),
    )
    await _wait_until(lambda: len(slack.messages) >= 1)

    assert response.status_code == 200
    assert read_signals(database_url)[0]["level"] == "WARNING"


async def test_canonical_example_body_is_accepted(client, database_url, slack):
    slack.fail = False
    example = json.loads(EXAMPLE_BODY.read_text())
    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {ROUTER_TOKEN}"},
        json=example,
    )
    await _wait_until(lambda: len(slack.messages) >= 1)

    assert response.status_code == 200
    assert read_signals(database_url) == [STORED_SIGNAL]
    assert slack.messages == [SLACK_MESSAGE]
