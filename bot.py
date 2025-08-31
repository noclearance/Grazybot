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

# --- Configuration & Setup ---
load_dotenv()
TOKEN = os.getenv('TOKEN')
WOM_CLAN_ID = os.getenv('WOM_CLAN_ID')
WOM_VERIFICATION_CODE = os.getenv('WOM_VERIFICATION_CODE')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DEBUG_GUILD_ID = int(os.getenv('DEBUG_GUILD_ID'))
DATABASE_URL = os.getenv('DATABASE_URL')
TASKS_FILE = "tasks.json"
# IMPORTANT: Place a TrueType Font (e.g., Arial.ttf, OpenSans.ttf) in your bot's directory
# and put its filename here. This will make the bingo board look much better.
BINGO_FONT_FILE = "arial.ttf" 

# Channel IDs
SOTW_CHANNEL_ID = int(os.getenv('SOTW_CHANNEL_ID'))
BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID'))
RAFFLE_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID'))
GIVEAWAY_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID')) # Using raffle channel for giveaways
RECAP_CHANNEL_ID = int(os.getenv('RECAP_CHANNEL_ID'))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))
PVM_EVENT_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID')) # Default to announcements

# Configure the Gemini AI (for text)
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-1.0-pro')

# Define WOM skill metrics & Bot Intents
WOM_SKILLS = ["overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herlore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"]
intents = discord.Intents.default()
intents.members = True
intents.message_content = True # Needed for the on_message event
bot = discord.Bot(intents=intents, debug_guilds=[DEBUG_GUILD_ID])
bot.active_polls = {}

# --- Command Groups ---
admin = discord.SlashCommandGroup("admin", "Admin-only commands")

@admin.command(name="announce", description="Send a message as the bot to a specific channel.")
async def announce(ctx: discord.ApplicationContext, channel: discord.Option(discord.TextChannel), message: str):
    await channel.send(message)
    await ctx.respond(f"Message sent to {channel.mention}", ephemeral=True)

# Register the group
bot.add_application_command(admin)


# --- Database Setup ---
def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    return psycopg2.connect(DATABASE_URL)

def setup_database():
    """
    Sets up the necessary database tables if they don't exist.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Existing tables...
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS active_competitions (
        id INTEGER PRIMARY KEY, title TEXT, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, 
        midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE, winners_awarded BOOLEAN DEFAULT FALSE
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raffles (
        id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, prize TEXT, ends_at TIMESTAMPTZ, winner_id BIGINT,
        final_ping_sent BOOLEAN DEFAULT FALSE
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raffle_entries (
        entry_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, raffle_id INTEGER REFERENCES raffles(id) ON DELETE CASCADE, 
        user_id BIGINT NOT NULL, source TEXT DEFAULT 'self'
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bingo_events (
        id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, 
        board_json TEXT, message_id BIGINT, midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bingo_submissions (
        id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, user_id BIGINT, task_name TEXT, proof_url TEXT, 
        status TEXT DEFAULT 'pending', bingo_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bingo_completed_tiles (
        bingo_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE, task_name TEXT, PRIMARY KEY (bingo_id, task_name)
    )""")
    cursor.execute("CREATE TABLE IF NOT EXISTS user_links (discord_id BIGINT PRIMARY KEY, osrs_name TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS clan_points (discord_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0)")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, prize TEXT NOT NULL, ends_at TIMESTAMPTZ NOT NULL,
        max_number INTEGER NOT NULL, winner_id BIGINT, winning_number INTEGER
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS giveaway_entries (
        entry_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY, giveaway_id INTEGER REFERENCES giveaways(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL, chosen_number INTEGER NOT NULL,
        UNIQUE (giveaway_id, chosen_number), UNIQUE (giveaway_id, user_id)
    )""")
    
    # New table for PVM guides
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pvm_guides (
        boss_name TEXT PRIMARY KEY,
        guide_text TEXT NOT NULL
    )""")

    conn.commit()
    cursor.close()
    conn.close()

# --- SOTW Poll View ---
class SotwPollView(discord.ui.View):
    def __init__(self, author):
        super().__init__(timeout=86400); self.author = author; self.votes = {};
    
    async def create_embed(self):
        ai_embed_data = await generate_announcement_json("sotw_poll")
        vote_description = "\n\n**Current Votes:**\n"
        for skill, voters in self.votes.items(): vote_description += f"**{skill.capitalize()}**: {len(voters)} vote(s)\n"
        
        embed = discord.Embed.from_dict(ai_embed_data)
        embed.description += vote_description
        embed.set_footer(text=f"Poll started by {self.author.display_name}", icon_url=self.author.display_avatar.url); 
        return embed

    def add_buttons(self, skills):
        for skill in skills: self.votes[skill] = []; self.add_item(SotwButton(label=skill.capitalize(), custom_id=skill))
        self.add_item(FinishButton(label="Finish Poll & Start SOTW", custom_id="finish_poll"))

class SotwButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction):
        # Simplified logic for vote casting/changing
        for skill_key, voters in self.view.votes.items():
            if interaction.user in voters:
                voters.remove(interaction.user)
        
        # Add the new vote
        self.view.votes[self.custom_id].append(interaction.user)
        
        new_embed = await self.view.create_embed()
        await interaction.response.edit_message(embed=new_embed, view=self.view)
        
        await interaction.followup.send(f"Your vote for **{self.label}** has been counted.", ephemeral=True)

class FinishButton(discord.ui.Button):
    def __init__(self, label, custom_id): super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id=custom_id)
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.author.id: return await interaction.response.send_message("Only the poll starter can finish it.", ephemeral=True)
        view = self.view
        if not any(v for v in view.votes.values()): return await interaction.response.send_message("No votes cast yet.", ephemeral=True)
        winner = max(view.votes, key=lambda k: len(view.votes[k])); await interaction.response.defer(ephemeral=True)
        data, error = await create_competition(WOM_CLAN_ID, winner, 7)
        if error: await interaction.followup.send(f"Poll finished, but failed to start for **{winner.capitalize()}**: {error}", ephemeral=True); return
        
        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            embed = await create_competition_embed(data, interaction.user, poll_winner=True)
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

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_submission")
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, task_name, bingo_id FROM bingo_submissions WHERE id = %s AND status = 'pending'", (submission_id,))
        submission_data = cursor.fetchone()
        if not submission_data:
            conn.close()
            await interaction.message.delete()
            return await interaction.response.send_message("This submission was already handled or does not exist.", ephemeral=True)
            
        user_id, task_name, bingo_id = submission_data
        
        cursor.execute("UPDATE bingo_submissions SET status = 'approved' WHERE id = %s", (submission_id,))
        cursor.execute("INSERT INTO bingo_completed_tiles (bingo_id, task_name) VALUES (%s, %s) ON CONFLICT (bingo_id, task_name) DO NOTHING", (bingo_id, task_name))
        conn.commit()
        cursor.close()
        conn.close()

        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} approved.", ephemeral=True)
        
        member = interaction.guild.get_member(user_id)
        if member:
            await award_points(member, 25, f"completing the bingo task: '{task_name}'")
        
        await update_bingo_board_post(bingo_id)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_submission")
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        # Check if it's pending before updating to avoid race conditions
        cursor.execute("UPDATE bingo_submissions SET status = 'denied' WHERE id = %s AND status = 'pending'", (submission_id,))
        if cursor.rowcount == 0:
            conn.close()
            await interaction.message.delete()
            return await interaction.response.send_message("This submission was already handled.", ephemeral=True)

        conn.commit()
        cursor.close()
        conn.close()

        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} denied.", ephemeral=True)

# --- Helper Functions ---
async def award_points(member: discord.Member, amount: int, reason: str):
    if not member or member.bot: return

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO clan_points (discord_id, points) VALUES (%s, 0) ON CONFLICT (discord_id) DO NOTHING", (member.id,))
        cursor.execute("UPDATE clan_points SET points = points + %s WHERE discord_id = %s RETURNING points", (amount, member.id))
        new_balance = cursor.fetchone()[0]
        conn.commit()
    except Exception as e:
        print(f"Database error in award_points: {e}")
        conn.rollback()
        return
    finally:
        cursor.close()
        conn.close()

    try:
        details = {"amount": amount, "reason": reason}
        ai_dm_data = await generate_announcement_json("points_award", details)
        dm_embed = discord.Embed.from_dict(ai_dm_data)
        dm_embed.add_field(name="New Balance", value=f"You now have **{new_balance}** Clan Points.")
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        print(f"Could not send DM to {member.display_name} (they may have DMs disabled).")
    except Exception as e:
        print(f"Failed to send points DM: {e}")

