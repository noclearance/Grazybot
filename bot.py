import discord
from discord.ext import tasks
import os
from dotenv import load_dotenv
import aiohttp
from aiohttp import web
import asyncio
from datetime import datetime, timedelta, timezone
import random
import psycopg2
from psycopg2 import pool, extras
import json
import textwrap
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai
from io import BytesIO
from discord.commands import SlashCommandGroup, Option
import re
from urllib.parse import urlparse
import logging
from functools import wraps

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration & Setup ---
load_dotenv()

# Environment Variables
TOKEN = os.getenv('TOKEN')
WOM_CLAN_ID = int(os.getenv('WOM_CLAN_ID'))
WOM_VERIFICATION_CODE = os.getenv('WOM_VERIFICATION_CODE')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
MAIN_GUILD_ID = int(os.getenv('MAIN_GUILD_ID'))
DATABASE_URL = os.getenv('DATABASE_URL')

# File Paths
TASKS_FILE = "tasks.json"
BINGO_FONT_PATH = "arial.ttf" # Or any other .ttf font file you have access to

# Channel IDs
EVENTS_CHANNEL_ID= int(os.getenv('EVENTS_CHANNEL_ID'))
SOTW_CHANNEL_ID = int(os.getenv('SOTW_CHANNEL_ID'))
BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID'))
RAFFLE_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID'))
RECAP_CHANNEL_ID = int(os.getenv('RECAP_CHANNEL_ID'))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))
GIVEAWAY_CHANNEL_ID = ANNOUNCEMENTS_CHANNEL_ID # Assume announcements for giveaways as per original

# Configure the Gemini AI (for text)
genai.configure(api_key=GEMINI_API_KEY)
AI_MODEL = genai.GenerativeModel('gemini-1.0-pro')

# Gemini Prompts
PERSONA_PROMPT = """
You are TaskmasterGPT, the grandmaster of clan events for a Discord server.
Your tone is epic, engaging, slightly cheeky, and highly detailed. You are here to build excitement, rally the members with compelling narratives, and provide all necessary information with flair.
Your task is to generate a JSON object for a Discord embed with "title", "description", and "color" keys.
Use vivid language and Discord markdown like **bold** or *italics*. you can when use emojis if necessary or as applies/
Make every announcement sound like a legendary event is unfolding, providing rich, descriptive text for the "description" field. Aim for a few sentences or a short paragraph for the description, not just one short line.
"""

# WOM skill metrics
WOM_SKILLS = ["overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herblore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"]

# Bot Intents
intents = discord.Intents.default()
intents.members = True # Required for fetching members and their roles
intents.message_content = True # Needed for some potential future commands or features

# Bot Initialization
bot = discord.Bot(intents=intents, debug_guilds=[MAIN_GUILD_ID]) # Use MAIN_GUILD_ID for debug guilds
bot.item_mapping = {}
bot.active_polls = {} # Maps guild_id to SotwPollView instance

# Global DB Connection Pool
db_pool = None

# --- Database Setup ---
def get_db_pool():
    global db_pool
    if db_pool is None:
        url = urlparse(DATABASE_URL)
        db_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10, # Adjust max connections as needed
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            sslmode='require'
        )
        logger.info("Database connection pool initialized.")
    return db_pool

