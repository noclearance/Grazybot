# bot/cogs/pb.py
# Contains commands for tracking Boss Personal Bests (PBs).

import discord
from discord.commands import SlashCommandGroup
from discord.ext import commands

class PersonalBests(commands.Cog):
    """Cog for logging and viewing personal best times for bosses."""
    
    def __init__(self, bot):
        self.bot = bot

    pb = SlashCommandGroup("pb", "Commands for tracking Boss Personal Bests.")

    @pb.command(name="log", description="Log or update your Personal Best time for a boss.")
    async def log_pb(self, ctx: discord.ApplicationContext,
                       boss_name: discord.Option(str, "Name of the boss."),
                       time_in_seconds: discord.Option(float, "Your PB time in seconds (e.g., 123.45)."),
                       proof_url: discord.Option(str, "URL to proof (screenshot/video).")):
        await ctx.defer(ephemeral=True)
        
        if not proof_url.startswith(('http://', 'https://')):
            return await ctx.respond("Proof URL must be a valid web link.", ephemeral=True)

        pb_time_ms = int(time_in_seconds * 1000)
        
        async with self.bot.db_pool.acquire() as conn:
            # Check if the new PB is actually better than the old one
            existing_pb_ms = await conn.fetchval("SELECT pb_time_ms FROM boss_pbs WHERE discord_id = $1 AND boss_name ILIKE $2", ctx.author.id, boss_name)

            if existing_pb_ms and pb_time_ms >= existing_pb_ms:
                return await ctx.respond(f"Your time ({time_in_seconds:.2f}s) is not faster than your current PB ({existing_pb_ms/1000:.2f}s).", ephemeral=True)

            await conn.execute("""
                INSERT INTO boss_pbs (discord_id, boss_name, pb_time_ms, proof_url) 
                VALUES ($1, $2, $3, $4) 
                ON CONFLICT (discord_id, boss_name) 
                DO UPDATE SET pb_time_ms = EXCLUDED.pb_time_ms, proof_url = EXCLUDED.proof_url, logged_at = NOW()
            """, ctx.author.id, boss_name.title(), pb_time_ms, proof_url)
            
            await ctx.respond(f"Your PB for **{boss_name.title()}** has been updated to **{time_in_seconds:.2f}s**!", ephemeral=True)

    @pb.command(name="my", description="View your Personal Best for a specific boss.")
    async def my_pb(self, ctx: discord.ApplicationContext, boss_name: discord.Option(str, "Name of the boss.")):
        await ctx.defer()
        async with self.bot.db_pool.acquire() as conn:
            pb_data = await conn.fetchrow("SELECT * FROM boss_pbs WHERE discord_id = $1 AND boss_name ILIKE $2", ctx.author.id, boss_name)

        if not pb_data:
            return await ctx.respond(f"You have no logged PB for **{boss_name.title()}**.", ephemeral=True)

        embed = discord.Embed(title=f"{ctx.author.display_name}'s PB for {boss_name.title()}", color=discord.Color.gold())
        embed.add_field(name="Time", value=f"**{pb_data['pb_time_ms'] / 1000:.2f} seconds**", inline=False)
        embed.add_field(name="Proof", value=f"[View Proof]({pb_data['proof_url']})", inline=False)
        embed.set_footer(text=f"Logged on: {pb_data['logged_at'].strftime('%Y-%m-%d')}")
        await ctx.respond(embed=embed)

    @pb.command(name="clan", description="View the clan leaderboard for a specific boss PB.")
    async def clan_pb(self, ctx: discord.ApplicationContext, boss_name: discord.Option(str, "Name of the boss.")):
        await ctx.defer()
        async with self.bot.db_pool.acquire() as conn:
            leaderboard_data = await conn.fetch("SELECT * FROM boss_pbs WHERE boss_name ILIKE $1 ORDER BY pb_time_ms ASC LIMIT 10", boss_name)

        embed = discord.Embed(title=f"Clan PB Leaderboard: {boss_name.title()}", color=discord.Color.blue())

        if not leaderboard_data:
            embed.description = f"No PBs logged for **{boss_name.title()}** yet."
        else:
            leaderboard_text = ""
            for i, entry in enumerate(leaderboard_data):
                member = ctx.guild.get_member(entry['discord_id'])
                member_name = member.display_name if member else f"User ID: {entry['discord_id']}"
                rank_emoji = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"\`{i + 1}.\`")
                leaderboard_text += f"{rank_emoji} **{member_name}**: {entry['pb_time_ms']/1000:.2f}s\n"
            embed.description = leaderboard_text
        
        await ctx.respond(embed=embed)

def setup(bot):
    bot.add_cog(PersonalBests(bot))