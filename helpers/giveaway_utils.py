# bot/helpers/giveaway_utils.py
# Helper functions for the giveaway cog.

import discord
import random

async def end_giveaway(bot, giveaway_data: dict):
    """Handles the logic for ending a giveaway and announcing winners."""
    message_id = giveaway_data['message_id']
    channel_id = giveaway_data['channel_id']
    prize = giveaway_data['prize']
    winner_count = giveaway_data['winner_count']
    role_id = giveaway_data.get('role_id')
    
    async with bot.db_pool.acquire() as conn:
        await conn.execute("UPDATE giveaways SET is_active = FALSE WHERE message_id = $1", message_id)
        entries = await conn.fetch("SELECT user_id FROM giveaway_entries WHERE message_id = $1", message_id)
        
    channel = bot.get_channel(channel_id)
    if not channel: return

    user_ids = [entry['user_id'] for entry in entries]
    if not user_ids:
        await channel.send(f"The giveaway for **{prize}** has ended with no entries.")
        return

    num_to_select = min(winner_count, len(user_ids))
    winner_ids = random.sample(user_ids, k=num_to_select)
    winner_mentions = [f"<@{w_id}>" for w_id in winner_ids]
    
    win_str = "Winner" if len(winner_mentions) == 1 else "Winners"
    embed = discord.Embed(title=f"Giveaway {win_str}!", description=f"Congratulations to {', '.join(winner_mentions)}! You won **{prize}**!", color=discord.Color.gold())
    
    # Award role if specified
    if role_id and (role := channel.guild.get_role(role_id)):
        for winner_id in winner_ids:
            if (member := await channel.guild.fetch_member(winner_id)):
                await member.add_roles(role)
        embed.description += f"\nYou have also been awarded the **{role.name}** role!"

    await channel.send(content=f"Congratulations {', '.join(winner_mentions)}!", embed=embed)

    # Edit original message
    try:
        message = await channel.fetch_message(message_id)
        ended_embed = message.embeds[0]
        ended_embed.title = "Giveaway Ended"
        ended_embed.color = discord.Color.dark_red()
        ended_embed.clear_fields()
        ended_embed.add_field(name=f"{win_str}", value=', '.join(winner_mentions))
        await message.edit(embed=ended_embed, view=None)
    except discord.NotFound:
        pass