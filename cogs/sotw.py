# bot/cogs/sotw.py
# Contains commands for Skill of the Week (SOTW) competitions.

import discord
import random
from discord.commands import SlashCommandGroup
from discord.ext import commands
from datetime import datetime

from bot.config import WOM_CLAN_ID, WOM_SKILLS, SOTW_CHANNEL_ID
from bot.helpers.ai import generate_announcement_json
from bot.helpers.wom import create_competition, get_competition_details
from bot.helpers.embeds import create_competition_embed
from bot.helpers.utils import send_global_announcement

# --- UI Views for SOTW Polls ---
class SotwPollView(discord.ui.View):
    def __init__(self, author: discord.Member, bot_instance):
        super().__init__(timeout=86400) # Poll lasts 24 hours
        self.author = author
        self.bot = bot_instance
        self.votes = {}

    async def create_embed(self) -> discord.Embed:
        ai_embed_data = await generate_announcement_json("sotw_poll")
        vote_description = "\n\n**Current Votes:**\n"
        for skill, voters in self.votes.items():
            vote_description += f"**{skill.capitalize()}**: {len(voters)} vote(s)\n"
        
        embed = discord.Embed.from_dict(ai_embed_data)
        embed.description += vote_description
        embed.set_footer(text=f"Poll by {self.author.display_name}", icon_url=self.author.display_avatar.url)
        return embed

    def add_buttons(self, skills: list):
        for skill in skills:
            self.votes[skill] = []
            self.add_item(SotwButton(label=skill.capitalize(), custom_id=skill))
        self.add_item(FinishButton(label="Finish Poll", custom_id="finish_poll"))

class SotwButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        skill_voted_for = self.custom_id
        
        # Check if user has already voted
        current_vote = next((skill for skill, voters in self.view.votes.items() if user in voters), None)

        if current_vote == skill_voted_for:
            self.view.votes[skill_voted_for].remove(user)
            await interaction.response.send_message(f"Your vote for **{self.label}** was removed.", ephemeral=True)
        else:
            if current_vote:
                self.view.votes[current_vote].remove(user)
            self.view.votes[skill_voted_for].append(user)
            await interaction.response.send_message(f"Your vote for **{self.label}** has been counted.", ephemeral=True)
        
        await interaction.message.edit(embed=await self.view.create_embed())

class FinishButton(discord.ui.Button):
    def __init__(self, label: str, custom_id: str):
        super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id=custom_id)
        
    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_events:
             return await interaction.response.send_message("You don't have permission to finish the poll.", ephemeral=True)

        view = self.view
        if not any(view.votes.values()):
            return await interaction.response.send_message("No votes cast yet.", ephemeral=True)
        
        winner = max(view.votes, key=lambda k: len(view.votes[k]))
        await interaction.response.defer(ephemeral=True)
        
        # Access bot instance from the view
        sotw_cog = view.bot.get_cog("SOTW")
        await sotw_cog.start_sotw_logic(interaction, winner, 7)
        
        for item in view.children:
            item.disabled = True
        await interaction.message.edit(view=view)
        view.bot.active_polls.pop(interaction.guild.id, None)

# --- SOTW Cog ---
class SOTW(commands.Cog):
    """Cog for SOTW commands."""
    
    def __init__(self, bot):
        self.bot = bot

    sotw = SlashCommandGroup("sotw", "Commands for Skill of the Week")

    async def start_sotw_logic(self, ctx_or_interaction, skill: str, duration_days: int):
        """Shared logic for starting an SOTW, usable by commands and views."""
        data, error = await create_competition(skill, duration_days)
        if error:
            await ctx_or_interaction.followup.send(error, ephemeral=True)
            return

        sotw_channel = self.bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            author = ctx_or_interaction.user
            embed = await create_competition_embed(data, author)
            sotw_message = await sotw_channel.send(embed=embed)
            await send_global_announcement(self.bot, "sotw_start", {"skill": skill.capitalize()}, sotw_message.jump_url)
            await ctx_or_interaction.followup.send("SOTW started in the designated channel!", ephemeral=True)
        else:
            await ctx_or_interaction.followup.send("Error: SOTW Channel not configured.", ephemeral=True)

    @sotw.command(name="start", description="Manually start a new SOTW competition.")
    @commands.has_permissions(manage_events=True)
    async def start(self, ctx: discord.ApplicationContext, 
                    skill: discord.Option(str, choices=WOM_SKILLS), 
                    duration_days: discord.Option(int, default=7)):
        await ctx.defer(ephemeral=True)
        await self.start_sotw_logic(ctx, skill, duration_days)

    @sotw.command(name="poll", description="Start a poll to choose the next SOTW.")
    @commands.has_permissions(manage_events=True)
    async def poll(self, ctx: discord.ApplicationContext):
        if ctx.guild.id in self.bot.active_polls:
            return await ctx.respond("An SOTW poll is already active.", ephemeral=True)
        
        poll_skills = random.sample([s for s in WOM_SKILLS if s != 'overall'], 6)
        view = SotwPollView(ctx.author, self.bot)
        view.add_buttons(poll_skills)
        
        sotw_channel = self.bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            await sotw_channel.send(embed=await view.create_embed(), view=view)
            await ctx.respond("SOTW Poll created!", ephemeral=True)
            self.bot.active_polls[ctx.guild.id] = view
        else:
            await ctx.respond("Error: SOTW Channel not configured.", ephemeral=True)

    @sotw.command(name="view", description="View the leaderboard for the current SOTW.")
    async def view(self, ctx: discord.ApplicationContext, competition_id: discord.Option(int, "Optional ID of a specific competition", required=False)):
        await ctx.defer()
        
        if not competition_id:
            async with self.bot.db_pool.acquire() as conn:
                comp = await conn.fetchrow("SELECT id FROM active_competitions ORDER BY ends_at DESC LIMIT 1")
                if not comp:
                    return await ctx.respond("No active SOTW competition found.")
                competition_id = comp['id']
        
        data, error = await get_competition_details(competition_id)
        if error:
            return await ctx.respond(f"Could not fetch details for competition ID {competition_id}. Error: {error}")

        embed = discord.Embed(title=f"Leaderboard: {data['title']}", url=f"https://wiseoldman.net/competitions/{data['id']}", color=discord.Color.purple())
        leaderboard_text = ""
        for i, p in enumerate(data['participations'][:10]):
            rank = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"\`{i+1}.\`")
            leaderboard_text += f"{rank} **{p['player']['displayName']}**: {p['progress']['gained']:,} XP\n"
        
        embed.description = leaderboard_text or "No participants have gained XP yet."
        embed.set_footer(text="Competition ends")
        embed.timestamp = datetime.fromisoformat(data['endsAt'].replace('Z', '+00:00'))
        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(SOTW(bot))