# bot/cogs/points.py
# Commands related to clan points.

import discord
from discord.commands import SlashCommandGroup
from discord.ext import commands

class Points(commands.Cog):
    """Cog for viewing clan points."""
    
    def __init__(self, bot):
        self.bot = bot

    points = SlashCommandGroup("points", "Commands related to Clan Points.")

    @points.command(name="view", description="Check your current Clan Point balance.")
    async def view_points(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            point_data = await conn.fetchval("SELECT points FROM clan_points WHERE discord_id = $1", ctx.author.id)
        
        current_points = point_data if point_data is not None else 0
        await ctx.respond(f"You currently have **{current_points}** Clan Points.")

    @points.command(name="leaderboard", description="View the Clan Points leaderboard.")
    async def leaderboard(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        async with self.bot.db_pool.acquire() as conn:
            leaders = await conn.fetch("SELECT discord_id, points FROM clan_points WHERE points > 0 ORDER BY points DESC LIMIT 10")
            
        embed = discord.Embed(title="Clan Points Leaderboard", color=discord.Color.gold())
        if not leaders:
            embed.description = "No one has earned any points yet."
        else:
            leaderboard_text = ""
            for i, record in enumerate(leaders):
                member = ctx.guild.get_member(record['discord_id'])
                member_name = member.display_name if member else f"User ID: {record['discord_id']}"
                rank_emoji = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"\`{i + 1}.\`")
                leaderboard_text += f"{rank_emoji} **{member_name}**: {record['points']:,} points\n"
            embed.description = leaderboard_text
            
        await ctx.respond(embed=embed)

def setup(bot):
    bot.add_cog(Points(bot))