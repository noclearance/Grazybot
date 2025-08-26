# cogs/competitions.py
import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup
import random
import aiohttp
from datetime import datetime

# Import the helper functions from our new utils file
from .utils import (
    get_db_connection,
    create_competition,
    create_competition_embed,
    send_global_announcement,
    generate_announcement_json,
    WOM_CLAN_ID # Also import necessary constants
)

# Define WOM skill metrics - better to keep it with the command that uses it
WOM_SKILLS = ["overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herblore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"]

# --- SOTW Poll View and Buttons ---
# These classes are now inside the cog file for better organization
class SotwButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction):
        # ... (full callback logic from your original file)
        await interaction.response.defer() # Placeholder

class FinishButton(discord.ui.Button):
    def __init__(self, **kwargs):
        super().__init__(style=discord.ButtonStyle.danger, **kwargs)
    
    async def callback(self, interaction: discord.Interaction):
        # ... (full callback logic from your original file)
        await interaction.response.defer() # Placeholder

class SotwPollView(discord.ui.View):
    def __init__(self, author):
        super().__init__(timeout=86400)
        self.author = author
        self.votes = {}

    async def create_embed(self):
        # ... (full create_embed logic from your original file)
        return discord.Embed(title="Poll") # Placeholder

    def add_buttons(self, skills):
        for skill in skills:
            self.votes[skill] = []
            self.add_item(SotwButton(label=skill.capitalize(), custom_id=skill))
        self.add_item(FinishButton(label="Finish Poll & Start SOTW", custom_id="finish_poll"))

# --- Cog Class ---
class Competitions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.active_polls = {} # Store active polls on the bot instance

    # Create the slash command group
    sotw = SlashCommandGroup("sotw", "Commands for Skill of the Week")

    @sotw.command(name="poll", description="Start a poll to choose the next SOTW.")
    async def poll(self, ctx: discord.ApplicationContext):
        if ctx.guild.id in self.bot.active_polls:
            return await ctx.respond("There is already an active SOTW poll.", ephemeral=True)
        
        poll_skills = random.sample(WOM_SKILLS, 6)
        view = SotwPollView(ctx.author) # The view now lives here
        view.add_buttons(poll_skills)
        embed = await view.create_embed()
        
        # You'll need to define SOTW_CHANNEL_ID in your env/utils file
        from .utils import SOTW_CHANNEL_ID 
        sotw_channel = self.bot.get_channel(SOTW_CHANNEL_ID)
        
        if sotw_channel:
            poll_message = await sotw_channel.send(embed=embed, view=view)
            await ctx.respond("SOTW Poll created!", ephemeral=True)
            self.bot.active_polls[ctx.guild.id] = view
        else:
            await ctx.respond("Error: SOTW Channel ID not configured correctly.", ephemeral=True)

    @sotw.command(name="view", description="View the leaderboard for the current SOTW.")
    async def view(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        list_url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/competitions"
        async with aiohttp.ClientSession() as session:
            async with session.get(list_url) as response:
                if response.status != 200:
                    return await ctx.respond("Could not fetch competition list.")
                competitions = await response.json()
                if not competitions:
                    return await ctx.respond("This clan has no competitions on Wise Old Man.")
                latest_comp_id = competitions[0]['id']
        
        details_url = f"https://api.wiseoldman.net/v2/competitions/{latest_comp_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(details_url) as response:
                if response.status != 200:
                    return await ctx.respond(f"Could not fetch details for competition ID {latest_comp_id}.")
                data = await response.json()
        
        embed = discord.Embed(
            title=f"Leaderboard: {data['title']}",
            description=f"Current standings for the **{data['metric'].capitalize()}** competition.",
            color=discord.Color.purple(),
            url=f"https://wiseoldman.net/competitions/{data['id']}"
        )
        leaderboard_text = ""
        for i, player in enumerate(data['participations'][:10]):
            rank_emoji = {1: "🏆", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
            leaderboard_text += f"{rank_emoji} **{player['player']['displayName']}**: {player['progress']['gained']:,} XP\n"
        
        if not leaderboard_text:
            leaderboard_text = "No participants have gained XP yet."
        
        embed.add_field(name="Top 10", value=leaderboard_text, inline=False)
        end_dt = datetime.fromisoformat(data['endsAt'].replace('Z', '+00:00'))
        embed.set_footer(text="Competition ends")
        embed.timestamp = end_dt
        await ctx.respond(embed=embed)

# This function is required for the cog to be loaded by the bot
def setup(bot):
    bot.add_cog(Competitions(bot))