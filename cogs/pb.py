# cogs/pb.py
# Contains commands for tracking Boss Personal Bests (PBs).

# cogs/pb.py
# Contains commands for tracking Boss Personal Bests (PBs).

import discord
import logging
from discord import app_commands
from discord.ext import commands

from core.bot import GrazyBot

logger = logging.getLogger(__name__)

class PersonalBests(commands.Cog):
    """Cog for logging and viewing personal best times for bosses."""
    
    def __init__(self, bot: GrazyBot):
        self.bot = bot

    pb_group = app_commands.Group(name="pb", description="Commands for tracking Boss Personal Bests.")

    @pb_group.command(name="log", description="Log or update your Personal Best time for a boss.")
    async def log_pb(self, interaction: discord.Interaction,
                       boss_name: str,
                       time_in_seconds: float,
                       proof_url: str):
        """Logs a user's personal best time for a boss to the database."""
        await interaction.response.defer(ephemeral=True)
        
        if not proof_url.startswith(('http://', 'https://')):
            return await interaction.followup.send("Proof URL must be a valid web link.", ephemeral=True)

        pb_time_ms = int(time_in_seconds * 1000)
        
        try:
            async with self.bot.db_pool.acquire() as conn:
                existing_pb_ms = await conn.fetchval(
                    "SELECT pb_time_ms FROM boss_pbs WHERE discord_id = $1 AND boss_name ILIKE $2",
                    interaction.user.id, boss_name
                )

                if existing_pb_ms and pb_time_ms >= existing_pb_ms:
                    return await interaction.followup.send(f"Your time ({time_in_seconds:.2f}s) is not faster than your current PB of {existing_pb_ms/1000:.2f}s.", ephemeral=True)

                await conn.execute(
                    """
                    INSERT INTO boss_pbs (discord_id, boss_name, pb_time_ms, proof_url)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (discord_id, boss_name)
                    DO UPDATE SET pb_time_ms = EXCLUDED.pb_time_ms, proof_url = EXCLUDED.proof_url, logged_at = NOW()
                    """,
                    interaction.user.id, boss_name.title(), pb_time_ms, proof_url
                )
            
            await interaction.followup.send(f"Your PB for **{boss_name.title()}** has been updated to **{time_in_seconds:.2f}s**!", ephemeral=True)
            logger.info(f"User {interaction.user} logged a new PB for {boss_name.title()}: {time_in_seconds}s")
        except Exception as e:
            logger.error(f"Error logging PB for {interaction.user}: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while logging your PB.", ephemeral=True)

    @pb_group.command(name="my", description="View your Personal Best for a specific boss.")
    async def my_pb(self, interaction: discord.Interaction, boss_name: str):
        """Displays a user's logged personal best for a specific boss."""
        await interaction.response.defer()
        try:
            async with self.bot.db_pool.acquire() as conn:
                pb_data = await conn.fetchrow("SELECT * FROM boss_pbs WHERE discord_id = $1 AND boss_name ILIKE $2", interaction.user.id, boss_name)

            if not pb_data:
                return await interaction.followup.send(f"You have no logged PB for **{boss_name.title()}**. Use `/pb log` to add one.", ephemeral=True)

            embed = discord.Embed(title=f"{interaction.user.display_name}'s PB for {boss_name.title()}", color=discord.Color.gold())
            embed.add_field(name="Time", value=f"**{pb_data['pb_time_ms'] / 1000:.2f} seconds**", inline=False)
            embed.add_field(name="Proof", value=f"[View Proof]({pb_data['proof_url']})", inline=False)
            embed.set_footer(text=f"Logged on: {pb_data['logged_at'].strftime('%Y-%m-%d')}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error fetching PB for {interaction.user} and boss {boss_name}: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching your PB.", ephemeral=True)

    @pb_group.command(name="clan", description="View the clan leaderboard for a specific boss PB.")
    async def clan_pb(self, interaction: discord.Interaction, boss_name: str):
        """Displays the top 10 clan PBs for a specific boss."""
        await interaction.response.defer()
        try:
            async with self.bot.db_pool.acquire() as conn:
                leaderboard_data = await conn.fetch(
                    "SELECT * FROM boss_pbs WHERE boss_name ILIKE $1 ORDER BY pb_time_ms ASC LIMIT 10",
                    boss_name
                )

            embed = discord.Embed(title=f"🏆 Clan PB Leaderboard: {boss_name.title()}", color=discord.Color.blue())

            if not leaderboard_data:
                embed.description = f"No PBs logged for **{boss_name.title()}** yet."
            else:
                leaderboard_text = []
                for i, entry in enumerate(leaderboard_data):
                    member = interaction.guild.get_member(entry['discord_id'])
                    member_name = member.display_name if member else f"User ID: {entry['discord_id']}"
                    rank_emoji = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"**#{i + 1}**")
                    leaderboard_text.append(f"{rank_emoji} {member_name}: `{entry['pb_time_ms']/1000:.2f}s` ([Proof]({entry['proof_url']}))")
                embed.description = "\n".join(leaderboard_text)

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error fetching clan PB leaderboard for {boss_name}: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching the leaderboard.", ephemeral=True)

async def setup(bot: GrazyBot):
    await bot.add_cog(PersonalBests(bot))