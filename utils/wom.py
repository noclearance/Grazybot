# utils/wom.py
# Helper functions for interacting with the Wise Old Man (WOM) API.

import aiohttp
from datetime import datetime, timezone, timedelta
import logging

from core import config

logger = logging.getLogger(__name__)
BASE_URL = "https://api.wiseoldman.net/v2"
HEADERS = {"User-Agent": "GrazyBot/2.0"}

async def get_competition_details(competition_id: int) -> tuple[dict | None, str | None]:
    """Fetches details for a specific competition."""
    url = f"{BASE_URL}/competitions/{competition_id}"
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.json(), None
        except aiohttp.ClientError as e:
            logger.error(f"WOM API Error fetching competition {competition_id}: {e}")
            return None, f"API Error: {e}"

async def create_competition(skill: str, duration_days: int) -> tuple[dict | None, str | None]:
    """Creates a new competition on WOM."""
    if not config.WOM_CLAN_ID or not config.WOM_VERIFICATION_CODE:
        logger.error("WOM_CLAN_ID or WOM_VERIFICATION_CODE is not set.")
        return None, "Bot is not configured for WOM competitions."

    url = f"{BASE_URL}/competitions"
    start_date = datetime.now(timezone.utc) + timedelta(minutes=1)
    end_date = start_date + timedelta(days=duration_days)

    payload = {
        "title": f"{skill.capitalize()} SOTW ({start_date.strftime('%b %d')})",
        "metric": skill,
        "startsAt": start_date.isoformat(),
        "endsAt": end_date.isoformat(),
        "groupId": int(config.WOM_CLAN_ID),
        "groupVerificationCode": config.WOM_VERIFICATION_CODE
    }

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.post(url, json=payload) as response:
                response.raise_for_status()
                data = await response.json()
                logger.info(f"Successfully created WOM competition: {data.get('competition', {}).get('id')}")
                return data, None
        except aiohttp.ClientError as e:
            logger.error(f"WOM API Error creating competition for {skill}: {e}")
            return None, f"API Error creating competition: {e}"

async def get_weekly_gains() -> tuple[list | None, str | None]:
    """Fetches the weekly overall gains for the clan."""
    if not config.WOM_CLAN_ID:
        logger.error("WOM_CLAN_ID is not set.")
        return None, "Bot is not configured to fetch clan gains."

    url = f"{BASE_URL}/groups/{config.WOM_CLAN_ID}/gained?period=week&metric=overall"
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.json(), None
        except aiohttp.ClientError as e:
            logger.error(f"WOM API Error fetching weekly gains: {e}")
            return None, f"Error fetching weekly gains: {e}"