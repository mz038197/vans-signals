import httpx


class SlackWebhook:
    def __init__(self, webhook_url: str, *, client: httpx.AsyncClient | None = None):
        self._webhook_url = webhook_url
        self._client = client if client is not None else httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    async def post(self, text: str) -> None:
        response = await self._client.post(self._webhook_url, json={"text": text})
        response.raise_for_status()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
