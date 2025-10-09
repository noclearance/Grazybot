# bot/cogs/giveaway.py
# Contains commands for managing giveaways.

import discord
from discord.commands import SlashCommandGroup
from discord.ext import commands
from datetime import datetime, timedelta, timezone

from bot.config import GIVEAWAY_CHANNEL_ID
from bot.helpers.ai import generate_announcement_json
from bot.helpers.utils import parse_duration
from bot.views import GiveawayView

class Giveaway(commands.Cog):
    """Cog for managing giveaways with buttons."""
    
    def __init__(self, bot):
        self.bot = bot

    giveaway = SlashCommandGroup("giveaway", "Commands for managing giveaways.")

    @giveaway.command(name="start", description="Start a new giveaway.")
    @commands.has_permissions(manage_events=True)
    async def start_giveaway(self, ctx: discord.ApplicationContext, 
                             prize: discord.Option(str, "What is the prize?"), 
                             duration: discord.Option(str, "How long? (e.g., 7d, 12h, 30m)"), 
                             winners: discord.Option(int, "How many winners?", min_value=1, default=1), 
                             reward_role: discord.Option(discord.Role, "Optional role for winner(s).", required=False)):
        await ctx.defer(ephemeral=True)
        delta = parse_duration(duration)
        if delta is None:
            return await ctx.respond("Invalid duration format. Use 'd' for days, 'h' for hours, 'm' for minutes.", ephemeral=True)
        
        ends_at = datetime.now(timezone.utc) + delta
        giveaway_channel = self.bot.get_channel(GIVEAWAY_CHANNEL_ID)
        if not giveaway_channel:
            return await ctx.respond("Giveaway channel not found.", ephemeral=True)

        details = {"prize": prize, "winner_count": winners}
        ai_embed_data = await generate_announcement_json("giveaway_start", details)
        embed = discord.Embed.from_dict(ai_embed_data)
        embed.add_field(name="Ends In", value=f"<t:{int(ends_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Winners", value=f"**{winners}**", inline=True)
        if reward_role:
            embed.add_field(name="Bonus Reward", value=f"Winner(s) will also receive the {reward_role.mention} role!", inline=False)
        
        giveaway_message = await giveaway_channel.send(embed=embed)
        view = GiveawayView(message_id=giveaway_message.id)
        await giveaway_message.edit(view=view)

        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO giveaways (message_id, channel_id, prize, ends_at, winner_count, role_id) VALUES ($1, $2, $3, $4, $5, $6)",
                giveaway_message.id, giveaway_channel.id, prize, ends_at, winners, reward_role.id if reward_role else None
            )
        await ctx.respond(f"Giveaway for **{prize}** has been started!", ephemeral=True)

    @giveaway.command(name="entries", description="View the list of entrants for a giveaway.")
    @commands.has_permissions(manage_events=True)
    async def view_entries(self, ctx: discord.ApplicationContext, message_id: discord.Option(str, "The message ID of the giveaway.")):
        await ctx.defer(ephemeral=True)
        try:
            msg_id = int(message_id)
        except ValueError:
            return await ctx.respond("Invalid message ID.", ephemeral=True)

        async with self.bot.db_pool.acquire() as conn:
            giveaway_data = await conn.fetchrow("SELECT * FROM giveaways WHERE message_id = $1", msg_id)
            if not giveaway_data:
                return await ctx.respond("No giveaway found with that message ID.", ephemeral=True)
            
            entries = await conn.fetch("SELECT user_id FROM giveaway_entries WHERE message_id = $1", msg_id)
        
        embed = discord.Embed(title=f"Entries for '{giveaway_data['prize']}'", description=f"Total Entries: **{len(entries)}**", color=discord.Color.blue())
        
        if entries:
            entrant_list = []
            for entry in entries:
                member = ctx.guild.get_member(entry['user_id'])
                entrant_list.append(member.display_name if member else f"User ID: {entry['user_id']}")
            embed.description += "\n\n" + "\n".join(f"- {e}" for e in entrant_list)
            
        await ctx.respond(embed=embed, ephemeral=True)

def setup(bot):
    bot.add_cog(Giveaway(bot))