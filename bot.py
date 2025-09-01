# bot.py

import discord
from discord.ext import tasks
import os
from dotenv import load_dotenv
import aiohttp
from aiohttp import web
import asyncio
from datetime import datetime, timedelta, timezone, time
import random
import asyncpg
import json
import textwrap
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai
from io import BytesIO
from functools import partial
import logging
import sys

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
log = logging.getLogger(__name__)

# --- Configuration & Setup ---
load_dotenv()
TOKEN = os.getenv('TOKEN')
WOM_CLAN_ID = os.getenv('WOM_CLAN_ID')
WOM_VERIFICATION_CODE = os.getenv('WOM_VERIFICATION_CODE')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DEBUG_GUILD_ID_STR = os.getenv('DEBUG_GUILD_ID')
DATABASE_URL = os.getenv('DATABASE_URL')
TASKS_FILE = "tasks.json"
BINGO_FONT_FILE = "arial.ttf" 

# --- Environment Variable Validation ---
if not all([TOKEN, WOM_CLAN_ID, WOM_VERIFICATION_CODE, GEMINI_API_KEY, DEBUG_GUILD_ID_STR, DATABASE_URL]):
    log.critical("CRITICAL: One or more environment variables are missing. Please check your Render dashboard.")
    # We don't exit here, so the diagnostics command can still run and report the issue.
    
DEBUG_GUILD_ID = int(DEBUG_GUILD_ID_STR) if DEBUG_GUILD_ID_STR else None

# Channel IDs - Using .get() to avoid crashing if one is missing
SOTW_CHANNEL_ID = int(os.getenv('SOTW_CHANNEL_ID', 0))
BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID', 0))
RAFFLE_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID', 0))
GIVEAWAY_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID', 0)) # Uses raffle channel
RECAP_CHANNEL_ID = int(os.getenv('RECAP_CHANNEL_ID', 0))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID', 0))
PVM_EVENT_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID', 0))

# Configure the Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-1.0-pro')
else:
    ai_model = None

# Define WOM skill metrics & Bot Intents
WOM_SKILLS = ["overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herlore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"]
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = discord.Bot(intents=intents)
bot.db_pool = None # To be initialized in on_ready
bot.active_polls = {}

# --- Command Groups ---
admin = discord.SlashCommandGroup("admin", "Admin-only commands")
bot.add_application_command(admin)

# --- Database & Threading Helpers ---
async def run_in_executor(func, *args, **kwargs):
    """Runs a synchronous function (like image generation) in a separate thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

async def setup_database():
    """Sets up the necessary database tables if they don't exist using asyncpg."""
    try:
        async with bot.db_pool.acquire() as conn:
            await conn.execute("""CREATE TABLE IF NOT EXISTS active_competitions (id INTEGER PRIMARY KEY, title TEXT, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE, winners_awarded BOOLEAN DEFAULT FALSE)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS raffles (id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, prize TEXT, ends_at TIMESTAMPTZ, winner_id BIGINT, final_ping_sent BOOLEAN DEFAULT FALSE)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS raffle_entries (entry_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, raffle_id INTEGER REFERENCES raffles(id) ON DELETE CASCADE, user_id BIGINT NOT NULL, source TEXT DEFAULT 'self')""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS bingo_events (id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, board_json TEXT, message_id BIGINT, midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS bingo_submissions (id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, user_id BIGINT, task_name TEXT, proof_url TEXT, status TEXT DEFAULT 'pending', bingo_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS bingo_completed_tiles (bingo_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE, task_name TEXT, PRIMARY KEY (bingo_id, task_name))""")
            await conn.execute("CREATE TABLE IF NOT EXISTS user_links (discord_id BIGINT PRIMARY KEY, osrs_name TEXT NOT NULL)")
            await conn.execute("CREATE TABLE IF NOT EXISTS clan_points (discord_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0)")
            await conn.execute("""CREATE TABLE IF NOT EXISTS giveaways (id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, prize TEXT NOT NULL, ends_at TIMESTAMPTZ NOT NULL, max_number INTEGER NOT NULL, winner_id BIGINT, winning_number INTEGER)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS giveaway_entries (entry_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, giveaway_id INTEGER REFERENCES giveaways(id) ON DELETE CASCADE, user_id BIGINT NOT NULL, chosen_number INTEGER NOT NULL, UNIQUE (giveaway_id, chosen_number), UNIQUE (giveaway_id, user_id))""")
            await conn.execute("CREATE TABLE IF NOT EXISTS pvm_guides (boss_name TEXT PRIMARY KEY, guide_text TEXT NOT NULL)")
        log.info("Database setup checked/completed.")
    except Exception as e:
        log.critical(f"DATABASE SETUP FAILED: {e}", exc_info=True)


# --- SOTW Poll View ---
class SotwPollView(discord.ui.View):
    def __init__(self, author):
        super().__init__(timeout=86400); self.author = author; self.votes = {};
    
    async def create_embed(self):
        prompt = "Create a Discord embed JSON for a new 'Skill of the Week' poll. Encourage everyone to vote to determine the clan's next challenge."
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title="📊 Skill of the Week Poll", description="The time has come to choose our next battleground! Cast your vote to determine the clan's next great challenge.", color=15105600)
        
        vote_description = "\n\n**Current Votes:**\n"
        for skill, voters in self.votes.items(): vote_description += f"**{skill.capitalize()}**: {len(voters)} vote(s)\n"
        
        current_desc = embed.description if embed.description else ""
        embed.description = current_desc + vote_description
        embed.set_footer(text=f"Poll started by {self.author.display_name}", icon_url=self.author.display_avatar.url); 
        return embed

    def add_buttons(self, skills):
        for skill in skills: self.votes[skill] = []; self.add_item(SotwButton(label=skill.capitalize(), custom_id=skill))
        self.add_item(FinishButton(label="Finish Poll & Start SOTW", custom_id="finish_poll"))

class SotwButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction):
        for skill_key, voters in self.view.votes.items():
            if interaction.user in voters:
                voters.remove(interaction.user)
        self.view.votes[self.custom_id].append(interaction.user)
        new_embed = await self.view.create_embed()
        await interaction.response.edit_message(embed=new_embed, view=self.view)
        await interaction.followup.send(f"Your vote for **{self.label}** has been counted.", ephemeral=True)