def run_sync_db_op(func):
    """Decorator to run synchronous DB operations in a separate thread."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
    return wrapper

# Replace your existing _sync_execute_db_query function with this one

@run_sync_db_op
def _sync_execute_db_query(query, params=None, fetchone=False, fetchall=False, commit=False, cursor_factory=None, use_execute_values=False):
    """Synchronous function to execute a database query from the pool."""
    conn = None
    cursor = None
    try:
        conn = get_db_pool().getconn()
        cursor = conn.cursor(cursor_factory=cursor_factory)
        
        # ADDED THIS LOGIC BLOCK
        if use_execute_values:
            extras.execute_values(cursor, query, params)
        else:
            cursor.execute(query, params)
            
        if commit:
            conn.commit()
        if fetchone:
            return cursor.fetchone()
        if fetchall:
            return cursor.fetchall()
        return None
    except Exception as e:
        logger.error(f"Database operation failed: {e}", exc_info=True)
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            get_db_pool().putconn(conn)

# Replace your existing execute_db_query function with this one
async def execute_db_query(query, params=None, fetchone=False, fetchall=False, commit=False, cursor_factory=None, use_execute_values=False):
    """Asynchronous wrapper for database queries."""
    return await _sync_execute_db_query(query, params, fetchone, fetchall, commit, cursor_factory, use_execute_values)


async def setup_database():
    """Initializes the database schema."""
    table_creations = [
        """CREATE TABLE IF NOT EXISTS active_competitions (
            id INTEGER PRIMARY KEY, title TEXT, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
            midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE, winners_awarded BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS raffles (
            id INTEGER PRIMARY KEY, prize TEXT, ends_at TIMESTAMPTZ, winner_id BIGINT
        )""",
        """CREATE TABLE IF NOT EXISTS raffle_entries (
            entry_id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, source TEXT DEFAULT 'self'
        )""",
        """CREATE TABLE IF NOT EXISTS bingo_events (
            id INTEGER PRIMARY KEY, ends_at TIMESTAMPTZ, board_json TEXT, message_id BIGINT
        )""",
        """CREATE TABLE IF NOT EXISTS bingo_submissions (
            id SERIAL PRIMARY KEY, user_id BIGINT, task_name TEXT, proof_url TEXT, status TEXT DEFAULT 'pending'
        )""",
        """CREATE TABLE IF NOT EXISTS bingo_completed_tiles (
            task_name TEXT PRIMARY KEY
        )""",
        """CREATE TABLE IF NOT EXISTS user_links (
            discord_id BIGINT PRIMARY KEY, osrs_name TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS clan_points (
            discord_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS rewards (
            id SERIAL PRIMARY KEY, reward_name TEXT NOT NULL UNIQUE, point_cost INTEGER NOT NULL,
            description TEXT, is_active BOOLEAN DEFAULT TRUE
        )""",
        """CREATE TABLE IF NOT EXISTS role_rewards (
            reward_id INTEGER PRIMARY KEY, role_id BIGINT NOT NULL,
            FOREIGN KEY (reward_id) REFERENCES rewards(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS redeem_transactions (
            transaction_id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, reward_id INTEGER NOT NULL,
            reward_name TEXT NOT NULL, point_cost INTEGER NOT NULL, redeemed_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS giveaways (
            message_id BIGINT PRIMARY KEY, channel_id BIGINT NOT NULL, prize TEXT NOT NULL,
            ends_at TIMESTAMPTZ NOT NULL, winner_count INTEGER NOT NULL, is_active BOOLEAN DEFAULT TRUE,
            role_id BIGINT
        )""",
        """CREATE TABLE IF NOT EXISTS giveaway_entries (
            entry_id SERIAL PRIMARY KEY, message_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
            UNIQUE (message_id, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS pvm_events (
            id SERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT, starts_at TIMESTAMPTZ NOT NULL,
            message_id BIGINT, channel_id BIGINT, reminder_sent BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS pvm_event_signups (
            event_id INTEGER REFERENCES pvm_events(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL, PRIMARY KEY (event_id, user_id)
        )"""
    ]
    for query in table_creations:
        await execute_db_query(query, commit=True)
    logger.info("Database setup complete.")


# --- All View Classes ---
class SotwPollView(discord.ui.View):
    def __init__(self, author: discord.Member, skills: list[str]):
        super().__init__(timeout=86400) # 24 hours
        self.author = author
        self.votes: dict[str, list[discord.User]] = {skill: [] for skill in skills}
        self.add_buttons(skills)

    def add_buttons(self, skills: list[str]):
        """Adds skill and finish buttons to the view."""
        for skill in skills:
            self.add_item(SotwButton(label=skill.capitalize(), custom_id=skill))
        self.add_item(FinishButton(label="Finish Poll & Start SOTW", custom_id="finish_poll"))

    async def create_embed(self) -> discord.Embed:
        """Creates the embed for the SOTW poll."""
        ai_embed_data = await generate_announcement_json("sotw_poll")
        vote_description = "\n\n**Current Votes:**\n"
        if not self.votes:
            vote_description += "No votes cast yet."
        else:
            for skill, voters in self.votes.items():
                vote_description += f"**{skill.capitalize()}**: {len(voters)} vote(s)\n"

        embed = discord.Embed.from_dict(ai_embed_data)
        embed.description += vote_description
        embed.set_footer(text=f"Poll started by {self.author.display_name}", icon_url=self.author.display_avatar.url)
        return embed

class SotwButton(discord.ui.Button):
    def __init__(self, label: str, custom_id: str):
        super().__init__(label=label, custom_id=custom_id, style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        view: SotwPollView = self.view # Type hint for better IDE support

        user_voted_for_current_skill = False
        user_previously_voted = False

        # Check if user already voted and remove their previous vote
        for skill_key, voters in view.votes.items():
            if interaction.user in voters:
                user_previously_voted = True
                if skill_key == self.custom_id:
                    voters.remove(interaction.user)
                    user_voted_for_current_skill = True
                else:
                    voters.remove(interaction.user)
                    # User voted for a different skill, remove old vote and add new
                    view.votes[self.custom_id].append(interaction.user)
                break

        if not user_previously_voted:
            # First time voting, add their vote
            view.votes[self.custom_id].append(interaction.user)
            await interaction.response.send_message(f"Your vote for **{self.label}** has been counted.", ephemeral=True)
        elif user_voted_for_current_skill:
            # User clicked the same button, so their vote was removed
            await interaction.response.send_message("Your vote has been removed.", ephemeral=True)
        else:
            # User changed their vote
            await interaction.response.send_message(f"Your vote has been changed to **{self.label}**.", ephemeral=True)

        new_embed = await view.create_embed()
        await interaction.message.edit(embed=new_embed, view=view)


class FinishButton(discord.ui.Button):
    def __init__(self, label: str, custom_id: str):
        super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        view: SotwPollView = self.view # Type hint for better IDE support

        if interaction.user.id != view.author.id:
            return await interaction.response.send_message("Only the poll starter can finish it.", ephemeral=True)

        if not any(v for v in view.votes.values()):
            return await interaction.response.send_message("No votes cast yet.", ephemeral=True)

        # Determine winner
        winner = max(view.votes, key=lambda k: len(view.votes[k]))
        if len(view.votes[winner]) == 0:
            return await interaction.response.send_message("No votes cast yet.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        # Create competition
        data, error = await create_competition(WOM_CLAN_ID, winner, 7)
        if error:
            logger.error(f"Failed to create WOM competition for {winner}: {error}")
            await interaction.followup.send(f"Poll finished, but failed to start for **{winner.capitalize()}**: {error}", ephemeral=True)
            return

        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            embed = await create_competition_embed(data, interaction.user, poll_winner=True)
            sotw_message = await sotw_channel.send(embed=embed)
            await send_global_announcement("sotw_start", {"skill": winner.capitalize()}, sotw_message.jump_url)
            await interaction.followup.send("Competition created in the SOTW channel!", ephemeral=True)
        else:
            logger.error(f"SOTW Channel ID {SOTW_CHANNEL_ID} not found.")
            await interaction.followup.send("Competition started, but failed to announce in Discord (SOTW Channel not found).", ephemeral=True)

        # Disable buttons and remove poll from active polls
        for item in view.children:
            item.disabled = True
        await interaction.message.edit(view=view)
        bot.active_polls.pop(interaction.guild.id, None)

class SubmissionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persistent view

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_submission")
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("Could not find submission details.", ephemeral=True)

        try:
            footer_text = interaction.message.embeds[0].footer.text
            submission_id = int(footer_text.split(": ")[1])
        except (IndexError, ValueError):
            return await interaction.response.send_message("Invalid submission ID in embed footer.", ephemeral=True)

        submission_data = await execute_db_query(
            "SELECT user_id, task_name FROM bingo_submissions WHERE id = %s AND status = 'pending'",
            (submission_id,), fetchone=True, cursor_factory=extras.DictCursor
        )

        if not submission_data:
            return await interaction.response.send_message("This submission was already handled or does not exist.", ephemeral=True)

        user_id, task_name = submission_data['user_id'], submission_data['task_name']

        await execute_db_query(
            "UPDATE bingo_submissions SET status = 'approved' WHERE id = %s",
            (submission_id,), commit=True
        )
        await execute_db_query(
            "INSERT INTO bingo_completed_tiles (task_name) VALUES (%s) ON CONFLICT (task_name) DO NOTHING",
            (task_name,), commit=True
        )

        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} approved.", ephemeral=True)

        member = interaction.guild.get_member(user_id)
        if member:
            await award_points(member, 25, f"completing the bingo task: '{task_name}'")
        else:
            logger.warning(f"Could not find member {user_id} to award points for bingo submission.")

        await update_bingo_board_post()

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_submission")
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.message or not interaction.message.embeds:
            return await interaction.response.send_message("Could not find submission details.", ephemeral=True)

        try:
            footer_text = interaction.message.embeds[0].footer.text
            submission_id = int(footer_text.split(": ")[1])
        except (IndexError, ValueError):
            return await interaction.response.send_message("Invalid submission ID in embed footer.", ephemeral=True)

        # Check if submission exists and is pending before denying
        existing_submission = await execute_db_query(
            "SELECT id FROM bingo_submissions WHERE id = %s AND status = 'pending'",
            (submission_id,), fetchone=True
        )

        if not existing_submission:
            return await interaction.response.send_message("This submission was already handled or does not exist.", ephemeral=True)

        await execute_db_query(
            "UPDATE bingo_submissions SET status = 'denied' WHERE id = %s",
            (submission_id,), commit=True
        )

        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} denied.", ephemeral=True)


class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None) # Persistent view
        self.message_id = message_id

    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.primary, custom_id="giveaway_entry_button")
    async def enter_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            result = await execute_db_query(
                "INSERT INTO giveaway_entries (message_id, user_id) VALUES (%s, %s) ON CONFLICT (message_id, user_id) DO NOTHING RETURNING entry_id",
                (self.message_id, interaction.user.id), fetchone=True, commit=True
            )
            if result:
                await interaction.response.send_message("You have successfully entered the giveaway!", ephemeral=True)
            else:
                await interaction.response.send_message("You have already entered this giveaway.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error adding giveaway entry for user {interaction.user.id} in message {self.message_id}: {e}", exc_info=True)
            await interaction.response.send_message("An error occurred while entering the giveaway.", ephemeral=True)


# --- Helper Functions ---
async def load_item_mapping():
    """Fetches the item name-to-ID mapping from the OSRS Cloud API on startup."""
    url = "https://prices.osrs.cloud/api/v1/latest/mapping"
    headers = {'User-Agent': 'GrazyBot/1.0'}
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
                data = await response.json()
                bot.item_mapping = {item['name'].lower(): item for item in data}
                logger.info(f"Successfully loaded {len(bot.item_mapping)} items in the background.")
    except asyncio.TimeoutError:
        logger.error("Error loading item mapping: The request timed out.")
    except aiohttp.ClientError as e:
        logger.error(f"Error loading item mapping from API: {e}")
    except Exception as e:
        logger.error(f"An unexpected exception occurred while loading item mapping: {e}", exc_info=True)

def format_price_timestamp(ts: int) -> str:
    """Formats a UNIX timestamp into a human-readable relative time string."""
    if not ts:
        return "N/A"
    dt_object = datetime.fromtimestamp(ts, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - dt_object
    if delta.total_seconds() < 60:
        return "just now"
    elif delta.total_seconds() < 3600:
        minutes = int(delta.total_seconds() / 60)
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    elif delta.total_seconds() < 86400:
        hours = int(delta.total_seconds() / 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    else:
        days = delta.days
        return f"{days} day{'s' if days > 1 else ''} ago"

def get_wom_metric_url(metric: str) -> str:
    """Generates a URL for a specific OSRS Wiki skill/activity icon."""
    base_url = "https://oldschool.runescape.wiki/images/"
    # Expanded icon map for more skills/bosses
    icon_map = {
        "overall": "Overall_icon.png", "attack": "Attack_icon.png", "strength": "Strength_icon.png",
        "defence": "Defence_icon.png", "hitpoints": "Hitpoints_icon.png", "ranged": "Ranged_icon.png",
        "prayer": "Prayer_icon.png", "magic": "Magic_icon.png", "cooking": "Cooking_icon.png",
        "woodcutting": "Woodcutting_icon.png", "fletching": "Fletching_icon.png", "fishing": "Fishing_icon.png",
        "firemaking": "Firemaking_icon.png", "crafting": "Crafting_icon.png", "smithing": "Smithing_icon.png",
        "mining": "Mining_icon.png", "herblore": "Herblore_icon.png", "agility": "Agility_icon.png",
        "thieving": "Thieving_icon.png", "slayer": "Slayer_icon.png", "farming": "Farming_icon.png",
        "runecraft": "Runecraft_icon.png", "hunter": "Hunter_icon.png", "construction": "Construction_icon.png",
        "vorkath": "Vorkath.png", "zulrah": "Zulrah.png", "chambers_of_xeric": "Olmlet.png",
        "tombs_of_amascut": "Tumeken's_guardian.png", "theatre_of_blood": "Sanguine_mutagen.png"
    }
    filename = icon_map.get(metric.lower().replace(" ", "_"), "Coins_10000.png")
    return f"{base_url}{filename}"

async def award_points(member: discord.Member, amount: int, reason: str):
    """Awards clan points to a member and sends them a DM."""
    if not member or member.bot:
        return

    try:
        # Upsert clan_points, then update and return new balance
        result = await execute_db_query(
            "INSERT INTO clan_points (discord_id, points) VALUES (%s, %s) ON CONFLICT (discord_id) DO UPDATE SET points = clan_points.points + EXCLUDED.points RETURNING points",
            (member.id, amount), fetchone=True, commit=True
        )
        new_balance = result[0] if result else amount # If no row existed, it's the initial amount

        details = {"amount": amount, "reason": reason}
        ai_dm_data = await generate_announcement_json("points_award", details)
        dm_embed = discord.Embed.from_dict(ai_dm_data)
        dm_embed.add_field(name="New Balance", value=f"You now have **{new_balance}** Clan Points.")
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        logger.warning(f"Could not send DM to {member.display_name} (they may have DMs disabled).")
    except Exception as e:
        logger.error(f"Failed to award points or send points DM to {member.display_name}: {e}", exc_info=True)

async def create_competition(clan_id: int, skill: str, duration_days: int) -> tuple[dict | None, str | None]:
    """Creates a new competition on Wise Old Man."""
    url = "https://api.wiseoldman.net/v2/competitions"
    start_date = datetime.now(timezone.utc) + timedelta(minutes=1) # Start slightly in the future
    end_date = start_date + timedelta(days=duration_days)

    payload = {
        "title": f"{skill.capitalize()} SOTW ({duration_days} days)",
        "metric": skill,
        "startsAt": start_date.isoformat(),
        "endsAt": end_date.isoformat(),
        "groupId": clan_id,
        "groupVerificationCode": WOM_VERIFICATION_CODE
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
                comp_data = await response.json()
                await execute_db_query(
                    "INSERT INTO active_competitions (id, title, starts_at, ends_at) VALUES (%s, %s, %s, %s)",
                    (comp_data['competition']['id'], comp_data['competition']['title'],
                     comp_data['competition']['startsAt'], comp_data['competition']['endsAt']), commit=True
                )
                return comp_data, None
    except aiohttp.ClientError as e:
        error_msg = f"WOM API Error: {e}"
        if response and response.status != 200:
            try:
                error_details = await response.json()
                error_msg = f"WOM API Error: {error_details.get('message', 'Unknown error')} (Status: {response.status})"
            except aiohttp.ContentTypeError:
                error_msg = f"WOM API Error: Non-JSON response (Status: {response.status})"
        logger.error(f"Failed to create competition: {error_msg}", exc_info=True)
        return None, error_msg
    except Exception as e:
        logger.error(f"An unexpected error occurred while creating competition: {e}", exc_info=True)
        return None, f"An unexpected error occurred: {e}"

async def create_competition_embed(data: dict, author: discord.Member, poll_winner: bool = False) -> discord.Embed:
    """Creates an embed for a new SOTW competition."""
    comp = data['competition']
    comp_id = comp['id']
    details = {"skill": comp['metric'].capitalize()}
    ai_embed_data = await generate_announcement_json("sotw_start", details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.url = f"https://wiseoldman.net/competitions/{comp_id}"
    start_dt = datetime.fromisoformat(comp['startsAt'].replace('Z', '+00:00'))
    end_dt = datetime.fromisoformat(comp['endsAt'].replace('Z', '+00:00'))

    embed.add_field(name="Skill", value=comp['metric'].capitalize(), inline=True)
    embed.add_field(name="Duration", value=f"{(end_dt - start_dt).days} days", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True) # Spacer
    embed.add_field(name="Start Time", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True)
    embed.add_field(name="End Time", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
    embed.set_footer(text=f"Competition started by {author.display_name}", icon_url=author.display_avatar.url)
    return embed

async def generate_recap_text(gains_data: list) -> str:
    """Generates a text recap using the Gemini AI model."""
    data_summary = ""
    for i, player in enumerate(gains_data[:10]):
        rank = i + 1
        username = player['player']['displayName']
        gained = player.get('gained', 0)
        data_summary += f"{rank}. {username}: {gained:,} XP\n"

    prompt = textwrap.dedent(f"""
    You are the Taskmaster for an Old School RuneScape clan. Your tone is formal and encouraging.
    Write a weekly recap based on the following data. Announce the top 3 with extra flair.
    Keep it to a few short paragraphs. Do not use emojis or markdown.
    Data:
    {data_summary}
    """)
    try:
        response = await AI_MODEL.generate_content_async(prompt)
        return response.text
    except Exception as e:
        logger.error(f"An error occurred with the Gemini API during recap generation: {e}", exc_info=True)
        return "The Taskmaster is currently reviewing the ledgers."

async def generate_announcement_json(event_type: str, details: dict = None) -> dict:
    """Generates a Discord embed JSON using the Gemini AI model based on event type."""
    details = details or {}
    fallback_data = {
        "sotw_poll": {"title": "📊 A Council of Skills is Convened!", "description": "The time has come, warriors! The council is convened to determine our next great trial. Which skill shall we dedicate ourselves to mastering in the coming week? Lend your voice and cast your vote below, for your decision holds the power to shape our destiny and focus our collective might!", "color": 15105600},
        "sotw_start": {"title": f"⚔️ The Trial of {details.get('skill', 'a new skill').capitalize()} Begins! ⚔️", "description": f"Hark, warriors! The clan has spoken, and the gauntlet is thrown! A grand trial of **{details.get('skill', 'a new skill').capitalize()}** commences now, a test of endurance and mastery. Dedicate yourselves to the grind, for the gods of skill observe! Prove your worth, rise through the ranks, and claim the champion's glory that awaits the victor!", "color": 5763719},
        "raffle_start": {"title": "🎟️ Fortune's Favor is Upon Us!", "description": f"Tremble before the whims of fate! The gods of chance have smiled upon our clan, bestowing upon us a grand raffle! A magnificent prize of **{details.get('prize', 'a grand prize')}** is at stake, a treasure worthy of legends. To claim your chance at this boon, simply utter the ancient command: `/raffle enter`. Your ticket to destiny awaits!", "color": 15844367},
        "giveaway_start": {"title": "🎁 A Gift to the Worthy! 🎁", "description": f"To honor your dedication, a new giveaway has commenced! Press the button below for a chance to claim the prize of **{details.get('prize', 'a fabulous prize')}**!", "color": 3066993},
        "bingo_start": {"title": "🧩 The Taskmaster's Gauntlet is Thrown! 🧩", "description": "Behold, warriors! The Taskmaster has unveiled a new challenge, a complex tapestry of trials designed to test the full breadth of your abilities! The clan bingo board awaits, filled with unique tasks that demand versatility and teamwork. Step forth, examine the challenges, and prove your mastery!", "color": 11027200},
        "points_award": {"title": "🏆 Your Renown Grows!", "description": f"Hark! For your commendable dedication in *{details.get('reason', 'your excellent performance')}*, your standing within the clan has increased! You have been awarded a significant **{details.get('amount', 'a number of')} Clan Points**! These points are a testament to your growing renown and can be exchanged for powerful boons and legendary artifacts within the clan's esteemed point store. Well done, warrior!", "color": 5763719},
        "default": {"title": "🎉 A New Calling!", "description": "A new event has begun! Answer the call.", "color": 3447003}
    }

    specific_prompt_map = {
        "sotw_poll": "Generate a detailed and engaging embed description for a new Skill of the Week poll. The description must implore the clan to lend their voice to the council, explaining that their choice will shape the clan's focus for the coming week. Frame it as a vital call to arms, emphasizing the importance of their vote in selecting the next skill challenge that will test their mettle and bring glory.",
        "sotw_start": f"Generate a detailed and engaging embed description announcing the triumphant start of a grand Skill of the Week competition for the skill: **{details.get('skill', 'a new skill').capitalize()}**. Describe it as a demanding trial of dedication and perseverance. Encourage all warriors to hone their craft in this specific skill, declaring that the ancient gods of skill are watching their every action. Announce clearly that immense glory and recognition await the champion who rises to the top of the leaderboard.",
        "raffle_start": f"Generate a detailed and engaging embed description for a new clan raffle. Describe the grand prize of **{details.get('prize', 'a grand prize')}** as a magnificent treasure or a legendary boon from the gods of fortune. Clearly and enticingly instruct members on how to enter by simply using the `/raffle enter` command, framing it as claiming their single, precious ticket to destiny and a chance at immense luck.",
        "giveaway_start": f"Generate an embed announcing a new giveaway for **{details.get('prize', 'a fabulous prize')}**. Frame it as a token of appreciation from the clan leadership. State that **{'a single victor' if details.get('winner_count', 1) == 1 else f'{details.get('winner_count', 1)} lucky victors'}** will be chosen. Instruct members to click the button below to enter for a chance to win.",
        "bingo_start": "Generate a detailed and engaging embed description announcing the commencement of a new clan bingo event. Describe it as a complex tapestry of diverse trials and unique challenges woven by the Taskmaster himself to test the clan's versatility, skill, and teamwork. Issue a clear challenge to the clan to prove their adaptability and work together by completing the various tasks laid out on the ancient, mystical board.",
        "points_award": f"Generate a detailed and engaging embed description for a private message to a member. Announce they have been awarded **{details.get('amount', 'a number of')} Clan Points** specifically for *{details.get('reason', 'your excellent performance')}*. Explain that these points are not mere tokens, but a tangible measure of their growing renown, dedication, and value to the clan, and that they can be traded for legendary artifacts, powerful boons, and exclusive privileges within the clan's esteemed point store."
    }

    specific_prompt = specific_prompt_map.get(event_type, "Generate a general event announcement.")
    full_prompt = f"{PERSONA_PROMPT}\n\nRequest: {specific_prompt}\n\nJSON Output:"

    try:
        response = await AI_MODEL.generate_content_async(full_prompt)
        clean_json_string = response.text.strip().lstrip("```json").rstrip("```")
        return json.loads(clean_json_string)
    except Exception as e:
        logger.error(f"An error occurred during Gemini JSON generation for '{event_type}': {e}", exc_info=True)
        return fallback_data.get(event_type, fallback_data["default"])

async def draw_raffle_winner(raffle_channel: discord.TextChannel):
    """Draws a winner for the current raffle and announces it."""
    raffle_data = await execute_db_query(
        "SELECT * FROM raffles WHERE winner_id IS NULL LIMIT 1",
        fetchone=True, cursor_factory=extras.DictCursor
    )
    if not raffle_data:
        return "No active raffle to draw."

    prize = raffle_data['prize']
    entries = await execute_db_query(
        "SELECT user_id FROM raffle_entries",
        fetchall=True, cursor_factory=extras.DictCursor
    )

    if not entries:
        await raffle_channel.send(f"The raffle for **{prize}** has ended, but alas, no one entered the contest of fate.")
        await execute_db_query("UPDATE raffles SET winner_id = 0 WHERE id = %s", (raffle_data['id'],), commit=True)
    else:
        winner_id = random.choice(entries)['user_id']
        winner_user = await bot.fetch_user(winner_id)

        if not winner_user:
            logger.warning(f"Raffle winner (ID: {winner_id}) not found, selecting another.")
            # Attempt to pick another winner if the first one cannot be fetched
            remaining_entries = [e for e in entries if e['user_id'] != winner_id]
            if remaining_entries:
                winner_id = random.choice(remaining_entries)['user_id']
                winner_user = await bot.fetch_user(winner_id)
            if not winner_user:
                await raffle_channel.send(f"The raffle for **{prize}** has ended. A winner was drawn but could not be found.")
                await execute_db_query("UPDATE raffles SET winner_id = 0 WHERE id = %s", (raffle_data['id'],), commit=True)
                return "Raffle ended, winner could not be fetched."

        await award_points(winner_user, 50, f"winning the raffle for {prize}")
        raffle_embed = discord.Embed(
            title="🎉 Raffle Winner Announcement! 🎉",
            description=f"The fates have chosen! Congratulations to {winner_user.mention}, you have won the raffle!",
            color=discord.Color.fuchsia()
        )
        raffle_embed.add_field(name="Prize", value=f"**{prize}**", inline=False)
        raffle_embed.set_footer(text="Thanks to everyone for participating!")
        raffle_embed.set_thumbnail(url=winner_user.display_avatar.url)
        await raffle_channel.send(embed=raffle_embed)

        announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
        if announcements_channel:
            announcement_embed = discord.Embed(
                title="🏆 A Champion of Fortune! 🏆",
                description=f"Let the entire clan celebrate! {winner_user.mention} has emerged victorious in the recent test of luck.",
                color=discord.Color.gold()
            )
            announcement_embed.add_field(name="Prize Claimed", value=f"The grand prize of **{prize}** is now theirs!", inline=False)
            announcement_embed.add_field(name="Bonus Reward", value="For this victory, they have also been granted **50 Clan Points**!", inline=False)
            announcement_embed.set_thumbnail(url=winner_user.display_avatar.url)
            announcement_embed.set_footer(text="May their luck inspire us all.")
            await announcements_channel.send(content=f"@everyone Congratulations to our winner, {winner_user.mention}!", embed=announcement_embed)

        await execute_db_query(
            "UPDATE raffles SET winner_id = %s WHERE id = %s",
            (winner_id, raffle_data['id']), commit=True
        )
    return f"Winner drawn for the '{prize}' raffle."

async def end_giveaway(giveaway_data: dict):
    """Handles the ending of a giveaway, drawing winners and updating messages."""
    message_id = giveaway_data['message_id']
    channel_id = giveaway_data['channel_id']
    prize = giveaway_data['prize']
    winner_count = giveaway_data['winner_count']
    role_id = giveaway_data.get('role_id')

    # Update giveaway status first
    await execute_db_query("UPDATE giveaways SET is_active = FALSE WHERE message_id = %s", (message_id,), commit=True)

    entries = await execute_db_query(
        "SELECT user_id FROM giveaway_entries WHERE message_id = %s",
        (message_id,), fetchall=True, cursor_factory=extras.DictCursor
    )
    user_ids = [entry['user_id'] for entry in entries]

    channel = bot.get_channel(channel_id)
    if not channel:
        logger.error(f"Error: Could not find channel {channel_id} for giveaway {message_id}")
        return

    guild = channel.guild # Guild should always be available if channel is found

    message = None
    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        logger.warning(f"Original giveaway message {message_id} not found in channel {channel_id}.")
    except discord.Forbidden:
        logger.warning(f"Missing permissions to fetch message {message_id} in channel {channel_id}.")
        return

    if not user_ids:
        no_entries_embed = discord.Embed(
            title="🎁 Giveaway Ended",
            description=f"The giveaway for **{prize}** has ended, but there were no entries.",
            color=discord.Color.dark_grey()
        )
        await channel.send(embed=no_entries_embed)
        if message:
            await message.edit(view=None)
        return

    num_to_select = min(winner_count, len(user_ids))
    winner_ids = random.sample(user_ids, k=num_to_select)

    winner_mentions = []
    for winner_id in winner_ids:
        try:
            member = guild.get_member(winner_id) or await guild.fetch_member(winner_id)
            winner_mentions.append(member.mention)
        except (discord.NotFound, discord.HTTPException):
            winner_mentions.append(f"<@{winner_id}> (User not found)")
            logger.warning(f"Could not fetch winner {winner_id} for giveaway {message_id}.")

    win_str = "Winner" if len(winner_mentions) == 1 else "Winners"
    announcement_embed = discord.Embed(
        title=f"🎉 Giveaway {win_str}! 🎉",
        description=f"Congratulations to {', '.join(winner_mentions)}! You have won the giveaway!",
        color=discord.Color.gold()
    )
    announcement_embed.add_field(name="Prize", value=f"**{prize}**", inline=False)

    role_to_award = guild.get_role(role_id) if role_id else None
    if role_to_award:
        successful_role_awards = []
        for winner_id in winner_ids:
            try:
                member = guild.get_member(winner_id) or await guild.fetch_member(winner_id)
                if member:
                    await member.add_roles(role_to_award)
                    successful_role_awards.append(member.display_name)
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.error(f"Failed to add role {role_to_award.name} to member {winner_id}: {e}")
        if successful_role_awards:
            announcement_embed.description += f"\nThey have also been awarded the **{role_to_award.name}** role!"
        else:
            announcement_embed.description += f"\nAttempted to award the **{role_to_award.name}** role, but encountered issues."

    await channel.send(content=f"Congratulations {', '.join(winner_mentions)}!", embed=announcement_embed)

    if message:
        ended_embed = message.embeds[0]
        ended_embed.title = "🎁 Giveaway Ended 🎁"
        ended_embed.color = discord.Color.dark_red()
        # Remove dynamic fields like "Ends In" and "Entries"
        ended_embed.fields = [field for field in ended_embed.fields if "Ends In" not in field.name and "Entries" not in field.name]
        ended_embed.add_field(name=f"{win_str}", value=', '.join(winner_mentions), inline=False)
        await message.edit(embed=ended_embed, view=None)

def parse_duration(duration_str: str) -> timedelta | None:
    """Parses a duration string (e.g., '7d', '12h', '30m') into a timedelta object."""
    match = re.match(r"(\d+)([mhd])", duration_str.lower())
    if not match:
        return None
    value, unit = match.groups()
    value = int(value)
    if unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'h':
        return timedelta(hours=value)
    elif unit == 'd':
        return timedelta(days=value)
    return None

@run_sync_db_op
def _sync_generate_bingo_image(tasks: list[dict], completed_tasks: list[str] = []) -> tuple[str | None, str | None]:
    """Synchronous image generation for bingo board using PIL."""
    try:
        width, height = 1000, 1000
        background_color = (40, 26, 13) # Darker, desaturated brown
        img = Image.new('RGB', (width, height), background_color)
        draw = ImageDraw.Draw(img)

        try:
            # Attempt to load a better font for readability
            title_font = ImageFont.truetype(BINGO_FONT_PATH, 48)
            task_font = ImageFont.truetype(BINGO_FONT_PATH, 24)
        except IOError:
            logger.warning(f"Font file '{BINGO_FONT_PATH}' not found. Using default PIL font.")
            title_font = ImageFont.load_default(size=48)
            task_font = ImageFont.load_default(size=24)

        # Title
        title_text = "CLAN BINGO"
        bbox = draw.textbbox((0,0), title_text, font=title_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        draw.text(((width - text_width) / 2, 50 - text_height / 2), title_text, font=title_font, fill=(255, 215, 0)) # Gold color

        grid_size = 5
        cell_size = 170 # Each cell will be 170x170
        margin = (width - grid_size * cell_size) // 2 # Center the grid

        # Adjust y-offset for grid to be below title
        grid_start_y = 100 + text_height + 20 # Below title with some padding

        line_color = (255, 215, 0) # Gold
        line_width = 3

        # Draw grid lines
        for i in range(grid_size + 1):
            draw.line([(margin + i * cell_size, grid_start_y),
                       (margin + i * cell_size, grid_start_y + grid_size * cell_size)],
                      fill=line_color, width=line_width)
            draw.line([(margin, grid_start_y + i * cell_size),
                       (margin + grid_size * cell_size, grid_start_y + i * cell_size)],
                      fill=line_color, width=line_width)

        # Draw tasks
        for i, task in enumerate(tasks):
            if i >= grid_size * grid_size: # Limit to 25 tasks for a 5x5 grid
                break

            row = i // grid_size
            col = i % grid_size

            cell_x = margin + col * cell_size
            cell_y = grid_start_y + row * cell_size

            # Draw green overlay for completed tasks
            if task['name'] in completed_tasks:
                overlay = Image.new('RGBA', (cell_size, cell_size), (0, 255, 0, 90)) # Green with 90 alpha
                img.paste(overlay, (cell_x, cell_y), overlay)

            task_name = task['name']
            wrapped_text = textwrap.fill(task_name, width=20) # Adjust width for better wrapping

            # Calculate text position to center it
            text_bbox = draw.textbbox((0,0), wrapped_text, font=task_font, align="center")
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]

            text_x = cell_x + (cell_size - text_width) / 2
            text_y = cell_y + (cell_size - text_height) / 2

            draw.text((text_x, text_y), wrapped_text, font=task_font, fill=(255, 255, 255), align="center")

        output_path = "bingo_board.png"
        img.save(output_path)
        return output_path, None
    except Exception as e:
        logger.error(f"An unexpected error occurred during bingo image generation: {e}", exc_info=True)
        return None, f"An unexpected error occurred during image generation: {e}"


async def update_bingo_board_post():
    """Updates the existing bingo board message with the latest image."""
    event_data = await execute_db_query(
        "SELECT board_json, message_id FROM bingo_events LIMIT 1",
        fetchone=True
    )
    if not event_data:
        logger.info("No active bingo event found to update.")
        return

    board_tasks = json.loads(event_data[0])
    message_id = event_data[1]

    completed_tiles = await execute_db_query(
        "SELECT task_name FROM bingo_completed_tiles",
        fetchall=True
    )
    completed_tiles = [row[0] for row in completed_tiles]

    image_path, error = await _sync_generate_bingo_image(board_tasks, completed_tiles) # Run blocking image generation in executor
    if error:
        logger.error(f"Failed to update bingo board image: {error}")
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
            logger.info(f"Bingo board message {message_id} updated successfully.")
        else:
            logger.error(f"Bingo channel {BINGO_CHANNEL_ID} not found to update message.")
    except discord.NotFound:
        logger.warning(f"Could not find bingo message {message_id} to update. It might have been deleted.")
    except discord.Forbidden:
        logger.error(f"Missing permissions to fetch or edit bingo message {message_id} in channel {BINGO_CHANNEL_ID}.")
    except Exception as e:
        logger.error(f"Error updating bingo board message: {e}", exc_info=True)

async def send_global_announcement(event_type: str, details: dict, message_url: str):
    """Sends a formatted announcement to the designated announcements channel."""
    announcement_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not announcement_channel:
        logger.error("Error: Global announcements channel not found.")
        return

    ai_embed_data = await generate_announcement_json(event_type, details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.url = message_url # Make the title clickable
    # Replace or add a field with a direct link if description doesn't already contain it prominently
    if "Click here to view the event" not in embed.description:
        embed.add_field(name="Event Link", value=f"[Click here to view the event!]({message_url})", inline=False)
    embed.set_footer(text="A new clan event has started!")

    try:
        await announcement_channel.send(content="@everyone", embed=embed)
        logger.info(f"Global announcement for '{event_type}' sent successfully.")
    except discord.Forbidden:
        logger.error(f"Missing permissions to send announcement in channel {ANNOUNCEMENTS_CHANNEL_ID}.")
    except Exception as e:
        logger.error(f"Error sending global announcement for '{event_type}': {e}", exc_info=True)


# --- Event Manager & Periodic Reminder Tasks ---
@tasks.loop(hours=4)
async def periodic_event_reminder():
    await bot.wait_until_ready()
    announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not announcements_channel:
        logger.warning("Cannot send periodic reminder: Announcements channel not found.")
        return

    sotw = await execute_db_query(
        "SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1",
        fetchone=True, cursor_factory=extras.DictCursor
    )
    raffle = await execute_db_query(
        "SELECT * FROM raffles WHERE ends_at > NOW() AND winner_id IS NULL LIMIT 1",
        fetchone=True, cursor_factory=extras.DictCursor
    )
    giveaway = await execute_db_query(
        "SELECT * FROM giveaways WHERE ends_at > NOW() AND is_active = TRUE LIMIT 1",
        fetchone=True, cursor_factory=extras.DictCursor
    )

    event_summary = ""
    if sotw:
        event_summary += f"- A Skill of the Week competition for **{sotw['title']}** is underway! (Ends <t:{int(sotw['ends_at'].timestamp())}:R>)\n"
    if raffle:
        event_summary += f"- A raffle for the legendary **{raffle['prize']}** is active! Use `/raffle enter` to participate. (Ends <t:{int(raffle['ends_at'].timestamp())}:R>)\n"
    if giveaway:
        giveaway_channel = bot.get_channel(giveaway['channel_id'])
        if giveaway_channel:
            event_summary += f"- A giveaway for **{giveaway['prize']}** is happening now in {giveaway_channel.mention}! Find the message and click the button to enter. (Ends <t:{int(giveaway['ends_at'].timestamp())}:R>)\n"
        else:
             event_summary += f"- A giveaway for **{giveaway['prize']}** is happening now! Find the message and click the button to enter. (Ends <t:{int(giveaway['ends_at'].timestamp())}:R>)\n"

    if not event_summary:
        logger.info("No active events for periodic reminder.")
        return

    prompt = textwrap.dedent(f"""
    You are TaskmasterGPT, the wise and ancient lore-keeper for a clan of warriors.
    Your task is to write a bulletin summarizing the clan's active events. Your tone is epic, grand, and encouraging.
    Use the following information to compose your message. Frame it as a call to continue the good fight and remind everyone of the glories to be won.
    Active Events:
    {event_summary}
    Write a compelling summary in a few short paragraphs.
    """)
    try:
        response = await AI_MODEL.generate_content_async(prompt)
        description = response.text
        embed = discord.Embed(title="📜 The Taskmaster's Bulletin 📜", description=description, color=discord.Color.dark_gold())
        embed.set_footer(text="Seize the day, warriors!")
        await announcements_channel.send(embed=embed)
        logger.info("Periodic event reminder sent successfully.")
    except Exception as e:
        logger.error(f"Failed to generate or send periodic event reminder: {e}", exc_info=True)

@tasks.loop(minutes=5)
async def event_manager():
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)

    # --- Weekly Recap ---
    recap_channel = bot.get_channel(RECAP_CHANNEL_ID)
    # Check for Sunday 19:00 UTC (7 PM UTC) - to send recap
    if recap_channel and now.weekday() == 6 and now.hour == 19 and now.minute >= 0 and now.minute < 5:
        logger.info("Attempting to send weekly recap.")
        url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/gained?period=week&metric=overall"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json()
                    recap_text = await generate_recap_text(data)
                    embed = discord.Embed(
                        title="📈 Weekly Recap from the Taskmaster",
                        description=recap_text,
                        color=discord.Color.from_rgb(100, 150, 255)
                    )
                    embed.set_footer(text=f"Recap for the week ending {now.strftime('%B %d, %Y')}")
                    await recap_channel.send(embed=embed)
                    logger.info("Weekly recap sent successfully.")
            except aiohttp.ClientError as e:
                logger.error(f"Error fetching WOM data for weekly recap: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"An unexpected error occurred during weekly recap: {e}", exc_info=True)

    # --- SOTW Competition Management ---
    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if sotw_channel:
        competitions = await execute_db_query(
            "SELECT * FROM active_competitions",
            fetchall=True, cursor_factory=extras.DictCursor
        )
        for comp in competitions:
            ends_at = comp['ends_at']
            starts_at = comp['starts_at']

            # Award winners after competition ends
            if now > ends_at and not comp['winners_awarded']:
                logger.info(f"SOTW competition {comp['id']} ({comp['title']}) ended. Awarding points.")
                details_url = f"https://api.wiseoldman.net/v2/competitions/{comp['id']}"
                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.get(details_url) as response:
                            response.raise_for_status()
                            comp_data = await response.json()
                            point_values = [100, 50, 25] # 1st, 2nd, 3rd

                            for i, participant in enumerate(comp_data.get('participations', [])[:3]):
                                osrs_name = participant['player']['displayName']
                                user_data = await execute_db_query(
                                    "SELECT discord_id FROM user_links WHERE osrs_name ILIKE %s",
                                    (osrs_name,), fetchone=True
                                )
                                if user_data:
                                    member = bot.get_guild(MAIN_GUILD_ID).get_member(user_data[0])
                                    if member:
                                        await award_points(member, point_values[i], f"placing #{i+1} in the {comp['title']} SOTW")
                                    else:
                                        logger.warning(f"SOTW winner {osrs_name} (Discord ID: {user_data[0]}) not found in guild to award points.")
                                else:
                                    logger.info(f"SOTW winner {osrs_name} is not linked to a Discord account.")
                    except aiohttp.ClientError as e:
                        logger.error(f"Error fetching WOM competition details for {comp['id']}: {e}", exc_info=True)
                    except Exception as e:
                        logger.error(f"An unexpected error occurred during SOTW winner awarding: {e}", exc_info=True)

                await execute_db_query(
                    "UPDATE active_competitions SET winners_awarded = TRUE WHERE id = %s",
                    (comp['id'],), commit=True
                )
                logger.info(f"SOTW competition {comp['id']} winners awarded status updated.")

            # Final hour reminder
            if not comp['final_ping_sent'] and (ends_at - now) <= timedelta(hours=1) and now < ends_at:
                reminder_embed = discord.Embed(
                    title="⏳ Final Hour!",
                    description=f"The **{comp['title']}** competition ends in less than an hour! Get those last gains in!",
                    color=discord.Color.red(),
                    url=f"https://wiseoldman.net/competitions/{comp['id']}"
                )
                await sotw_channel.send(content="@everyone", embed=reminder_embed)
                await execute_db_query(
                    "UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = %s",
                    (comp['id'],), commit=True
                )
                logger.info(f"Sent final ping for SOTW competition {comp['id']}.")

            # Midway reminder
            elif not comp['midway_ping_sent'] and now >= starts_at + ((ends_at - starts_at) / 2) and now < ends_at:
                midway_embed = discord.Embed(
                    title="½ Midway Point Reached!",
                    description=f"The **{comp['title']}** competition is halfway through! Keep up the grind!",
                    color=discord.Color.yellow(),
                    url=f"https://wiseoldman.net/competitions/{comp['id']}"
                )
                await sotw_channel.send(embed=midway_embed)
                await execute_db_query(
                    "UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = %s",
                    (comp['id'],), commit=True
                )
                logger.info(f"Sent midway ping for SOTW competition {comp['id']}.")

    # --- Raffle Management ---
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if raffle_channel:
        raffle_data = await execute_db_query(
            "SELECT * FROM raffles WHERE ends_at < %s AND winner_id IS NULL LIMIT 1",
            (now,), fetchone=True, cursor_factory=extras.DictCursor
        )
        if raffle_data:
            logger.info(f"Raffle {raffle_data['id']} ({raffle_data['prize']}) has ended. Drawing winner.")
            await draw_raffle_winner(raffle_channel)

    # --- Giveaway Management ---
    ended_giveaways = await execute_db_query(
        "SELECT * FROM giveaways WHERE ends_at < %s AND is_active = TRUE",
        (now,), fetchall=True, cursor_factory=extras.DictCursor
    )
    for giveaway in ended_giveaways:
        logger.info(f"Giveaway {giveaway['message_id']} ({giveaway['prize']}) has ended. Processing.")
        await end_giveaway(giveaway)

    # Update active giveaway entry counts
    active_giveaways = await execute_db_query(
        "SELECT message_id, channel_id FROM giveaways WHERE is_active = TRUE",
        fetchall=True, cursor_factory=extras.DictCursor
    )
    for giveaway in active_giveaways:
        try:
            entry_count_result = await execute_db_query(
                "SELECT COUNT(user_id) FROM giveaway_entries WHERE message_id = %s",
                (giveaway['message_id'],), fetchone=True
            )
            entry_count = entry_count_result[0]

            channel = bot.get_channel(giveaway['channel_id'])
            if not channel:
                logger.warning(f"Giveaway channel {giveaway['channel_id']} not found for message {giveaway['message_id']}.")
                continue

            message = await channel.fetch_message(giveaway['message_id'])
            embed = message.embeds[0]

            entry_field_index = -1
            for i, field in enumerate(embed.fields):
                if "Entries" in field.name:
                    entry_field_index = i
                    break

            new_entry_text = f"👥 **Entries:** {entry_count}"
            if entry_field_index != -1:
                # Check if the value has actually changed before editing
                if embed.fields[entry_field_index].value != new_entry_text:
                    embed.set_field_at(entry_field_index, name="Entries", value=new_entry_text, inline=True)
                    await message.edit(embed=embed)
            elif len(embed.fields) < 3: # Only add if there's space for it (Discord embed field limit)
                embed.add_field(name="Entries", value=new_entry_text, inline=True)
                await message.edit(embed=embed)

        except discord.NotFound:
            logger.warning(f"Giveaway message {giveaway['message_id']} not found for update, marking as inactive.")
            await execute_db_query(
                "UPDATE giveaways SET is_active = FALSE WHERE message_id = %s",
                (giveaway['message_id'],), commit=True
            )
        except discord.Forbidden:
            logger.error(f"Missing permissions to fetch or edit giveaway message {giveaway['message_id']}.")
        except Exception as e:
            logger.error(f"Error updating giveaway entry count for {giveaway['message_id']}: {e}", exc_info=True)


async def handle_http(request):
    """A simple HTTP handler for health checks."""
    return web.Response(text="Bot is running.")

async def start_web_server():
    """Starts a simple aiohttp web server for health checks."""
    app = web.Application()
    app.router.add_get('/', handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        logger.info(f"Web server started on port {port}")
        # Keep the server running indefinitely
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info("Web server task cancelled.")
    except Exception as e:
        logger.error(f"Error starting web server: {e}", exc_info=True)
    finally:
        await runner.cleanup()


# --- BOT EVENTS ---
@bot.event
async def on_ready():
    logger.info(f"{bot.user} is online and ready!")

    # Initialize DB pool and setup database
    get_db_pool() # Initialize pool on ready
    await setup_database()

    # Launch item mapping loading as a background task
    asyncio.create_task(load_item_mapping())

    # Start periodic tasks
    event_manager.start()
    periodic_event_reminder.start()

    # Re-register persistent views
    bot.add_view(SubmissionView()) # SubmissionView is stateless, just needs to be added once

    active_giveaways = await execute_db_query(
        "SELECT message_id FROM giveaways WHERE is_active = TRUE AND ends_at > NOW()",
        fetchall=True, cursor_factory=extras.DictCursor
    )
    if active_giveaways:
        logger.info(f"Re-registering {len(active_giveaways)} active giveaway view(s)...")
        for gw in active_giveaways:
            bot.add_view(GiveawayView(message_id=gw['message_id']))
    logger.info("Persistent views re-registered.")


# --- BOT COMMANDS ---
admin = bot.create_group("admin", "Admin-only commands for managing the bot and server.")

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
        logger.info(f"Admin {ctx.author.display_name} sent an announcement to {channel.name}.")
    except discord.Forbidden:
        await ctx.respond("Error: I don't have permission to send messages in that channel.", ephemeral=True)
        logger.error(f"Bot missing permissions to send announcement in {channel.name}.")
    except Exception as e:
        await ctx.respond(f"An unexpected error occurred: {e}", ephemeral=True)
        logger.error(f"Error sending announcement: {e}", exc_info=True)

@admin.command(name="manage_points", description="Add or remove Clan Points from a member.")
@discord.default_permissions(manage_guild=True)
async def manage_points(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member to manage points for."), action: discord.Option(str, "Whether to add or remove points.", choices=["add", "remove"]), amount: discord.Option(int, "The number of points to add or remove.", min_value=1), reason: discord.Option(str, "The reason for this point adjustment.")):
    await ctx.defer(ephemeral=True)
    if action == "add":
        await award_points(member, amount, reason)
    else: # remove
        try:
            # Ensure the user has an entry, then update points, not going below zero
            await execute_db_query(
                "INSERT INTO clan_points (discord_id, points) VALUES (%s, 0) ON CONFLICT (discord_id) DO NOTHING",
                (member.id,), commit=True
            )
            await execute_db_query(
                "UPDATE clan_points SET points = GREATEST(0, points - %s) WHERE discord_id = %s",
                (amount, member.id), commit=True
            )
        except Exception as e:
            logger.error(f"Error removing points from {member.display_name}: {e}", exc_info=True)
            return await ctx.respond(f"An error occurred while removing points: {e}", ephemeral=True)

    point_data = await execute_db_query(
        "SELECT points FROM clan_points WHERE discord_id = %s",
        (member.id,), fetchone=True
    )
    new_balance = point_data[0] if point_data else 0
    await ctx.respond(f"Successfully updated {member.display_name}'s points. Their new balance is {new_balance}.", ephemeral=True)
    logger.info(f"Admin {ctx.author.display_name} {action}ed {amount} points for {member.display_name} (Reason: {reason}). New balance: {new_balance}.")

@admin.command(name="award_sotw_winners", description="Manually award points for a past SOTW competition.")
@discord.default_permissions(manage_guild=True)
async def award_sotw_winners(ctx: discord.ApplicationContext, competition_id: discord.Option(int, "The ID of the competition from Wise Old Man.")):
    await ctx.defer(ephemeral=True)
    details_url = f"https://api.wiseoldman.net/v2/competitions/{competition_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(details_url) as response:
                response.raise_for_status()
                comp_data = await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"Failed to fetch WOM competition details for manual award: {e}", exc_info=True)
        return await ctx.respond(f"Could not fetch details for competition ID {competition_id}.", ephemeral=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred fetching WOM competition details: {e}", exc_info=True)
        return await ctx.respond(f"An unexpected error occurred: {e}", ephemeral=True)

    awarded_to = []
    point_values = [100, 50, 25] # 1st, 2nd, 3rd

    for i, participant in enumerate(comp_data.get('participations', [])[:3]):
        osrs_name = participant['player']['displayName']
        user_data = await execute_db_query(
            "SELECT discord_id FROM user_links WHERE osrs_name ILIKE %s",
            (osrs_name,), fetchone=True
        )
        if user_data:
            member = ctx.guild.get_member(user_data[0])
            if member:
                await award_points(member, point_values[i], f"placing #{i+1} in the {comp_data['title']} SOTW (manual award)")
                awarded_to.append(f"#{i+1}: {member.display_name} ({point_values[i]} points)")
            else:
                awarded_to.append(f"#{i+1}: {osrs_name} (Discord user not found in guild)")
                logger.warning(f"SOTW winner {osrs_name} (Discord ID: {user_data[0]}) not found in guild to award points manually.")
        else:
            awarded_to.append(f"#{i+1}: {osrs_name} (Not linked to Discord)")
            logger.info(f"SOTW winner {osrs_name} is not linked to a Discord account for manual award.")

    if not awarded_to:
        return await ctx.respond("No winners could be found or linked for that competition.", ephemeral=True)

    await ctx.respond("Successfully awarded points to:\n" + "\n".join(awarded_to), ephemeral=True)
    logger.info(f"Admin {ctx.author.display_name} manually awarded SOTW winners for competition {competition_id}.")


@admin.command(name="check_items", description="Check the status of the OSRS item mapping.")
@discord.default_permissions(manage_guild=True)
async def check_items(ctx: discord.ApplicationContext):
    if bot.item_mapping:
        await ctx.respond(f"✅ The item list is loaded with **{len(bot.item_mapping)}** items.", ephemeral=True)
    else:
        await ctx.respond("❌ The item list is not loaded yet. Please check the logs for errors.", ephemeral=True)

ge = bot.create_group("ge", "Commands for the Grand Exchange.")

async def item_autocomplete(ctx: discord.AutocompleteContext):
    """Provides autocomplete suggestions for OSRS items."""
    query = ctx.value.lower()
    if not bot.item_mapping:
        return ["Item list loading... please wait."]
    if not query:
        # Return some popular items if no query
        popular_items = ["Twisted bow", "Scythe of vitur", "Abyssal whip", "Dragon claws", "Bandos chestplate"]
        return popular_items
    matches = [name.title() for name in bot.item_mapping.keys() if query in name] # 'in' for broader search
    return matches[:25] # Limit to Discord's autocomplete max

@ge.command(name="price", description="Check the Grand Exchange price of an item.")
async def price(ctx: discord.ApplicationContext, item: discord.Option(str, "The name of the item to check.", autocomplete=item_autocomplete)):
    if not bot.item_mapping:
        return await ctx.respond("The item list is still loading from the server. Please wait a few more seconds and try again.", ephemeral=True)
    await ctx.defer()
    item_name_lower = item.lower()

    # Find the best match, prioritizing exact match or startswith
    item_details = bot.item_mapping.get(item_name_lower)
    if not item_details:
        # Fallback to a broader 'in' search if direct match fails
        found_keys = [k for k in bot.item_mapping.keys() if item_name_lower in k]
        if found_keys:
            item_details = bot.item_mapping[found_keys[0]] # Take the first match
        else:
            return await ctx.respond(f"Could not find an item matching '{item}'. Please try selecting from the autocomplete suggestions.", ephemeral=True)

    item_id = item_details['id']
    url = f"https://prices.osrs.cloud/api/v1/latest/item/{item_id}"
    headers = {'User-Agent': 'GrazyBot/1.0'} # Custom User-Agent for API
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                price_data = await response.json()

                embed = discord.Embed(
                    title=f"Price Check: {item_details['name']}",
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc)
                )
                icon_url = item_details.get('icon')
                if icon_url:
                    embed.set_thumbnail(url=icon_url)

                buy_price = price_data.get('high', 0)
                sell_price = price_data.get('low', 0)
                margin = buy_price - sell_price

                embed.add_field(name="Buy Price (Instant)", value=f"{buy_price:,} gp", inline=True)
                embed.add_field(name="Sell Price (Instant)", value=f"{sell_price:,} gp", inline=True)
                embed.add_field(name="Profit Margin", value=f"{margin:,} gp", inline=True)

                buy_time = format_price_timestamp(price_data.get('highTime'))
                sell_time = format_price_timestamp(price_data.get('lowTime'))

                embed.add_field(name="Last Buy", value=f"Updated {buy_time}", inline=True)
                embed.add_field(name="Last Sell", value=f"Updated {sell_time}", inline=True)
                embed.set_footer(text="Price data from osrs.cloud")
                await ctx.respond(embed=embed)
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching GE price data for {item}: {e}", exc_info=True)
        await ctx.respond(f"Error fetching price data (API error). Please try again later.", ephemeral=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred in /ge price command for {item}: {e}", exc_info=True)
        await ctx.respond("An unexpected error occurred while fetching price data.", ephemeral=True)

sotw = bot.create_group("sotw", "Commands for Skill of the Week")

@sotw.command(name="start", description="Manually start a new SOTW competition.")
@discord.default_permissions(manage_events=True) # Assuming this is an admin/event manager command
async def start_sotw(ctx: discord.ApplicationContext, skill: discord.Option(str, choices=WOM_SKILLS), duration_days: discord.Option(int, default=7, min_value=1, max_value=30)):
    await ctx.defer(ephemeral=True)
    data, error = await create_competition(WOM_CLAN_ID, skill, duration_days)
    if error:
        await ctx.respond(error, ephemeral=True)
        return

    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if sotw_channel:
        embed = await create_competition_embed(data, ctx.author)
        sotw_message = await sotw_channel.send(embed=embed)
        await send_global_announcement("sotw_start", {"skill": skill.capitalize()}, sotw_message.jump_url)
        await ctx.respond("SOTW started successfully in the designated channel!", ephemeral=True)
        logger.info(f"Admin {ctx.author.display_name} manually started SOTW for {skill}.")
    else:
        logger.error(f"SOTW Channel ID {SOTW_CHANNEL_ID} not found.")
        await ctx.respond("Error: SOTW Channel ID not configured correctly.", ephemeral=True)

@sotw.command(name="poll", description="Start a poll to choose the next SOTW.")
@discord.default_permissions(manage_events=True)
async def poll_sotw(ctx: discord.ApplicationContext):
    if ctx.guild.id in bot.active_polls:
        return await ctx.respond("There is already an active SOTW poll.", ephemeral=True)

    poll_skills = random.sample(WOM_SKILLS, 6) # Select 6 random skills for the poll
    view = SotwPollView(ctx.author, poll_skills) # Pass skills to view init
    embed = await view.create_embed()

    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if sotw_channel:
        poll_message = await sotw_channel.send(embed=embed, view=view)
        await ctx.respond("SOTW Poll created!", ephemeral=True)
        view.message_id = poll_message.id # Store message_id for the view
        bot.active_polls[ctx.guild.id] = view
        logger.info(f"Admin {ctx.author.display_name} started an SOTW poll in {sotw_channel.name}.")
    else:
        logger.error(f"SOTW Channel ID {SOTW_CHANNEL_ID} not found.")
        await ctx.respond("Error: SOTW Channel ID not configured correctly.", ephemeral=True)


@sotw.command(name="view", description="View the leaderboard for the current SOTW.")
async def view_sotw(ctx: discord.ApplicationContext):
    await ctx.defer()
    try:
        competitions = await execute_db_query(
            f"SELECT id FROM active_competitions WHERE ends_at > NOW() AND starts_at < NOW() ORDER BY ends_at DESC LIMIT 1",
            fetchone=True, cursor_factory=extras.DictCursor
        )
        if not competitions:
            return await ctx.respond("There is no active SOTW competition right now.", ephemeral=True)

        latest_comp_id = competitions['id']

        details_url = f"https://api.wiseoldman.net/v2/competitions/{latest_comp_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(details_url) as response:
                response.raise_for_status()
                data = await response.json()

        embed = discord.Embed(
            title=f"Leaderboard: {data['title']}",
            description=f"Current standings for the **{data['metric'].capitalize()}** competition.",
            color=discord.Color.purple(),
            url=f"https://wiseoldman.net/competitions/{data['id']}"
        )

        leaderboard_text = ""
        participants = sorted(data.get('participations', []), key=lambda p: p['progress']['gained'], reverse=True)

        for i, player in enumerate(participants[:10]):
            rank_emoji = {1: "🏆", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
            leaderboard_text += f"{rank_emoji} **{player['player']['displayName']}**: {player['progress']['gained']:,} XP\n"

        if not leaderboard_text:
            leaderboard_text = "No participants have gained XP yet."

        embed.add_field(name="Top 10", value=leaderboard_text, inline=False)
        end_dt = datetime.fromisoformat(data['endsAt'].replace('Z', '+00:00'))
        embed.set_footer(text=f"Competition ends")
        embed.timestamp = end_dt
        await ctx.respond(embed=embed)
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching WOM competition data for /sotw view: {e}", exc_info=True)
        await ctx.respond("Could not fetch competition leaderboard. WOM API might be down.", ephemeral=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred in /sotw view: {e}", exc_info=True)
        await ctx.respond("An unexpected error occurred while fetching SOTW data.", ephemeral=True)

raffle = bot.create_group("raffle", "Commands for managing raffles.")

@raffle.command(name="start", description="Start a new raffle.")
@discord.default_permissions(manage_events=True)
async def start_raffle(ctx: discord.ApplicationContext, prize: discord.Option(str, "What is the prize?"), duration_days: discord.Option(float, "How many days will it last?", min_value=0.1)):
    await ctx.defer(ephemeral=True)

    # Clear previous raffle data
    await execute_db_query("DELETE FROM raffles", commit=True)
    await execute_db_query("DELETE FROM raffle_entries", commit=True)

    ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    await execute_db_query(
        "INSERT INTO raffles (id, prize, ends_at, winner_id) VALUES (1, %s, %s, NULL)", # winner_id is NULL initially
        (prize, ends_at), commit=True
    )

    details = {"prize": prize}
    ai_embed_data = await generate_announcement_json("raffle_start", details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.add_field(name="How to Enter", value="Use `/raffle enter` to get a ticket! (Max 10 self-entries per person)", inline=False)
    embed.add_field(name="Raffle Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
    embed.set_footer(text=f"Raffle started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if raffle_channel:
        raffle_message = await raffle_channel.send(embed=embed)
        await send_global_announcement("raffle_start", {"prize": prize}, raffle_message.jump_url)
        await ctx.respond("Raffle created successfully!", ephemeral=True)
        logger.info(f"Admin {ctx.author.display_name} started a raffle for {prize}.")
    else:
        logger.error(f"Raffle Channel ID {RAFFLE_CHANNEL_ID} not found.")
        await ctx.respond("Error: Raffle Channel ID not configured correctly.", ephemeral=True)

@raffle.command(name="enter", description="Get one ticket for the current raffle (max 10).")
async def enter_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    raffle_data = await execute_db_query(
        "SELECT prize FROM raffles WHERE winner_id IS NULL LIMIT 1",
        fetchone=True
    )
    if not raffle_data:
        return await ctx.respond("There is no active raffle to enter right now.", ephemeral=True)

    self_entries_count = await execute_db_query(
        "SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND source = 'self'",
        (ctx.author.id,), fetchone=True
    )
    if self_entries_count and self_entries_count[0] >= 10:
        return await ctx.respond("You have already claimed your maximum of 10 tickets for this raffle!", ephemeral=True)

    await execute_db_query(
        "INSERT INTO raffle_entries (user_id, source) VALUES (%s, 'self')",
        (ctx.author.id,), commit=True
    )
    total_tickets_count = await execute_db_query(
        "SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s",
        (ctx.author.id,), fetchone=True
    )
    total_tickets = total_tickets_count[0] if total_tickets_count else 0
    await ctx.respond(f"You have successfully claimed a ticket for the **{raffle_data[0]}** raffle! You now have a total of {total_tickets} ticket(s).", ephemeral=True)
    logger.info(f"User {ctx.author.display_name} entered raffle, now has {total_tickets} tickets.")

@raffle.command(name="give_tickets", description="ADMIN: Give raffle tickets to a member.")
@discord.default_permissions(manage_events=True)
async def give_tickets(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member to give tickets to."), amount: discord.Option(int, "How many tickets to give.", min_value=1)):
    await ctx.defer(ephemeral=True)
    raffle_data = await execute_db_query(
        "SELECT id FROM raffles WHERE winner_id IS NULL LIMIT 1",
        fetchone=True
    )
    if not raffle_data:
        return await ctx.respond("There is no active raffle.", ephemeral=True)

    entries = [(member.id, 'admin_award') for _ in range(amount)] # Use 'admin_award' as source
  await execute_db_query(
    "INSERT INTO raffle_entries (user_id, source) VALUES %s",
    entries, # No longer needs to be in a tuple
    commit=True,
    use_execute_values=True
)

    total_tickets_count = await execute_db_query(
        "SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s",
        (member.id,), fetchone=True
    )
    total_tickets = total_tickets_count[0] if total_tickets_count else 0
    await ctx.respond(f"Successfully gave {amount} ticket(s) to {member.display_name}. They now have {total_tickets} ticket(s).", ephemeral=True)
    logger.info(f"Admin {ctx.author.display_name} gave {amount} tickets to {member.display_name}. New total: {total_tickets}.")

@raffle.command(name="edit_tickets", description="ADMIN: Set a member's total ticket count.")
@discord.default_permissions(manage_events=True)
async def edit_tickets(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member whose tickets you want to edit."), new_total: discord.Option(int, "The new total number of tickets they should have.", min_value=0)):
    await ctx.defer(ephemeral=True)
    raffle_data = await execute_db_query(
        "SELECT id FROM raffles WHERE winner_id IS NULL LIMIT 1",
        fetchone=True
    )
    if not raffle_data:
        return await ctx.respond("There is no active raffle.", ephemeral=True)

    await execute_db_query(
        "DELETE FROM raffle_entries WHERE user_id = %s",
        (member.id,), commit=True
    )
    if new_total > 0:
        entries = [(member.id, 'admin_edit') for _ in range(new_total)]
await execute_db_query(
    "INSERT INTO raffle_entries (user_id, source) VALUES %s",
    entries, # No longer needs to be in a tuple
    commit=True,
    use_execute_values=True
)
    await ctx.respond(f"Successfully set {member.display_name}'s ticket count to {new_total}.", ephemeral=True)
    logger.info(f"Admin {ctx.author.display_name} set {member.display_name}'s tickets to {new_total}.")

@raffle.command(name="view_tickets", description="View the current ticket count for all participants.")
async def view_tickets(ctx: discord.ApplicationContext):
    await ctx.defer()
    raffle_data = await execute_db_query(
        "SELECT prize FROM raffles WHERE winner_id IS NULL LIMIT 1",
        fetchone=True
    )
    if not raffle_data:
        return await ctx.respond("There is no active raffle.")

    entries = await execute_db_query(
        "SELECT user_id, COUNT(user_id) FROM raffle_entries GROUP BY user_id ORDER BY COUNT(user_id) DESC LIMIT 20",
        fetchall=True
    ) # Show top 20 for brevity

    embed = discord.Embed(title=f"🎟️ Raffle Tickets for '{raffle_data[0]}'", color=discord.Color.gold())
    if not entries:
        embed.description = "No tickets have been given out yet."
    else:
        description = ""
        for user_id, count in entries:
            try:
                member = ctx.guild.get_member(user_id) or await ctx.guild.fetch_member(user_id)
                description += f"**{member.display_name}**: {count} ticket(s)\n"
            except (discord.NotFound, discord.HTTPException):
                description += f"**Unknown User (ID: {user_id})**: {count} ticket(s)\n" # User left server
        embed.description = description
    await ctx.respond(embed=embed)

@raffle.command(name="draw_now", description="ADMIN: Immediately ends the raffle and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not channel:
        logger.error(f"Raffle channel {RAFFLE_CHANNEL_ID} not found for draw_now command.")
        return await ctx.respond("Error: Raffle channel not found.", ephemeral=True)
    result = await draw_raffle_winner(channel)
    await ctx.respond(f"Successfully triggered winner drawing: {result}", ephemeral=True)
    logger.info(f"Admin {ctx.author.display_name} manually drew raffle winner.")

@raffle.command(name="cancel", description="ADMIN: Cancels the current raffle without drawing a winner.")
@discord.default_permissions(manage_events=True)
async def cancel_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    raffle_data = await execute_db_query(
        "SELECT prize FROM raffles WHERE winner_id IS NULL LIMIT 1",
        fetchone=True
    )
    if not raffle_data:
        return await ctx.respond("There is no active raffle to cancel.", ephemeral=True)
    prize = raffle_data[0]

    await execute_db_query("DELETE FROM raffles", commit=True)
    await execute_db_query("DELETE FROM raffle_entries", commit=True)

    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if channel:
        await channel.send(f"The raffle for **{prize}** has been cancelled by an admin.")
    await ctx.respond("Raffle successfully cancelled.", ephemeral=True)
    logger.info(f"Admin {ctx.author.display_name} cancelled raffle for {prize}.")

events = bot.create_group("events", "View all active clan events.")

@events.command(name="view", description="Shows all currently active competitions and raffles.")
async def view_events(ctx: discord.ApplicationContext):
    await ctx.defer()
    comp = await execute_db_query(
        "SELECT * FROM active_competitions WHERE ends_at > NOW() AND starts_at < NOW() ORDER BY ends_at DESC LIMIT 1",
        fetchone=True, cursor_factory=extras.DictCursor
    )
    raf = await execute_db_query(
        "SELECT * FROM raffles WHERE winner_id IS NULL LIMIT 1",
        fetchone=True, cursor_factory=extras.DictCursor
    )
    giveaway = await execute_db_query(
        "SELECT * FROM giveaways WHERE is_active = TRUE AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1",
        fetchone=True, cursor_factory=extras.DictCursor
    )

    embed = discord.Embed(title="📅 Clan Event Status", description="Here's a look at all the events currently running.", color=discord.Color.blurple())

    if comp:
        comp_ends_dt = comp['ends_at']
        comp_info = (f"**Title:** [{comp['title']}](https://wiseoldman.net/competitions/{comp['id']})\n"
                     f"**Ends:** <t:{int(comp_ends_dt.timestamp())}:R>")
        embed.add_field(name="⚔️ Active Competition", value=comp_info, inline=False)
    else:
        embed.add_field(name="⚔️ Active Competition", value="There is no SOTW competition currently running.", inline=False)

    if raf:
        raf_ends_dt = raf['ends_at']
        raf_info = (f"**Prize:** {raf['prize']}\n"
                    f"**Ends:** <t:{int(raf_ends_dt.timestamp())}:R>")
        embed.add_field(name="🎟️ Active Raffle", value=raf_info, inline=False)
    else:
        embed.add_field(name="🎟️ Active Raffle", value="There is no raffle currently running.", inline=False)

    if giveaway:
        giveaway_ends_dt = giveaway['ends_at']
        giveaway_channel = bot.get_channel(giveaway['channel_id'])
        giveaway_info = (f"**Prize:** {giveaway['prize']}\n"
                         f"**Winners:** {giveaway['winner_count']}\n"
                         f"**Ends:** <t:{int(giveaway_ends_dt.timestamp())}:R>\n"
                         f"[Jump to Giveaway]({message.jump_url if (message := await giveaway_channel.fetch_message(giveaway['message_id'])) else 'https://discord.com'})"
                         if giveaway_channel else "Giveaway channel not found.")
        embed.add_field(name="🎁 Active Giveaway", value=giveaway_info, inline=False)
    else:
        embed.add_field(name="🎁 Active Giveaway", value="There is no giveaway currently running.", inline=False)


    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.respond(embed=embed)

bingo = bot.create_group("bingo", "Commands for clan bingo events.")

@bingo.command(name="start", description="Start a new bingo event.")
@discord.default_permissions(manage_events=True)
async def start_bingo(ctx: discord.ApplicationContext, duration_days: discord.Option(int, "How many days the bingo event will last.", min_value=1, max_value=30)):
    await ctx.defer(ephemeral=True)
    await ctx.followup.send("The Taskmaster is forging a new challenge... This may take a moment.", ephemeral=True) # Send feedback immediately

    try:
        with open(TASKS_FILE, 'r') as f:
            all_tasks = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error loading tasks.json for bingo: {e}", exc_info=True)
        return await ctx.edit(content="Error: `tasks.json` not found or is invalid.")

    tasks_by_difficulty = {"common": [], "uncommon": [], "rare": []}
    for task in all_tasks:
        if 'difficulty' in task and task['difficulty'] in tasks_by_difficulty:
            tasks_by_difficulty[task['difficulty']].append(task)
        else:
            logger.warning(f"Task '{task.get('name', 'Unnamed task')}' has invalid or missing difficulty.")

    board_composition = {"common": 15, "uncommon": 7, "rare": 3} # Example distribution
    board_tasks = []

    for difficulty, count in board_composition.items():
        available_tasks = tasks_by_difficulty.get(difficulty, [])
        if len(available_tasks) < count:
            logger.error(f"Not enough '{difficulty}' tasks in tasks.json (needed: {count}, found: {len(available_tasks)}).")
            return await ctx.edit(content=f"Error: Not enough '{difficulty}' tasks in `tasks.json`. Please add more tasks or adjust board composition.")
        board_tasks.extend(random.sample(available_tasks, count))

    # Ensure exactly 25 tasks are selected and shuffled
    if len(board_tasks) < 25:
        logger.error(f"Not enough tasks in total to create a 25-slot board (found: {len(board_tasks)}).")
        return await ctx.edit(content="Error: Not enough tasks in total to create a 25-slot board.")
    random.shuffle(board_tasks)
    board_tasks = board_tasks[:25]

    # Clear previous event data
    await execute_db_query("DELETE FROM bingo_events", commit=True)
    await execute_db_query("DELETE FROM bingo_submissions", commit=True)
    await execute_db_query("DELETE FROM bingo_completed_tiles", commit=True)

    ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    board_json = json.dumps(board_tasks)

    # Generate image in a separate thread
    image_path, error = await _sync_generate_bingo_image(board_tasks)
    if error:
        return await ctx.edit(content=f"Failed to generate bingo image: {error}")

    bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
    if not bingo_channel:
        logger.error(f"Bingo Channel ID {BINGO_CHANNEL_ID} not found.")
        return await ctx.edit(content="Error: Bingo Channel ID not configured correctly.")

    ai_embed_data = await generate_announcement_json("bingo_start")
    embed = discord.Embed.from_dict(ai_embed_data)
    file = discord.File(image_path, filename="bingo_board.png")
    embed.set_image(url="attachment://bingo_board.png")
    embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
    embed.set_footer(text=f"Bingo started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

    try:
        message = await bingo_channel.send(embed=embed, file=file)
        await execute_db_query(
            "INSERT INTO bingo_events (id, ends_at, board_json, message_id) VALUES (1, %s, %s, %s)",
            (ends_at, board_json, message.id), commit=True
        )
        await send_global_announcement("bingo_start", {}, message.jump_url)
        await ctx.edit(content="Bingo event created successfully!")
        logger.info(f"Admin {ctx.author.display_name} started a bingo event.")
    except discord.Forbidden:
        logger.error(f"Missing permissions to send bingo message in channel {BINGO_CHANNEL_ID}.")
        await ctx.edit(content="Error: I don't have permission to send messages in the bingo channel.")
    except Exception as e:
        logger.error(f"Error starting bingo event: {e}", exc_info=True)
        await ctx.edit(content=f"An unexpected error occurred: {e}")

@bingo.command(name="complete", description="Submit a task for bingo completion.")
async def complete_task(ctx: discord.ApplicationContext, task: discord.Option(str, "The name of the task you completed."), proof: discord.Option(str, "A URL link to a screenshot or video proof.")):
    await ctx.defer(ephemeral=True)

    board_data = await execute_db_query(
        "SELECT board_json FROM bingo_events LIMIT 1",
        fetchone=True
    )
    if not board_data:
        return await ctx.respond("There is no active bingo event.", ephemeral=True)

    board_tasks = json.loads(board_data[0])
    task_names = [t['name'] for t in board_tasks]

    # Case-insensitive task matching
    matched_task = next((t for t in task_names if t.lower() == task.lower()), None)
    if not matched_task:
        return await ctx.respond("That task is not on the current bingo board.", ephemeral=True)

    # Check if task is already approved
    completed_check = await execute_db_query(
        "SELECT task_name FROM bingo_completed_tiles WHERE task_name = %s",
        (matched_task,), fetchone=True
    )
    if completed_check:
        return await ctx.respond("This task has already been completed and approved for the current bingo event!", ephemeral=True)

    # Check for existing pending submission by this user for this task
    pending_check = await execute_db_query(
        "SELECT id FROM bingo_submissions WHERE user_id = %s AND task_name = %s AND status = 'pending'",
        (ctx.author.id, matched_task), fetchone=True
    )
    if pending_check:
        return await ctx.respond("You already have a pending submission for this task.", ephemeral=True)

    # Validate proof URL format
    if not (proof.startswith('http://') or proof.startswith('https://')):
        return await ctx.respond("Please provide a valid URL for your proof (must start with http:// or https://).", ephemeral=True)

    try:
        insert_id_result = await execute_db_query(
            "INSERT INTO bingo_submissions (user_id, task_name, proof_url, status) VALUES (%s, %s, %s, 'pending') RETURNING id",
            (ctx.author.id, matched_task, proof), fetchone=True, commit=True
        )
        submission_id = insert_id_result[0]

        await ctx.respond("Your submission has been sent to the admins for review!", ephemeral=True)
        logger.info(f"User {ctx.author.display_name} submitted bingo task '{matched_task}' (ID: {submission_id}).")

        # Notify admins in a dedicated channel or log
        # For now, just logging; could extend to send a message to an admin-only channel.
    except Exception as e:
        logger.error(f"Error submitting bingo task for {ctx.author.display_name}: {e}", exc_info=True)
        await ctx.respond(f"An error occurred while submitting your task: {e}", ephemeral=True)

@bingo.command(name="submissions", description="ADMIN: View pending bingo task submissions.")
@discord.default_permissions(manage_events=True)
async def view_submissions(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    pending = await execute_db_query(
        "SELECT * FROM bingo_submissions WHERE status = 'pending'",
        fetchall=True, cursor_factory=extras.DictCursor
    )
    if not pending:
        return await ctx.respond("There are no pending bingo submissions.", ephemeral=True)

    await ctx.respond("Here are the pending submissions:", ephemeral=True)
    for sub in pending:
        try:
            user = await bot.fetch_user(sub['user_id'])
            embed = discord.Embed(title="📝 Bingo Submission", description=f"**Task:** {sub['task_name']}", color=discord.Color.yellow())
            embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
            embed.add_field(name="Proof", value=f"[Click to view]({sub['proof_url']})", inline=False)
            embed.set_footer(text=f"Submission ID: {sub['id']}")
            # Send submission to the same channel where command was issued, ephemeral
            await ctx.channel.send(embed=embed, view=SubmissionView(), ephemeral=True)
        except discord.NotFound:
            logger.warning(f"User {sub['user_id']} not found for bingo submission {sub['id']}.")
            await ctx.channel.send(f"Submission ID {sub['id']} from an unknown user (ID: {sub['user_id']}) for task: {sub['task_name']} [Proof]({sub['proof_url']}). Status: Pending.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error processing bingo submission {sub['id']} for view: {e}", exc_info=True)
            await ctx.channel.send(f"Error displaying submission {sub['id']}.", ephemeral=True)

@bingo.command(name="board", description="View the current bingo board.")
async def view_board(ctx: discord.ApplicationContext):
    await ctx.defer()
    event_data = await execute_db_query(
        "SELECT message_id FROM bingo_events LIMIT 1",
        fetchone=True
    )
    if not event_data or not event_data[0]:
        return await ctx.respond("There is no active bingo board to display.")

    bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
    if bingo_channel:
        try:
            message = await bingo_channel.fetch_message(event_data[0])
            await ctx.respond(f"Here is the current bingo board: {message.jump_url}")
        except discord.NotFound:
            logger.warning(f"Original bingo board message {event_data[0]} not found.")
            await ctx.respond("Could not find the original bingo board message. It may have been deleted.")
        except discord.Forbidden:
            logger.error(f"Missing permissions to fetch bingo message {event_data[0]} in channel {BINGO_CHANNEL_ID}.")
            await ctx.respond("I don't have permission to view the bingo board message in the bingo channel.")
    else:
        await ctx.respond("Bingo channel not configured.")

pointstore = bot.create_group("pointstore", "Manage and redeem clan points.")

@pointstore.command(name="rewards", description="View available rewards in the point store.")
async def view_rewards(ctx: discord.ApplicationContext):
    await ctx.defer()
    try:
        rewards = await execute_db_query(
            "SELECT id, reward_name, point_cost, description FROM rewards WHERE is_active = TRUE ORDER BY point_cost ASC",
            fetchall=True, cursor_factory=extras.DictCursor
        )
        embed = discord.Embed(title="🛍️ Clan Point Store Rewards 🛍️", color=discord.Color.gold())
        if not rewards:
            embed.description = "There are currently no active rewards in the point store."
        else:
            for reward in rewards:
                role_reward_data = await execute_db_query(
                    "SELECT role_id FROM role_rewards WHERE reward_id = %s",
                    (reward['id'],), fetchone=True, cursor_factory=extras.DictCursor
                )
                role_reward_text = ""
                if role_reward_data:
                    role_id = role_reward_data['role_id']
                    guild = bot.get_guild(MAIN_GUILD_ID) # Assume roles are managed in the main guild
                    if guild:
                        role = guild.get_role(role_id)
                        if role:
                            role_reward_text = f"\n**Role:** {role.mention}"
                        else:
                            role_reward_text = f"\n**Role ID:** {role_id} (Role not found in guild)"
                embed.add_field(
                    name=f"{reward['reward_name']} ({reward['point_cost']} points)",
                    value=f"{reward['description'] or 'No description provided.'}{role_reward_text}",
                    inline=False
                )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = datetime.now(timezone.utc)
        await ctx.respond(embed=embed)
    except Exception as e:
        logger.error(f"Error fetching rewards: {e}", exc_info=True)
        await ctx.respond("An error occurred while fetching rewards.", ephemeral=True)


@pointstore.command(name="redeem", description="Redeem a reward from the point store.")
async def redeem_reward(ctx: discord.ApplicationContext, reward_name: str):
    await ctx.defer(ephemeral=True)
    try:
        reward = await execute_db_query(
            "SELECT id, reward_name, point_cost, description FROM rewards WHERE reward_name ILIKE %s AND is_active = TRUE",
            (reward_name,), fetchone=True, cursor_factory=extras.DictCursor
        )
        if not reward:
            return await ctx.respond(f"Reward '{reward_name}' not found or is not currently active.", ephemeral=True)

        user_points_data = await execute_db_query(
            "SELECT points FROM clan_points WHERE discord_id = %s",
            (ctx.user.id,), fetchone=True, cursor_factory=extras.DictCursor
        )
        current_points = user_points_data['points'] if user_points_data else 0

        if current_points < reward['point_cost']:
            return await ctx.respond(f"You don't have enough points to redeem '{reward['reward_name']}'. You need {reward['point_cost']} points, but you only have {current_points}.", ephemeral=True)

        new_balance = current_points - reward['point_cost']
        await execute_db_query(
            "UPDATE clan_points SET points = %s WHERE discord_id = %s",
            (new_balance, ctx.user.id), commit=True
        )
        await execute_db_query(
            "INSERT INTO redeem_transactions (user_id, reward_id, reward_name, point_cost) VALUES (%s, %s, %s, %s)",
            (ctx.user.id, reward['id'], reward['reward_name'], reward['point_cost']), commit=True
        )

        feedback_message = f"You have successfully redeemed '{reward['reward_name']}'! Your new point balance is **{new_balance}**."
        role_reward_data = await execute_db_query(
            "SELECT role_id FROM role_rewards WHERE reward_id = %s",
            (reward['id'],), fetchone=True, cursor_factory=extras.DictCursor
        )
        if role_reward_data:
            role_id = role_reward_data['role_id']
            guild = bot.get_guild(MAIN_GUILD_ID)
            if guild:
                role = guild.get_role(role_id)
                member = guild.get_member(ctx.user.id)
                if role and member:
                    try:
                        await member.add_roles(role)
                        feedback_message += f" The role **{role.name}** has been added to you."
                    except discord.Forbidden:
                        logger.error(f"Missing permissions to add role {role.name} to {member.display_name}")
                        feedback_message += f" Points deducted, but I lack permissions to assign the role **{role.name}**."
                    except Exception as e:
                        logger.error(f"Error adding role {role.name} to {member.display_name}: {e}", exc_info=True)
                        feedback_message += f" Points deducted, but an error occurred while assigning the role."
                else:
                    logger.warning(f"Role {role_id} or member {ctx.user.id} not found for redemption.")
                    feedback_message += " Points deducted, but the associated role was not found or could not be assigned."
            else:
                 logger.error(f"Could not get guild {MAIN_GUILD_ID} for role assignment for user {ctx.user.id}.")
                 feedback_message += " Points deducted, but I could not access the guild to assign the role."
        else:
            feedback_message += " Please contact an admin for reward fulfillment."

        await ctx.followup.send(feedback_message, ephemeral=False)
        logger.info(f"User {ctx.user.display_name} redeemed '{reward['reward_name']}'. New balance: {new_balance}.")
    except Exception as e:
        logger.error(f"Error redeeming reward for {ctx.user.display_name}: {e}", exc_info=True)
        await ctx.respond("An error occurred while redeeming the reward.", ephemeral=True)

@pointstore.command(name="addreward", description="ADMIN: Add a new reward to the point store.")
@discord.default_permissions(manage_guild=True)
async def add_reward(ctx: discord.ApplicationContext, name: str, cost: int, description: Option(str, "Optional description for the reward.", required=False), role_id: Option(str, "Optional Discord Role ID to link to this reward.", required=False)):
    await ctx.defer(ephemeral=True)
    try:
        # Insert reward
        insert_result = await execute_db_query(
            "INSERT INTO rewards (reward_name, point_cost, description) VALUES (%s, %s, %s) RETURNING id",
            (name, cost, description), fetchone=True, commit=True
        )
        reward_id = insert_result[0]

        response_message = f"Reward '{name}' added to the point store with a cost of {cost} points."

        # Handle role linking
        if role_id:
            try:
                role_id_int = int(role_id)
                guild = ctx.guild
                if guild and guild.get_role(role_id_int):
                    await execute_db_query(
                        "INSERT INTO role_rewards (reward_id, role_id) VALUES (%s, %s)",
                        (reward_id, role_id_int), commit=True
                    )
                    response_message += f" Linked to role <@&{role_id_int}>."
                elif guild and not guild.get_role(role_id_int):
                    response_message += f" Warning: Role with ID {role_id} not found in this guild. Reward added, but role not linked."
                    logger.warning(f"Role {role_id} not found in guild {ctx.guild.name} when adding reward.")
                else:
                    response_message += f" Warning: Could not access guild to validate role ID. Reward added, but role may not be linked correctly."
                    logger.warning(f"Could not access guild {ctx.guild.name} to validate role {role_id}.")
            except ValueError:
                response_message += f" Warning: Invalid Role ID '{role_id}'. Role not linked."
                logger.warning(f"Invalid Role ID '{role_id}' provided for reward.")
            except Exception as e:
                logger.error(f"Error linking role reward for {name}: {e}", exc_info=True)
                response_message += f" Warning: An error occurred while linking the role reward. {e}"

        await ctx.respond(response_message, ephemeral=True)
        logger.info(f"Admin {ctx.author.display_name} added reward '{name}'.")
    except psycopg2.errors.UniqueViolation:
        await ctx.respond(f"A reward with the name '{name}' already exists.", ephemeral=True)
    except Exception as e:
        logger.error(f"Error adding reward '{name}': {e}", exc_info=True)
        await ctx.respond("An error occurred while adding the reward.", ephemeral=True)

@pointstore.command(name="removereward", description="ADMIN: Remove a reward from the point store.")
@discord.default_permissions(manage_guild=True)
async def remove_reward(ctx: discord.ApplicationContext, reward_name: str):
    await ctx.defer(ephemeral=True)
    try:
        deleted_result = await execute_db_query(
            "DELETE FROM rewards WHERE reward_name ILIKE %s RETURNING id",
            (reward_name,), fetchone=True, commit=True
        )
        if deleted_result:
            await ctx.respond(f"Reward '{reward_name}' removed from the point store.", ephemeral=True)
            logger.info(f"Admin {ctx.author.display_name} removed reward '{reward_name}'.")
        else:
            await ctx.respond(f"Reward '{reward_name}' not found.", ephemeral=True)
    except Exception as e:
        logger.error(f"Error removing reward '{reward_name}': {e}", exc_info=True)
        await ctx.respond("An error occurred while removing the reward.", ephemeral=True)

@pointstore.command(name="togglereward", description="ADMIN: Toggle the active status of a reward.")
@discord.default_permissions(manage_guild=True)
async def toggle_reward(ctx: discord.ApplicationContext, reward_name: str):
    await ctx.defer(ephemeral=True)
    try:
        reward_data = await execute_db_query(
            "SELECT id, is_active FROM rewards WHERE reward_name ILIKE %s",
            (reward_name,), fetchone=True
        )
        if not reward_data:
            return await ctx.respond(f"Reward '{reward_name}' not found.", ephemeral=True)

        new_status = not reward_data[1]
        await execute_db_query(
            "UPDATE rewards SET is_active = %s WHERE id = %s",
            (new_status, reward_data[0]), commit=True
        )
        status_text = "active" if new_status else "inactive"
        await ctx.respond(f"Reward '{reward_name}' is now set to **{status_text}**.", ephemeral=True)
        logger.info(f"Admin {ctx.author.display_name} toggled reward '{reward_name}' to {status_text}.")
    except Exception as e:
        logger.error(f"Error toggling reward status for '{reward_name}': {e}", exc_info=True)
        await ctx.respond("An error occurred while toggling the reward status.", ephemeral=True)

giveaway = bot.create_group("giveaway", "Commands for managing giveaways.")

@giveaway.command(name="start", description="Start a new giveaway.")
@discord.default_permissions(manage_events=True)
async def start_giveaway(ctx: discord.ApplicationContext, prize: discord.Option(str, "What is the prize?"), duration: discord.Option(str, "How long? (e.g., 7d, 12h, 30m)"), winners: discord.Option(int, "How many winners?", min_value=1, default=1), reward_role: discord.Option(discord.Role, "Optional role for winner(s).", required=False)):
    await ctx.defer(ephemeral=True)
    delta = parse_duration(duration)
    if delta is None:
        return await ctx.respond("Invalid duration format. Use e.g., '7d', '12h', '30m'.", ephemeral=True)

    ends_at = datetime.now(timezone.utc) + delta
    if ends_at <= datetime.now(timezone.utc):
        return await ctx.respond("Giveaway duration must be in the future.", ephemeral=True)

    giveaway_channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if not giveaway_channel:
        logger.error(f"Giveaway channel {GIVEAWAY_CHANNEL_ID} not found.")
        return await ctx.respond("Giveaway channel not found. Please configure `GIVEAWAY_CHANNEL_ID`.", ephemeral=True)

    details = {"prize": prize, "winner_count": winners}
    ai_embed_data = await generate_announcement_json("giveaway_start", details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.add_field(name="Ends In", value=f"<t:{int(ends_at.timestamp())}:R>", inline=True)
    embed.add_field(name="Winners", value=f"**{winners}**", inline=True)
    # Adding an initial "Entries" field
    embed.add_field(name="Entries", value="👥 **Entries:** 0", inline=True)
    if reward_role:
        embed.add_field(name="🏆 Bonus Reward", value=f"Winner(s) will receive the {reward_role.mention} role!", inline=False)
    embed.set_footer(text=f"Giveaway started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

    try:
        giveaway_message = await giveaway_channel.send(embed=embed)
        view = GiveawayView(message_id=giveaway_message.id)
        await giveaway_message.edit(view=view)

        role_id_to_save = reward_role.id if reward_role else None
        await execute_db_query(
            "INSERT INTO giveaways (message_id, channel_id, prize, ends_at, winner_count, role_id) VALUES (%s, %s, %s, %s, %s, %s)",
            (giveaway_message.id, giveaway_channel.id, prize, ends_at, winners, role_id_to_save), commit=True
        )
        await ctx.respond(f"Giveaway for **{prize}** has been started!", ephemeral=True)
        logger.info(f"Admin {ctx.author.display_name} started a giveaway for {prize}.")
    except discord.Forbidden:
        logger.error(f"Missing permissions to send giveaway message in channel {GIVEAWAY_CHANNEL_ID}.")
        await ctx.respond("Error: I don't have permission to send messages in the giveaway channel.", ephemeral=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred starting giveaway: {e}", exc_info=True)
        await ctx.respond(f"An unexpected error occurred: {e}", ephemeral=True)

@giveaway.command(name="entries", description="View the list of entrants for the current giveaway.")
@discord.default_permissions(manage_events=True)
async def view_entries(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)

    active_giveaway = await execute_db_query(
        "SELECT message_id, prize FROM giveaways WHERE is_active = TRUE ORDER BY ends_at DESC LIMIT 1",
        fetchone=True, cursor_factory=extras.DictCursor
    )
    if not active_giveaway:
        return await ctx.respond("There are no active giveaways.", ephemeral=True)

    entries = await execute_db_query(
        "SELECT user_id FROM giveaway_entries WHERE message_id = %s",
        (active_giveaway['message_id'],), fetchall=True, cursor_factory=extras.DictCursor
    )

    embed = discord.Embed(
        title=f"🎟️ Entries for '{active_giveaway['prize']}'",
        description=f"Total Entries: **{len(entries)}**",
        color=discord.Color.blue()
    )
    if not entries:
        embed.description += "\n\nNo one has entered yet."
    else:
        entrant_list = []
        for entry in entries:
            try:
                member = ctx.guild.get_member(entry['user_id'])
                if member:
                    entrant_list.append(f"- {member.display_name} (`{member.name}`)")
                else:
                    entrant_list.append(f"- *User not in server? (ID: {entry['user_id']})*")
            except Exception: # Catch any error during member fetch
                entrant_list.append(f"- *Unknown User (ID: {entry['user_id']})*")

        entrants_text = "\n".join(entrant_list)
        if len(entrants_text) > 4000: # Discord embed field value limit
             entrants_text = entrants_text[:3900] + "\n...and more. (List truncated)"
        embed.description += f"\n\n{entrants_text}"
    await ctx.respond(embed=embed, ephemeral=True)

points = bot.create_group("points", "Commands related to Clan Points.")

@points.command(name="view", description="Check your current Clan Point balance.")
async def view_points(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    point_data = await execute_db_query(
        "SELECT points FROM clan_points WHERE discord_id = %s",
        (ctx.author.id,), fetchone=True
    )
    current_points = point_data[0] if point_data else 0
    await ctx.followup.send(f"You currently have **{current_points}** Clan Points.")

@points.command(name="leaderboard", description="View the Clan Points leaderboard.")
async def leaderboard(ctx: discord.ApplicationContext):
    await ctx.defer()
    leaders = await execute_db_query(
        "SELECT discord_id, points FROM clan_points ORDER BY points DESC LIMIT 10",
        fetchall=True
    )
    embed = discord.Embed(title="🏆 Clan Points Leaderboard 🏆", color=discord.Color.gold())
    if not leaders:
        embed.description = "No one has earned any points yet."
    else:
        leaderboard_text = ""
        for i, (user_id, points) in enumerate(leaders):
            rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
            try:
                member = ctx.guild.get_member(user_id) or await ctx.guild.fetch_member(user_id)
                leaderboard_text += f"{rank_emoji} **{member.display_name}**: {points:,} points\n"
            except (discord.NotFound, discord.HTTPException):
                leaderboard_text += f"{rank_emoji} **Unknown User (ID: {user_id})**: {points:,} points\n"
                logger.warning(f"Leaderboard user {user_id} not found in guild.")
        embed.description = leaderboard_text
    await ctx.respond(embed=embed)


@bot.slash_command(name="help", description="Shows a list of all available commands.")
async def help_command(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    embed = discord.Embed(
        title="📜 GrazyBot Command List 📜",
        description="Here are all the commands you can use to manage clan events.",
        color=discord.Color.blurple()
    )
    member_commands = """
    `/ge price` - Check the Grand Exchange price of an item.
    `/points view` - Check your current Clan Point balance.
    `/points leaderboard` - View the Clan Points leaderboard.
    `/sotw view` - View the leaderboard for the current Skill of the Week.
    `/raffle enter` - Get one ticket for the current raffle (max 10 self-entries).
    `/raffle view_tickets` - See how many tickets everyone has.
    `/bingo board` - Get a link to the current bingo board.
    `/bingo complete` - Submit a task for bingo completion.
    `/pointstore rewards` - See what you can buy with your points.
    `/pointstore redeem` - Spend your points on a reward.
    `/events view` - See all currently active events.
    """
    admin_commands = """
    `/admin announce` - Send a global announcement as the bot.
    `/admin manage_points` - Add or remove Clan Points from a member.
    `/admin award_sotw_winners` - Manually award points for a past SOTW.
    `/admin check_items` - Check if the GE item list has loaded.
    `/sotw start` - Manually start a new SOTW competition.
    `/sotw poll` - Start a poll to choose the next SOTW.
    `/giveaway start` - Start a new giveaway with a prize and duration.
    `/giveaway entries` - View entrants for the current giveaway.
    `/raffle start` - Start a new raffle.
    `/raffle give_tickets` - Give raffle tickets to a member.
    `/raffle edit_tickets` - Set a member's total ticket count.
    `/raffle draw_now` - End the raffle and draw a winner immediately.
    `/raffle cancel` - Cancel the current raffle.
    `/bingo start` - Start a new clan bingo event.
    `/bingo submissions` - View and manage pending bingo submissions.
    `/pointstore addreward` - Add a new reward to the store.
    `/pointstore removereward` - Remove a reward from the store.
    `/pointstore togglereward` - Activate or deactivate a reward.
    """
    embed.add_field(name="✅ Member Commands", value=textwrap.dedent(member_commands), inline=False)
    embed.add_field(name="👑 Admin Commands", value=textwrap.dedent(admin_commands), inline=False)
    embed.set_footer(text="Let the games begin!")
    await ctx.respond(embed=embed, ephemeral=True)


# --- Main Execution Block ---
async def run_bot():
    """A resilient function to start the bot and handle common Discord API errors."""
    while True:
        try:
            await bot.start(TOKEN)
        except discord.errors.LoginFailure:
            logger.critical("Invalid bot token provided. Exiting.")
            break
        except discord.errors.ConnectionClosed as e:
            logger.error(f"Discord connection closed unexpectedly: {e}. Reconnecting in 10 seconds...", exc_info=True)
            await asyncio.sleep(10)
        except discord.errors.HTTPException as e:
            if e.status == 429: # Rate limit
                retry_after = int(e.response.headers.get("Retry-After", 300))
                logger.warning(f"Bot is being rate-limited by Discord (HTTP 429). Retrying in {retry_after} seconds...")
                await asyncio.sleep(retry_after)
            else:
                logger.error(f"An unexpected Discord HTTP error occurred: {e}", exc_info=True)
                break # For other HTTP errors, exit or consider more specific handling
        except Exception as e:
            logger.critical(f"An unhandled exception occurred while running the bot: {e}", exc_info=True)
            break

async def main():
    """Main entry point to run both the web server and the Discord bot."""
    web_task = asyncio.create_task(start_web_server())
    bot_task = asyncio.create_task(run_bot())
    await asyncio.gather(web_task, bot_task)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"Fatal error in main execution: {e}", exc_info=True)
    finally:
        if db_pool:
            db_pool.closeall()
            logger.info("Database connection pool closed.")
