# bot/helpers/raffle_utils.py
# Helper functions for the raffle cog.

import discord
import random
from bot.config import RAFFLE_CHANNEL_ID, ANNOUNCEMENTS_CHANNEL_ID
from bot.helpers.utils import award_points

async def draw_raffle_winner(bot, raffle_id: int) -> str:
    """
    Handles drawing a winner for a specific raffle, awarding points, and announcing.
    Returns a status message.
    """
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not raffle_channel:
        return f"Raffle channel not found for raffle ID {raffle_id}."

    async with bot.db_pool.acquire() as conn:
        raffle_data = await conn.fetchrow("SELECT * FROM raffles WHERE id = $1", raffle_id)
        if not raffle_data or raffle_data['winner_id'] is not None:
            return f"Raffle {raffle_id} not found or already drawn."

        prize = raffle_data['prize']
        entries = await conn.fetch("SELECT user_id FROM raffle_entries WHERE raffle_id = $1", raffle_id)
        
        if not entries:
            await raffle_channel.send(f"The raffle for **{prize}** has ended, but no one entered.")
            await conn.execute("UPDATE raffles SET winner_id = 0 WHERE id = $1", raffle_id) # Mark as drawn with no winner
            return "Raffle ended with no entries."

        winner_id = random.choice(entries)['user_id']
        winner_user = await bot.fetch_user(winner_id)
        
        # Award points to the winner
        await award_points(bot, winner_user, 50, f"winning the raffle for {prize}")
        
        # Announce in raffle channel
        win_embed = discord.Embed(title="Raffle Winner Announcement!", description=f"Congratulations {winner_user.mention}, you have won **{prize}**!", color=discord.Color.fuchsia())
        await raffle_channel.send(embed=win_embed)
        
        # Announce globally
        ann_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
        if ann_channel:
            ann_embed = discord.Embed(title="A Champion of Fortune!", description=f"{winner_user.mention} has won the **{prize}** raffle and **50 Clan Points**!", color=discord.Color.gold())
            await ann_channel.send(content=f"@everyone Congratulations to {winner_user.mention}!", embed=ann_embed)
        
        await conn.execute("UPDATE raffles SET winner_id = $1 WHERE id = $2", winner_id, raffle_id)
        return f"Winner drawn: {winner_user.name}."