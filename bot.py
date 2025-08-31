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
import psycopg2
import psycopg2.extras
import json
import textwrap
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai
from io import BytesIO
from functools import partial

# --- Configuration & Setup ---
load_dotenv()
TOKEN = os.getenv('TOKEN')
WOM_CLAN_ID = os.getenv('WOM_CLAN_ID')
WOM_VERIFICATION_CODE = os.getenv('WOM_VERIFICATION_CODE')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DEBUG_GUILD_ID = int(os.getenv('DEBUG_GUILD_ID'))
DATABASE_URL = os.getenv('DATABASE_URL')
TASKS_FILE = "tasks.json"
BINGO_FONT_FILE = "arial.ttf" 

# Channel IDs
SOTW_CHANNEL_ID = int(os.getenv('SOTW_CHANNEL_ID'))
BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID'))
RAFFLE_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID'))
GIVEAWAY_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID'))
RECAP_CHANNEL_ID = int(os.getenv('RECAP_CHANNEL_ID'))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))
PVM_EVENT_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))

# Configure the Gemini AI
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-1.0-pro')

# Define WOM skill metrics & Bot Intents
WOM_SKILLS = ["overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herlore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"]
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = discord.Bot(intents=intents)
bot.active_polls = {}

# --- Command Groups ---
admin = discord.SlashCommandGroup("admin", "Admin-only commands")
bot.add_application_command(admin)

# --- Database & Threading Helpers ---
def get_db_connection():
    """Establishes a connection to the PostgreSQL database. This is a blocking operation."""
    return psycopg2.connect(DATABASE_URL)

