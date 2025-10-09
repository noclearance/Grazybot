# bot/helpers/wom.py
# Helper functions for interacting with the Wise Old Man (WOM) API.

import aiohttp
from datetime import datetime, timezone, timedelta

from bot.config import WOM_CLAN_ID, WOM_VERIFICATION_CODE

BASE_URL = "https://api.wiseoldman.net/v2"

async def get_competition_details(competition_id: int) -> tuple[dict | None, str | None]:
    """Fetches details for a specific competition."""
    url = f"{BASE_URL}/competitions/{competition_id}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.json(), None
        except aiohttp.ClientError as e:
            return None, f"API Error: {e}"

async def create_competition(skill: str, duration_days: int) -> tuple[dict | None, str | None]:
    """Creates a new competition on WOM."""
    url = f"{BASE_URL}/competitions"
    start_date = datetime.now(timezone.utc) + timedelta(minutes=1)
    end_date = start_date + timedelta(days=duration_days)
    payload = {
        "title": f"{skill.capitalize()} SOTW ({duration_days} days)",
        "metric": skill,
        "startsAt": start_date.isoformat(),
        "endsAt": end_date.isoformat(),
        "groupId": int(WOM_CLAN_ID),
        "groupVerificationCode": WOM_VERIFICATION_CODE
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as response:
                response.raise_for_status()
                # Assuming the bot's DB logic is handled in the calling cog
                return await response.json(), None
        except aiohttp.ClientError as e:
            return None, f"API Error creating competition: {e}"

async def get_weekly_gains() -> tuple[list | None, str | None]:
    """Fetches the weekly overall gains for the clan."""
    url = f"{BASE_URL}/groups/{WOM_CLAN_ID}/gained?period=week&metric=overall"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.json(), None
        except aiohttp.ClientError as e:
            return None, f"Error fetching weekly gains: {e}"