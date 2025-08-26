# cogs/bingo.py
import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
import json
import random
from datetime import datetime, timezone, timedelta

# Import the helper functions from our utils file
from .utils import (
    get_db_connection,
    award_points,
    generate_announcement_json,
    generate_bingo_image,
    update_bingo_board_post,
    send_global_announcement,
)

# --- Bingo Submission View ---
# This class now lives inside the cog file for better organization.
class SubmissionView(discord.ui.View):
    def __init__(self, bot_instance):
        super().__init__(timeout=None)
        self.bot = bot_instance

    async def handle_submission(self, interaction: discord.Interaction, new_status: str):
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id, task_name FROM bingo_submissions WHERE id = %s", (submission_id,))
        submission_data = cursor.fetchone()
        
        cursor.execute("UPDATE bingo_submissions SET status = %s WHERE id = %s", (new_status, submission_id))
        conn.commit()
        
        if new_status == 'approved' and submission_data:
            user_id, task_name = submission_data
            cursor.execute("INSERT INTO bingo_completed_tiles (task_name) VALUES (%s) ON CONFLICT (task_name) DO NOTHING", (task_name,))
            conn.commit()
            
            member = interaction.guild.get_member(user_id)
            if member:
                await award_points(member, 25, f"completing the bingo task: '{task_name}'")
            
            await update_bingo_board_post(self.bot)

        cursor.close()
        conn.close()

        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} has been {new_status}.", ephemeral=True)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_submission")
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.handle_submission(interaction, "approved")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_submission")
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.handle_submission(interaction, "denied")

# --- The Cog Class ---
class Bingo(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        # We add the persistent view when the cog is initialized, passing the bot instance to it
        self.bot.add_view(SubmissionView(bot))

    bingo = SlashCommandGroup("bingo", "Commands for clan bingo events.")

    @bingo.command(name="start", description="Start a new bingo event.")
    @discord.default_permissions(manage_events=True)
    async def start_bingo(self, ctx: discord.ApplicationContext, duration_days: Option(int, "How many days the bingo event will last.")):
        await ctx.defer(ephemeral=True)
        await ctx.respond("The Taskmaster is forging a new challenge...", ephemeral=True)
        
        TASKS_FILE = "tasks.json" # Define in scope or move to utils
        BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID'))
        
        try:
            with open(TASKS_FILE, 'r') as f: all_tasks = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return await ctx.edit(content="Error: `tasks.json` not found or is invalid.")
        
        tasks_by_difficulty = {"common": [], "uncommon": [], "rare": []}
        for task in all_tasks: tasks_by_difficulty.setdefault(task['difficulty'], []).append(task)
        board_composition = {"common": 15, "uncommon": 7, "rare": 3}
        board_tasks = []
        for difficulty, count in board_composition.items():
            if len(tasks_by_difficulty.get(difficulty, [])) < count:
                return await ctx.edit(content=f"Error: Not enough '{difficulty}' tasks in `tasks.json`.")
            board_tasks.extend(random.sample(tasks_by_difficulty[difficulty], count))
        
        if len(board_tasks) < 25:
            return await ctx.edit(content="Error: Not enough tasks in total to create a 25-slot board.")
        
        random.shuffle(board_tasks); board_tasks = board_tasks[:25]
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("DELETE FROM bingo_events"); cursor.execute("DELETE FROM bingo_submissions"); cursor.execute("DELETE FROM bingo_completed_tiles")
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        board_json = json.dumps(board_tasks)
        
        image_path, error = generate_bingo_image(board_tasks)
        if error: return await ctx.edit(content=f"Failed to generate bingo image: {error}")
        
        bingo_channel = self.bot.get_channel(BINGO_CHANNEL_ID)
        if not bingo_channel: return await ctx.edit(content="Error: Bingo Channel ID not configured correctly.")
        
        ai_embed_data = await generate_announcement_json("bingo_start")
        embed = discord.Embed.from_dict(ai_embed_data)
        file = discord.File(image_path, filename="bingo_board.png")
        embed.set_image(url="attachment://bingo_board.png")
        embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"Bingo started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        message = await bingo_channel.send(embed=embed, file=file)
        
        cursor.execute("INSERT INTO bingo_events (id, ends_at, board_json, message_id) VALUES (1, %s, %s, %s)", (ends_at, board_json, message.id))
        conn.commit(); cursor.close(); conn.close()
        
        await send_global_announcement(self.bot, "bingo_start", {}, message.jump_url)
        await ctx.edit(content="Bingo event created successfully!")

    @bingo.command(name="complete", description="Submit a task for bingo completion.")
    async def complete_task(self, ctx: discord.ApplicationContext, task: Option(str, "The name of the task you completed."), proof: Option(str, "A URL link to a screenshot or video proof.")):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT board_json FROM bingo_events LIMIT 1")
        board_data = cursor.fetchone()
        if not board_data:
            cursor.close(); conn.close(); return await ctx.respond("There is no active bingo event.", ephemeral=True)
        
        board_tasks = json.loads(board_data[0])
        task_names = [t['name'] for t in board_tasks]
        if task not in task_names:
            cursor.close(); conn.close(); return await ctx.respond("That task is not on the current bingo board.", ephemeral=True)
        
        cursor.execute("INSERT INTO bingo_submissions (user_id, task_name, proof_url) VALUES (%s, %s, %s)", (ctx.author.id, task, proof))
        conn.commit(); cursor.close(); conn.close()
        await ctx.respond("Your submission has been sent to the admins for review!", ephemeral=True)

    @bingo.command(name="submissions", description="ADMIN: View pending bingo task submissions.")
    @discord.default_permissions(manage_events=True)
    async def view_submissions(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM bingo_submissions WHERE status = 'pending'")
        pending = cursor.fetchall()
        cursor.close(); conn.close()
        if not pending:
            return await ctx.respond("There are no pending bingo submissions.", ephemeral=True)
        
        await ctx.respond("Here are the pending submissions:", ephemeral=True)
        for sub in pending:
            user = await self.bot.fetch_user(sub['user_id'])
            embed = discord.Embed(title="📝 Bingo Submission", description=f"**Task:** {sub['task_name']}", color=discord.Color.yellow())
            embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
            embed.add_field(name="Proof", value=f"[Click to view]({sub['proof_url']})", inline=False)
            embed.set_footer(text=f"Submission ID: {sub['id']}")
            await ctx.channel.send(embed=embed, view=SubmissionView(self.bot), ephemeral=True)

    @bingo.command(name="board", description="View the current bingo board.")
    async def view_board(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID'))
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT message_id FROM bingo_events LIMIT 1")
        event_data = cursor.fetchone()
        cursor.close(); conn.close()
        if not event_data or not event_data[0]:
            return await ctx.respond("There is no active bingo board to display.")
        
        bingo_channel = self.bot.get_channel(BINGO_CHANNEL_ID)
        if bingo_channel:
            try:
                message = await bingo_channel.fetch_message(event_data[0])
                await ctx.respond(f"Here is the current bingo board: {message.jump_url}")
            except discord.NotFound:
                await ctx.respond("Could not find the original bingo board message.")
        else:
            await ctx.respond("Bingo channel not configured.")

# This function is required for the cog to be loaded by the bot
def setup(bot):
    bot.add_cog(Bingo(bot))