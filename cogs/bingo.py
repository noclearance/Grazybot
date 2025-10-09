# bot/cogs/bingo.py
# Contains commands related to clan bingo events.

import discord
import json
import random
from discord.commands import SlashCommandGroup
from discord.ext import commands
from datetime import datetime, timedelta, timezone

from bot.config import TASKS_FILE, BINGO_CHANNEL_ID
from bot.helpers.ai import generate_announcement_json
from bot.helpers.bingo_utils import generate_bingo_image, update_bingo_board_post
from bot.helpers.utils import send_global_announcement, award_points
from bot.views import SubmissionView

class Bingo(commands.Cog):
    """Cog for all bingo-related commands."""
    
    def __init__(self, bot):
        self.bot = bot

    bingo = SlashCommandGroup("bingo", "Commands for clan bingo events.")

    @bingo.command(name="start", description="Start a new bingo event.")
    @commands.has_permissions(manage_events=True)
    async def start_bingo(self, ctx: discord.ApplicationContext, 
                          duration_days: discord.Option(int, "How many days the bingo event will last.")):
        await ctx.defer(ephemeral=True)
        await ctx.followup.send("The Taskmaster is forging a new challenge... This may take a moment.", ephemeral=True)
        
        try:
            with open(TASKS_FILE, 'r') as f:
                all_tasks = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return await ctx.edit(content=f"Error: \`{TASKS_FILE}\` not found or is invalid: {e}")
        
        tasks_by_difficulty = {"common": [], "uncommon": [], "rare": []}
        for task in all_tasks:
            tasks_by_difficulty.setdefault(task.get('difficulty', 'common'), []).append(task)
        
        board_composition = {"common": 15, "uncommon": 7, "rare": 3}
        board_tasks = []
        for difficulty, count in board_composition.items():
            if len(tasks_by_difficulty.get(difficulty, [])) < count:
                return await ctx.edit(content=f"Error: Not enough '{difficulty}' tasks in \`{TASKS_FILE}\`.")
            board_tasks.extend(random.sample(tasks_by_difficulty[difficulty], count))
        
        random.shuffle(board_tasks)
        board_tasks = board_tasks[:25]
        
        async with self.bot.db_pool.acquire() as conn:
            # Deactivate any previous bingo events
            await conn.execute("UPDATE bingo_events SET is_active = FALSE WHERE is_active = TRUE")
            
            ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
            board_json = json.dumps(board_tasks)
            
            image_path, error = await generate_bingo_image(board_tasks)
            if error: 
                return await ctx.edit(content=f"Failed to generate bingo image: {error}")
            
            bingo_channel = self.bot.get_channel(BINGO_CHANNEL_ID)
            if not bingo_channel: 
                return await ctx.edit(content="Error: Bingo Channel ID not configured correctly.")
            
            ai_embed_data = await generate_announcement_json("bingo_start")
            embed = discord.Embed.from_dict(ai_embed_data)
            
            with open(image_path, 'rb') as f:
                file = discord.File(f, filename="bingo_board.png")
                embed.set_image(url="attachment://bingo_board.png")
                embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
                embed.set_footer(text=f"Bingo started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
                message = await bingo_channel.send(embed=embed, file=file)
            
            current_event_id = await conn.fetchval("INSERT INTO bingo_events (ends_at, board_json, message_id) VALUES ($1, $2, $3) RETURNING id",
                                                   ends_at, board_json, message.id)
        
        await send_global_announcement(self.bot, "bingo_start", {}, message.jump_url)
        await ctx.edit(content=f"Bingo event (ID: {current_event_id}) created successfully!")

    @bingo.command(name="complete", description="Submit a task for bingo completion.")
    async def complete_task(self, ctx: discord.ApplicationContext, 
                            task: discord.Option(str, "The name of the task you completed."), 
                            proof: discord.Option(str, "A URL link to a screenshot or video proof.")):
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            event_data = await conn.fetchrow("SELECT id, board_json FROM bingo_events WHERE is_active = TRUE LIMIT 1")
            if not event_data:
                return await ctx.respond("There is no active bingo event.", ephemeral=True)
            
            board_tasks = json.loads(event_data['board_json'])
            task_names = [t['name'] for t in board_tasks]
            
            if task not in task_names:
                return await ctx.respond("That task is not on the current bingo board.", ephemeral=True)
            
            submission_id = await conn.fetchval("INSERT INTO bingo_submissions (event_id, user_id, task_name, proof_url) VALUES ($1, $2, $3, $4) RETURNING id",
                               event_data['id'], ctx.author.id, task, proof)

        # Notify admins in the channel where the command was used
        admin_embed = discord.Embed(title="New Bingo Submission", description=f"**Task:** {task}", color=discord.Color.yellow())
        user = ctx.author
        admin_embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        admin_embed.add_field(name="Proof", value=f"[Click to view]({proof})", inline=False)
        admin_embed.set_footer(text=f"Submission ID: {submission_id}")
        
        # We find a channel admins can see to post the submission review message
        await ctx.channel.send(content="Admins, a new submission requires review:", embed=admin_embed, view=SubmissionView())
        await ctx.respond("Your submission has been sent for review!", ephemeral=True)

    @bingo.command(name="board", description="View the current bingo board.")
    async def view_board(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        async with self.bot.db_pool.acquire() as conn:
            event_data = await conn.fetchrow("SELECT message_id FROM bingo_events WHERE is_active = TRUE LIMIT 1")
        
        if not event_data or not event_data['message_id']:
            return await ctx.respond("There is no active bingo board to display.")
            
        bingo_channel = self.bot.get_channel(BINGO_CHANNEL_ID)
        if bingo_channel:
            try:
                message = await bingo_channel.fetch_message(event_data['message_id'])
                await ctx.respond(f"Here is the current bingo board: {message.jump_url}")
            except discord.NotFound:
                await ctx.respond("Could not find the original bingo board message.")
        else:
            await ctx.respond("Bingo channel not configured.")

def setup(bot):
    bot.add_cog(Bingo(bot))