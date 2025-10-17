# utils/views.py
# Contains persistent and reusable UI components like Buttons and Views.

import discord
import logging
from . import ai, clan  # Assuming clan has start_sotw_logic or similar

logger = logging.getLogger(__name__)

# --- SOTW Poll View ---

class SotwPollView(discord.ui.View):
    def __init__(self, author: discord.Member, bot_instance, skills_to_poll: list[str], callback):
        super().__init__(timeout=86400)  # Poll lasts 24 hours
        self.author = author
        self.bot = bot_instance
        self.votes = {skill: [] for skill in skills_to_poll}
        self.add_buttons(skills_to_poll)
        self.callback_function = callback

    async def create_embed(self) -> discord.Embed:
        ai_embed_data = await ai.generate_announcement_json("sotw_poll")
        embed = discord.Embed.from_dict(ai_embed_data)

        vote_summary = []
        for skill, voters in self.votes.items():
            vote_count = len(voters)
            if vote_count > 0:
                vote_summary.append(f"**{skill.capitalize()}**: {vote_count} vote(s)")

        if vote_summary:
            embed.description += "\n\n**Current Votes:**\n" + "\n".join(vote_summary)

        embed.set_footer(text=f"Poll by {self.author.display_name}", icon_url=self.author.display_avatar.url)
        return embed

    def add_buttons(self, skills: list[str]):
        for skill in skills:
            self.add_item(SotwButton(label=skill.capitalize(), custom_id=f"sotw_vote_{skill}"))
        self.add_item(FinishPollButton(custom_id="finish_sotw_poll"))

class SotwButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        skill_voted_for = self.custom_id.replace("sotw_vote_", "")

        # Atomically update votes
        for skill, voters in self.view.votes.items():
            if user in voters and skill != skill_voted_for:
                voters.remove(user)

        if user in self.view.votes[skill_voted_for]:
            self.view.votes[skill_voted_for].remove(user)
            await interaction.response.send_message(f"Your vote for **{self.label}** has been removed.", ephemeral=True)
        else:
            self.view.votes[skill_voted_for].append(user)
            await interaction.response.send_message(f"Your vote for **{self.label}** has been counted.", ephemeral=True)

        await interaction.message.edit(embed=await self.view.create_embed())

class FinishPollButton(discord.ui.Button):
    def __init__(self, custom_id: str):
        super().__init__(label="Finish Poll", style=discord.ButtonStyle.danger, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_events:
            return await interaction.response.send_message("You don't have permission to end this poll.", ephemeral=True)

        view = self.view
        if not any(view.votes.values()):
            return await interaction.response.send_message("Cannot finish poll, no votes have been cast.", ephemeral=True)

        winner = max(view.votes, key=lambda k: len(view.votes[k]))

        # Disable the view
        for item in view.children:
            item.disabled = True

        # Update the message to show the poll has ended
        final_embed = await view.create_embed()
        final_embed.description += f"\n\n**POLL ENDED! The winning skill is {winner.capitalize()}!**"
        final_embed.color = discord.Color.dark_red()
        await interaction.message.edit(embed=final_embed, view=view)

        # Pop from active polls
        view.bot.active_polls.pop(interaction.guild.id, None)

        # Use the callback to trigger the SOTW start in the cog
        if view.callback_function:
            await view.callback_function(interaction, winner)

        await interaction.response.send_message(f"The poll has been closed. The winning skill is **{winner.capitalize()}**. The SOTW has been started.", ephemeral=True)


# --- PVM Event View ---
class PvmEventView(discord.ui.View):
    def __init__(self, event_id: int):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.add_item(discord.ui.Button(label="Sign Up", style=discord.ButtonStyle.green, custom_id=f"pvm_signup_{event_id}"))
        self.add_item(discord.ui.Button(label="Withdraw", style=discord.ButtonStyle.red, custom_id=f"pvm_withdraw_{event_id}"))

# --- Giveaway View ---
class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int, prize: str):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.prize = prize
        self.add_item(discord.ui.Button(label="Enter Giveaway", emoji="🎉", style=discord.ButtonStyle.primary, custom_id=f"giveaway_enter_{message_id}"))

# --- Bingo Submission View ---
class SubmissionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Approve", style=discord.ButtonStyle.green, custom_id="bingo_approve"))
        self.add_item(discord.ui.Button(label="Reject", style=discord.ButtonStyle.red, custom_id="bingo_reject"))