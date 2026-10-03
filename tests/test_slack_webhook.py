import json

import httpx
import pytest

from vans_signals.slack import SlackWebhook

WEBHOOK_URL = "https://hooks.slack.test/services/T/B/X"
SLACK_TEXT = (
    "2026-10-03T03:37:00+00:00 src.jobs.catalog ERROR upstream openrouter did not update"
)


@pytest.mark.asyncio
async def test_slack_webhook_posts_the_message_text():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await SlackWebhook(WEBHOOK_URL, client=http).post(SLACK_TEXT)

    assert seen == {"url": WEBHOOK_URL, "body": {"text": SLACK_TEXT}}


@pytest.mark.asyncio
async def test_slack_webhook_rejects_a_failed_post():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await SlackWebhook(WEBHOOK_URL, client=http).post(SLACK_TEXT)
