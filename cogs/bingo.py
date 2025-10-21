# cogs/bingo.py
# Contains commands related to clan bingo events.

# cogs/bingo.py
# Contains commands related to clan bingo events.

import discord
import json
import random
import logging
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone

from core.bot import GrazyBot
from core import config
from utils import bingo as bingo_utils, clan
from utils.views import SubmissionView

logger = logging.getLogger(__name__)
TASKS_FILE = "tasks.json" # Assumes this file exists at the project root

class Bingo(commands.Cog):
    """Cog for all bingo-related commands."""
    
    def __init__(self, bot: GrazyBot):
        self.bot = bot

    bingo_group = app_commands.Group(name="bingo", description="Commands for clan bingo events.")

    @bingo_group.command(name="start", description="Start a new bingo event.")
    @commands.has_permissions(manage_events=True)
    async def start_bingo(self, interaction: discord.Interaction,
                          duration_days: int):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        try:
            with open(TASKS_FILE, 'r') as f:
                all_tasks = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.error(f"'{TASKS_FILE}' not found or is invalid.")
            return await interaction.followup.send(f"Error: Could not load the bingo tasks file.", ephemeral=True)
        
        tasks_by_difficulty = {"common": [], "uncommon": [], "rare": []}
        for task in all_tasks:
            tasks_by_difficulty.setdefault(task.get('difficulty', 'common'), []).append(task)
        
        board_composition = {"common": 15, "uncommon": 7, "rare": 3}
        board_tasks = []
        for difficulty, count in board_composition.items():
            if len(tasks_by_difficulty.get(difficulty, [])) < count:
                return await interaction.followup.send(f"Error: Not enough '{difficulty}' tasks in `{TASKS_FILE}`.", ephemeral=True)
            board_tasks.extend(random.sample(tasks_by_difficulty[difficulty], count))
        
        random.shuffle(board_tasks)
        board_tasks = board_tasks[:25]
        
        image_path, error = await bingo_utils.generate_bingo_image(board_tasks)
        if error:
            return await interaction.followup.send(f"Failed to generate bingo image: {error}", ephemeral=True)

        bingo_channel = self.bot.get_channel(config.BINGO_CHANNEL_ID)
        if not bingo_channel:
            return await interaction.followup.send("Error: Bingo Channel ID not configured correctly.", ephemeral=True)

        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        ai_embed_data = await clan.ai.generate_announcement_json("bingo_start")
        embed = discord.Embed.from_dict(ai_embed_data)

        with open(image_path, 'rb') as f:
            file = discord.File(f, filename="bingo_board.png")
            embed.set_image(url="attachment://bingo_board.png")
            embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
            embed.set_footer(text=f"Bingo started by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
            message = await bingo_channel.send(embed=embed, file=file)

        async with self.bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE bingo_events SET is_active = FALSE WHERE is_active = TRUE")
            await conn.execute(
                "INSERT INTO bingo_events (ends_at, board_json, message_id) VALUES ($1, $2, $3)",
                ends_at, json.dumps(board_tasks), message.id
            )
        
        await clan.send_global_announcement(self.bot, "bingo_start", {}, message.jump_url)
        await interaction.followup.send(f"Bingo event created successfully in {bingo_channel.mention}!", ephemeral=True)

    @bingo_group.command(name="complete", description="Submit a task for bingo completion.")
    async def complete_task(self, interaction: discord.Interaction,
                            task: str,
                            proof: str):
        await interaction.response.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            event = await conn.fetchrow("SELECT id, board_json FROM bingo_events WHERE is_active = TRUE LIMIT 1")
            if not event:
                return await interaction.followup.send("There is no active bingo event.", ephemeral=True)
            
            task_names = [t['name'] for t in json.loads(event['board_json'])]
            if task not in task_names:
                return await interaction.followup.send("That task is not on the current bingo board.", ephemeral=True)
            
            submission_id = await conn.fetchval(
                "INSERT INTO bingo_submissions (event_id, user_id, task_name, proof_url) VALUES ($1, $2, $3, $4) RETURNING id",
                event['id'], interaction.user.id, task, proof
            )

        admin_embed = discord.Embed(title="New Bingo Submission", description=f"**Task:** {task}", color=discord.Color.yellow())
        admin_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        admin_embed.add_field(name="Proof", value=f"[Click to view]({proof})", inline=False)
        admin_embed.set_footer(text=f"Submission ID: {submission_id}")
        
        # This will post the review message in the channel the command was used.
        # Consider having a dedicated admin channel for this.
        await interaction.channel.send(content="Admins, a new submission requires review:", embed=admin_embed, view=SubmissionView())
        await interaction.followup.send("Your submission has been sent for review!", ephemeral=True)

    @bingo_group.command(name="board", description="View the current bingo board.")
    async def view_board(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with self.bot.db_pool.acquire() as conn:
            event = await conn.fetchrow("SELECT message_id, channel_id FROM bingo_events WHERE is_active = TRUE LIMIT 1")
        
        if not event or not event['message_id']:
            return await interaction.followup.send("There is no active bingo board to display.", ephemeral=True)
            
        channel = self.bot.get_channel(event['channel_id'] or config.BINGO_CHANNEL_ID)
        if channel:
            try:
                message = await channel.fetch_message(event['message_id'])
                await interaction.followup.send(f"Here is the current bingo board: {message.jump_url}")
            except discord.NotFound:
                await interaction.followup.send("Could not find the original bingo board message.", ephemeral=True)
        else:
            await interaction.followup.send("Bingo channel not found.", ephemeral=True)

async def setup(bot: GrazyBot):
    await bot.add_cog(Bingo(bot))