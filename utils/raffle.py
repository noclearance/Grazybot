# utils/raffle.py
# Helper functions for the raffle cog.

import discord
import random
import logging
from core import config
from . import clan

logger = logging.getLogger(__name__)

async def draw_raffle_winner(bot, raffle_id: int) -> str:
    """
    Handles drawing a winner for a specific raffle, awarding points, and announcing.
    Returns a status message.
    """
    raffle_channel = bot.get_channel(config.RAFFLE_CHANNEL_ID)
    if not raffle_channel:
        logger.error(f"Raffle channel ID {config.RAFFLE_CHANNEL_ID} not found.")
        return f"Raffle channel not configured for raffle ID {raffle_id}."

    try:
        async with bot.db_pool.acquire() as conn:
            raffle_data = await conn.fetchrow("SELECT * FROM raffles WHERE id = $1", raffle_id)
            if not raffle_data or raffle_data['winner_id'] is not None:
                return f"Raffle {raffle_id} not found or has already been drawn."

            prize = raffle_data['prize']
            entries = await conn.fetch("SELECT user_id FROM raffle_entries WHERE raffle_id = $1", raffle_id)

            if not entries:
                await raffle_channel.send(f"The raffle for **{prize}** has ended, but unfortunately, no one entered.")
                await conn.execute("UPDATE raffles SET winner_id = 0 WHERE id = $1", raffle_id) # Mark as drawn, no winner
                return "Raffle ended with no entries."

            winner_id = random.choice([entry['user_id'] for entry in entries])
            winner_user = await bot.fetch_user(winner_id)

            # Award points to the winner using the centralized function
            await clan.award_points(bot, winner_user, 50, f"winning the raffle for '{prize}'")

            # Announce in the raffle channel
            win_embed = discord.Embed(
                title="🎉 Raffle Winner Announcement! 🎉",
                description=f"Congratulations {winner_user.mention}, you have won **{prize}**!",
                color=discord.Color.fuchsia()
            )
            win_embed.set_footer(text="May your luck continue!")
            await raffle_channel.send(content=winner_user.mention, embed=win_embed)

            # Send a global announcement
            await clan.send_global_announcement(
                bot,
                "raffle_win",
                {"winner_name": winner_user.display_name, "prize": prize},
                raffle_channel.last_message.jump_url if raffle_channel.last_message else ""
            )

            await conn.execute("UPDATE raffles SET winner_id = $1 WHERE id = $2", winner_id, raffle_id)
            logger.info(f"Raffle {raffle_id} winner drawn: {winner_user.name} ({winner_id}).")
            return f"Winner drawn: {winner_user.name}."
    except Exception as e:
        logger.error(f"An error occurred while drawing winner for raffle {raffle_id}: {e}", exc_info=True)
        return "An internal error occurred during the draw."