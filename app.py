import asyncio
import os

from vans_signals.app import create_app, load_signals_tokens
from vans_signals.slack import SlackWebhook


def build_app():
    return create_app(
        database_url=os.environ["DATABASE_URL"],
        tokens=load_signals_tokens(os.environ.get("SIGNALS_TOKENS")),
        slack=SlackWebhook(os.environ["SLACK_WEBHOOK_URL"]),
        sleep=asyncio.sleep,
    )


app = build_app()
