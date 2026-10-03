import json
from datetime import datetime

import httpx
import pytest

from vans_signals.slack import SlackWebhook, slack_message
from vans_signals.store import Signal

WEBHOOK_URL = "https://hooks.slack.test/services/T/B/X"
PAYLOAD = {"text": "ERROR · src.jobs.catalog · upstream openrouter did not update"}


@pytest.mark.asyncio
async def test_slack_webhook_posts_the_message():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await SlackWebhook(WEBHOOK_URL, client=http).post(PAYLOAD)

    assert seen == {"url": WEBHOOK_URL, "body": PAYLOAD}


@pytest.mark.asyncio
async def test_slack_webhook_rejects_a_failed_post():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await SlackWebhook(WEBHOOK_URL, client=http).post(PAYLOAD)


def test_slack_message_splits_the_log_into_labeled_parts():
    message = slack_message(
        Signal(
            log_time=datetime.fromisoformat("2026-10-03T05:20:13.845830+00:00"),
            logger_name="src.signals_test",
            level="ERROR",
            message="vans-signals test signal from the production router",
            source="vans-coding-router",
        )
    )

    assert message == {
        "text": (
            "ERROR · src.signals_test · "
            "vans-signals test signal from the production router"
        ),
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "ERROR", "emoji": False},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "vans-signals test signal from the production router",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": "*Time*\n2026-10-03 05:20:13.845830 +00:00",
                    },
                    {"type": "mrkdwn", "text": "*Logger*\nsrc.signals_test"},
                    {"type": "mrkdwn", "text": "*Source*\nvans-coding-router"},
                ],
            },
        ],
    }


def test_slack_message_escapes_slack_markup():
    message = slack_message(
        Signal(
            log_time=datetime.fromisoformat("2026-10-03T03:37:00+00:00"),
            logger_name="src.<jobs>",
            level="ERROR",
            message="see <https://evil.test|click> & wait",
            source="a & b",
        )
    )

    assert message["blocks"][1]["text"]["text"] == "see &lt;https://evil.test|click&gt; &amp; wait"
    fields = message["blocks"][3]["fields"]
    assert fields[1]["text"] == "*Logger*\nsrc.&lt;jobs&gt;"
    assert fields[2]["text"] == "*Source*\na &amp; b"