class FinishButton(discord.ui.Button):
    def __init__(self, label, custom_id): super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id=custom_id)
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            if interaction.user.id != self.view.author.id: return await interaction.followup.send("Only the poll starter can finish it.", ephemeral=True)
            view = self.view
            if not any(v for v in view.votes.values()): return await interaction.followup.send("No votes cast yet.", ephemeral=True)
            winner = max(view.votes, key=lambda k: len(view.votes[k]))
            data, error = await create_competition(WOM_CLAN_ID, winner, 7)
            if error: await interaction.followup.send(f"Poll finished, but failed to start for **{winner.capitalize()}**: {error}", ephemeral=True); return
            
            sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
            if sotw_channel:
                comp = data['competition']
                prompt = f"Write a Discord embed JSON announcing a Skill of the Week competition for **{winner.capitalize()}**, lasting 7 days, as the winner of the clan poll."
                embed = await generate_embed_from_prompt(prompt)
                if not embed:
                     embed = discord.Embed(title=f"⚔️ SOTW Started: {winner.capitalize()}! ⚔️", description=f"The clan has spoken! The grind for **{winner.capitalize()}** begins now!", color=5763719)
                
                start_dt = datetime.fromisoformat(comp['startsAt'].replace('Z', '+00:00')); end_dt = datetime.fromisoformat(comp['endsAt'].replace('Z', '+00:00'))
                embed.url = f"https://wiseoldman.net/competitions/{comp['id']}"
                embed.add_field(name="Start Time", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True)
                embed.add_field(name="End Time", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
                embed.set_footer(text=f"Competition started by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
                sotw_message = await sotw_channel.send(embed=embed)
                await send_global_announcement("sotw_start", {"skill": winner.capitalize(), "duration": "7 days"}, sotw_message.jump_url)
                await interaction.followup.send("Competition created in the SOTW channel!", ephemeral=True)
            
            for item in view.children: item.disabled = True
            await interaction.message.edit(view=view)
            bot.active_polls.pop(interaction.guild.id, None)
        except Exception as e:
            log.error(f"Error in FinishButton callback: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while finishing the poll.", ephemeral=True)

# --- Bingo Submission View ---
class SubmissionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_submission")
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
            async with bot.db_pool.acquire() as conn:
                async with conn.transaction():
                    rec = await conn.fetchrow("SELECT user_id, task_name, bingo_id FROM bingo_submissions WHERE id = $1 AND status = 'pending'", submission_id)
                    if not rec:
                        await interaction.message.delete()
                        return await interaction.followup.send("This submission was already handled or does not exist.", ephemeral=True)
                    await conn.execute("UPDATE bingo_submissions SET status = 'approved' WHERE id = $1", submission_id)
                    await conn.execute("INSERT INTO bingo_completed_tiles (bingo_id, task_name) VALUES ($1, $2) ON CONFLICT (bingo_id, task_name) DO NOTHING", rec['bingo_id'], rec['task_name'])
            
            await interaction.message.delete()
            await interaction.followup.send(f"Submission #{submission_id} approved.", ephemeral=True)
            member = interaction.guild.get_member(rec['user_id'])
            if member:
                await award_points(member, 25, f"completing the bingo task: '{rec['task_name']}'")
            await update_bingo_board_post(rec['bingo_id'])
        except Exception as e:
            log.error(f"Error in approve_button: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while approving the submission.", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_submission")
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
            res = await bot.db_pool.execute("UPDATE bingo_submissions SET status = 'denied' WHERE id = $1 AND status = 'pending'", submission_id)
            if res == "UPDATE 0":
                await interaction.message.delete()
                return await interaction.followup.send("This submission was already handled.", ephemeral=True)
            await interaction.message.delete()
            await interaction.followup.send(f"Submission #{submission_id} denied.", ephemeral=True)
        except Exception as e:
            log.error(f"Error in deny_button: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while denying the submission.", ephemeral=True)

# --- Helper Functions ---
async def award_points(member: discord.Member, amount: int, reason: str):
    if not member or member.bot: return
    try:
        async with bot.db_pool.acquire() as conn:
            await conn.execute("INSERT INTO clan_points (discord_id, points) VALUES ($1, 0) ON CONFLICT (discord_id) DO NOTHING", member.id)
            new_balance = await conn.fetchval("UPDATE clan_points SET points = points + $1 WHERE discord_id = $2 RETURNING points", amount, member.id)
        
        dm_embed = discord.Embed(
            title="🏆 Points Awarded!",
            description=f"You have been awarded **{amount} Clan Points** for *{reason}*! Clan Points are a measure of your dedication and can be used for rewards. Well done.",
            color=5763719
        )
        dm_embed.add_field(name="New Balance", value=f"You now have **{new_balance}** Clan Points.")
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        log.warning(f"Could not send DM to {member.display_name} (they may have DMs disabled).")
    except Exception as e:
        log.error(f"Failed to award points/send DM to {member.id}: {e}", exc_info=True)

async def create_competition(clan_id: str, skill: str, duration_days: int):
    url = "https://api.wiseoldman.net/v2/competitions"
    start_date = datetime.now(timezone.utc) + timedelta(minutes=1); end_date = start_date + timedelta(days=duration_days)
    payload = {"title": f"{skill.capitalize()} SOTW ({duration_days} days)","metric": skill,"startsAt": start_date.isoformat(),"endsAt": end_date.isoformat(),"groupId": int(clan_id),"groupVerificationCode": WOM_VERIFICATION_CODE}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status == 201:
                comp_data = await response.json()
                c = comp_data['competition']
                await bot.db_pool.execute("INSERT INTO active_competitions (id, title, starts_at, ends_at) VALUES ($1, $2, $3, $4)", c['id'], c['title'], c['startsAt'], c['endsAt'])
                return comp_data, None
            else: 
                try:
                    error_msg = await response.json()
                    return None, f"API Error: {error_msg.get('message', 'Failed to create competition.')}"
                except Exception:
                    return None, f"API Error: Status {response.status}"

async def generate_embed_from_prompt(prompt: str) -> discord.Embed | None:
    if not ai_model:
        log.warning("Gemini AI model not configured. Skipping prompt generation.")
        return None
    full_prompt = f"""
    You are TaskmasterGPT, the official announcer for an Old School RuneScape clan.
    Your tone is epic, engaging, and a little bit cheeky. You are the clan's ultimate hype man.
    Your task is to generate a single, complete JSON object for a Discord embed based on the user's request.
    The JSON should be a single object, starting with {{ and ending with }}. It must be valid JSON.
    The embed must have a 'title', 'description', and 'color' (as a decimal integer). You can also add 'fields'.
    Use Discord markdown like **bold** and *italics*. Do not use emojis unless specifically asked.

    User Request: "{prompt}"

    JSON Output:
    """
    try:
        response = await run_in_executor(ai_model.generate_content, full_prompt)
        clean_json_string = response.text.strip().lstrip("```json").rstrip("```")
        ai_data = json.loads(clean_json_string)
        return discord.Embed.from_dict(ai_data)
    except Exception as e:
        log.error(f"An error occurred during Gemini embed generation: {e}", exc_info=True)
        return None

async def draw_raffle_winner(channel: discord.TextChannel, raffle_id: int):
    try:
        async with bot.db_pool.acquire() as conn:
            raffle_data = await conn.fetchrow("SELECT * FROM raffles WHERE id = $1", raffle_id)
            if not raffle_data:
                log.error(f"Could not find raffle {raffle_id} to draw.")
                return
            
            entries = await conn.fetch("SELECT user_id FROM raffle_entries WHERE raffle_id = $1", raffle_id)
            if not entries:
                await channel.send(f"The raffle for **{raffle_data['prize']}** has ended, but unfortunately, no one entered.")
                return

            winner_record = random.choice(entries)
            winner_id = winner_record['user_id']
            await conn.execute("UPDATE raffles SET winner_id = $1 WHERE id = $2", winner_id, raffle_id)
        
        await award_points(await bot.fetch_user(winner_id), 50, f"winning the raffle for {raffle_data['prize']}")
        
        winner_user = await bot.fetch_user(winner_id)
        prompt = f"Create a Discord embed JSON announcing the winner of a raffle. The winner is {winner_user.mention} and they won **{raffle_data['prize']}**. Congratulate them with epic flair."
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title="🎉 Raffle Winner Announcement! 🎉", description=f"Congratulations to {winner_user.mention}, you have won the raffle!", color=discord.Color.fuchsia())
        
        embed.add_field(name="Prize", value=f"**{raffle_data['prize']}**", inline=False)
        embed.add_field(name="Bonus Reward", value="You have also been awarded **50 Clan Points**!", inline=False)
        embed.set_footer(text="Thanks to everyone for participating!")
        embed.set_thumbnail(url=winner_user.display_avatar.url)
        await channel.send(content=f"Congratulations {winner_user.mention}!", embed=embed)
    except Exception as e:
        log.error(f"Error in draw_raffle_winner for ID {raffle_id}: {e}", exc_info=True)

async def draw_giveaway_winner(channel: discord.TextChannel, giveaway_id: int):
    try:
        async with bot.db_pool.acquire() as conn:
            giveaway_data = await conn.fetchrow("SELECT * FROM giveaways WHERE id = $1", giveaway_id)
            if not giveaway_data: return
            
            prize = giveaway_data['prize']
            max_number = giveaway_data['max_number']
            winning_number = random.randint(1, max_number)
            
            winner_data = await conn.fetchrow("SELECT user_id FROM giveaway_entries WHERE giveaway_id = $1 AND chosen_number = $2", giveaway_id, winning_number)
            
            embed = None
            if winner_data:
                winner_id = winner_data['user_id']
                await conn.execute("UPDATE giveaways SET winner_id = $1, winning_number = $2 WHERE id = $3", winner_id, winning_number, giveaway_id)
                winner_user = await bot.fetch_user(winner_id)
                prompt = f"Create a Discord embed JSON for a giveaway result. The prize was a **{prize}**. The winning number was **{winning_number}**, and {winner_user.mention} correctly guessed it! Congratulate them."
                embed = await generate_embed_from_prompt(prompt)
                if not embed:
                    embed = discord.Embed(title=f"🎉 Giveaway Results for {prize}! 🎉", description=f"Congratulations to {winner_user.mention}, who picked the lucky number!", color=discord.Color.dark_gold())
                embed.set_thumbnail(url=winner_user.display_avatar.url)
                await channel.send(content=f"Congratulations {winner_user.mention}!", embed=embed)
            else:
                await conn.execute("UPDATE giveaways SET winning_number = $1 WHERE id = $2", winning_number, giveaway_id)
                prompt = f"Create a Discord embed JSON for a giveaway result where nobody won. The prize was a **{prize}**. The winning number was **{winning_number}**, but nobody picked it. State that the prize remains in the clan vault."
                embed = await generate_embed_from_prompt(prompt)
                if not embed:
                    embed = discord.Embed(title=f"🎉 Giveaway Results for {prize}! 🎉", description="Unfortunately, nobody picked the winning number this time. The prize remains in the clan vault!", color=discord.Color.dark_gold())
                await channel.send(embed=embed)

            embed.add_field(name="The Winning Number Was...", value=f"**{winning_number}**", inline=False)
    except Exception as e:
        log.error(f"Error in draw_giveaway_winner for ID {giveaway_id}: {e}", exc_info=True)

def generate_bingo_image(tasks: list, completed_tasks: list = []):
    try:
        width, height = 1000, 1000; background_color = (40, 26, 13)
        img = Image.new('RGB', (width, height), background_color)
        draw = ImageDraw.Draw(img)
        try:
            title_font = ImageFont.truetype(BINGO_FONT_FILE, size=70)
            task_font = ImageFont.truetype(BINGO_FONT_FILE, size=22)
        except IOError:
            log.warning(f"Font file '{BINGO_FONT_FILE}' not found. Falling back to default font.")
            title_font = ImageFont.load_default(); task_font = ImageFont.load_default()
        
        draw.text((width/2, 60), "CLAN BINGO", font=title_font, fill=(255, 215, 0), anchor="ms")
        grid_size = 5; cell_size = 170; line_width = 4
        grid_start_x, grid_start_y = 75, 125
        grid_end_x, grid_end_y = grid_start_x + (grid_size * cell_size), grid_start_y + (grid_size * cell_size)
        line_color = (255, 215, 0)
        for i in range(grid_size + 1):
            draw.line([(grid_start_x + i * cell_size, grid_start_y), (grid_start_x + i * cell_size, grid_end_y)], fill=line_color, width=line_width)
            draw.line([(grid_start_x, grid_start_y + i * cell_size), (grid_end_x, grid_start_y + i * cell_size)], fill=line_color, width=line_width)
        
        for i, task in enumerate(tasks):
            if i >= 25: break
            row, col = i // grid_size, i % grid_size
            cell_x, cell_y = grid_start_x + col * cell_size, grid_start_y + row * cell_size
            if task['name'] in completed_tasks:
                overlay = Image.new('RGBA', (cell_size - line_width, cell_size - line_width), (0, 255, 0, 90))
                img.paste(overlay, (cell_x + line_width//2, cell_y + line_width//2), overlay)
            
            lines = textwrap.wrap(task['name'], width=15)
            total_text_height = sum(task_font.getbbox(line)[3] for line in lines)
            current_y = (cell_y + (cell_size / 2)) - (total_text_height / 2)
            for line in lines:
                line_width_bbox, line_height_bbox = draw.textbbox((0,0), line, font=task_font)[2:4]
                draw.text(((cell_x + (cell_size / 2)) - (line_width_bbox / 2), current_y), line, font=task_font, fill=(255, 255, 255), align="center")
                current_y += line_height_bbox + 2
        
        output_path = "bingo_board.png"; img.save(output_path)
        return output_path, None
    except Exception as e:
        log.error(f"An unexpected error occurred during image generation: {e}", exc_info=True)
        return None, str(e)

async def update_bingo_board_post(bingo_id: int):
    try:
        async with bot.db_pool.acquire() as conn:
            event_data = await conn.fetchrow("SELECT board_json, message_id FROM bingo_events WHERE id = $1", bingo_id)
            if not event_data: return
            
            completed_recs = await conn.fetch("SELECT task_name FROM bingo_completed_tiles WHERE bingo_id = $1", bingo_id)
            completed_tiles = [rec['task_name'] for rec in completed_recs]
        
        board_tasks = json.loads(event_data['board_json'])
        image_path, error = await run_in_executor(generate_bingo_image, board_tasks, completed_tiles)
        if error:
            log.error(f"Failed to update bingo board image: {error}")
            return
            
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if bingo_channel:
            message = await bingo_channel.fetch_message(event_data["message_id"])
            with open(image_path, 'rb') as f:
                new_file = discord.File(f, filename="bingo_board.png")
                embed = message.embeds[0]
                embed.set_image(url="attachment://bingo_board.png")
                await message.edit(embed=embed, files=[new_file])
    except discord.NotFound:
        log.warning(f"Could not find bingo message {event_data['message_id']} to update.")
    except Exception as e:
        log.error(f"Error updating bingo board {bingo_id}: {e}", exc_info=True)

async def send_global_announcement(event_type: str, details: dict, message_url: str):
    announcement_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not announcement_channel:
        log.error("Global announcements channel not found.")
        return
    if event_type == "sotw_start":
        title = f"⚔️ New SOTW: {details.get('skill', 'Unknown')}!"
        desc = f"A new Skill of the Week has begun! It will last for **{details.get('duration', 'a while')}**."
    elif event_type == "raffle_start":
        title = "🎟️ New Raffle Started!"
        desc = f"A raffle for a **{details.get('prize', 'mystery prize')}** has started!"
    elif event_type == "bingo_start":
        title = "🧩 New Clan Bingo!"
        desc = f"A new clan bingo event has started and will last for **{details.get('duration', 'a while')}**."
    else:
        title = "🎉 New Event!"
        desc = "A new clan event has started!"
    embed = discord.Embed(title=title, description=desc, color=discord.Color.blue())
    embed.url = message_url
    embed.add_field(name="Details", value=f"[Click here to view the event!]({message_url})")
    embed.set_footer(text="A new clan event has started!")
    await announcement_channel.send(content="@everyone", embed=embed)

# --- TASKS ---
daily_summary_time = time(hour=12, minute=0, tzinfo=timezone.utc)
@tasks.loop(time=daily_summary_time)
async def daily_event_summary():
    await bot.wait_until_ready()
    try:
        announcement_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
        if not announcement_channel:
            log.error("Cannot post daily summary, announcements channel not found.")
            return

        async with bot.db_pool.acquire() as conn:
            competitions = await conn.fetch("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
            raffles = await conn.fetch("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
            bingo = await conn.fetchrow("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        
        if not competitions and not raffles and not bingo:
            log.info("No active events to summarize today.")
            return
            
        event_data_string = ""
        if competitions:
            event_data_string += "Skill of the Week Competitions:\n" + "".join([f"- {c['title']} (Ends <t:{int(c['ends_at'].timestamp())}:R>)\n" for c in competitions])
        if raffles:
            event_data_string += "\nRaffles:\n" + "".join([f"- Prize: {r['prize']} (Ends <t:{int(r['ends_at'].timestamp())}:R>)\n" for r in raffles])
        if bingo:
            event_data_string += f"\nBingo Event:\nA clan-wide bingo is active! (Ends <t:{int(bingo['ends_at'].timestamp())}:R>)\n"
            
        prompt = f"Create a Discord embed JSON for a daily summary of active clan events. Make it engaging. Here is the data:\n{event_data_string}"
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title="📅 Daily Clan Events Summary", description=event_data_string, color=10181046)
        embed.set_footer(text="Good luck, have fun!")
        embed.timestamp=datetime.now(timezone.utc)
        await announcement_channel.send(embed=embed)
    except Exception as e:
        log.error(f"Error in daily_event_summary task: {e}", exc_info=True)

@tasks.loop(minutes=5)
async def event_manager():
    await bot.wait_until_ready()
    async def run_task(handler):
        try:
            await handler()
        except Exception as e:
            log.error(f"Error in event_manager task '{handler.__name__}': {e}", exc_info=True)

    await asyncio.gather(
        run_task(handle_weekly_recap),
        run_task(handle_sotw_management),
        run_task(handle_raffle_management),
        run_task(handle_bingo_management),
        run_task(handle_giveaway_management)
    )

async def handle_weekly_recap():
    now = datetime.now(timezone.utc)
    recap_channel = bot.get_channel(RECAP_CHANNEL_ID)
    if not (recap_channel and now.weekday() == 6 and now.hour == 19 and now.minute < 5):
        return
    url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/gained?period=week&metric=overall"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                embed = None
                if not data:
                     embed = discord.Embed(title="📈 Weekly Recap", description="It seems the clan was quiet this week, with no XP gains to report. Let's pick up the pace for next week!", color=discord.Color.blue())
                else:
                    data_summary = ""
                    for i, player in enumerate(data[:10]): data_summary += f"{i+1}. {player['player']['displayName']}: {player.get('gained', 0):,} XP\n"
                    prompt = f"Create a Discord embed JSON for our clan's weekly recap. Announce the top 3 with extra flair. Here is the data:\n{data_summary}"
                    embed = await generate_embed_from_prompt(prompt)
                    if not embed:
                        embed = discord.Embed(title="📈 Weekly Recap", description="Here are the top performers:\n" + data_summary, color=discord.Color.blue())
                embed.set_footer(text=f"Recap for the week ending {now.strftime('%B %d, %Y')}")
                await recap_channel.send(embed=embed)

async def handle_sotw_management():
    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if not sotw_channel: return
    
    async with bot.db_pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        competitions = await conn.fetch("SELECT * FROM active_competitions WHERE ends_at < NOW() + interval '7 days'")
        
        for comp in competitions:
            ends_at, starts_at = comp['ends_at'], comp['starts_at']
            if now > ends_at and not comp['winners_awarded']:
                await conn.execute("UPDATE active_competitions SET winners_awarded = TRUE WHERE id = $1", comp['id'])
                asyncio.create_task(award_sotw_winners_for_comp(comp))

            elif not comp['final_ping_sent'] and (ends_at - now) <= timedelta(hours=1) and now < ends_at:
                await conn.execute("UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = $1", comp['id'])
                await sotw_channel.send(content="@everyone", embed=discord.Embed(title="⏳ Final Hour!", description=f"The **{comp['title']}** competition ends in less than an hour!", color=discord.Color.red(), url=f"https://wiseoldman.net/competitions/{comp['id']}"))

            elif not comp['midway_ping_sent'] and now >= starts_at + ((ends_at - starts_at) / 2) and now < ends_at:
                await conn.execute("UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = $1", comp['id'])
                await sotw_channel.send(embed=discord.Embed(title="½ Midway Point Reached!", description=f"The **{comp['title']}** competition is halfway through!", color=discord.Color.yellow(), url=f"https://wiseoldman.net/competitions/{comp['id']}"))

async def award_sotw_winners_for_comp(comp):
    details_url = f"https://api.wiseoldman.net/v2/competitions/{comp['id']}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(details_url) as response:
                if response.status == 200:
                    comp_data = await response.json()
                    for i, participant in enumerate(comp_data.get('participations', [])[:3]):
                        await award_sotw_winner_points(participant, i, comp['title'])
    except Exception as e:
        log.error(f"Error awarding SOTW winners for comp {comp['id']}: {e}", exc_info=True)

async def award_sotw_winner_points(participant, rank, title):
    osrs_name = participant['player']['displayName']
    discord_id = await bot.db_pool.fetchval("SELECT discord_id FROM user_links WHERE osrs_name = $1", osrs_name)
    if discord_id:
        member = bot.get_guild(DEBUG_GUILD_ID).get_member(discord_id)
        if member:
            point_values = [100, 50, 25]
            await award_points(member, point_values[rank], f"placing #{rank+1} in the {title} SOTW")

async def handle_raffle_management():
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not raffle_channel: return
    now = datetime.now(timezone.utc)
    async with bot.db_pool.acquire() as conn:
        raffles_to_draw = await conn.fetch("SELECT id FROM raffles WHERE winner_id IS NULL AND ends_at <= $1", now)
        raffles_to_remind = await conn.fetch("SELECT * FROM raffles WHERE winner_id IS NULL AND final_ping_sent = FALSE AND ends_at - $1 <= interval '1 day'", now)
        
        for r in raffles_to_remind:
            await conn.execute("UPDATE raffles SET final_ping_sent = TRUE WHERE id = $1", r['id'])
            embed = discord.Embed(title="🎟️ Raffle Ending Soon!", description=f"There are only **24 hours left** to enter the raffle for a **{r['prize']}**!", color=discord.Color.orange())
            await raffle_channel.send(content="@everyone", embed=embed)

    for r in raffles_to_draw:
        asyncio.create_task(draw_raffle_winner(raffle_channel, r['id']))

async def handle_bingo_management():
    pass

async def handle_giveaway_management():
    giveaway_channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if not giveaway_channel: return
    ended_giveaways = await bot.db_pool.fetch("SELECT * FROM giveaways WHERE winner_id IS NULL AND ends_at <= NOW()")
    for giveaway in ended_giveaways:
        asyncio.create_task(draw_giveaway_winner(giveaway_channel, giveaway['id']))

# --- Web Server for Hosting ---
async def handle_http(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        log.info(f"Web server started on port {port}")
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    try:
        if DATABASE_URL:
            bot.db_pool = await asyncpg.create_pool(DATABASE_URL)
            if bot.db_pool:
                log.info("Successfully connected to the database.")
                await setup_database()
        else:
            log.critical("DATABASE_URL is not set. Database features will be disabled.")

        event_manager.start()
        daily_event_summary.start()
        bot.add_view(SubmissionView())
        await bot.sync_commands()
        log.info(f"{bot.user} is online and ready!")
    except Exception as e:
        log.critical(f"An error occurred during bot startup: {e}", exc_info=True)

@bot.event
async def on_message(message):
    if message.author.bot: return
    trigger_phrases = ["what gear for", "setup for", "inventory for"]
    if any(message.content.lower().startswith(phrase) for phrase in trigger_phrases):
        question = message.content
        async with message.channel.typing():
            prompt = f"You are an expert Old School RuneScape (OSRS) player. Respond to the following query with a gear/inventory setup formatted for Discord: \"{question}\""
            try:
                response = await run_in_executor(ai_model.generate_content, prompt)
                embed = discord.Embed(title="Gear & Inventory Guide", description=response.text, color=discord.Color.blue())
                embed.set_footer(text=f"Guide for: {question}")
                await message.reply(embed=embed)
            except Exception as e:
                log.error(f"Error generating PVM guide: {e}", exc_info=True)
                await message.reply("Sorry, I couldn't fetch a guide for that right now.")

# --- DIAGNOSTIC COMMANDS ---
@bot.slash_command(name="ping", description="A simple command to check if the bot is responsive.")
async def ping(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    await ctx.edit(content="Pong! The bot is online and responding to commands.")

@admin.command(name="diagnostics", description="Runs a full system check to ensure the bot is configured correctly.")
async def diagnostics(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    
    results = ["**--- Bot Diagnostics Report ---**"]
    
    # 1. Check Environment Variables
    results.append("\n**Environment Variables:**")
    env_vars = ['TOKEN', 'WOM_CLAN_ID', 'WOM_VERIFICATION_CODE', 'GEMINI_API_KEY', 'DEBUG_GUILD_ID', 'DATABASE_URL']
    all_vars_set = True
    for var in env_vars:
        if os.getenv(var):
            results.append(f"✅ `{var}` is set.")
        else:
            results.append(f"❌ `{var}` is **MISSING**.")
            all_vars_set = False
            
    # 2. Check Database Connection
    results.append("\n**Database Connection:**")
    if bot.db_pool and all_vars_set:
        try:
            async with bot.db_pool.acquire() as conn:
                val = await conn.fetchval('SELECT 1')
                if val == 1:
                    results.append("✅ Successfully connected to the database and executed a query.")
                else:
                    results.append("❌ Connected to DB, but query failed.")
        except Exception as e:
            results.append(f"❌ **FAILED** to connect to the database. Error: `{e}`")
    else:
        results.append("❌ Database pool is not available (likely because DATABASE_URL is missing).")

    # 3. Check Gemini API
    results.append("\n**Gemini AI API:**")
    if ai_model:
        try:
            test_embed = await generate_embed_from_prompt("test")
            if test_embed:
                results.append("✅ Successfully received a response from the Gemini API.")
            else:
                results.append("❌ **FAILED** to get a valid response from Gemini API.")
        except Exception as e:
            results.append(f"❌ **FAILED** to communicate with Gemini API. Error: `{e}`")
    else:
        results.append("❌ Gemini API key is missing, so the model was not initialized.")
        
    # 4. Check File Access
    results.append("\n**File Access:**")
    try:
        with open(TASKS_FILE, 'r') as f:
            json.load(f)
        results.append(f"✅ Successfully read and parsed `{TASKS_FILE}`.")
    except FileNotFoundError:
        results.append(f"❌ **FAILED** to find the file `{TASKS_FILE}`.")
    except json.JSONDecodeError:
        results.append(f"❌ **FAILED** to parse `{TASKS_FILE}`. It may be corrupt.")
    except Exception as e:
        results.append(f"❌ An unknown error occurred while reading `{TASKS_FILE}`. Error: `{e}`")

    await ctx.edit(content="\n".join(results))


# --- BOT COMMANDS ---
sotw = bot.create_group("sotw", "Commands for Skill of the Week")

@sotw.command(name="start", description="Manually start a new SOTW competition.")
@discord.default_permissions(manage_events=True)
async def start(ctx, skill: discord.Option(str, choices=WOM_SKILLS), duration_days: discord.Option(int, default=7)):
    await ctx.defer(ephemeral=True)
    try:
        data, error = await create_competition(WOM_CLAN_ID, skill, duration_days)
        if error: await ctx.edit(content=error); return
        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            comp = data['competition']
            prompt = f"Write a Discord embed JSON announcing a Skill of the Week: **{skill.capitalize()}**, lasting **{duration_days}** days."
            embed = await generate_embed_from_prompt(prompt)
            if not embed:
                embed = discord.Embed(title=f"⚔️ SOTW Started: {skill.capitalize()}! ⚔️", description=f"The great grind for **{skill.capitalize()}** begins now!", color=5763719)
            start_dt = datetime.fromisoformat(comp['startsAt'].replace('Z', '+00:00')); end_dt = datetime.fromisoformat(comp['endsAt'].replace('Z', '+00:00'))
            embed.url = f"https://wiseoldman.net/competitions/{comp['id']}"
            embed.add_field(name="Start Time", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True)
            embed.add_field(name="End Time", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
            embed.set_footer(text=f"Competition started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
            sotw_message = await sotw_channel.send(embed=embed)
            await send_global_announcement("sotw_start", {"skill": skill.capitalize(), "duration": f"{duration_days} days"}, sotw_message.jump_url)
            await ctx.edit(content=f"SOTW for {skill.capitalize()} created! [Jump]({sotw_message.jump_url})")
        else:
            await ctx.edit(content="Error: SOTW Channel ID not configured correctly.")
    except Exception as e:
        log.error(f"Error in /sotw start: {e}", exc_info=True)
        try: await ctx.edit(content="An unexpected error occurred. Please check the logs.")
        except discord.NotFound: pass

@sotw.command(name="poll", description="Start a poll to choose the next SOTW.")
@discord.default_permissions(manage_events=True)
async def poll(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        if ctx.guild.id in bot.active_polls: return await ctx.edit(content="There is already an active SOTW poll.")
        poll_skills = random.sample(WOM_SKILLS, 6); view = SotwPollView(ctx.author); view.add_buttons(poll_skills)
        embed = await view.create_embed();
        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            poll_message = await sotw_channel.send(embed=embed, view=view)
            await ctx.edit(content="SOTW Poll created!"); view.message_id = poll_message.id
            bot.active_polls[ctx.guild.id] = view
        else:
            await ctx.edit(content="Error: SOTW Channel ID not configured correctly.")
    except Exception as e:
        log.error(f"Error in /sotw poll: {e}", exc_info=True)
        try: await ctx.edit(content="An unexpected error occurred. Please check the logs.")
        except discord.NotFound: pass

@sotw.command(name="view", description="View the leaderboard for the current SOTW.")
async def view(ctx: discord.ApplicationContext):
    await ctx.defer()
    try:
        list_url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/competitions"
        async with aiohttp.ClientSession() as session:
            async with session.get(list_url) as response:
                if response.status != 200: return await ctx.edit(content="Could not fetch competition list.")
                competitions = await response.json()
                if not competitions: return await ctx.edit(content="This clan has no competitions on Wise Old Man.")
                latest_comp_id = competitions[0]['id']
        details_url = f"https://api.wiseoldman.net/v2/competitions/{latest_comp_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(details_url) as response:
                if response.status != 200: return await ctx.edit(content=f"Could not fetch details for competition ID {latest_comp_id}.")
                data = await response.json()
        embed = discord.Embed(title=f"Leaderboard: {data['title']}", description=f"Current standings for the **{data['metric'].capitalize()}** competition.", color=discord.Color.purple(), url=f"https://wiseoldman.net/competitions/{data['id']}")
        leaderboard_text = ""
        for i, player in enumerate(data['participations'][:10]):
            rank_emoji = {1: "🏆", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
            leaderboard_text += f"{rank_emoji} **{player['player']['displayName']}**: {player['progress']['gained']:,} XP\n"
        if not leaderboard_text: leaderboard_text = "No participants have gained XP yet."
        embed.add_field(name="Top 10", value=leaderboard_text, inline=False)
        end_dt = datetime.fromisoformat(data['endsAt'].replace('Z', '+00:00'))
        embed.set_footer(text=f"Competition ends"); embed.timestamp = end_dt
        await ctx.edit(embed=embed)
    except Exception as e:
        log.error(f"Error in /sotw view: {e}", exc_info=True)
        try: await ctx.edit(content="An unexpected error occurred. Please check the logs.")
        except discord.NotFound: pass

raffle = bot.create_group("raffle", "Commands for managing raffles.")
@raffle.command(name="start", description="Start a new raffle.")
@discord.default_permissions(manage_events=True)
async def start_raffle(ctx: discord.ApplicationContext, prize: discord.Option(str, "What is the prize?"), duration_days: discord.Option(float, "How many days will it last?")):
    await ctx.defer(ephemeral=True)
    try:
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        duration_str = f"{int(duration_days)} day(s)" if duration_days >= 1 else f"{int(duration_days*24)} hours"
        prompt = f"Create a Discord embed JSON for a raffle starting now. Prize: **{prize}**. Duration: **{duration_str}**."
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title="🎟️ A New Raffle has Begun!", description=f"A new raffle is underway for a **{prize}**!", color=15844367)
        
        raffle_id = await bot.db_pool.fetchval("INSERT INTO raffles (prize, ends_at) VALUES ($1, $2) RETURNING id", prize, ends_at)
        
        embed.add_field(name="How to Enter", value="Use `/raffle enter` to get a ticket! (Max 10 per person)", inline=False)
        embed.add_field(name="Raffle Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"Raffle ID: {raffle_id}")
        raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
        if raffle_channel:
            raffle_message = await raffle_channel.send(embed=embed)
            await send_global_announcement("raffle_start", {"prize": prize, "duration": duration_str}, raffle_message.jump_url)
            await ctx.edit(content=f"Raffle for **{prize}** created! [Jump to message]({raffle_message.jump_url})")
        else:
            await ctx.edit(content="Error: Raffle Channel ID not configured correctly.")
    except Exception as e:
        log.error(f"Error in /raffle start: {e}", exc_info=True)
        try: await ctx.edit(content=f"An unexpected error occurred. Please check the logs.")
        except discord.NotFound: pass

@raffle.command(name="enter", description="Get one ticket for the current raffle (max 10).")
async def enter_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        async with bot.db_pool.acquire() as conn:
            raffle_data = await conn.fetchrow("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
            if not raffle_data:
                return await ctx.edit(content="There is no active raffle to enter right now.")
            
            count = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2 AND source = 'self'", ctx.author.id, raffle_data['id'])
            if count >= 10:
                return await ctx.edit(content="You have already claimed your maximum of 10 tickets for this raffle!")

            await conn.execute("INSERT INTO raffle_entries (user_id, source, raffle_id) VALUES ($1, 'self', $2)", ctx.author.id, raffle_data['id'])
            total_tickets = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", ctx.author.id, raffle_data['id'])
        
        await ctx.edit(content=f"You have successfully claimed a ticket for the **{raffle_data['prize']}** raffle! You now have a total of {total_tickets} ticket(s).")
    except Exception as e:
        log.error(f"Error in /raffle enter: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while entering the raffle.")
        except discord.NotFound: pass

@raffle.command(name="give_tickets", description="ADMIN: Give raffle tickets to a member.")
@discord.default_permissions(manage_events=True)
async def give_tickets(ctx: discord.ApplicationContext, member: discord.Member, amount: int):
    await ctx.defer(ephemeral=True)
    try:
        async with bot.db_pool.acquire() as conn:
            raffle_data = await conn.fetchrow("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
            if not raffle_data: return await ctx.edit(content="There is no active raffle.")
            
            entries = [(raffle_data['id'], member.id, 'admin') for _ in range(amount)]
            await conn.copy_records_to_table('raffle_entries', records=entries, columns=['raffle_id', 'user_id', 'source'])
            
            total = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", member.id, raffle_data['id'])
        
        await ctx.edit(content=f"Successfully gave {amount} ticket(s) to {member.display_name} for the '{raffle_data['prize']}' raffle. They now have {total} ticket(s).")
    except Exception as e:
        log.error(f"Error in /raffle give_tickets: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while giving tickets.")
        except discord.NotFound: pass

@raffle.command(name="edit_tickets", description="ADMIN: Set a member's total ticket count for the active raffle.")
@discord.default_permissions(manage_events=True)
async def edit_tickets(ctx: discord.ApplicationContext, member: discord.Member, new_total: int):
    await ctx.defer(ephemeral=True)
    try:
        async with bot.db_pool.acquire() as conn:
            raffle_data = await conn.fetchrow("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
            if not raffle_data: return await ctx.edit(content="There is no active raffle.")

            async with conn.transaction():
                await conn.execute("DELETE FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", member.id, raffle_data['id'])
                if new_total > 0:
                    entries = [(raffle_data['id'], member.id, 'admin_edit') for _ in range(new_total)]
                    await conn.copy_records_to_table('raffle_entries', records=entries, columns=['raffle_id', 'user_id', 'source'])
        
        await ctx.edit(content=f"Successfully set {member.display_name}'s ticket count to {new_total} for the '{raffle_data['prize']}' raffle.")
    except Exception as e:
        log.error(f"Error in /raffle edit_tickets: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while editing tickets.")
        except discord.NotFound: pass

@raffle.command(name="view_tickets", description="View the current ticket count for all participants.")
async def view_tickets(ctx: discord.ApplicationContext):
    await ctx.defer()
    try:
        raffle_data = await bot.db_pool.fetchrow("SELECT id, prize FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        if not raffle_data:
            return await ctx.edit(content="There is no active raffle.")
        
        entries = await bot.db_pool.fetch("SELECT user_id, COUNT(user_id) as count FROM raffle_entries WHERE raffle_id = $1 GROUP BY user_id ORDER BY count DESC", raffle_data['id'])
        
        embed = discord.Embed(title=f"🎟️ Raffle Tickets for '{raffle_data['prize']}'", color=discord.Color.gold())
        if not entries:
            embed.description = "No tickets have been given out yet."
        else:
            description_lines = []
            for entry in entries[:20]:
                member = ctx.guild.get_member(entry['user_id'])
                member_name = member.display_name if member else f"User ID: {entry['user_id']}"
                description_lines.append(f"**{member_name}**: {entry['count']} ticket(s)")
            embed.description = "\n".join(description_lines)
        
        await ctx.edit(embed=embed)
    except Exception as e:
        log.error(f"Error in /raffle view_tickets: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while viewing tickets.")
        except discord.NotFound: pass

@raffle.command(name="draw_now", description="ADMIN: Immediately ends the raffle and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        channel = bot.get_channel(RAFFLE_CHANNEL_ID)
        if not channel: return await ctx.edit(content="Error: Raffle channel not found.")
        
        raffle_data = await bot.db_pool.fetchrow("SELECT * FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        if not raffle_data:
            return await ctx.edit(content="There is no active raffle to draw.")
        
        await draw_raffle_winner(channel, raffle_data['id'])
        await ctx.edit(content=f"Successfully triggered winner drawing.")
    except Exception as e:
        log.error(f"Error in /raffle draw_now: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while drawing the raffle.")
        except discord.NotFound: pass

@raffle.command(name="cancel", description="ADMIN: Cancels the current raffle without drawing a winner.")
@discord.default_permissions(manage_events=True)
async def cancel_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        raffle_data = await bot.db_pool.fetchrow("SELECT id, prize FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        if not raffle_data:
            return await ctx.edit(content="There is no active raffle to cancel.")
        
        await bot.db_pool.execute("DELETE FROM raffles WHERE id = $1", raffle_data['id'])
        
        channel = bot.get_channel(RAFFLE_CHANNEL_ID)
        if channel: await channel.send(f"The raffle for **{raffle_data['prize']}** has been cancelled by an admin.")
        await ctx.edit(content="Raffle successfully cancelled.")
    except Exception as e:
        log.error(f"Error in /raffle cancel: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while canceling the raffle.")
        except discord.NotFound: pass

giveaway = bot.create_group("giveaway", "Commands for pick-a-number giveaways.")
@giveaway.command(name="start", description="ADMIN: Start a new pick-a-number giveaway.")
@discord.default_permissions(manage_events=True)
async def start_giveaway(ctx, prize: str, max_number: int, duration_days: float):
    await ctx.defer(ephemeral=True)
    try:
        active_giveaway = await bot.db_pool.fetchval("SELECT id FROM giveaways WHERE ends_at > NOW()")
        if active_giveaway:
            return await ctx.edit(content="There is already an active giveaway.")
        
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        await bot.db_pool.execute("INSERT INTO giveaways (prize, ends_at, max_number) VALUES ($1, $2, $3)", prize, ends_at, max_number)
        
        duration_str = f"{int(duration_days)} day(s)" if duration_days >= 1 else f"{int(duration_days*24)} hours"
        prompt = f"Create a Discord embed JSON for a 'pick-a-number' giveaway. Prize: **{prize}**. Number range: 1 to **{max_number}**. Duration: **{duration_str}**."
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title="🎉 A New Giveaway Has Started! 🎉", description=f"We're giving away a **{prize}**!", color=discord.Color.dark_magenta())
        embed.add_field(name="How to Enter", value=f"Pick a number between 1 and {max_number} using `/giveaway enter`.", inline=False)
        embed.add_field(name="Giveaway Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text="First come, first served for each number. Good luck!")
        giveaway_channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
        if giveaway_channel:
            await giveaway_channel.send(embed=embed)
            await ctx.edit(content="Giveaway created successfully!")
        else:
            await ctx.edit(content="Error: Giveaway channel not configured correctly.")
    except Exception as e:
        log.error(f"Error in /giveaway start: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while starting the giveaway.")
        except discord.NotFound: pass

@giveaway.command(name="enter", description="Enter the current giveaway by picking a number.")
async def enter_giveaway(ctx, number: discord.Option(int, required=False) = None):
    await ctx.defer(ephemeral=True)
    try:
        async with bot.db_pool.acquire() as conn:
            giveaway_data = await conn.fetchrow("SELECT * FROM giveaways WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
            if not giveaway_data: return await ctx.edit(content="There is no active giveaway.")
            
            gid, max_num = giveaway_data['id'], giveaway_data['max_number']
            if number is None:
                taken_recs = await conn.fetch("SELECT chosen_number FROM giveaway_entries WHERE giveaway_id = $1", gid)
                taken = {r['chosen_number'] for r in taken_recs}
                available = list(set(range(1, max_num + 1)) - taken)
                if not available: return await ctx.edit(content="Sorry, all numbers have been taken!")
                number = random.choice(available)
            
            if not (1 <= number <= max_num):
                return await ctx.edit(content=f"Please pick a number between 1 and {max_num}.")
            
            try:
                await conn.execute("INSERT INTO giveaway_entries (giveaway_id, user_id, chosen_number) VALUES ($1, $2, $3)", gid, ctx.author.id, number)
                await ctx.edit(content=f"Your entry for number **{number}** is locked in. Good luck!")
            except asyncpg.UniqueViolationError as e:
                if 'chosen_number' in e.constraint_name:
                    await ctx.edit(content=f"Sorry, the number **{number}** is taken!")
                elif 'user_id' in e.constraint_name:
                    await ctx.edit(content="You have already entered this giveaway!")
                else:
                    raise
    except Exception as e:
        log.error(f"Error in /giveaway enter: {e}", exc_info=True)
        try: await ctx.edit(content="An unexpected error occurred.")
        except discord.NotFound: pass

@giveaway.command(name="draw_now", description="ADMIN: Immediately ends the giveaway and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now_giveaway(ctx):
    await ctx.defer(ephemeral=True)
    try:
        channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
        if not channel: return await ctx.edit(content="Error: Giveaway channel not found.")
        
        giveaway_data = await bot.db_pool.fetchrow("SELECT * FROM giveaways WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        if not giveaway_data:
            return await ctx.edit(content="There is no active giveaway to draw.")
        
        await draw_giveaway_winner(channel, giveaway_data['id'])
        await ctx.edit(content=f"Successfully triggered winner drawing for the '{giveaway_data['prize']}' giveaway.")
    except Exception as e:
        log.error(f"Error in /giveaway draw_now: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while drawing the giveaway.")
        except discord.NotFound: pass

events = bot.create_group("events", "View all active clan events.")
@events.command(name="view", description="Shows all currently active competitions, raffles, and bingo events.")
async def view_events(ctx):
    await ctx.defer()
    try:
        async with bot.db_pool.acquire() as conn:
            competitions = await conn.fetch("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
            raffles = await conn.fetch("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
            bingo = await conn.fetchrow("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        
        embed = discord.Embed(title="📅 Clan Event Status", description="Here's a look at all the events currently running.", color=discord.Color.blurple())
        if competitions:
            comp_info = "".join([f"**Title:** [{c['title']}](https://wiseoldman.net/competitions/{c['id']})\n**Ends:** <t:{int(c['ends_at'].timestamp())}:R>\n\n" for c in competitions])
            embed.add_field(name="⚔️ Active Competitions", value=comp_info, inline=False)
        else: embed.add_field(name="⚔️ Active Competitions", value="No SOTW competitions are running.", inline=False)
        if raffles:
            raffle_info = "".join([f"**Prize:** {r['prize']}\n**Ends:** <t:{int(r['ends_at'].timestamp())}:R>\n\n" for r in raffles])
            embed.add_field(name="🎟️ Active Raffles", value=raffle_info, inline=False)
        else: embed.add_field(name="🎟️ Active Raffles", value="No raffles are running.", inline=False)
        if bingo:
            bingo_url = f"https://discord.com/channels/{ctx.guild.id}/{BINGO_CHANNEL_ID}/{bingo['message_id']}"
            bingo_info = f"A clan-wide bingo is underway!\n**[Click here to see the board!]({bingo_url})**\nEnds: <t:{int(bingo['ends_at'].timestamp())}:R>"
            embed.add_field(name="🧩 Active Bingo", value=bingo_info, inline=False)
        else: embed.add_field(name="🧩 Active Bingo", value="No bingo event is running.", inline=False)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.edit(embed=embed)
    except Exception as e:
        log.error(f"Error in /events view: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while fetching events.")
        except discord.NotFound: pass

bingo = bot.create_group("bingo", "Commands for clan bingo events.")
@bingo.command(name="start", description="Start a new bingo event.")
@discord.default_permissions(manage_events=True)
async def start_bingo(ctx, duration_days: int):
    await ctx.defer(ephemeral=True)
    try:
        await ctx.edit(content="The Taskmaster is forging a new challenge... This may take a moment.")
        try:
            with open(TASKS_FILE, 'r') as f: all_tasks = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return await ctx.edit(content=f"Error: `{TASKS_FILE}` not found or is invalid.")
        tasks_by_difficulty = {"common": [], "uncommon": [], "rare": []}
        for task in all_tasks: tasks_by_difficulty.setdefault(task['difficulty'], []).append(task)
        board_composition = {"common": 15, "uncommon": 7, "rare": 3}
        board_tasks = []
        for difficulty, count in board_composition.items():
            if len(tasks_by_difficulty.get(difficulty, [])) < count:
                return await ctx.edit(content=f"Error: Not enough '{difficulty}' tasks in `{TASKS_FILE}`.")
            board_tasks.extend(random.sample(tasks_by_difficulty[difficulty], count))
        if len(board_tasks) < 25:
            return await ctx.edit(content="Error: Not enough tasks in total to create a 25-slot board.")
        random.shuffle(board_tasks); board_tasks = board_tasks[:25]
        image_path, error = await run_in_executor(generate_bingo_image, board_tasks)
        if error: return await ctx.edit(content=f"Failed to generate bingo image: {error}")
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if not bingo_channel: return await ctx.edit(content="Error: Bingo Channel ID not configured correctly.")
        duration_str = f"{duration_days} day(s)"
        prompt = f"Create a Discord embed JSON for a new clan bingo event lasting **{duration_str}**. Describe it as a fun board of challenges to earn points."
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title="🧩 A New Clan Bingo Has Started! 🧩", description=f"A fresh board of challenges awaits for the next **{duration_str}**!", color=11027200)
        file = discord.File(image_path, filename="bingo_board.png")
        embed.set_image(url="attachment://bingo_board.png")
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"Bingo started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        message = await bingo_channel.send(embed=embed, file=file)
        
        bingo_id = await bot.db_pool.fetchval("INSERT INTO bingo_events (starts_at, ends_at, board_json, message_id) VALUES ($1, $2, $3, $4) RETURNING id", datetime.now(timezone.utc), ends_at, json.dumps(board_tasks), message.id)
        
        await send_global_announcement("bingo_start", {"duration": duration_str}, message.jump_url)
        await ctx.edit(content=f"Bingo event #{bingo_id} created successfully!")
    except Exception as e:
        log.error(f"Error in /bingo start: {e}", exc_info=True)
        try: await ctx.edit(content=f"An unexpected error occurred: {e}")
        except discord.NotFound: pass

@bingo.command(name="complete", description="Submit a task for bingo completion.")
async def complete_task(ctx, task: str, proof: str):
    await ctx.defer(ephemeral=True)
    try:
        async with bot.db_pool.acquire() as conn:
            event_data = await conn.fetchrow("SELECT id, board_json FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
            if not event_data: return await ctx.edit(content="There is no active bingo event.")
            
            board_tasks = json.loads(event_data['board_json'])
            if task not in [t['name'] for t in board_tasks]:
                return await ctx.edit(content="That task is not on the current bingo board.")
            
            await conn.execute("INSERT INTO bingo_submissions (user_id, task_name, proof_url, bingo_id) VALUES ($1, $2, $3, $4)", ctx.author.id, task, proof, event_data['id'])
        
        await ctx.edit(content="Your submission has been sent to the admins for review!")
    except Exception as e:
        log.error(f"Error in /bingo complete: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while submitting your task.")
        except discord.NotFound: pass

@bingo.command(name="submissions", description="ADMIN: View pending bingo task submissions.")
@discord.default_permissions(manage_events=True)
async def view_submissions(ctx):
    await ctx.defer(ephemeral=True)
    try:
        pending = await bot.db_pool.fetch("SELECT * FROM bingo_submissions WHERE status = 'pending'")
        if not pending:
            return await ctx.edit(content="There are no pending bingo submissions.")
        await ctx.edit(content="Here are the pending submissions:")
        for sub in pending:
            user = await bot.fetch_user(sub['user_id'])
            embed = discord.Embed(title="📝 Bingo Submission", description=f"**Task:** {sub['task_name']}", color=discord.Color.yellow())
            embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
            embed.add_field(name="Proof", value=f"[Click to view]({sub['proof_url']})", inline=False)
            embed.set_footer(text=f"Submission ID: {sub['id']}")
            await ctx.channel.send(embed=embed, view=SubmissionView())
    except Exception as e:
        log.error(f"Error in /bingo submissions: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while fetching submissions.")
        except discord.NotFound: pass

@bingo.command(name="board", description="View the current bingo board.")
async def view_board(ctx):
    await ctx.defer()
    try:
        message_id = await bot.db_pool.fetchval("SELECT message_id FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        if not message_id:
            return await ctx.edit(content="There is no active bingo board to display.")
        
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if bingo_channel:
            try:
                message = await bingo_channel.fetch_message(message_id)
                await ctx.edit(content=f"Here is the current bingo board: {message.jump_url}")
            except discord.NotFound:
                await ctx.edit(content="Could not find the original bingo board message.")
        else:
            await ctx.edit(content="Bingo channel not configured.")
    except Exception as e:
        log.error(f"Error in /bingo board: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while fetching the board.")
        except discord.NotFound: pass

@admin.command(name="announce", description="Send a message as the bot to a specific channel.")
@discord.default_permissions(manage_guild=True)
async def announce(ctx, message: str, channel: discord.TextChannel, ping_everyone: bool = False):
    await ctx.defer(ephemeral=True)
    try:
        content = "@everyone" if ping_everyone else ""
        embed = discord.Embed(title="📢 Clan Announcement", description=message, color=discord.Color.orange())
        embed.set_footer(text=f"Message sent by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await channel.send(content=content, embed=embed)
        await ctx.edit(content="Announcement sent successfully!")
    except discord.Forbidden:
        await ctx.edit(content="Error: I don't have permission to send messages in that channel.")
    except Exception as e:
        log.error(f"Error in /admin announce: {e}", exc_info=True)
        await ctx.edit(content=f"An unexpected error occurred: {e}")

@admin.command(name="manage_points", description="Add or remove Clan Points from a member.")
@discord.default_permissions(manage_guild=True)
async def manage_points(ctx, member: discord.Member, action: str, amount: int, reason: str):
    await ctx.defer(ephemeral=True)
    try:
        if action == "add":
            await award_points(member, amount, reason)
        else: # remove
            await bot.db_pool.execute("INSERT INTO clan_points (discord_id, points) VALUES ($1, 0) ON CONFLICT (discord_id) DO NOTHING", member.id)
            await bot.db_pool.execute("UPDATE clan_points SET points = GREATEST(0, points - $1) WHERE discord_id = $2", amount, member.id)
        
        new_balance = await bot.db_pool.fetchval("SELECT points FROM clan_points WHERE discord_id = $1", member.id) or 0
        await ctx.edit(content=f"Successfully updated {member.display_name}'s points. Their new balance is {new_balance}.")
    except Exception as e:
        log.error(f"Error in /admin manage_points: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while managing points.")
        except discord.NotFound: pass

@admin.command(name="award_sotw_winners", description="Manually award points for a past SOTW competition.")
@discord.default_permissions(manage_guild=True)
async def award_sotw_winners(ctx, competition_id: int):
    await ctx.defer(ephemeral=True)
    try:
        details_url = f"https://api.wiseoldman.net/v2/competitions/{competition_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(details_url) as response:
                if response.status != 200:
                    return await ctx.edit(content=f"Could not fetch details for competition ID {competition_id}.")
                comp_data = await response.json()
        awarded_to = []
        point_values = [100, 50, 25]
        for i, participant in enumerate(comp_data.get('participations', [])[:3]):
            discord_id = await bot.db_pool.fetchval("SELECT discord_id FROM user_links WHERE osrs_name = $1", participant['player']['displayName'])
            if discord_id:
                member = ctx.guild.get_member(discord_id)
                if member:
                    await award_points(member, point_values[i], f"placing #{i+1} in the {comp_data['title']} SOTW")
                    awarded_to.append(f"#{i+1}: {member.display_name} ({point_values[i]} points)")
        if not awarded_to:
            return await ctx.edit(content="No winners could be found or linked for that competition.")
        await ctx.edit(content="Successfully awarded points to:\n" + "\n".join(awarded_to))
    except Exception as e:
        log.error(f"Error in /admin award_sotw_winners: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while awarding points.")
        except discord.NotFound: pass

@admin.command(name="guide", description="Shows a detailed guide on how to use admin commands.")
@discord.default_permissions(manage_guild=True)
async def admin_guide(ctx):
    await ctx.defer(ephemeral=True)
    try:
        embed = discord.Embed(title="👑 Admin Command Guide 👑", description="Here’s how to use the admin commands to run clan events.", color=discord.Color.gold())
        sotw_guide = "`1. /sotw poll`\n**What it does:** Starts a vote for the next Skill of the Week.\n\n`2. /sotw start`\n**What it does:** Manually starts a competition without a poll."
        embed.add_field(name="⚔️ Skill of the Week (SOTW)", value=sotw_guide, inline=False)
        raffle_guide = "`1. /raffle start`\n**What it does:** Starts a new raffle for a prize.\n\n`2. /raffle give_tickets`\n**What it does:** Gives tickets to a player.\n\n`3. /raffle draw_now`\n**What it does:** Ends the current raffle and picks a winner immediately."
        embed.add_field(name="🎟️ Raffles", value=raffle_guide, inline=False)
        bingo_guide = "`1. /bingo start`\n**What it does:** Starts a new bingo game for the whole clan.\n\n`2. /bingo submissions`\n**What it does:** Shows you all the bingo tasks players have submitted for approval."
        embed.add_field(name="🧩 Bingo", value=bingo_guide, inline=False)
        other_guide = "`1. /admin announce`\n**What it does:** Sends a message as the bot.\n\n`2. /admin manage_points`\n**What it does:** Lets you give or take away Clan Points from someone."
        embed.add_field(name="⚙️ Other Tools", value=other_guide, inline=False)
        embed.set_footer(text="Just take it one step at a time. You got this!")
        await ctx.edit(embed=embed)
    except Exception as e:
        log.error(f"Error in /admin guide: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while fetching the guide.")
        except discord.NotFound: pass

osrs = bot.create_group("osrs", "Commands related to your OSRS account.")
@osrs.command(name="link", description="Link your Discord account to your OSRS username.")
async def link(ctx, username: str):
    await ctx.defer(ephemeral=True)
    try:
        url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={username.replace(' ', '_')}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return await ctx.edit(content=f"Could not find '{username}' on the OSRS HiScores.")
        
        await bot.db_pool.execute("INSERT INTO user_links (discord_id, osrs_name) VALUES ($1, $2) ON CONFLICT (discord_id) DO UPDATE SET osrs_name = EXCLUDED.osrs_name", ctx.author.id, username)
        await ctx.edit(content=f"Success! Your Discord account has been linked to the OSRS name: **{username}**.")
    except Exception as e:
        log.error(f"Error in /osrs link: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while linking your account.")
        except discord.NotFound: pass

points = bot.create_group("points", "Commands related to Clan Points.")
@points.command(name="view", description="Check your current Clan Point balance.")
async def view_points(ctx):
    await ctx.defer(ephemeral=True)
    try:
        balance = await bot.db_pool.fetchval("SELECT points FROM clan_points WHERE discord_id = $1", ctx.author.id) or 0
        await ctx.edit(content=f"You currently have **{balance}** Clan Points.")
    except Exception as e:
        log.error(f"Error in /points view: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while fetching your points.")
        except discord.NotFound: pass

@points.command(name="leaderboard", description="View the Clan Points leaderboard.")
async def leaderboard(ctx):
    await ctx.defer()
    try:
        leaders = await bot.db_pool.fetch("SELECT discord_id, points FROM clan_points ORDER BY points DESC LIMIT 10")
        embed = discord.Embed(title="🏆 Clan Points Leaderboard 🏆", color=discord.Color.gold())
        if not leaders:
            embed.description = "No one has earned any points yet."
        else:
            desc_lines = []
            for i, rec in enumerate(leaders):
                rank = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
                member = ctx.guild.get_member(rec['discord_id'])
                name = member.display_name if member else f"User ID: {rec['discord_id']}"
                desc_lines.append(f"{rank} **{name}**: {rec['points']:,} points")
            embed.description = "\n".join(desc_lines)
        await ctx.edit(embed=embed)
    except Exception as e:
        log.error(f"Error in /points leaderboard: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while fetching the leaderboard.")
        except discord.NotFound: pass

@bot.slash_command(name="help", description="Shows a list of all available commands.")
async def help(ctx):
    await ctx.defer(ephemeral=True)
    try:
        embed = discord.Embed(title="📜 GrazyBot Command List 📜", description="Here are all the commands you can use.", color=discord.Color.blurple())
        member_commands = """
        `/sotw view` - View the leaderboard for the current Skill of the Week.
        `/raffle enter` - Get one ticket for the current raffle (max 10).
        `/raffle view_tickets` - See how many tickets everyone has.
        `/bingo board` - Get a link to the current bingo board.
        `/bingo complete` - Submit a task for bingo completion.
        `/points view` - Check your current Clan Point balance.
        `/points leaderboard` - View the Clan Points leaderboard.
        `/osrs link` - Link your Discord account to your OSRS name.
        `/events view` - See all currently active events.
        """
        admin_commands = """
        `/sotw start` - Manually start a new SOTW competition.
        `/sotw poll` - Start a poll to choose the next SOTW.
        `/raffle start` - Start a new raffle.
        `/raffle give_tickets` - Give raffle tickets to a member.
        `/raffle edit_tickets` - Set a member's total ticket count.
        `/raffle draw_now` - End the raffle and draw a winner immediately.
        `/raffle cancel` - Cancel the current raffle.
        `/bingo start` - Start a new clan bingo event.
        `/bingo submissions` - View and manage pending bingo submissions.
        `/admin announce` - Send a global announcement as the bot.
        `/admin manage_points` - Add or remove Clan Points from a member.
        `/admin award_sotw_winners` - Manually award points for a past SOTW.
        `/admin guide` - Shows a detailed guide for admin commands.
        """
        embed.add_field(name="✅ Member Commands", value=textwrap.dedent(member_commands), inline=False)
        embed.add_field(name="👑 Admin Commands", value=textwrap.dedent(admin_commands), inline=False)
        embed.set_footer(text="Let the games begin!")
        await ctx.edit(embed=embed)
    except Exception as e:
        log.error(f"Error in /help: {e}", exc_info=True)
        try: await ctx.edit(content="An error occurred while fetching the help command.")
        except discord.NotFound: pass

# --- Main Execution Block ---
async def run_bot():
    """A resilient function to start the bot and handle rate limits."""
    while True:
        try:
            await bot.start(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                log.warning("BOT is being rate-limited by Discord. Retrying in 5 minutes...")
                await asyncio.sleep(300)
            else:
                log.critical(f"An unexpected HTTP error occurred with the bot: {e}", exc_info=True)
                break
        except Exception as e:
            log.critical(f"An unexpected error occurred while running the bot: {e}", exc_info=True)
            break

async def main():
    web_task = asyncio.create_task(start_web_server())
    bot_task = asyncio.create_task(run_bot())
    await asyncio.gather(web_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())