async def create_competition(clan_id: str, skill: str, duration_days: int):
    url = "https://api.wiseoldman.net/v2/competitions"
    start_date = datetime.now(timezone.utc) + timedelta(minutes=1); end_date = start_date + timedelta(days=duration_days)
    payload = {"title": f"{skill.capitalize()} SOTW ({duration_days} days)","metric": skill,"startsAt": start_date.isoformat(),"endsAt": end_date.isoformat(),"groupId": int(clan_id),"groupVerificationCode": WOM_VERIFICATION_CODE}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status == 201:
                comp_data = await response.json()
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO active_competitions (id, title, starts_at, ends_at) VALUES (%s, %s, %s, %s)", (comp_data['competition']['id'], comp_data['competition']['title'], comp_data['competition']['startsAt'], comp_data['competition']['endsAt']))
                conn.commit(); cursor.close(); conn.close()
                return comp_data, None
            else: return None, f"API Error: {(await response.json()).get('message', 'Failed to create competition.')}"

async def create_competition_embed(data, author, poll_winner=False):
    comp = data['competition']; comp_id = comp['id']
    
    details = {"skill": comp['metric'].capitalize()}
    ai_embed_data = await generate_announcement_json("sotw_start", details)
    
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.url = f"https://wiseoldman.net/competitions/{comp_id}"
    start_dt = datetime.fromisoformat(comp['startsAt'].replace('Z', '+00:00')); end_dt = datetime.fromisoformat(comp['endsAt'].replace('Z', '+00:00'))
    embed.add_field(name="Skill", value=comp['metric'].capitalize(), inline=True); embed.add_field(name="Duration", value=f"{(end_dt - start_dt).days} days", inline=True); embed.add_field(name="\u200b", value="\u200b", inline=True); embed.add_field(name="Start Time", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True); embed.add_field(name="End Time", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
    embed.set_footer(text=f"Competition started by {author.display_name}", icon_url=author.display_avatar.url)
    return embed

async def generate_recap_text(gains_data: list) -> str:
    data_summary = ""
    for i, player in enumerate(gains_data[:10]):
        rank = i + 1; username = player['player']['displayName']; gained = player.get('gained', 0)
        data_summary += f"{rank}. {username}: {gained:,} XP\n"
    prompt = f"You are the Taskmaster for an Old School RuneScape clan. Your tone is formal and encouraging. Write a weekly recap based on the following data. Announce the top 3 with extra flair. Keep it to a few short paragraphs. Do not use emojis or markdown. Data:\n{data_summary}"
    try:
        response = await ai_model.generate_content_async(prompt); return response.text
    except Exception as e:
        print(f"An error occurred with the Gemini API: {e}"); return "The Taskmaster is currently reviewing the ledgers."

async def generate_announcement_json(event_type: str, details: dict = None) -> dict:
    """
    Generates a JSON object for a Discord embed with more personality and detail.
    This version gives the AI more creative freedom over the description.
    """
    details = details or {}
    
    # --- Persona and Instructions for the AI ---
    persona_prompt = """
    You are TaskmasterGPT, the official announcer for an Old School RuneScape clan. 
    Your tone is epic, engaging, and a little bit cheeky. You are the clan's ultimate hype man.
    Your task is to generate a JSON object with a single key: "description".
    The value of "description" should be a full, narrative-style announcement message.
    You MUST use Discord markdown like **bold** and *italics* to add emphasis. Do not use emojis.
    Incorporate all the details provided in the request to make the announcement informative and exciting.
    """
    
    # --- Default titles and colors defined in the bot, not by the AI ---
    title = "🎉 A New Event Has Started!"
    color = 3447003
    
    if event_type == "sotw_poll":
        title = "📊 Skill of the Week Poll"
        color = 15105600
        specific_prompt = "Write an announcement kicking off a new poll to decide the next Skill of the Week. Encourage everyone to cast their vote to determine the clan's next great challenge."
        fallback_desc = "The time has come to choose our next battleground! A poll has been started to determine the next Skill of the Week. Head to the SOTW channel and cast your vote!"

    elif event_type == "sotw_start":
        skill = details.get('skill', 'a new skill')
        duration = details.get('duration', 'a set period')
        title = f"⚔️ SOTW Started: {skill}! ⚔️"
        color = 5763719
        specific_prompt = f"Write an epic announcement declaring the start of a new Skill of the Week competition. The chosen skill is **{skill}**, and the competition will last for **{duration}**. Frame it as a grand challenge for glory and honor."
        fallback_desc = f"The clan has spoken! The great grind for **{skill}** begins *now*. You have **{duration}** to prove your dedication. May the most relentless skiller achieve victory!"

    elif event_type == "raffle_start":
        prize = details.get('prize', 'a grand prize')
        duration = details.get('duration', 'a set period')
        title = "🎟️ A New Raffle has Begun!"
        color = 15844367
        specific_prompt = f"Write a compelling announcement for a new clan raffle. The grand prize is a legendary **{prize}**. The raffle will be open for **{duration}**. Encourage members to test their luck for a chance to win big."
        fallback_desc = f"Fortune favors the bold! A new raffle is underway, and a legendary **{prize}** is up for grabs. You have **{duration}** to enter and claim your shot at glory. Good luck!"

    elif event_type == "bingo_start":
        duration = details.get('duration', 'a set period')
        title = "🧩 A New Clan Bingo Has Started! 🧩"
        color = 11027200
        specific_prompt = f"Write a fun and engaging announcement for the start of a new clan bingo event. It will last for **{duration}**. Describe it as a board full of diverse challenges and a great way for members to earn points and show their skills."
        fallback_desc = f"The Taskmaster has devised a new trial! A fresh board of challenges awaits all clan members for the next **{duration}**. Complete tasks, fill your tiles, and earn points. Let the games begin!"

    elif event_type == "points_award":
        # This one remains structured as it's a direct notification
        amount = details.get('amount', 'a number of')
        reason = details.get('reason', 'your excellent performance')
        return {
            "title": "🏆 Points Awarded!",
            "description": f"You have been awarded **{amount} Clan Points** for *{reason}*! Clan Points are a measure of your dedication and can be used for rewards. Well done.",
            "color": 5763719
        }
    
    elif event_type == "daily_summary":
        title = "📅 Daily Clan Events Summary"
        color = 10181046 # A nice purple
        event_data = details.get('event_data', 'No active events.')
        specific_prompt = f"Write an engaging daily summary of all active clan events based on the following data. Make it sound cool, remind people what they can win, and encourage them to participate. Here is the data:\n{event_data}"
        fallback_desc = "Here's a look at all the events currently running! Get involved and earn some points!"

    else:
        # Generic fallback
        return {"title": title, "description": "A new event has started!", "color": color}

    full_prompt = f"{persona_prompt}\n\nRequest: {specific_prompt}\n\nJSON Output:"
    
    try:
        response = await ai_model.generate_content_async(full_prompt)
        clean_json_string = response.text.strip().lstrip("```json").rstrip("```")
        # We only expect the 'description' from the AI now
        ai_data = json.loads(clean_json_string)
        description = ai_data.get("description", fallback_desc)
    except Exception as e:
        print(f"An error occurred during JSON generation: {e}")
        description = fallback_desc

    return {"title": title, "description": description, "color": color}


async def draw_raffle_winner(channel: discord.TextChannel, raffle_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE id = %s", (raffle_id,))
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close()
        return "Could not find the specified raffle to draw."
    prize = raffle_data['prize']
    cursor.execute("SELECT user_id FROM raffle_entries WHERE raffle_id = %s", (raffle_id,))
    entries = cursor.fetchall()
    if not entries:
        await channel.send(f"The raffle for **{prize}** has ended, but unfortunately, no one entered.")
    else:
        winner_id = random.choice(entries)['user_id']
        winner_user = await bot.fetch_user(winner_id)
        
        await award_points(winner_user, 50, f"winning the raffle for {prize}")

        embed = discord.Embed(title="🎉 Raffle Winner Announcement! 🎉", description=f"Congratulations to {winner_user.mention}, you have won the raffle!", color=discord.Color.fuchsia())
        embed.add_field(name="Prize", value=f"**{prize}**", inline=False)
        embed.add_field(name="Bonus Reward", value="You have also been awarded **50 Clan Points**!", inline=False)
        embed.set_footer(text="Thanks to everyone for participating!")
        embed.set_thumbnail(url=winner_user.display_avatar.url)
        await channel.send(content=f"Congratulations {winner_user.mention}!", embed=embed)
        cursor.execute("UPDATE raffles SET winner_id = %s WHERE id = %s", (winner_id, raffle_id))
    conn.commit()
    cursor.close()
    conn.close()
    return f"Winner drawn for the '{prize}' raffle."

async def draw_giveaway_winner(channel: discord.TextChannel, giveaway_id: int):
    """Handles the logic for drawing a giveaway winner."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
    giveaway_data = cursor.fetchone()
    if not giveaway_data:
        cursor.close(); conn.close()
        return
    
    prize = giveaway_data['prize']
    max_number = giveaway_data['max_number']
    winning_number = random.randint(1, max_number)
    
    cursor.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id = %s AND chosen_number = %s", (giveaway_id, winning_number))
    winner_data = cursor.fetchone()
    
    embed = discord.Embed(title=f"🎉 Giveaway Results for {prize}! 🎉", color=discord.Color.dark_gold())
    embed.add_field(name="The Winning Number Was...", value=f"**{winning_number}**", inline=False)

    if winner_data:
        winner_id = winner_data['user_id']
        winner_user = await bot.fetch_user(winner_id)
        embed.description = f"Congratulations to {winner_user.mention}, who picked the lucky number!"
        embed.set_thumbnail(url=winner_user.display_avatar.url)
        await channel.send(content=f"Congratulations {winner_user.mention}!", embed=embed)
        cursor.execute("UPDATE giveaways SET winner_id = %s, winning_number = %s WHERE id = %s", (winner_id, winning_number, giveaway_id))
    else:
        embed.description = "Unfortunately, nobody picked the winning number this time. The prize remains in the clan vault!"
        await channel.send(embed=embed)
        cursor.execute("UPDATE giveaways SET winning_number = %s WHERE id = %s", (winning_number, giveaway_id))
        
    conn.commit()
    cursor.close()
    conn.close()


def generate_bingo_image(tasks: list, completed_tasks: list = []):
    """Generates the bingo board image with better fonts and text wrapping."""
    try:
        width, height = 1000, 1000
        background_color = (40, 26, 13) # Dark wood color
        img = Image.new('RGB', (width, height), background_color)
        draw = ImageDraw.Draw(img)
        
        # --- FONT LOADING (IMPROVED) ---
        try:
            # Use a TrueType font for much better quality.
            # The BINGO_FONT_FILE must exist in your bot's directory.
            title_font = ImageFont.truetype(BINGO_FONT_FILE, size=70)
            task_font = ImageFont.truetype(BINGO_FONT_FILE, size=22)
        except IOError:
            print(f"Warning: Font file '{BINGO_FONT_FILE}' not found. Falling back to default font.")
            # Fallback to the default font if the file isn't found
            title_font = ImageFont.load_default()
            task_font = ImageFont.load_default()

        # --- DRAWING ---
        draw.text((width/2, 60), "CLAN BINGO", font=title_font, fill=(255, 215, 0), anchor="ms")

        grid_size = 5; cell_size = 170; line_width = 4
        grid_start_x, grid_start_y = 75, 125
        grid_end_x, grid_end_y = grid_start_x + (grid_size * cell_size), grid_start_y + (grid_size * cell_size)
        line_color = (255, 215, 0) # Gold color
        
        for i in range(grid_size + 1):
            # Vertical lines
            draw.line([(grid_start_x + i * cell_size, grid_start_y), (grid_start_x + i * cell_size, grid_end_y)], fill=line_color, width=line_width)
            # Horizontal lines
            draw.line([(grid_start_x, grid_start_y + i * cell_size), (grid_end_x, grid_start_y + i * cell_size)], fill=line_color, width=line_width)

        for i, task in enumerate(tasks):
            if i >= 25: break
            row = i // grid_size; col = i % grid_size
            cell_x, cell_y = grid_start_x + col * cell_size, grid_start_y + row * cell_size
            
            # --- COMPLETED TILE OVERLAY ---
            if task['name'] in completed_tasks:
                # Create a semi-transparent green overlay
                overlay = Image.new('RGBA', (cell_size - line_width, cell_size - line_width), (0, 255, 0, 90))
                # Paste it inside the cell, accounting for line widths
                img.paste(overlay, (cell_x + line_width//2, cell_y + line_width//2), overlay)

            # --- TEXT WRAPPING & CENTERING (IMPROVED) ---
            text_x = cell_x + (cell_size / 2)
            text_y = cell_y + (cell_size / 2)
            task_name = task['name']
            
            # Manual wrap for better control
            lines = textwrap.wrap(task_name, width=15) # Adjust width based on font size
            
            # Calculate total text height to center it vertically
            total_text_height = sum(task_font.getbbox(line)[3] for line in lines)
            current_y = text_y - (total_text_height / 2)

            for line in lines:
                line_width_bbox, line_height_bbox = draw.textbbox((0,0), line, font=task_font)[2:4]
                draw.text(
                    (text_x - (line_width_bbox / 2), current_y),
                    line,
                    font=task_font,
                    fill=(255, 255, 255),
                    align="center"
                )
                current_y += line_height_bbox + 2 # Add a small gap between lines

        output_path = "bingo_board.png"; img.save(output_path)
        return output_path, None
    except Exception as e:
        print(f"An unexpected error occurred during image generation: {e}")
        return None, f"An unexpected error occurred during image generation: {e}"

async def update_bingo_board_post(bingo_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT board_json, message_id FROM bingo_events WHERE id = %s", (bingo_id,))
    event_data = cursor.fetchone()
    if not event_data:
        cursor.close(); conn.close()
        return
    
    board_tasks = json.loads(event_data[0])
    message_id = event_data[1]
    
    cursor.execute("SELECT task_name FROM bingo_completed_tiles WHERE bingo_id = %s", (bingo_id,))
    completed_tiles = [row[0] for row in cursor.fetchall()]
    cursor.close(); conn.close()

    image_path, error = generate_bingo_image(board_tasks, completed_tiles)
    if error:
        print(f"Failed to update bingo board image: {error}")
        return

    try:
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if bingo_channel:
            message = await bingo_channel.fetch_message(message_id)
            with open(image_path, 'rb') as f:
                new_file = discord.File(f, filename="bingo_board.png")
                embed = message.embeds[0]
                embed.set_image(url="attachment://bingo_board.png")
                await message.edit(embed=embed, files=[new_file])
    except discord.NotFound:
        print(f"Could not find bingo message {message_id} to update.")
    except Exception as e:
        print(f"Error updating bingo board: {e}")

async def send_global_announcement(event_type: str, details: dict, message_url: str):
    announcement_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not announcement_channel:
        print("Error: Global announcements channel not found.")
        return
        
    ai_embed_data = await generate_announcement_json(event_type, details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.url = message_url
    embed.add_field(name="Details", value=f"[Click here to view the event!]({message_url})")
    embed.set_footer(text="A new clan event has started!")
    
    await announcement_channel.send(content="@everyone", embed=embed)

# --- TASKS ---

# Set a specific time for the daily summary, e.g., 12:00 PM UTC
daily_summary_time = time(hour=12, minute=0, tzinfo=timezone.utc)

@tasks.loop(time=daily_summary_time)
async def daily_event_summary():
    """Posts a daily summary of all active events to the announcements channel."""
    await bot.wait_until_ready()
    announcement_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not announcement_channel:
        print("ERROR: Cannot post daily summary, announcements channel not found.")
        return

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Fetch all active events
    cursor.execute("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
    competitions = cursor.fetchall()
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
    raffles = cursor.fetchall()
    cursor.execute("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    bingo = cursor.fetchone()
    
    cursor.close()
    conn.close()

    # Only post if there is at least one active event
    if not competitions and not raffles and not bingo:
        print("No active events to summarize today.")
        return

    # --- AI-Powered Summary ---
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

    ai_embed_data = await generate_announcement_json("daily_summary", {"event_data": event_data_string})
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.set_footer(text="Good luck, have fun!")
    embed.timestamp=datetime.now(timezone.utc)

    await announcement_channel.send(embed=embed)


@tasks.loop(minutes=5)
async def event_manager():
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)
    
    # --- Weekly Recap Check (Isolated) ---
    try:
        recap_channel = bot.get_channel(RECAP_CHANNEL_ID)
        # Check for Sunday at 7 PM UTC (adjust as needed)
        if recap_channel and now.weekday() == 6 and now.hour == 19 and now.minute < 5:
            url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/gained?period=week&metric=overall"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        # FIX: Handle cases where there are no XP gains for the week.
                        if not data:
                            recap_text = "It seems the clan was quiet this week, with no XP gains to report. Let's pick up the pace for next week!"
                        else:
                            recap_text = await generate_recap_text(data)
                        
                        embed = discord.Embed(title="📈 Weekly Recap from the Taskmaster", description=recap_text, color=discord.Color.from_rgb(100, 150, 255))
                        embed.set_footer(text=f"Recap for the week ending {now.strftime('%B %d, %Y')}")
                        await recap_channel.send(embed=embed)
    except Exception as e:
        print(f"ERROR in event_manager (Recap): {e}")

    # --- SOTW Management (Isolated) ---
    try:
        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT * FROM active_competitions WHERE ends_at < NOW() + interval '7 days'") # Fetch recent/active
            competitions = cursor.fetchall()
            
            for comp in competitions:
                ends_at = comp['ends_at']; starts_at = comp['starts_at']
                
                # --- Award Winners (DB Connection Optimized) ---
                if now > ends_at and not comp['winners_awarded']:
                    details_url = f"https://api.wiseoldman.net/v2/competitions/{comp['id']}"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(details_url) as response:
                            if response.status == 200:
                                comp_data = await response.json()
                                point_values = [100, 50, 25] # 1st, 2nd, 3rd
                                for i, participant in enumerate(comp_data.get('participations', [])[:3]):
                                    osrs_name = participant['player']['displayName']
                                    # Use the existing cursor
                                    cursor.execute("SELECT discord_id FROM user_links WHERE osrs_name = %s", (osrs_name,))
                                    user_data = cursor.fetchone()
                                    if user_data:
                                        member = bot.get_guild(DEBUG_GUILD_ID).get_member(user_data['discord_id'])
                                        if member:
                                            await award_points(member, point_values[i], f"placing #{i+1} in the {comp['title']} SOTW")
                    cursor.execute("UPDATE active_competitions SET winners_awarded = TRUE WHERE id = %s", (comp['id'],))
                
                # --- Send Reminders ---
                elif not comp['final_ping_sent'] and (ends_at - now) <= timedelta(hours=1) and now < ends_at:
                    reminder_embed = discord.Embed(title="⏳ Final Hour!", description=f"The **{comp['title']}** competition ends in less than an hour!", color=discord.Color.red(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                    await sotw_channel.send(content="@everyone", embed=reminder_embed)
                    cursor.execute("UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = %s", (comp['id'],))
                elif not comp['midway_ping_sent'] and now >= starts_at + ((ends_at - starts_at) / 2) and now < ends_at:
                    midway_embed = discord.Embed(title="½ Midway Point Reached!", description=f"The **{comp['title']}** competition is halfway through!", color=discord.Color.yellow(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                    await sotw_channel.send(embed=midway_embed)
                    cursor.execute("UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = %s", (comp['id'],))
            
            conn.commit(); cursor.close(); conn.close()
    except Exception as e:
        print(f"ERROR in event_manager (SOTW): {e}")
    
    # --- Raffle Drawing & Reminders (Isolated) ---
    try:
        raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
        if raffle_channel:
            conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT * FROM raffles WHERE winner_id IS NULL")
            active_raffles = cursor.fetchall()
            for raffle in active_raffles:
                ends_at = raffle['ends_at']
                if now >= ends_at:
                    await draw_raffle_winner(raffle_channel, raffle['id'])
                elif not raffle['final_ping_sent'] and (ends_at - now) <= timedelta(days=1):
                    embed = discord.Embed(title="🎟️ Raffle Ending Soon!", description=f"There are only **24 hours left** to enter the raffle for a **{raffle['prize']}**!", color=discord.Color.orange())
                    await raffle_channel.send(content="@everyone", embed=embed)
                    cursor.execute("UPDATE raffles SET final_ping_sent = TRUE WHERE id = %s", (raffle['id'],))
            conn.commit(); cursor.close(); conn.close()
    except Exception as e:
        print(f"ERROR in event_manager (Raffles): {e}")

    # --- Bingo Reminders (Isolated) ---
    try:
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if bingo_channel:
            conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT * FROM bingo_events WHERE ends_at > NOW()")
            active_bingo = cursor.fetchone() # Assuming only one bingo at a time
            if active_bingo:
                ends_at = active_bingo['ends_at']; starts_at = active_bingo['starts_at']
                if not active_bingo['final_ping_sent'] and (ends_at - now) <= timedelta(days=1):
                    embed = discord.Embed(title="🧩 Bingo Ending Soon!", description="There's only **24 hours left** to complete your bingo tasks! Submit your entries now!", color=discord.Color.orange())
                    await bingo_channel.send(content="@everyone", embed=embed)
                    cursor.execute("UPDATE bingo_events SET final_ping_sent = TRUE WHERE id = %s", (active_bingo['id'],))
                elif not active_bingo['midway_ping_sent'] and now >= starts_at + ((ends_at - starts_at) / 2):
                    embed = discord.Embed(title="½ Bingo Midway Point!", description="The clan bingo is halfway through! Keep up the great work and let's see those completed boards!", color=discord.Color.yellow())
                    await bingo_channel.send(embed=embed)
                    cursor.execute("UPDATE bingo_events SET midway_ping_sent = TRUE WHERE id = %s", (active_bingo['id'],))
            conn.commit(); cursor.close(); conn.close()
    except Exception as e:
        print(f"ERROR in event_manager (Bingo): {e}")

    # --- Giveaway Drawing (Isolated) ---
    try:
        giveaway_channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
        if giveaway_channel:
            conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT * FROM giveaways WHERE winner_id IS NULL AND ends_at <= NOW()")
            ended_giveaways = cursor.fetchall()
            for giveaway_data in ended_giveaways:
                await draw_giveaway_winner(giveaway_channel, giveaway_data['id'])
            conn.commit(); cursor.close(); conn.close()
    except Exception as e:
        print(f"ERROR in event_manager (Giveaways): {e}")


# --- Web Server for Hosting ---
async def handle_http(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 10000)) # Use port 10000 for Render
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
    setup_database()
    event_manager.start()
    daily_event_summary.start() # Start the new daily summary task
    bot.add_view(SubmissionView())
    await bot.sync_commands()
@bot.event
async def on_message(message):
    if message.author.bot:
        return # Ignore messages from the bot itself

    # A list of trigger phrases for the PVM helper
    trigger_phrases = ["what gear for", "setup for", "inventory for"]
    
    # Check if the message content starts with any of the trigger phrases
    if any(message.content.lower().startswith(phrase) for phrase in trigger_phrases):
        # The user's actual question is the part after the trigger phrase
        question = message.content
        
        async with message.channel.typing():
            prompt = f"""
            You are an expert Old School RuneScape (OSRS) player acting as a helpful clan assistant.
            A user is asking for a gear or inventory setup. Your response should be clear, concise, and formatted for Discord.
            Use Discord markdown like **bold** for items and new lines for lists.
            Provide a recommended setup for the following query: "{question}"
            """
            try:
                response = await ai_model.generate_content_async(prompt)
                
                # Create a nicely formatted embed for the response
                embed = discord.Embed(
                    title=f"Gear & Inventory Guide",
                    description=response.text,
                    color=discord.Color.blue()
                )
                embed.set_footer(text=f"Guide for: {question}")
                
                await message.reply(embed=embed)
            except Exception as e:
                print(f"Error generating PVM guide: {e}")
                await message.reply("Sorry, I couldn't fetch a guide for that right now. Please try again later.")
    
    # This line is important to ensure slash commands still work
    # await bot.process_application_commands(message) -> This is deprecated and can cause issues

# --- BOT COMMANDS ---
sotw = bot.create_group("sotw", "Commands for Skill of the Week")
@sotw.command(name="start", description="Manually start a new SOTW competition.")
async def start(ctx, skill: discord.Option(str, choices=WOM_SKILLS), duration_days: discord.Option(int, default=7)):
    try:
        await ctx.defer(ephemeral=True)
        data, error = await create_competition(WOM_CLAN_ID, skill, duration_days)
        if error: await ctx.respond(error); return
        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            embed = await create_competition_embed(data, ctx.author)
            sotw_message = await sotw_channel.send(embed=embed)
            await send_global_announcement("sotw_start", {"skill": skill.capitalize(), "duration": f"{duration_days} days"}, sotw_message.jump_url)
            await ctx.respond("SOTW started successfully in the designated channel!", ephemeral=True)
        else:
            await ctx.respond("Error: SOTW Channel ID not configured correctly.", ephemeral=True)
    except discord.NotFound:
        print("ERROR: Interaction in /sotw start expired before it could be handled.")
    except Exception as e:
        print(f"Error in /sotw start: {e}")
        try:
            await ctx.respond("An unexpected error occurred while starting the SOTW.", ephemeral=True)
        except discord.NotFound:
            pass # Can't send error if interaction is gone

@sotw.command(name="poll", description="Start a poll to choose the next SOTW.")
@discord.default_permissions(manage_events=True)
async def poll(ctx: discord.ApplicationContext):
    if ctx.guild.id in bot.active_polls: return await ctx.respond("There is already an active SOTW poll.", ephemeral=True)
    poll_skills = random.sample(WOM_SKILLS, 6); view = SotwPollView(ctx.author); view.add_buttons(poll_skills)
    embed = await view.create_embed();
    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if sotw_channel:
        poll_message = await sotw_channel.send(embed=embed, view=view)
        await ctx.respond("SOTW Poll created!", ephemeral=True); view.message_id = poll_message.id
        bot.active_polls[ctx.guild.id] = view
    else:
        await ctx.respond("Error: SOTW Channel ID not configured correctly.", ephemeral=True)

@sotw.command(name="view", description="View the leaderboard for the current SOTW.")
async def view(ctx: discord.ApplicationContext):
    await ctx.defer()
    try:
        list_url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/competitions"
        async with aiohttp.ClientSession() as session:
            async with session.get(list_url) as response:
                if response.status != 200: return await ctx.respond("Could not fetch competition list.")
                competitions = await response.json()
                if not competitions: return await ctx.respond("This clan has no competitions on Wise Old Man.")
                latest_comp_id = competitions[0]['id']
        details_url = f"https://api.wiseoldman.net/v2/competitions/{latest_comp_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(details_url) as response:
                if response.status != 200: return await ctx.respond(f"Could not fetch details for competition ID {latest_comp_id}.")
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
        await ctx.respond(embed=embed)
    except Exception as e:
        print(f"Error in /sotw view: {e}")
        await ctx.respond("An error occurred while fetching the SOTW leaderboard.")

raffle = bot.create_group("raffle", "Commands for managing raffles.")
@raffle.command(name="start", description="Start a new raffle.")
@discord.default_permissions(manage_events=True)
async def start_raffle(ctx: discord.ApplicationContext, prize: discord.Option(str, "What is the prize?"), duration_days: discord.Option(float, "How many days will it last?")):
    # FIX: Added robust error handling for interaction timeouts.
    try:
        await ctx.defer(ephemeral=True)
        
        conn = get_db_connection(); cursor = conn.cursor()
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        cursor.execute("INSERT INTO raffles (prize, ends_at) VALUES (%s, %s) RETURNING id", (prize, ends_at.isoformat()))
        raffle_id = cursor.fetchone()[0]
        conn.commit(); cursor.close(); conn.close()
        
        duration_str = f"{int(duration_days)} day(s)" if duration_days >= 1 else f"{int(duration_days * 24)} hours"
        details = {"prize": prize, "duration": duration_str}
        ai_embed_data = await generate_announcement_json("raffle_start", details)
        embed = discord.Embed.from_dict(ai_embed_data)
        
        embed.add_field(name="How to Enter", value="Use `/raffle enter` to get a ticket! (Max 10 per person)", inline=False)
        embed.add_field(name="Raffle Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"Raffle ID: {raffle_id}")
        raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
        if raffle_channel:
            raffle_message = await raffle_channel.send(embed=embed)
            await send_global_announcement("raffle_start", details, raffle_message.jump_url)
            await ctx.respond("Raffle created successfully!", ephemeral=True)
        else:
            await ctx.respond("Error: Raffle Channel ID not configured correctly.", ephemeral=True)
    except discord.NotFound:
        print("ERROR: Interaction in /raffle start expired before it could be handled.")
    except Exception as e:
        print(f"Error in /raffle start: {e}")
        try:
            await ctx.respond(f"An unexpected error occurred. Please check the logs.", ephemeral=True)
        except discord.NotFound:
            print("ERROR: Interaction in /raffle start expired before error message could be sent.")


@raffle.command(name="enter", description="Get one ticket for the current raffle (max 10).")
async def enter_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close(); return await ctx.respond("There is no active raffle to enter right now.", ephemeral=True)
    
    raffle_id = raffle_data['id']
    cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND raffle_id = %s AND source = 'self'", (ctx.author.id, raffle_id))
    self_entries = cursor.fetchone()[0]
    if self_entries >= 10:
        cursor.close(); conn.close(); return await ctx.respond("You have already claimed your maximum of 10 tickets for this raffle!", ephemeral=True)
    
    cursor.execute("INSERT INTO raffle_entries (user_id, source, raffle_id) VALUES (%s, 'self', %s)", (ctx.author.id, raffle_id))
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND raffle_id = %s", (ctx.author.id, raffle_id))
    total_tickets = cursor.fetchone()[0]
    cursor.close(); conn.close()
    await ctx.respond(f"You have successfully claimed a ticket for the **{raffle_data['prize']}** raffle! You now have a total of {total_tickets} ticket(s).", ephemeral=True)

@raffle.command(name="give_tickets", description="ADMIN: Give raffle tickets to a member.")
@discord.default_permissions(manage_events=True)
async def give_tickets(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member to give tickets to."), amount: discord.Option(int, "How many tickets to give.", min_value=1)):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close(); return await ctx.respond("There is no active raffle.", ephemeral=True)
    
    raffle_id = raffle_data['id']
    entries = [(raffle_id, member.id, 'admin') for _ in range(amount)]
    cursor.executemany("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES (%s, %s, %s)", entries)
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND raffle_id = %s", (member.id, raffle_id))
    total_tickets = cursor.fetchone()[0]
    cursor.close(); conn.close()
    
    await ctx.respond(f"Successfully gave {amount} ticket(s) to {member.display_name} for the '{raffle_data['prize']}' raffle. They now have {total_tickets} ticket(s).", ephemeral=True)

@raffle.command(name="edit_tickets", description="ADMIN: Set a member's total ticket count for the active raffle.")
@discord.default_permissions(manage_events=True)
async def edit_tickets(
    ctx: discord.ApplicationContext,
    member: discord.Option(discord.Member, "The member whose tickets you want to edit."),
    new_total: discord.Option(int, "The new total number of tickets they should have.", min_value=0)
):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close(); return await ctx.respond("There is no active raffle.", ephemeral=True)
        
    raffle_id = raffle_data['id']
    cursor.execute("DELETE FROM raffle_entries WHERE user_id = %s AND raffle_id = %s", (member.id, raffle_id))
    
    if new_total > 0:
        entries = [(raffle_id, member.id, 'admin_edit') for _ in range(new_total)]
        cursor.executemany("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES (%s, %s, %s)", entries)
    
    conn.commit()
    cursor.close(); conn.close()
    
    await ctx.respond(f"Successfully set {member.display_name}'s ticket count to {new_total} for the '{raffle_data['prize']}' raffle.", ephemeral=True)

@raffle.command(name="view_tickets", description="View the current ticket count for all participants.")
async def view_tickets(ctx: discord.ApplicationContext):
    try:
        await ctx.defer()
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, prize FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        raffle_data = cursor.fetchone()
        if not raffle_data:
            cursor.close(); conn.close(); return await ctx.respond("There is no active raffle.")
        
        raffle_id, raffle_prize = raffle_data
        cursor.execute("SELECT user_id, COUNT(user_id) FROM raffle_entries WHERE raffle_id = %s GROUP BY user_id ORDER BY COUNT(user_id) DESC", (raffle_id,))
        entries = cursor.fetchall()
        cursor.close(); conn.close()

        embed = discord.Embed(title=f"🎟️ Raffle Tickets for '{raffle_prize}'", color=discord.Color.gold())
        if not entries:
            embed.description = "No tickets have been given out yet."
        else:
            description_lines = []
            for user_id, count in entries[:20]: # Show top 20
                # FIX: Use the bot's cache to get member names. This is fast and reliable.
                member = ctx.guild.get_member(user_id)
                member_name = member.display_name if member else f"User ID: {user_id}"
                description_lines.append(f"**{member_name}**: {count} ticket(s)")
            embed.description = "\n".join(description_lines)
        
        await ctx.respond(embed=embed)
    except Exception as e:
        print(f"ERROR in /raffle view_tickets: {e}")
        try:
            await ctx.respond("An error occurred while trying to fetch the ticket list. Please try again later.", ephemeral=True)
        except discord.NotFound:
            pass # Interaction already gone


@raffle.command(name="draw_now", description="ADMIN: Immediately ends the raffle and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not channel: return await ctx.respond("Error: Raffle channel not found.")
    
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    cursor.close(); conn.close()
    if not raffle_data:
        return await ctx.respond("There is no active raffle to draw.", ephemeral=True)
        
    result = await draw_raffle_winner(channel, raffle_data['id'])
    await ctx.respond(f"Successfully triggered winner drawing: {result}")

@raffle.command(name="cancel", description="ADMIN: Cancels the current raffle without drawing a winner.")
@discord.default_permissions(manage_events=True)
async def cancel_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data: cursor.close(); conn.close(); return await ctx.respond("There is no active raffle to cancel.")
    
    raffle_id = raffle_data[0]
    prize = raffle_data[1]
    
    # Using ON DELETE CASCADE is better, but this is a safe explicit delete
    cursor.execute("DELETE FROM raffle_entries WHERE raffle_id = %s", (raffle_id,))
    cursor.execute("DELETE FROM raffles WHERE id = %s", (raffle_id,))
    conn.commit(); cursor.close(); conn.close()
    
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if channel: await channel.send(f"The raffle for **{prize}** has been cancelled by an admin.")
    await ctx.respond("Raffle successfully cancelled.")

giveaway = bot.create_group("giveaway", "Commands for pick-a-number giveaways.")
@giveaway.command(name="start", description="ADMIN: Start a new pick-a-number giveaway.")
@discord.default_permissions(manage_events=True)
async def start_giveaway(
    ctx: discord.ApplicationContext, 
    prize: discord.Option(str, "What is the prize?"), 
    max_number: discord.Option(int, "The highest number a user can pick.", min_value=10),
    duration_days: discord.Option(float, "How many days the giveaway will last.")
):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM giveaways WHERE ends_at > NOW()")
    active_giveaway = cursor.fetchone()
    if active_giveaway:
        cursor.close(); conn.close()
        return await ctx.respond("There is already an active giveaway. Please wait for it to end before starting a new one.", ephemeral=True)

    ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    cursor.execute("INSERT INTO giveaways (prize, ends_at, max_number) VALUES (%s, %s, %s)", (prize, ends_at, max_number))
    conn.commit()
    cursor.close()
    conn.close()

    # Silent confirmation for testing
    # await ctx.respond(f"✅ **Giveaway started silently for testing.**\n**Prize:** {prize}\n**Max Number:** {max_number}\nThis event will now run in the background and a winner will be drawn automatically.", ephemeral=True)
    
    # --- To re-enable public announcements, uncomment the block below ---
    giveaway_channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if giveaway_channel:
        embed = discord.Embed(
            title="🎉 A New Giveaway Has Started! 🎉",
            description=f"We're giving away a **{prize}**!",
            color=discord.Color.dark_magenta()
        )
        embed.add_field(name="How to Enter", value=f"Pick a number between 1 and {max_number} using `/giveaway enter`.", inline=False)
        embed.add_field(name="Giveaway Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text="First come, first served for each number. Good luck!")
        await giveaway_channel.send(embed=embed)
        await ctx.respond("Giveaway created successfully!", ephemeral=True)
    else:
        await ctx.respond("Error: Giveaway channel not configured correctly.", ephemeral=True)


@giveaway.command(name="enter", description="Enter the current giveaway by picking a number.")
async def enter_giveaway(
    ctx: discord.ApplicationContext, 
    number: discord.Option(int, "Your lucky number! (Leave blank for a random one)", required=False) = None
):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM giveaways WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    giveaway_data = cursor.fetchone()

    if not giveaway_data:
        cursor.close(); conn.close()
        return await ctx.respond("There is no active giveaway to enter right now.", ephemeral=True)

    giveaway_id = giveaway_data['id']
    max_number = giveaway_data['max_number']

    # --- Logic for picking a random number ---
    if number is None:
        cursor.execute("SELECT chosen_number FROM giveaway_entries WHERE giveaway_id = %s", (giveaway_id,))
        taken_numbers = {row['chosen_number'] for row in cursor.fetchall()}
        all_possible_numbers = set(range(1, max_number + 1))
        available_numbers = list(all_possible_numbers - taken_numbers)

        if not available_numbers:
            cursor.close(); conn.close()
            return await ctx.respond("Sorry, all numbers for this giveaway have been taken!", ephemeral=True)
        
        number = random.choice(available_numbers)

    # --- Standard entry logic ---
    if not (1 <= number <= max_number):
        cursor.close(); conn.close()
        return await ctx.respond(f"That's not a valid number! Please pick a number between 1 and {max_number}.", ephemeral=True)

    try:
        cursor.execute(
            "INSERT INTO giveaway_entries (giveaway_id, user_id, chosen_number) VALUES (%s, %s, %s)",
            (giveaway_id, ctx.author.id, number)
        )
        conn.commit()
        await ctx.respond(f"Your entry for number **{number}** has been locked in. Good luck!", ephemeral=True)
    except psycopg2.IntegrityError as e:
        conn.rollback()
        if 'giveaway_entries_giveaway_id_chosen_number_key' in str(e):
            await ctx.respond(f"Sorry, the number **{number}** has already been taken! Please choose another.", ephemeral=True)
        elif 'giveaway_entries_giveaway_id_user_id_key' in str(e):
            await ctx.respond("You have already entered this giveaway! You can only pick one number.", ephemeral=True)
        else:
            await ctx.respond("An unexpected error occurred. Please try again.", ephemeral=True)
    finally:
        cursor.close()
        conn.close()

@giveaway.command(name="draw_now", description="ADMIN: Immediately ends the giveaway and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if not channel: return await ctx.respond("Error: Giveaway channel not found.")
    
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM giveaways WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    giveaway_data = cursor.fetchone()
    cursor.close(); conn.close()
    if not giveaway_data:
        return await ctx.respond("There is no active giveaway to draw.", ephemeral=True)
        
    await draw_giveaway_winner(channel, giveaway_data['id'])
    await ctx.respond(f"Successfully triggered winner drawing for the '{giveaway_data['prize']}' giveaway.")


events = bot.create_group("events", "View all active clan events.")
@events.command(name="view", description="Shows all currently active competitions, raffles, and bingo events.")
async def view_events(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Fetch all active events
    cursor.execute("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
    competitions = cursor.fetchall()
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
    raffles = cursor.fetchall()
    cursor.execute("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    bingo = cursor.fetchone()
    
    cursor.close(); conn.close()
    
    embed = discord.Embed(title="📅 Clan Event Status", description="Here's a look at all the events currently running.", color=discord.Color.blurple())
    
    # Competitions
    if competitions:
        comp_info = ""
        for comp in competitions:
            comp_ends_dt = comp['ends_at']
            comp_info += f"**Title:** [{comp['title']}](https://wiseoldman.net/competitions/{comp['id']})\n**Ends:** <t:{int(comp_ends_dt.timestamp())}:R>\n\n"
        embed.add_field(name="⚔️ Active Competitions", value=comp_info, inline=False)
    else:
        embed.add_field(name="⚔️ Active Competitions", value="There are no SOTW competitions currently running.", inline=False)
        
    # Raffles
    if raffles:
        raffle_info = ""
        for raf in raffles:
            raf_ends_dt = raf['ends_at']
            raffle_info += f"**Prize:** {raf['prize']}\n**Ends:** <t:{int(raf_ends_dt.timestamp())}:R>\n\n"
        embed.add_field(name="🎟️ Active Raffles", value=raffle_info, inline=False)
    else:
        embed.add_field(name="🎟️ Active Raffles", value="There are no raffles currently running.", inline=False)

    # Bingo
    if bingo:
        bingo_ends_dt = bingo['ends_at']
        bingo_url = f"https://discord.com/channels/{ctx.guild.id}/{BINGO_CHANNEL_ID}/{bingo['message_id']}"
        bingo_info = f"A clan-wide bingo is underway!\n**[Click here to see the board!]({bingo_url})**\nEnds: <t:{int(bingo_ends_dt.timestamp())}:R>"
        embed.add_field(name="🧩 Active Bingo", value=bingo_info, inline=False)
    else:
        embed.add_field(name="🧩 Active Bingo", value="There is no bingo event currently running.", inline=False)
        
    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.respond(embed=embed)

bingo = bot.create_group("bingo", "Commands for clan bingo events.")
@bingo.command(name="start", description="Start a new bingo event.")
@discord.default_permissions(manage_events=True)
async def start_bingo(ctx: discord.ApplicationContext, duration_days: discord.Option(int, "How many days the bingo event will last.")):
    try:
        await ctx.defer(ephemeral=True)
        # Send an initial response so the user knows something is happening
        await ctx.respond("The Taskmaster is forging a new challenge... This may take a moment.", ephemeral=True)
        
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
        
        image_path, error = generate_bingo_image(board_tasks)
        if error: return await ctx.edit(content=f"Failed to generate bingo image: {error}")
        
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if not bingo_channel: return await ctx.edit(content="Error: Bingo Channel ID not configured correctly.")
        
        duration_str = f"{duration_days} day(s)"
        details = {"duration": duration_str}
        ai_embed_data = await generate_announcement_json("bingo_start", details)
        embed = discord.Embed.from_dict(ai_embed_data)
        file = discord.File(image_path, filename="bingo_board.png")
        embed.set_image(url="attachment://bingo_board.png")
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"Bingo started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        message = await bingo_channel.send(embed=embed, file=file)
        
        conn = get_db_connection(); cursor = conn.cursor()
        board_json = json.dumps(board_tasks)
        starts_at = datetime.now(timezone.utc)
        cursor.execute("INSERT INTO bingo_events (starts_at, ends_at, board_json, message_id) VALUES (%s, %s, %s, %s) RETURNING id", (starts_at, ends_at, board_json, message.id))
        bingo_id = cursor.fetchone()[0]
        conn.commit(); cursor.close(); conn.close()
        
        await send_global_announcement("bingo_start", details, message.jump_url)
        await ctx.edit(content=f"Bingo event #{bingo_id} created successfully!")
    except discord.NotFound:
        print("ERROR: Interaction in /bingo start expired before it could be handled.")
    except Exception as e:
        print(f"Error in /bingo start: {e}")
        try:
            await ctx.edit(content=f"An unexpected error occurred: {e}")
        except discord.NotFound:
            pass # Can't send error if interaction is gone

@bingo.command(name="complete", description="Submit a task for bingo completion.")
async def complete_task(ctx: discord.ApplicationContext, task: discord.Option(str, "The name of the task you completed."), proof: discord.Option(str, "A URL link to a screenshot or video proof.")):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
    event_data = cursor.fetchone()
    if not event_data:
        cursor.close(); conn.close(); return await ctx.respond("There is no active bingo event.", ephemeral=True)
    
    bingo_id = event_data['id']
    board_tasks = json.loads(event_data['board_json'])
    task_names = [t['name'] for t in board_tasks]
    if task not in task_names:
        cursor.close(); conn.close(); return await ctx.respond("That task is not on the current bingo board.", ephemeral=True)

    cursor.execute("INSERT INTO bingo_submissions (user_id, task_name, proof_url, bingo_id) VALUES (%s, %s, %s, %s)", (ctx.author.id, task, proof, bingo_id))
    conn.commit(); cursor.close(); conn.close()
    await ctx.respond("Your submission has been sent to the admins for review!", ephemeral=True)

@bingo.command(name="submissions", description="ADMIN: View pending bingo task submissions.")
@discord.default_permissions(manage_events=True)
async def view_submissions(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM bingo_submissions WHERE status = 'pending'")
    pending = cursor.fetchall()
    cursor.close(); conn.close()
    if not pending:
        return await ctx.respond("There are no pending bingo submissions.", ephemeral=True)
    
    await ctx.respond("Here are the pending submissions:", ephemeral=True)
    for sub in pending:
        user = await bot.fetch_user(sub['user_id'])
        embed = discord.Embed(title="📝 Bingo Submission", description=f"**Task:** {sub['task_name']}", color=discord.Color.yellow())
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.add_field(name="Proof", value=f"[Click to view]({sub['proof_url']})", inline=False)
        embed.set_footer(text=f"Submission ID: {sub['id']}")
        await ctx.channel.send(embed=embed, view=SubmissionView(), ephemeral=True)

@bingo.command(name="board", description="View the current bingo board.")
async def view_board(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT message_id FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
    event_data = cursor.fetchone()
    cursor.close(); conn.close()
    if not event_data or not event_data[0]:
        return await ctx.respond("There is no active bingo board to display.")
    
    bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
    if bingo_channel:
        try:
            message = await bingo_channel.fetch_message(event_data[0])
            await ctx.respond(f"Here is the current bingo board: {message.jump_url}")
        except discord.NotFound:
            await ctx.respond("Could not find the original bingo board message.")
    else:
        await ctx.respond("Bingo channel not configured.")

@admin.command(name="announce", description="Send a message as the bot to a specific channel.")
@discord.default_permissions(manage_guild=True)
async def announce(ctx: discord.ApplicationContext, message: discord.Option(str, "The message to send."), channel: discord.Option(discord.TextChannel, "The channel to send to."), ping_everyone: discord.Option(bool, "Whether to ping @everyone.", default=False)):
    await ctx.defer(ephemeral=True)
    content = "@everyone" if ping_everyone else ""
    embed = discord.Embed(title="📢 Clan Announcement", description=message, color=discord.Color.orange())
    embed.set_footer(text=f"Message sent by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    try:
        await channel.send(content=content, embed=embed)
        await ctx.respond("Announcement sent successfully!", ephemeral=True)
    except discord.Forbidden:
        await ctx.respond("Error: I don't have permission to send messages in that channel.", ephemeral=True)
    except Exception as e:
        await ctx.respond(f"An unexpected error occurred: {e}", ephemeral=True)

@admin.command(name="manage_points", description="Add or remove Clan Points from a member.")
@discord.default_permissions(manage_guild=True)
async def manage_points(
    ctx: discord.ApplicationContext,
    member: discord.Option(discord.Member, "The member to manage points for."),
    action: discord.Option(str, "Whether to add or remove points.", choices=["add", "remove"]),
    amount: discord.Option(int, "The number of points to add or remove.", min_value=1),
    reason: discord.Option(str, "The reason for this point adjustment.")
):
    await ctx.defer(ephemeral=True)
    
    if action == "add":
        await award_points(member, amount, reason)
    else: # remove
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO clan_points (discord_id, points) VALUES (%s, 0) ON CONFLICT (discord_id) DO NOTHING", (member.id,))
        cursor.execute("UPDATE clan_points SET points = GREATEST(0, points - %s) WHERE discord_id = %s", (amount, member.id))
        conn.commit()
    
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT points FROM clan_points WHERE discord_id = %s", (member.id,))
    point_data = cursor.fetchone()
    new_balance = point_data[0] if point_data else 0
    cursor.close(); conn.close()

    await ctx.respond(f"Successfully updated {member.display_name}'s points. Their new balance is {new_balance}.", ephemeral=True)

@admin.command(name="award_sotw_winners", description="Manually award points for a past SOTW competition.")
@discord.default_permissions(manage_guild=True)
async def award_sotw_winners(ctx: discord.ApplicationContext, competition_id: discord.Option(int, "The ID of the competition from Wise Old Man.")):
    await ctx.defer(ephemeral=True)
    details_url = f"https://api.wiseoldman.net/v2/competitions/{competition_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(details_url) as response:
            if response.status != 200:
                return await ctx.respond(f"Could not fetch details for competition ID {competition_id}.")
            comp_data = await response.json()

    awarded_to = []
    point_values = [100, 50, 25]
    for i, participant in enumerate(comp_data.get('participations', [])[:3]):
        osrs_name = participant['player']['displayName']
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT discord_id FROM user_links WHERE osrs_name = %s", (osrs_name,))
        user_data = cursor.fetchone()
        conn.close()
        if user_data:
            member = ctx.guild.get_member(user_data[0])
            if member:
                await award_points(member, point_values[i], f"placing #{i+1} in the {comp_data['title']} SOTW")
                awarded_to.append(f"#{i+1}: {member.display_name} ({point_values[i]} points)")

    if not awarded_to:
        return await ctx.respond("No winners could be found or linked for that competition.")

    await ctx.respond("Successfully awarded points to:\n" + "\n".join(awarded_to))

@admin.command(name="guide", description="Shows a detailed guide on how to use admin commands.")
@discord.default_permissions(manage_guild=True)
async def admin_guide(ctx: discord.ApplicationContext):
    """Provides a user-friendly guide for admin commands."""
    await ctx.defer(ephemeral=True)

    embed = discord.Embed(
        title="👑 Admin Command Guide 👑",
        description="Here’s how to use the admin commands to run clan events. Don't worry, it's easy!",
        color=discord.Color.gold()
    )

    # SOTW Commands
    sotw_guide = """
    `1. /sotw poll`
    **What it does:** Starts a vote for the next Skill of the Week.
    **How to use:** Just type `/sotw poll`. The bot will pick 6 random skills and make a poll in the SOTW channel.
    
    `2. /sotw start`
    **What it does:** Manually starts a competition without a poll.
    **How to use:** Type `/sotw start`. It will ask you for the `skill` (e.g., Mining) and `duration_days` (usually 7).
    """
    embed.add_field(name="⚔️ Skill of the Week (SOTW)", value=textwrap.dedent(sotw_guide), inline=False)

    # Raffle Commands
    raffle_guide = """
    `1. /raffle start`
    **What it does:** Starts a new raffle for a prize.
    **How to use:** Type `/raffle start`. It will ask for the `prize` (e.g., "Dragon Warhammer") and `duration_days`.
    
    `2. /raffle give_tickets`
    **What it does:** Gives tickets to a player (e.g., if they paid you in-game).
    **How to use:** Type `/raffle give_tickets`. It will ask for the `member` (pick them from the list) and `amount` of tickets.
    
    `3. /raffle draw_now`
    **What it does:** Ends the current raffle and picks a winner immediately.
    **How to use:** Just type `/raffle draw_now`. Use this if you want to end a raffle early.
    """
    embed.add_field(name="🎟️ Raffles", value=textwrap.dedent(raffle_guide), inline=False)

    # Bingo Commands
    bingo_guide = """
    `1. /bingo start`
    **What it does:** Starts a new bingo game for the whole clan.
    **How to use:** Type `/bingo start`. It will ask for the `duration_days` (e.g., 14 or 30). The bot does the rest!
    
    `2. /bingo submissions`
    **What it does:** Shows you all the bingo tasks players have submitted for approval.
    **How to use:** Type `/bingo submissions`. The bot will show you the submissions with "Approve" and "Deny" buttons.
    """
    embed.add_field(name="🧩 Bingo", value=textwrap.dedent(bingo_guide), inline=False)
    
    # Other Admin Commands
    other_guide = """
    `1. /admin announce`
    **What it does:** Sends a message as the bot.
    **How to use:** Type `/admin announce`. It will ask for the `message`, the `channel` to send it to, and if you want to `ping_everyone`.
    
    `2. /admin manage_points`
    **What it does:** Lets you give or take away Clan Points from someone.
    **How to use:** Type `/admin manage_points`. It will ask for the `member`, the `action` (add or remove), the `amount`, and a `reason`.
    """
    embed.add_field(name="⚙️ Other Tools", value=textwrap.dedent(other_guide), inline=False)

    embed.set_footer(text="Just take it one step at a time. You got this!")
    await ctx.respond(embed=embed, ephemeral=True)


osrs = bot.create_group("osrs", "Commands related to your OSRS account.")
@osrs.command(name="link", description="Link your Discord account to your OSRS username.")
async def link(ctx: discord.ApplicationContext, username: discord.Option(str, "Your in-game RuneScape name.")):
    await ctx.defer(ephemeral=True)
    url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={username.replace(' ', '_')}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return await ctx.respond(f"Could not find '{username}' on the OSRS HiScores.", ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO user_links (discord_id, osrs_name) VALUES (%s, %s) ON CONFLICT (discord_id) DO UPDATE SET osrs_name = EXCLUDED.osrs_name", (ctx.author.id, username))
    conn.commit(); cursor.close(); conn.close()
    await ctx.respond(f"Success! Your Discord account has been linked to the OSRS name: **{username}**.", ephemeral=True)

points = bot.create_group("points", "Commands related to Clan Points.")
@points.command(name="view", description="Check your current Clan Point balance.")
async def view_points(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT points FROM clan_points WHERE discord_id = %s", (ctx.author.id,))
    point_data = cursor.fetchone()
    cursor.close(); conn.close()
    
    current_points = point_data[0] if point_data else 0
    await ctx.respond(f"You currently have **{current_points}** Clan Points.", ephemeral=True)

@points.command(name="leaderboard", description="View the Clan Points leaderboard.")
async def leaderboard(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT discord_id, points FROM clan_points ORDER BY points DESC LIMIT 10")
    leaders = cursor.fetchall()
    cursor.close(); conn.close()

    embed = discord.Embed(title="🏆 Clan Points Leaderboard 🏆", color=discord.Color.gold())
    if not leaders:
        embed.description = "No one has earned any points yet."
    else:
        description_lines = []
        for i, (user_id, points) in enumerate(leaders):
            rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
            # FIX: Use the bot's cache to get member names. This is fast and reliable.
            member = ctx.guild.get_member(user_id)
            member_name = member.display_name if member else f"User ID: {user_id}"
            description_lines.append(f"{rank_emoji} **{member_name}**: {points:,} points")
        embed.description = "\n".join(description_lines)
    
    await ctx.respond(embed=embed)

@bot.slash_command(name="help", description="Shows a list of all available commands.")
async def help(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    
    embed = discord.Embed(
        title="📜 GrazyBot Command List 📜",
        description="Here are all the commands you can use to manage clan events.",
        color=discord.Color.blurple()
    )

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
    
    await ctx.respond(embed=embed, ephemeral=True)

# --- Main Execution Block ---
async def run_bot():
    """A resilient function to start the bot and handle rate limits."""
    while True:
        try:
            await bot.start(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print("BOT is being rate-limited by Discord. Retrying in 5 minutes...")
                await asyncio.sleep(300) # Wait 5 minutes before trying to reconnect
            else:
                print(f"An unexpected HTTP error occurred with the bot: {e}")
                break # Exit on other HTTP errors
        except Exception as e:
            print(f"An unexpected error occurred while running the bot: {e}")
            break # Exit on other errors

async def main():
    web_task = asyncio.create_task(start_web_server())
    bot_task = asyncio.create_task(run_bot())
    await asyncio.gather(web_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())
