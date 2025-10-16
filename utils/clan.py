# utils/clan.py
# Utilities related to clan management and announcements.

import discord
import logging
from . import ai
from core import config
from core.bot import GrazyBot

logger = logging.getLogger(__name__)

async def award_points(bot: GrazyBot, member: discord.Member | discord.User, amount: int, reason: str):
    """
    Awards clan points to a member, updates the database, and sends them a DM.
    """
    if not member or member.bot:
        return

    try:
        async with bot.db_pool.acquire() as conn:
            # Ensure the user exists before updating points
            await conn.execute(
                "INSERT INTO clan_points (discord_id, points) VALUES ($1, 0) ON CONFLICT (discord_id) DO NOTHING",
                member.id
            )
            # Add points and get the new balance
            new_balance = await conn.fetchval(
                "UPDATE clan_points SET points = points + $1 WHERE discord_id = $2 RETURNING points",
                amount, member.id
            )

        # Send a confirmation DM to the user
        dm_embed = await ai.generate_announcement_json(
            "points_award",
            {"amount": amount, "reason": reason}
        )
        embed = discord.Embed.from_dict(dm_embed)
        embed.add_field(name="New Balance", value=f"You now have **{new_balance:,}** Clan Points.")

        await member.send(embed=embed)
        logger.info(f"Awarded {amount} points to {member.display_name} for: {reason}")

    except discord.Forbidden:
        logger.warning(f"Could not send points award DM to {member.display_name}. They may have DMs disabled.")
    except Exception as e:
        logger.error(f"An error occurred while awarding points to {member.display_name}: {e}")

async def send_global_announcement(bot: GrazyBot, event_type: str, details: dict, message_url: str):
    """
    Sends a standardized, AI-generated announcement to the global announcements channel.
    """
    if not config.ANNOUNCEMENTS_CHANNEL_ID:
        logger.warning("ANNOUNCEMENTS_CHANNEL_ID is not set. Cannot send global announcement.")
        return

    channel = bot.get_channel(config.ANNOUNCEMENTS_CHANNEL_ID)
    if not channel:
        logger.error(f"Could not find announcement channel with ID {config.ANNOUNCEMENTS_CHANNEL_ID}.")
        return

    try:
        ai_embed_data = await ai.generate_announcement_json(event_type, details)
        embed = discord.Embed.from_dict(ai_embed_data)
        embed.url = message_url

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="View Event", style=discord.ButtonStyle.link, url=message_url))

        await channel.send(content="@everyone", embed=embed, view=view)
        logger.info(f"Sent global announcement for event: {event_type}")
    except Exception as e:
        logger.error(f"Failed to send global announcement for {event_type}: {e}")