# utils/ge.py
# Utilities related to the Grand Exchange.

import aiohttp
import asyncio
import logging

logger = logging.getLogger(__name__)

async def load_item_mapping(bot):
    """
    Fetches the OSRS item name-to-ID mapping on startup and stores it in the bot.
    """
    url = "https://prices.osrs.cloud/api/v1/latest/mapping"
    headers = {'User-Agent': 'GrazyBot/2.0'}

    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json()
                    # Process the mapping to be lowercase for easier lookups
                    bot.item_mapping = {item['name'].lower(): item for item in data}
                    logger.info(f"Successfully loaded {len(bot.item_mapping)} OSRS items into mapping.")
                    return
        except aiohttp.ClientError as e:
            logger.warning(f"Error loading item mapping (attempt {attempt+1}/3): {e}")
            await asyncio.sleep(5) # Wait before retrying
        except Exception as e:
            logger.error(f"An unexpected error occurred during item mapping load: {e}")
            break # Exit loop on unexpected error

    logger.error("Failed to load OSRS item mapping after multiple attempts. GE commands may not function.")