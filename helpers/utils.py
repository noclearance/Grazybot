# bot/helpers/utils.py
# General utility functions used across multiple cogs.

import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta
import re
import discord

from bot.config import ANNOUNCEMENTS_CHANNEL_ID
from bot.helpers.ai import generate_announcement_json

async def load_item_mapping(bot):
    """Fetches the OSRS item name-to-ID mapping on startup."""
    url = "https://prices.osrs.cloud/api/v1/latest/mapping"
    headers = {'User-Agent': 'GrazyBot/1.0'}
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json()
                    bot.item_mapping = {item['name'].lower(): item for item in data}
                    print(f"Successfully loaded {len(bot.item_mapping)} items.")
                    return
        except Exception as e:
            print(f"Error loading item mapping (attempt {attempt+1}): {e}")
            await asyncio.sleep(5)
    print("Failed to load item mapping after multiple attempts.")

def format_price_timestamp(ts: int) -> str:
    """Formats a UNIX timestamp into a relative time string."""
    if not ts: return "N/A"
    delta = datetime.now(timezone.utc) - datetime.fromtimestamp(ts, tz=timezone.utc)
    # ... (formatting logic from original file) ...
    if delta.days > 0:
        return f"{delta.days} days ago"
    if delta.seconds > 3600:
        return f"{delta.seconds // 3600} hours ago"
    return f"{delta.seconds // 60} minutes ago"


def parse_duration(duration_str: str) -> timedelta | None:
    """Parses a duration string (e.g., '7d', '12h', '30m') into a timedelta."""
    match = re.match(r"(\d+)([mhd])", duration_str.lower())
    if not match: return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == 'm': return timedelta(minutes=value)
    if unit == 'h': return timedelta(hours=value)
    if unit == 'd': return timedelta(days=value)
    return None

async def award_points(bot, member: discord.Member | discord.User, amount: int, reason: str):
    """Awards clan points to a member and sends them a DM."""
    if not member or member.bot: return
    async with bot.db_pool.acquire() as conn:
        await conn.execute("INSERT INTO clan_points (discord_id, points) VALUES ($1, 0) ON CONFLICT (discord_id) DO NOTHING", member.id)
        new_balance = await conn.fetchval("UPDATE clan_points SET points = points + $1 WHERE discord_id = $2 RETURNING points", amount, member.id)
    try:
        details = {"amount": amount, "reason": reason}
        ai_dm_data = await generate_announcement_json("points_award", details)
        dm_embed = discord.Embed.from_dict(ai_dm_data)
        dm_embed.add_field(name="New Balance", value=f"You now have **{new_balance}** Clan Points.")
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        print(f"Could not DM {member.display_name}.")

async def send_global_announcement(bot, event_type: str, details: dict, message_url: str):
    """Sends a standardized announcement to the global announcements channel."""
    channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not channel: return
    
    ai_embed_data = await generate_announcement_json(event_type, details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.url = message_url
    embed.add_field(name="Details", value=f"[Click here to view the event!]({message_url})")
    await channel.send(content="@everyone", embed=embed)