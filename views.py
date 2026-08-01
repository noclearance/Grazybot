# bot/views.py
# Contains definitions for persistent UI Views.
# These are defined here to be easily imported by main.py for re-registration on startup,
# avoiding circular dependencies if they were defined inside their respective cogs.

import discord
from helpers.bingo_utils import update_bingo_board_post
from helpers.utils import award_points

class GiveawayView(discord.ui.View):
    """A persistent view for giveaway entry buttons."""
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.primary, custom_id="giveaway_entry_button")
    async def enter_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        # The bot object is available through the interaction
        async with interaction.client.db_pool.acquire() as conn:
            try:
                # Using ON CONFLICT DO NOTHING is a clean way to handle existing entries
                result = await conn.execute("INSERT INTO giveaway_entries (message_id, user_id) VALUES ($1, $2) ON CONFLICT (message_id, user_id) DO NOTHING", self.message_id, interaction.user.id)
                if 'INSERT 0 1' in result:
                    await interaction.response.send_message("You have successfully entered the giveaway!", ephemeral=True)
                else:
                    await interaction.response.send_message("You have already entered this giveaway.", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message("An error occurred while entering.", ephemeral=True)

class PvmEventView(discord.ui.View):
    """A persistent view for PVM event sign-ups."""
    def __init__(self, event_id: int):
        super().__init__(timeout=None)
        self.event_id = event_id

    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.success, custom_id="pvm_signup_button")
    async def signup_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        async with interaction.client.db_pool.acquire() as conn:
            result = await conn.execute("INSERT INTO pvm_event_signups (event_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", self.event_id, interaction.user.id)
            if 'INSERT 0 1' in result:
                await interaction.response.send_message("You have signed up for this PVM event!", ephemeral=True)
            else:
                await interaction.response.send_message("You are already signed up.", ephemeral=True)

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.danger, custom_id="pvm_withdraw_button")
    async def withdraw_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        async with interaction.client.db_pool.acquire() as conn:
            result = await conn.execute("DELETE FROM pvm_event_signups WHERE event_id = $1 AND user_id = $2", self.event_id, interaction.user.id)
            if 'DELETE 1' in result:
                await interaction.response.send_message("You have withdrawn from this event.", ephemeral=True)
            else:
                await interaction.response.send_message("You were not signed up for this event.", ephemeral=True)


class SubmissionView(discord.ui.View):
    """A persistent view for approving/denying bingo submissions."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_submission")
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
        bot = interaction.client
        async with bot.db_pool.acquire() as conn:
            data = await conn.fetchrow("SELECT user_id, task_name, event_id FROM bingo_submissions WHERE id = $1 AND status = 'pending'", submission_id)
            if not data:
                return await interaction.response.send_message("Submission already handled.", ephemeral=True)
            
            await conn.execute("UPDATE bingo_submissions SET status = 'approved' WHERE id = $1", submission_id)
            await conn.execute("INSERT INTO bingo_completed_tiles (event_id, task_name) VALUES ($1, $2) ON CONFLICT DO NOTHING", data['event_id'], data['task_name'])
        
        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} approved.", ephemeral=True)
        
        member = interaction.guild.get_member(data['user_id'])
        if member:
            await award_points(bot, member, 25, f"bingo task: '{data['task_name']}'")
        await update_bingo_board_post(bot)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_submission")
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
        await interaction.client.db_pool.execute("UPDATE bingo_submissions SET status = 'denied' WHERE id = $1", submission_id)
        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} denied.", ephemeral=True)