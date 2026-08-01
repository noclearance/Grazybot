# Optional push of bot events to the Base44 hub (Pattern A).
# Never raises into Discord command flow.

import logging
import os

import aiohttp

logger = logging.getLogger("grazybot.hub")

BASE44_HUB_URL = os.getenv("BASE44_HUB_URL", "").rstrip("/")
BASE44_HUB_TOKEN = os.getenv("BASE44_HUB_TOKEN", "")


async def push_to_hub(event: str, payload: dict) -> None:
    """POST an event to Base44 ingest endpoint. No-op if not configured."""
    if not BASE44_HUB_URL:
        return
    url = f"{BASE44_HUB_URL}/api/ingest"
    headers = {"Content-Type": "application/json"}
    if BASE44_HUB_TOKEN:
        headers["Authorization"] = f"Bearer {BASE44_HUB_TOKEN}"
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json={"event": event, "data": payload},
                headers=headers,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning("hub push failed %s: %s %s", event, resp.status, body[:200])
    except Exception as e:
        logger.warning("hub push failed %s: %s", event, e)
