import asyncio

import httpx

from tests.conftest import (
    ROUTER_SOURCE,
    ROUTER_TOKEN,
    RecordingSlack,
    RecordingSleeper,
    read_signals,
    signal_body,
    signal_columns,
    truncate_signals,
)
from vans_signals.app import create_app

STORED_SIGNAL = {
    "log_time": "2026-10-03T03:37:00+00:00",
    "logger_name": "src.jobs.catalog",
    "level": "ERROR",
    "message": "upstream openrouter did not update",
    "source": "vans-coding-router",
}
SLACK_TEXT = (
    "2026-10-03T03:37:00+00:00 src.jobs.catalog ERROR upstream openrouter did not update"
)


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
    assert slack.texts == []
    assert sleeper.delays == []


async def test_missing_token_is_rejected_and_stores_nothing(client, database_url, slack, sleeper):
    response = await client.post("/signals", json=signal_body())
    await _settle()

    assert response.status_code == 401
    assert read_signals(database_url) == []
    assert slack.texts == []
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
    assert slack.texts == []
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
    assert slack.texts == []
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
    assert slack.texts == []
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
    await _wait_until(lambda: len(slack.texts) >= 3)

    assert response.status_code == 200
    assert sleeper.delays == [1.0, 2.0, 4.0]
    assert slack.texts == [SLACK_TEXT, SLACK_TEXT, SLACK_TEXT]
    assert stored_at_response == [STORED_SIGNAL]
    assert read_signals(database_url) == [STORED_SIGNAL]


async def test_slack_success_does_not_post_again(client, slack, sleeper):
    slack.fail = False

    response = await client.post(
        "/signals",
        headers={"Authorization": f"Bearer {ROUTER_TOKEN}"},
        json=signal_body(),
    )
    await _wait_until(lambda: len(slack.texts) >= 1)
    await _settle()

    assert response.status_code == 200
    assert sleeper.delays == [1.0]
    assert slack.texts == [SLACK_TEXT]


async def test_restart_during_slack_tries_does_not_send_the_stored_signal(database_url):
    sleeper = RecordingSleeper(pause=True)
    slack = RecordingSlack()
    app = create_app(
        database_url=database_url,
        tokens={ROUTER_TOKEN: ROUTER_SOURCE},
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
            assert slack.texts == []

    restarted_slack = RecordingSlack(fail=False)
    restarted_sleeper = RecordingSleeper()
    restarted = create_app(
        database_url=database_url,
        tokens={ROUTER_TOKEN: ROUTER_SOURCE},
        slack=restarted_slack,
        sleep=restarted_sleeper,
    )
    async with restarted.router.lifespan_context(restarted):
        await _settle()
        assert restarted_sleeper.delays == []
        assert restarted_slack.texts == []
        assert read_signals(database_url) == [STORED_SIGNAL]
