import asyncio
import json
from pathlib import Path

import httpx
import pytest

from tests.conftest import (
    MCP_SOURCE,
    MCP_TOKEN,
    ROUTER_ROTATED_TOKEN,
    ROUTER_SOURCE,
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
        tokens=SIGNALS_TOKENS,
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
        tokens=SIGNALS_TOKENS,
        slack=restarted_slack,
        sleep=restarted_sleeper,
    )
    async with restarted.router.lifespan_context(restarted):
        await _settle()
        assert restarted_sleeper.delays == []
        assert restarted_slack.messages == []
        assert read_signals(database_url) == [STORED_SIGNAL]


EXAMPLE_BODY = Path("docs/signal-body.example.json")


def test_missing_tokens_secret_refuses_to_start():
    with pytest.raises(ValueError, match="SIGNALS_TOKENS"):
        load_signals_tokens(None)


def test_invalid_tokens_pair_refuses_to_start():
    with pytest.raises(ValueError, match="SIGNALS_TOKENS"):
        load_signals_tokens('{"not-a-hash": "vans-coding-router"}')
    with pytest.raises(ValueError, match="SIGNALS_TOKENS"):
        load_signals_tokens("{}")


def test_one_valid_pair_is_enough_to_start():
    one_hash, service = next(iter(SIGNALS_TOKENS.items()))
    tokens = load_signals_tokens(json.dumps({one_hash: service}))
    assert tokens == {one_hash: service}


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
    assert slack.messages[0]["blocks"][3]["fields"][2]["text"] == f"*Source*\n{MCP_SOURCE}"
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
    stored = read_signals(database_url)
    assert stored[0]["logger_name"] == example["logger_name"]
    assert stored[0]["level"] == example["level"]
    assert stored[0]["message"] == example["message"]
    assert stored[0]["source"] == example["source"]
