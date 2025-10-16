# cogs/sotw.py
# Contains commands for Skill of the Week (SOTW) competitions.

import discord
import random
import logging
from discord.commands import SlashCommandGroup, Option
from discord.ext import commands
from datetime import datetime

from core.bot import GrazyBot
from core import config
from utils import wom, clan
from utils.views import SotwPollView # This will be created in utils/views.py

logger = logging.getLogger(__name__)

# This should be in a config file, but for now, it's here.
WOM_SKILLS = [
    "overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer",
    "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking",
    "crafting", "smithing", "mining", "herblore", "agility", "thieving",
    "slayer", "farming", "runecrafting", "hunter", "construction"
]

class SOTW(commands.Cog):
    """Cog for SOTW commands."""
    
    def __init__(self, bot: GrazyBot):
        self.bot = bot

    sotw = SlashCommandGroup("sotw", "Commands for Skill of the Week")

    async def start_sotw_logic(self, interaction: discord.Interaction, skill: str, duration_days: int):
        """Shared logic for starting an SOTW, usable by commands and views."""
        data, error = await wom.create_competition(skill, duration_days)
        if error:
            await interaction.followup.send(f"Error creating WOM competition: {error}", ephemeral=True)
            return

        competition_id = data.get('competition', {}).get('id')
        if not competition_id:
            return await interaction.followup.send("Failed to get competition ID from WOM.", ephemeral=True)

        # Store the active competition in the database
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO active_competitions (id, ends_at) VALUES ($1, $2)",
                competition_id, data['endsAt']
            )

        sotw_channel = self.bot.get_channel(config.SOTW_CHANNEL_ID)
        if sotw_channel:
            embed = self.create_competition_embed(data, interaction.user)
            sotw_message = await sotw_channel.send(embed=embed)
            await clan.send_global_announcement(
                self.bot, "sotw_start", {"skill": skill.capitalize()}, sotw_message.jump_url
            )
            await interaction.followup.send(f"SOTW for **{skill.capitalize()}** started in {sotw_channel.mention}!", ephemeral=True)
        else:
            logger.warning("SOTW_CHANNEL_ID not configured.")
            await interaction.followup.send("SOTW Channel not configured. Please set it up.", ephemeral=True)

    @sotw.command(name="start", description="Manually start a new SOTW competition.")
    @commands.has_permissions(manage_events=True)
    async def start(self, ctx: discord.ApplicationContext, 
                    skill: Option(str, "The skill for the competition.", choices=WOM_SKILLS),
                    duration_days: Option(int, "Duration in days.", default=7)):
        await ctx.defer(ephemeral=True)
        await self.start_sotw_logic(ctx.interaction, skill, duration_days)

    @sotw.command(name="poll", description="Start a poll to choose the next SOTW.")
    @commands.has_permissions(manage_events=True)
    async def poll(self, ctx: discord.ApplicationContext):
        if ctx.guild.id in self.bot.active_polls:
            return await ctx.respond("An SOTW poll is already active in this server.", ephemeral=True)
        
        poll_skills = random.sample([s for s in WOM_SKILLS if s != 'overall'], 6)
        
        async def start_sotw_callback(interaction, winner):
            await self.start_sotw_logic(interaction, winner, 7)

        view = SotwPollView(
            author=ctx.author,
            bot_instance=self.bot,
            skills_to_poll=poll_skills,
            callback=start_sotw_callback
        )

        sotw_channel = self.bot.get_channel(config.SOTW_CHANNEL_ID)
        if sotw_channel:
            poll_message = await sotw_channel.send(embed=await view.create_embed(), view=view)
            self.bot.active_polls[ctx.guild.id] = poll_message.id
            await ctx.respond(f"SOTW Poll created in {sotw_channel.mention}!", ephemeral=True)
        else:
            await ctx.respond("Error: SOTW Channel not configured.", ephemeral=True)

    @sotw.command(name="view", description="View the leaderboard for the current SOTW.")
    async def view(self, ctx: discord.ApplicationContext,
                   competition_id: Option(int, "Optional ID of a specific competition", required=False)):
        await ctx.defer()
        
        if not competition_id:
            async with self.bot.db_pool.acquire() as conn:
                comp_id = await conn.fetchval("SELECT id FROM active_competitions ORDER BY ends_at DESC LIMIT 1")
                if not comp_id:
                    return await ctx.respond("No active SOTW competition found.", ephemeral=True)
                competition_id = comp_id
        
        data, error = await wom.get_competition_details(competition_id)
        if error:
            return await ctx.respond(f"Could not fetch details for competition ID {competition_id}. Error: {error}")

        embed = self.create_leaderboard_embed(data)
        await ctx.respond(embed=embed)

    def create_competition_embed(self, data: dict, author: discord.User) -> discord.Embed:
        """Helper to create the initial SOTW announcement embed."""
        embed = discord.Embed(
            title=f"New SOTW: {data['title']}",
            url=f"https://wiseoldman.net/competitions/{data['id']}",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Competition started by {author.display_name}", icon_url=author.display_avatar.url)
        embed.timestamp = datetime.fromisoformat(data['endsAt'].replace('Z', '+00:00'))
        return embed

    def create_leaderboard_embed(self, data: dict) -> discord.Embed:
        """Helper to create the SOTW leaderboard embed."""
        embed = discord.Embed(
            title=f"Leaderboard: {data['title']}",
            url=f"https://wiseoldman.net/competitions/{data['id']}",
            color=discord.Color.purple()
        )

        leaderboard_text = []
        for i, p in enumerate(data.get('participations', [])[:10]):
            rank = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"**#{i+1}**")
            gained_xp = p.get('progress', {}).get('gained', 0)
            leaderboard_text.append(f"{rank} **{p['player']['displayName']}**: `{gained_xp:,}` XP")
        
        embed.description = "\n".join(leaderboard_text) if leaderboard_text else "No participants have gained XP yet."
        embed.set_footer(text="Competition ends")
        embed.timestamp = datetime.fromisoformat(data['endsAt'].replace('Z', '+00:00'))
        return embed

async def setup(bot: GrazyBot):
    await bot.add_cog(SOTW(bot))