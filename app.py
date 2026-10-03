import asyncio
import os

from vans_signals.app import create_app
from vans_signals.slack import SlackWebhook


def build_app():
    return create_app(
        database_url=os.environ["DATABASE_URL"],
        tokens={os.environ["SIGNALS_ROUTER_TOKEN"]: os.environ["SIGNALS_ROUTER_SOURCE"]},
        slack=SlackWebhook(os.environ["SLACK_WEBHOOK_URL"]),
        sleep=asyncio.sleep,
    )


app = build_app()