async def run_in_executor(func, *args, **kwargs):
    """Runs a synchronous function in a separate thread to avoid blocking."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

def _setup_database_sync():
    """Synchronous part of database setup to be run in a thread."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS active_competitions (id INTEGER PRIMARY KEY, title TEXT, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE, winners_awarded BOOLEAN DEFAULT FALSE)""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raffles (id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, prize TEXT, ends_at TIMESTAMPTZ, winner_id BIGINT, final_ping_sent BOOLEAN DEFAULT FALSE)""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raffle_entries (entry_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, raffle_id INTEGER REFERENCES raffles(id) ON DELETE CASCADE, user_id BIGINT NOT NULL, source TEXT DEFAULT 'self')""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bingo_events (id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, board_json TEXT, message_id BIGINT, midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE)""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bingo_submissions (id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, user_id BIGINT, task_name TEXT, proof_url TEXT, status TEXT DEFAULT 'pending', bingo_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE)""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bingo_completed_tiles (bingo_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE, task_name TEXT, PRIMARY KEY (bingo_id, task_name))""")
    cursor.execute("CREATE TABLE IF NOT EXISTS user_links (discord_id BIGINT PRIMARY KEY, osrs_name TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS clan_points (discord_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0)")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS giveaways (id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, prize TEXT NOT NULL, ends_at TIMESTAMPTZ NOT NULL, max_number INTEGER NOT NULL, winner_id BIGINT, winning_number INTEGER)""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS giveaway_entries (entry_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, giveaway_id INTEGER REFERENCES giveaways(id) ON DELETE CASCADE, user_id BIGINT NOT NULL, chosen_number INTEGER NOT NULL, UNIQUE (giveaway_id, chosen_number), UNIQUE (giveaway_id, user_id))""")
    cursor.execute("CREATE TABLE IF NOT EXISTS pvm_guides (boss_name TEXT PRIMARY KEY, guide_text TEXT NOT NULL)")
    conn.commit()
    cursor.close()
    conn.close()

async def setup_database():
    """Runs the synchronous database setup in a thread."""
    await run_in_executor(_setup_database_sync)

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

# --- Bingo Submission View ---
class SubmissionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _approve_submission_sync(self, submission_id):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, task_name, bingo_id FROM bingo_submissions WHERE id = %s AND status = 'pending'", (submission_id,))
        submission_data = cursor.fetchone()
        if not submission_data:
            conn.close()
            return None
        user_id, task_name, bingo_id = submission_data
        cursor.execute("UPDATE bingo_submissions SET status = 'approved' WHERE id = %s", (submission_id,))
        cursor.execute("INSERT INTO bingo_completed_tiles (bingo_id, task_name) VALUES (%s, %s) ON CONFLICT (bingo_id, task_name) DO NOTHING", (bingo_id, task_name))
        conn.commit()
        cursor.close()
        conn.close()
        return {"user_id": user_id, "task_name": task_name, "bingo_id": bingo_id}

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_submission")
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
        result = await run_in_executor(self._approve_submission_sync, submission_id)
        if not result:
            await interaction.message.delete()
            return await interaction.followup.send("This submission was already handled or does not exist.", ephemeral=True)
        await interaction.message.delete()
        await interaction.followup.send(f"Submission #{submission_id} approved.", ephemeral=True)
        member = interaction.guild.get_member(result['user_id'])
        if member:
            await award_points(member, 25, f"completing the bingo task: '{result['task_name']}'")
        await update_bingo_board_post(result['bingo_id'])

    def _deny_submission_sync(self, submission_id):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE bingo_submissions SET status = 'denied' WHERE id = %s AND status = 'pending'", (submission_id,))
        updated_rows = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        return updated_rows

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_submission")
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
        updated_rows = await run_in_executor(self._deny_submission_sync, submission_id)
        if updated_rows == 0:
            await interaction.message.delete()
            return await interaction.followup.send("This submission was already handled.", ephemeral=True)
        await interaction.message.delete()
        await interaction.followup.send(f"Submission #{submission_id} denied.", ephemeral=True)

# --- Helper Functions ---
def _award_points_sync(member_id, amount):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO clan_points (discord_id, points) VALUES (%s, 0) ON CONFLICT (discord_id) DO NOTHING", (member_id,))
        cursor.execute("UPDATE clan_points SET points = points + %s WHERE discord_id = %s RETURNING points", (amount, member_id))
        new_balance = cursor.fetchone()[0]
        conn.commit()
        return new_balance
    except Exception as e:
        print(f"DB Error in _award_points_sync: {e}")
        conn.rollback()
        return None
    finally:
        cursor.close()
        conn.close()

async def award_points(member: discord.Member, amount: int, reason: str):
    if not member or member.bot: return
    new_balance = await run_in_executor(_award_points_sync, member.id, amount)
    if new_balance is None:
        return
    try:
        dm_embed = discord.Embed(
            title="🏆 Points Awarded!",
            description=f"You have been awarded **{amount} Clan Points** for *{reason}*! Clan Points are a measure of your dedication and can be used for rewards. Well done.",
            color=5763719
        )
        dm_embed.add_field(name="New Balance", value=f"You now have **{new_balance}** Clan Points.")
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        print(f"Could not send DM to {member.display_name} (they may have DMs disabled).")
    except Exception as e:
        print(f"Failed to send points DM: {e}")

def _create_competition_db_sync(comp_data):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO active_competitions (id, title, starts_at, ends_at) VALUES (%s, %s, %s, %s)", (comp_data['competition']['id'], comp_data['competition']['title'], comp_data['competition']['startsAt'], comp_data['competition']['endsAt']))
    conn.commit()
    cursor.close()
    conn.close()

async def create_competition(clan_id: str, skill: str, duration_days: int):
    url = "https://api.wiseoldman.net/v2/competitions"
    start_date = datetime.now(timezone.utc) + timedelta(minutes=1); end_date = start_date + timedelta(days=duration_days)
    payload = {"title": f"{skill.capitalize()} SOTW ({duration_days} days)","metric": skill,"startsAt": start_date.isoformat(),"endsAt": end_date.isoformat(),"groupId": int(clan_id),"groupVerificationCode": WOM_VERIFICATION_CODE}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status == 201:
                comp_data = await response.json()
                await run_in_executor(_create_competition_db_sync, comp_data)
                return comp_data, None
            else: return None, f"API Error: {(await response.json()).get('message', 'Failed to create competition.')}"

async def generate_embed_from_prompt(prompt: str) -> discord.Embed | None:
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
        print(f"An error occurred during Gemini embed generation: {e}")
        return None

def _draw_raffle_winner_sync(raffle_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE id = %s", (raffle_id,))
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close()
        return {"status": "error", "message": "Could not find the specified raffle to draw."}
    prize = raffle_data['prize']
    cursor.execute("SELECT user_id FROM raffle_entries WHERE raffle_id = %s", (raffle_id,))
    entries = cursor.fetchall()
    if not entries:
        conn.close()
        return {"status": "no_entries", "prize": prize}
    winner_id = random.choice(entries)['user_id']
    cursor.execute("UPDATE raffles SET winner_id = %s WHERE id = %s", (winner_id, raffle_id))
    conn.commit()
    cursor.close()
    conn.close()
    _award_points_sync(winner_id, 50)
    return {"status": "success", "prize": prize, "winner_id": winner_id}

async def draw_raffle_winner(channel: discord.TextChannel, raffle_id: int):
    result = await run_in_executor(_draw_raffle_winner_sync, raffle_id)
    if result['status'] == 'error':
        print(result['message'])
        return
    if result['status'] == 'no_entries':
        await channel.send(f"The raffle for **{result['prize']}** has ended, but unfortunately, no one entered.")
        return
    winner_user = await bot.fetch_user(result['winner_id'])
    prompt = f"Create a Discord embed JSON announcing the winner of a raffle. The winner is {winner_user.mention} and they won **{result['prize']}**. Congratulate them with epic flair."
    embed = await generate_embed_from_prompt(prompt)
    if not embed:
        embed = discord.Embed(title="🎉 Raffle Winner Announcement! 🎉", description=f"Congratulations to {winner_user.mention}, you have won the raffle!", color=discord.Color.fuchsia())
    embed.add_field(name="Prize", value=f"**{result['prize']}**", inline=False)
    embed.add_field(name="Bonus Reward", value="You have also been awarded **50 Clan Points**!", inline=False)
    embed.set_footer(text="Thanks to everyone for participating!")
    embed.set_thumbnail(url=winner_user.display_avatar.url)
    await channel.send(content=f"Congratulations {winner_user.mention}!", embed=embed)

def _draw_giveaway_winner_sync(giveaway_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
    giveaway_data = cursor.fetchone()
    if not giveaway_data:
        cursor.close(); conn.close()
        return None
    prize = giveaway_data['prize']
    max_number = giveaway_data['max_number']
    winning_number = random.randint(1, max_number)
    cursor.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id = %s AND chosen_number = %s", (giveaway_id, winning_number))
    winner_data = cursor.fetchone()
    if winner_data:
        winner_id = winner_data['user_id']
        cursor.execute("UPDATE giveaways SET winner_id = %s, winning_number = %s WHERE id = %s", (winner_id, winning_number, giveaway_id))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "winner", "prize": prize, "winning_number": winning_number, "winner_id": winner_id}
    else:
        cursor.execute("UPDATE giveaways SET winning_number = %s WHERE id = %s", (winning_number, giveaway_id))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "no_winner", "prize": prize, "winning_number": winning_number}

async def draw_giveaway_winner(channel: discord.TextChannel, giveaway_id: int):
    result = await run_in_executor(_draw_giveaway_winner_sync, giveaway_id)
    if not result:
        return
    embed = None
    if result['status'] == 'winner':
        winner_user = await bot.fetch_user(result['winner_id'])
        prompt = f"Create a Discord embed JSON for a giveaway result. The prize was a **{result['prize']}**. The winning number was **{result['winning_number']}**, and {winner_user.mention} correctly guessed it! Congratulate them."
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title=f"🎉 Giveaway Results for {result['prize']}! 🎉", description=f"Congratulations to {winner_user.mention}, who picked the lucky number!", color=discord.Color.dark_gold())
        embed.set_thumbnail(url=winner_user.display_avatar.url)
        await channel.send(content=f"Congratulations {winner_user.mention}!", embed=embed)
    else: # no_winner
        prompt = f"Create a Discord embed JSON for a giveaway result where nobody won. The prize was a **{result['prize']}**. The winning number was **{result['winning_number']}**, but nobody picked it. State that the prize remains in the clan vault."
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title=f"🎉 Giveaway Results for {result['prize']}! 🎉", description="Unfortunately, nobody picked the winning number this time. The prize remains in the clan vault!", color=discord.Color.dark_gold())
        await channel.send(embed=embed)
    embed.add_field(name="The Winning Number Was...", value=f"**{result['winning_number']}**", inline=False)

def generate_bingo_image(tasks: list, completed_tasks: list = []):
    try:
        width, height = 1000, 1000
        background_color = (40, 26, 13)
        img = Image.new('RGB', (width, height), background_color)
        draw = ImageDraw.Draw(img)
        try:
            title_font = ImageFont.truetype(BINGO_FONT_FILE, size=70)
            task_font = ImageFont.truetype(BINGO_FONT_FILE, size=22)
        except IOError:
            print(f"Warning: Font file '{BINGO_FONT_FILE}' not found. Falling back to default font.")
            title_font = ImageFont.load_default()
            task_font = ImageFont.load_default()
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
            row = i // grid_size; col = i % grid_size
            cell_x, cell_y = grid_start_x + col * cell_size, grid_start_y + row * cell_size
            if task['name'] in completed_tasks:
                overlay = Image.new('RGBA', (cell_size - line_width, cell_size - line_width), (0, 255, 0, 90))
                img.paste(overlay, (cell_x + line_width//2, cell_y + line_width//2), overlay)
            task_name = task['name']
            lines = textwrap.wrap(task_name, width=15)
            total_text_height = sum(task_font.getbbox(line)[3] for line in lines)
            current_y = (cell_y + (cell_size / 2)) - (total_text_height / 2)
            for line in lines:
                line_width_bbox, line_height_bbox = draw.textbbox((0,0), line, font=task_font)[2:4]
                draw.text(((cell_x + (cell_size / 2)) - (line_width_bbox / 2), current_y), line, font=task_font, fill=(255, 255, 255), align="center")
                current_y += line_height_bbox + 2
        output_path = "bingo_board.png"; img.save(output_path)
        return output_path, None
    except Exception as e:
        print(f"An unexpected error occurred during image generation: {e}")
        return None, str(e)

def _update_bingo_board_db_sync(bingo_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT board_json, message_id FROM bingo_events WHERE id = %s", (bingo_id,))
    event_data = cursor.fetchone()
    if not event_data:
        cursor.close(); conn.close()
        return None
    board_tasks_json, message_id = event_data
    cursor.execute("SELECT task_name FROM bingo_completed_tiles WHERE bingo_id = %s", (bingo_id,))
    completed_tiles = [row[0] for row in cursor.fetchall()]
    cursor.close(); conn.close()
    return {"board_tasks": json.loads(board_tasks_json), "message_id": message_id, "completed_tiles": completed_tiles}

async def update_bingo_board_post(bingo_id: int):
    db_data = await run_in_executor(_update_bingo_board_db_sync, bingo_id)
    if not db_data: return
    image_path, error = await run_in_executor(generate_bingo_image, db_data["board_tasks"], db_data["completed_tiles"])
    if error:
        print(f"Failed to update bingo board image: {error}")
        return
    try:
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if bingo_channel:
            message = await bingo_channel.fetch_message(db_data["message_id"])
            with open(image_path, 'rb') as f:
                new_file = discord.File(f, filename="bingo_board.png")
                embed = message.embeds[0]
                embed.set_image(url="attachment://bingo_board.png")
                await message.edit(embed=embed, files=[new_file])
    except discord.NotFound:
        print(f"Could not find bingo message {db_data['message_id']} to update.")
    except Exception as e:
        print(f"Error updating bingo board: {e}")

async def send_global_announcement(event_type: str, details: dict, message_url: str):
    announcement_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not announcement_channel:
        print("Error: Global announcements channel not found.")
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
    announcement_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not announcement_channel:
        print("ERROR: Cannot post daily summary, announcements channel not found.")
        return

    def _get_active_events_sync():
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
        competitions = cursor.fetchall()
        cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
        raffles = cursor.fetchall()
        cursor.execute("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        bingo = cursor.fetchone()
        cursor.close()
        conn.close()
        return competitions, raffles, bingo

    competitions, raffles, bingo = await run_in_executor(_get_active_events_sync)
    if not competitions and not raffles and not bingo:
        print("No active events to summarize today.")
        return
    event_data_string = ""
    if competitions:
        event_data_string += "Skill of the Week Competitions:\n"
        for comp in competitions:
            event_data_string += f"- {comp['title']} (Ends <t:{int(comp['ends_at'].timestamp())}:R>)\n"
    if raffles:
        event_data_string += "\nRaffles:\n"
        for raf in raffles:
            event_data_string += f"- Prize: {raf['prize']} (Ends <t:{int(raf['ends_at'].timestamp())}:R>)\n"
    if bingo:
        event_data_string += "\nBingo Event:\n"
        event_data_string += f"- A clan-wide bingo is active! (Ends <t:{int(bingo['ends_at'].timestamp())}:R>)\n"
    prompt = f"Create a Discord embed JSON for a daily summary of active clan events. Make it engaging. Here is the data:\n{event_data_string}"
    embed = await generate_embed_from_prompt(prompt)
    if not embed:
        embed = discord.Embed(title="📅 Daily Clan Events Summary", description=event_data_string, color=10181046)
    embed.set_footer(text="Good luck, have fun!")
    embed.timestamp=datetime.now(timezone.utc)
    await announcement_channel.send(embed=embed)

@tasks.loop(minutes=5)
async def event_manager():
    await bot.wait_until_ready()
    try:
        await asyncio.gather(
            handle_weekly_recap(),
            handle_sotw_management(),
            handle_raffle_management(),
            handle_bingo_management(),
            handle_giveaway_management()
        )
    except Exception as e:
        print(f"ERROR in event_manager loop: {e}")

async def handle_weekly_recap():
    now = datetime.now(timezone.utc)
    recap_channel = bot.get_channel(RECAP_CHANNEL_ID)
    if not (recap_channel and now.weekday() == 6 and now.hour == 19 and now.minute < 5):
        return
    url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/gained?period=week&metric=overall"
    try:
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
    except Exception as e:
        print(f"Error during weekly recap: {e}")

def _manage_sotw_sync():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    now = datetime.now(timezone.utc)
    cursor.execute("SELECT * FROM active_competitions WHERE ends_at < NOW() + interval '7 days'")
    competitions = cursor.fetchall()
    actions = {"reminders": [], "award_winners": []}
    for comp in competitions:
        ends_at = comp['ends_at']; starts_at = comp['starts_at']
        if now > ends_at and not comp['winners_awarded']:
            actions['award_winners'].append(comp)
            cursor.execute("UPDATE active_competitions SET winners_awarded = TRUE WHERE id = %s", (comp['id'],))
        elif not comp['final_ping_sent'] and (ends_at - now) <= timedelta(hours=1) and now < ends_at:
            actions['reminders'].append({"type": "final", "comp": comp})
            cursor.execute("UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = %s", (comp['id'],))
        elif not comp['midway_ping_sent'] and now >= starts_at + ((ends_at - starts_at) / 2) and now < ends_at:
            actions['reminders'].append({"type": "midway", "comp": comp})
            cursor.execute("UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = %s", (comp['id'],))
    conn.commit()
    cursor.close(); conn.close()
    return actions

async def handle_sotw_management():
    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if not sotw_channel: return
    actions = await run_in_executor(_manage_sotw_sync)
    for reminder in actions['reminders']:
        comp = reminder['comp']
        if reminder['type'] == 'final':
            await sotw_channel.send(content="@everyone", embed=discord.Embed(title="⏳ Final Hour!", description=f"The **{comp['title']}** competition ends in less than an hour!", color=discord.Color.red(), url=f"https://wiseoldman.net/competitions/{comp['id']}"))
        elif reminder['type'] == 'midway':
            await sotw_channel.send(embed=discord.Embed(title="½ Midway Point Reached!", description=f"The **{comp['title']}** competition is halfway through!", color=discord.Color.yellow(), url=f"https://wiseoldman.net/competitions/{comp['id']}"))
    for comp in actions['award_winners']:
        details_url = f"https://api.wiseoldman.net/v2/competitions/{comp['id']}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(details_url) as response:
                    if response.status == 200:
                        comp_data = await response.json()
                        for i, participant in enumerate(comp_data.get('participations', [])[:3]):
                            await award_sotw_winner_points(participant, i, comp['title'])
        except Exception as e:
            print(f"Error awarding SOTW winners for comp {comp['id']}: {e}")

def _get_discord_id_sync(osrs_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT discord_id FROM user_links WHERE osrs_name = %s", (osrs_name,))
    user_data = cursor.fetchone()
    conn.close()
    return user_data[0] if user_data else None

async def award_sotw_winner_points(participant, rank, title):
    osrs_name = participant['player']['displayName']
    discord_id = await run_in_executor(_get_discord_id_sync, osrs_name)
    if discord_id:
        member = bot.get_guild(DEBUG_GUILD_ID).get_member(discord_id)
        if member:
            point_values = [100, 50, 25]
            await award_points(member, point_values[rank], f"placing #{rank+1} in the {title} SOTW")

def _manage_raffles_sync():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    now = datetime.now(timezone.utc)
    cursor.execute("SELECT * FROM raffles WHERE winner_id IS NULL")
    active_raffles = cursor.fetchall()
    actions = {"draw": [], "remind": []}
    for raffle in active_raffles:
        if now >= raffle['ends_at']:
            actions['draw'].append(raffle['id'])
        elif not raffle['final_ping_sent'] and (raffle['ends_at'] - now) <= timedelta(days=1):
            actions['remind'].append(raffle)
            cursor.execute("UPDATE raffles SET final_ping_sent = TRUE WHERE id = %s", (raffle['id'],))
    conn.commit()
    cursor.close()
    conn.close()
    return actions

async def handle_raffle_management():
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not raffle_channel: return
    actions = await run_in_executor(_manage_raffles_sync)
    for raffle_id in actions['draw']:
        await draw_raffle_winner(raffle_channel, raffle_id)
    for raffle in actions['remind']:
        embed = discord.Embed(title="🎟️ Raffle Ending Soon!", description=f"There are only **24 hours left** to enter the raffle for a **{raffle['prize']}**!", color=discord.Color.orange())
        await raffle_channel.send(content="@everyone", embed=embed)

def _manage_bingo_sync():
    # Similar logic for bingo reminders
    return []
async def handle_bingo_management():
    pass

def _manage_giveaways_sync():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM giveaways WHERE winner_id IS NULL AND ends_at <= NOW()")
    ended_giveaways = cursor.fetchall()
    conn.close()
    return ended_giveaways

async def handle_giveaway_management():
    giveaway_channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if not giveaway_channel: return
    ended_giveaways = await run_in_executor(_manage_giveaways_sync)
    for giveaway in ended_giveaways:
        await draw_giveaway_winner(giveaway_channel, giveaway['id'])

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
        print(f"Web server started on port {port}")
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    print(f"{bot.user} is online and ready!")
    await setup_database()
    event_manager.start()
    daily_event_summary.start()
    bot.add_view(SubmissionView())
    await bot.sync_commands()

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
                print(f"Error generating PVM guide: {e}")
                await message.reply("Sorry, I couldn't fetch a guide for that right now.")

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
        print(f"Error in /sotw start: {e}")
        try: await ctx.edit(content="An unexpected error occurred while starting the SOTW.")
        except discord.NotFound: pass

@sotw.command(name="poll", description="Start a poll to choose the next SOTW.")
@discord.default_permissions(manage_events=True)
async def poll(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
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
        print(f"Error in /sotw view: {e}")
        try: await ctx.edit(content="An error occurred while fetching the SOTW leaderboard.")
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
        def _db_call(p, e):
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO raffles (prize, ends_at) VALUES (%s, %s) RETURNING id", (p, e.isoformat()))
            rid = cursor.fetchone()[0]
            conn.commit(); cursor.close(); conn.close()
            return rid
        raffle_id = await run_in_executor(_db_call, prize, ends_at)
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
        print(f"Error in /raffle start: {e}")
        try: await ctx.edit(content=f"An unexpected error occurred. Please check the logs.")
        except discord.NotFound: pass

@raffle.command(name="enter", description="Get one ticket for the current raffle (max 10).")
async def enter_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    def _db_call(user_id):
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        raffle_data = cursor.fetchone()
        if not raffle_data:
            cursor.close(); conn.close(); return {"status": "no_raffle"}
        raffle_id = raffle_data['id']
        cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND raffle_id = %s AND source = 'self'", (user_id, raffle_id))
        if cursor.fetchone()[0] >= 10:
            cursor.close(); conn.close(); return {"status": "max_tickets"}
        cursor.execute("INSERT INTO raffle_entries (user_id, source, raffle_id) VALUES (%s, 'self', %s)", (user_id, raffle_id))
        conn.commit()
        cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND raffle_id = %s", (user_id, raffle_id))
        total_tickets = cursor.fetchone()[0]
        cursor.close(); conn.close()
        return {"status": "success", "prize": raffle_data['prize'], "total": total_tickets}
    result = await run_in_executor(_db_call, ctx.author.id)
    if result['status'] == 'no_raffle': await ctx.edit(content="There is no active raffle to enter right now.")
    elif result['status'] == 'max_tickets': await ctx.edit(content="You have already claimed your maximum of 10 tickets for this raffle!")
    elif result['status'] == 'success': await ctx.edit(content=f"You have successfully claimed a ticket for the **{result['prize']}** raffle! You now have a total of {result['total']} ticket(s).")

@raffle.command(name="give_tickets", description="ADMIN: Give raffle tickets to a member.")
@discord.default_permissions(manage_events=True)
async def give_tickets(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member to give tickets to."), amount: discord.Option(int, "How many tickets to give.", min_value=1)):
    await ctx.defer(ephemeral=True)
    def _db_call(member_id, amt):
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        raffle_data = cursor.fetchone()
        if not raffle_data:
            cursor.close(); conn.close(); return {"status": "no_raffle"}
        raffle_id = raffle_data['id']
        entries = [(raffle_id, member_id, 'admin') for _ in range(amt)]
        cursor.executemany("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES (%s, %s, %s)", entries)
        conn.commit()
        cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND raffle_id = %s", (member_id, raffle_id))
        total = cursor.fetchone()[0]
        cursor.close(); conn.close()
        return {"status": "success", "prize": raffle_data['prize'], "total": total}
    result = await run_in_executor(_db_call, member.id, amount)
    if result['status'] == 'no_raffle': await ctx.edit(content="There is no active raffle.")
    elif result['status'] == 'success': await ctx.edit(content=f"Successfully gave {amount} ticket(s) to {member.display_name} for the '{result['prize']}' raffle. They now have {result['total']} ticket(s).")

@raffle.command(name="edit_tickets", description="ADMIN: Set a member's total ticket count for the active raffle.")
@discord.default_permissions(manage_events=True)
async def edit_tickets(ctx: discord.ApplicationContext, member: discord.Member, new_total: int):
    await ctx.defer(ephemeral=True)
    def _db_call(member_id, new_tot):
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        raffle_data = cursor.fetchone()
        if not raffle_data:
            cursor.close(); conn.close(); return {"status": "no_raffle"}
        raffle_id = raffle_data['id']
        cursor.execute("DELETE FROM raffle_entries WHERE user_id = %s AND raffle_id = %s", (member_id, raffle_id))
        if new_tot > 0:
            entries = [(raffle_id, member_id, 'admin_edit') for _ in range(new_tot)]
            cursor.executemany("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES (%s, %s, %s)", entries)
        conn.commit(); cursor.close(); conn.close()
        return {"status": "success", "prize": raffle_data['prize']}
    result = await run_in_executor(_db_call, member.id, new_total)
    if result['status'] == 'no_raffle': await ctx.edit(content="There is no active raffle.")
    elif result['status'] == 'success': await ctx.edit(content=f"Successfully set {member.display_name}'s ticket count to {new_total} for the '{result['prize']}' raffle.")

@raffle.command(name="view_tickets", description="View the current ticket count for all participants.")
async def view_tickets(ctx: discord.ApplicationContext):
    await ctx.defer()
    def _db_call():
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, prize FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        raffle_data = cursor.fetchone()
        if not raffle_data:
            cursor.close(); conn.close(); return None
        raffle_id, prize = raffle_data
        cursor.execute("SELECT user_id, COUNT(user_id) FROM raffle_entries WHERE raffle_id = %s GROUP BY user_id ORDER BY COUNT(user_id) DESC", (raffle_id,))
        entries = cursor.fetchall()
        cursor.close(); conn.close()
        return {"prize": prize, "entries": entries}
    result = await run_in_executor(_db_call)
    if not result:
        return await ctx.edit(content="There is no active raffle.")
    embed = discord.Embed(title=f"🎟️ Raffle Tickets for '{result['prize']}'", color=discord.Color.gold())
    if not result['entries']:
        embed.description = "No tickets have been given out yet."
    else:
        description_lines = []
        for user_id, count in result['entries'][:20]:
            member = ctx.guild.get_member(user_id)
            member_name = member.display_name if member else f"User ID: {user_id}"
            description_lines.append(f"**{member_name}**: {count} ticket(s)")
        embed.description = "\n".join(description_lines)
    await ctx.edit(embed=embed)

@raffle.command(name="draw_now", description="ADMIN: Immediately ends the raffle and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not channel: return await ctx.edit(content="Error: Raffle channel not found.")
    def _db_call():
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        raffle_data = cursor.fetchone()
        cursor.close(); conn.close()
        return raffle_data
    raffle_data = await run_in_executor(_db_call)
    if not raffle_data:
        return await ctx.edit(content="There is no active raffle to draw.")
    await draw_raffle_winner(channel, raffle_data['id'])
    await ctx.edit(content=f"Successfully triggered winner drawing.")

@raffle.command(name="cancel", description="ADMIN: Cancels the current raffle without drawing a winner.")
@discord.default_permissions(manage_events=True)
async def cancel_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    def _db_call():
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, prize FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        raffle_data = cursor.fetchone()
        if not raffle_data:
            cursor.close(); conn.close(); return None
        raffle_id, prize = raffle_data
        cursor.execute("DELETE FROM raffles WHERE id = %s", (raffle_id,))
        conn.commit(); cursor.close(); conn.close()
        return prize
    prize = await run_in_executor(_db_call)
    if not prize:
        return await ctx.edit(content="There is no active raffle to cancel.")
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if channel: await channel.send(f"The raffle for **{prize}** has been cancelled by an admin.")
    await ctx.edit(content="Raffle successfully cancelled.")

giveaway = bot.create_group("giveaway", "Commands for pick-a-number giveaways.")
@giveaway.command(name="start", description="ADMIN: Start a new pick-a-number giveaway.")
@discord.default_permissions(manage_events=True)
async def start_giveaway(ctx, prize: str, max_number: int, duration_days: float):
    await ctx.defer(ephemeral=True)
    def _db_call(p, mn, ends):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id FROM giveaways WHERE ends_at > NOW()")
        if cursor.fetchone():
            cursor.close(); conn.close(); return None
        cursor.execute("INSERT INTO giveaways (prize, ends_at, max_number) VALUES (%s, %s, %s)", (p, ends, mn))
        conn.commit(); cursor.close(); conn.close()
        return True
    ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    success = await run_in_executor(_db_call, prize, max_number, ends_at)
    if not success:
        return await ctx.edit(content="There is already an active giveaway.")
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

@giveaway.command(name="enter", description="Enter the current giveaway by picking a number.")
async def enter_giveaway(ctx, number: discord.Option(int, required=False) = None):
    await ctx.defer(ephemeral=True)
    def _db_call(user_id, num):
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM giveaways WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        giveaway_data = cursor.fetchone()
        if not giveaway_data:
            cursor.close(); conn.close(); return {"status": "no_giveaway"}
        gid, max_num = giveaway_data['id'], giveaway_data['max_number']
        if num is None:
            cursor.execute("SELECT chosen_number FROM giveaway_entries WHERE giveaway_id = %s", (gid,))
            taken = {r['chosen_number'] for r in cursor.fetchall()}
            available = list(set(range(1, max_num + 1)) - taken)
            if not available:
                cursor.close(); conn.close(); return {"status": "all_taken"}
            num = random.choice(available)
        if not (1 <= num <= max_num):
            cursor.close(); conn.close(); return {"status": "invalid_number", "max": max_num}
        try:
            cursor.execute("INSERT INTO giveaway_entries (giveaway_id, user_id, chosen_number) VALUES (%s, %s, %s)", (gid, user_id, num))
            conn.commit()
            return {"status": "success", "number": num}
        except psycopg2.IntegrityError as e:
            conn.rollback()
            if 'chosen_number' in str(e): return {"status": "already_taken", "number": num}
            if 'user_id' in str(e): return {"status": "already_entered"}
            return {"status": "error"}
        finally:
            cursor.close(); conn.close()
    result = await run_in_executor(_db_call, ctx.author.id, number)
    if result['status'] == 'no_giveaway': await ctx.edit(content="There is no active giveaway.")
    elif result['status'] == 'all_taken': await ctx.edit(content="Sorry, all numbers have been taken!")
    elif result['status'] == 'invalid_number': await ctx.edit(content=f"Please pick a number between 1 and {result['max']}.")
    elif result['status'] == 'already_taken': await ctx.edit(content=f"Sorry, the number **{result['number']}** is taken!")
    elif result['status'] == 'already_entered': await ctx.edit(content="You have already entered this giveaway!")
    elif result['status'] == 'success': await ctx.edit(content=f"Your entry for number **{result['number']}** is locked in. Good luck!")
    else: await ctx.edit(content="An unexpected error occurred.")

@giveaway.command(name="draw_now", description="ADMIN: Immediately ends the giveaway and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now_giveaway(ctx):
    await ctx.defer(ephemeral=True)
    channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if not channel: return await ctx.edit(content="Error: Giveaway channel not found.")
    def _db_call():
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM giveaways WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        data = cursor.fetchone()
        cursor.close(); conn.close()
        return data
    giveaway_data = await run_in_executor(_db_call)
    if not giveaway_data:
        return await ctx.edit(content="There is no active giveaway to draw.")
    await draw_giveaway_winner(channel, giveaway_data['id'])
    await ctx.edit(content=f"Successfully triggered winner drawing for the '{giveaway_data['prize']}' giveaway.")

events = bot.create_group("events", "View all active clan events.")
@events.command(name="view", description="Shows all currently active competitions, raffles, and bingo events.")
async def view_events(ctx):
    await ctx.defer()
    def _db_call():
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
        comps = cursor.fetchall()
        cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
        raffs = cursor.fetchall()
        cursor.execute("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        bing = cursor.fetchone()
        cursor.close(); conn.close()
        return comps, raffs, bing
    competitions, raffles, bingo = await run_in_executor(_db_call)
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
            return await ctx.edit(content=f"Error: `tasks.json` not found or is invalid.")
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
        def _db_call(s, e, b, m):
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO bingo_events (starts_at, ends_at, board_json, message_id) VALUES (%s, %s, %s, %s) RETURNING id", (s, e, b, m))
            bid = cursor.fetchone()[0]
            conn.commit(); cursor.close(); conn.close()
            return bid
        bingo_id = await run_in_executor(_db_call, datetime.now(timezone.utc), ends_at, json.dumps(board_tasks), message.id)
        await send_global_announcement("bingo_start", {"duration": duration_str}, message.jump_url)
        await ctx.edit(content=f"Bingo event #{bingo_id} created successfully!")
    except Exception as e:
        print(f"Error in /bingo start: {e}")
        try: await ctx.edit(content=f"An unexpected error occurred: {e}")
        except discord.NotFound: pass

@bingo.command(name="complete", description="Submit a task for bingo completion.")
async def complete_task(ctx, task: str, proof: str):
    await ctx.defer(ephemeral=True)
    def _db_call(uid, t, p):
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        event_data = cursor.fetchone()
        if not event_data:
            cursor.close(); conn.close(); return {"status": "no_bingo"}
        bingo_id = event_data['id']
        if t not in [tk['name'] for tk in json.loads(event_data['board_json'])]:
            cursor.close(); conn.close(); return {"status": "not_on_board"}
        cursor.execute("INSERT INTO bingo_submissions (user_id, task_name, proof_url, bingo_id) VALUES (%s, %s, %s, %s)", (uid, t, p, bingo_id))
        conn.commit(); cursor.close(); conn.close()
        return {"status": "success"}
    result = await run_in_executor(_db_call, ctx.author.id, task, proof)
    if result['status'] == 'no_bingo': await ctx.edit(content="There is no active bingo event.")
    elif result['status'] == 'not_on_board': await ctx.edit(content="That task is not on the current bingo board.")
    elif result['status'] == 'success': await ctx.edit(content="Your submission has been sent to the admins for review!")

@bingo.command(name="submissions", description="ADMIN: View pending bingo task submissions.")
@discord.default_permissions(manage_events=True)
async def view_submissions(ctx):
    await ctx.defer(ephemeral=True)
    def _db_call():
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM bingo_submissions WHERE status = 'pending'")
        pending = cursor.fetchall()
        cursor.close(); conn.close()
        return pending
    pending = await run_in_executor(_db_call)
    if not pending:
        return await ctx.edit(content="There are no pending bingo submissions.")
    await ctx.edit(content="Here are the pending submissions:")
    for sub in pending:
        user = await bot.fetch_user(sub['user_id'])
        embed = discord.Embed(title="📝 Bingo Submission", description=f"**Task:** {sub['task_name']}", color=discord.Color.yellow())
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.add_field(name="Proof", value=f"[Click to view]({sub['proof_url']})", inline=False)
        embed.set_footer(text=f"Submission ID: {sub['id']}")
        await ctx.channel.send(embed=embed, view=SubmissionView(), ephemeral=True)

@bingo.command(name="board", description="View the current bingo board.")
async def view_board(ctx):
    await ctx.defer()
    def _db_call():
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT message_id FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        data = cursor.fetchone()
        cursor.close(); conn.close()
        return data
    event_data = await run_in_executor(_db_call)
    if not event_data or not event_data[0]:
        return await ctx.edit(content="There is no active bingo board to display.")
    bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
    if bingo_channel:
        try:
            message = await bingo_channel.fetch_message(event_data[0])
            await ctx.edit(content=f"Here is the current bingo board: {message.jump_url}")
        except discord.NotFound:
            await ctx.edit(content="Could not find the original bingo board message.")
    else:
        await ctx.edit(content="Bingo channel not configured.")

@admin.command(name="announce", description="Send a message as the bot to a specific channel.")
@discord.default_permissions(manage_guild=True)
async def announce(ctx, message: str, channel: discord.TextChannel, ping_everyone: bool = False):
    await ctx.defer(ephemeral=True)
    content = "@everyone" if ping_everyone else ""
    embed = discord.Embed(title="📢 Clan Announcement", description=message, color=discord.Color.orange())
    embed.set_footer(text=f"Message sent by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    try:
        await channel.send(content=content, embed=embed)
        await ctx.edit(content="Announcement sent successfully!")
    except discord.Forbidden:
        await ctx.edit(content="Error: I don't have permission to send messages in that channel.")
    except Exception as e:
        await ctx.edit(content=f"An unexpected error occurred: {e}")

def _get_balance_sync(member_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT points FROM clan_points WHERE discord_id = %s", (member_id,))
    point_data = cursor.fetchone()
    cursor.close()
    conn.close()
    return point_data[0] if point_data else 0

@admin.command(name="manage_points", description="Add or remove Clan Points from a member.")
@discord.default_permissions(manage_guild=True)
async def manage_points(ctx, member: discord.Member, action: str, amount: int, reason: str):
    await ctx.defer(ephemeral=True)
    if action == "add":
        await award_points(member, amount, reason)
    else: # remove
        def _db_call(mid, amt):
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO clan_points (discord_id, points) VALUES (%s, 0) ON CONFLICT (discord_id) DO NOTHING", (mid,))
            cursor.execute("UPDATE clan_points SET points = GREATEST(0, points - %s) WHERE discord_id = %s", (amt, mid))
            conn.commit(); cursor.close(); conn.close()
        await run_in_executor(_db_call, member.id, amount)
    new_balance = await run_in_executor(_get_balance_sync, member.id)
    await ctx.edit(content=f"Successfully updated {member.display_name}'s points. Their new balance is {new_balance}.")

@admin.command(name="award_sotw_winners", description="Manually award points for a past SOTW competition.")
@discord.default_permissions(manage_guild=True)
async def award_sotw_winners(ctx, competition_id: int):
    await ctx.defer(ephemeral=True)
    details_url = f"https://api.wiseoldman.net/v2/competitions/{competition_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(details_url) as response:
                if response.status != 200:
                    return await ctx.edit(content=f"Could not fetch details for competition ID {competition_id}.")
                comp_data = await response.json()
        awarded_to = []
        point_values = [100, 50, 25]
        for i, participant in enumerate(comp_data.get('participations', [])[:3]):
            discord_id = await run_in_executor(_get_discord_id_sync, participant['player']['displayName'])
            if discord_id:
                member = ctx.guild.get_member(discord_id)
                if member:
                    await award_points(member, point_values[i], f"placing #{i+1} in the {comp_data['title']} SOTW")
                    awarded_to.append(f"#{i+1}: {member.display_name} ({point_values[i]} points)")
        if not awarded_to:
            return await ctx.edit(content="No winners could be found or linked for that competition.")
        await ctx.edit(content="Successfully awarded points to:\n" + "\n".join(awarded_to))
    except Exception as e:
        print(f"Error in award_sotw_winners: {e}")
        await ctx.edit(content="An error occurred while awarding points.")

@admin.command(name="guide", description="Shows a detailed guide on how to use admin commands.")
@discord.default_permissions(manage_guild=True)
async def admin_guide(ctx):
    await ctx.defer(ephemeral=True)
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

osrs = bot.create_group("osrs", "Commands related to your OSRS account.")
@osrs.command(name="link", description="Link your Discord account to your OSRS username.")
async def link(ctx, username: str):
    await ctx.defer(ephemeral=True)
    url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={username.replace(' ', '_')}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return await ctx.edit(content=f"Could not find '{username}' on the OSRS HiScores.")
        def _db_call(uid, uname):
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO user_links (discord_id, osrs_name) VALUES (%s, %s) ON CONFLICT (discord_id) DO UPDATE SET osrs_name = EXCLUDED.osrs_name", (uid, uname))
            conn.commit(); cursor.close(); conn.close()
        await run_in_executor(_db_call, ctx.author.id, username)
        await ctx.edit(content=f"Success! Your Discord account has been linked to the OSRS name: **{username}**.")
    except Exception as e:
        print(f"Error in /osrs link: {e}")
        await ctx.edit(content="An error occurred while linking your account.")

points = bot.create_group("points", "Commands related to Clan Points.")
@points.command(name="view", description="Check your current Clan Point balance.")
async def view_points(ctx):
    await ctx.defer(ephemeral=True)
    balance = await run_in_executor(_get_balance_sync, ctx.author.id)
    await ctx.edit(content=f"You currently have **{balance}** Clan Points.")

@points.command(name="leaderboard", description="View the Clan Points leaderboard.")
async def leaderboard(ctx):
    await ctx.defer()
    def _db_call():
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT discord_id, points FROM clan_points ORDER BY points DESC LIMIT 10")
        leaders = cursor.fetchall()
        cursor.close(); conn.close()
        return leaders
    leaders = await run_in_executor(_db_call)
    embed = discord.Embed(title="🏆 Clan Points Leaderboard 🏆", color=discord.Color.gold())
    if not leaders:
        embed.description = "No one has earned any points yet."
    else:
        desc_lines = []
        for i, (user_id, pts) in enumerate(leaders):
            rank = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
            member = ctx.guild.get_member(user_id)
            name = member.display_name if member else f"User ID: {user_id}"
            desc_lines.append(f"{rank} **{name}**: {pts:,} points")
        embed.description = "\n".join(desc_lines)
    await ctx.edit(embed=embed)

@bot.slash_command(name="help", description="Shows a list of all available commands.")
async def help(ctx):
    await ctx.defer(ephemeral=True)
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

# --- Main Execution Block ---
async def run_bot():
    """A resilient function to start the bot and handle rate limits."""
    while True:
        try:
            await bot.start(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print("BOT is being rate-limited by Discord. Retrying in 5 minutes...")
                await asyncio.sleep(300)
            else:
                print(f"An unexpected HTTP error occurred with the bot: {e}")
                break
        except Exception as e:
            print(f"An unexpected error occurred while running the bot: {e}")
            break

async def main():
    web_task = asyncio.create_task(start_web_server())
    bot_task = asyncio.create_task(run_bot())
    await asyncio.gather(web_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())

