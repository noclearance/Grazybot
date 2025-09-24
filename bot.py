# bot.py
import discord
from discord.ext import tasks
import os
from dotenv import load_dotenv
import aiohttp
from aiohttp import web
import asyncio
from datetime import datetime, timedelta, timezone
import random
import json
import textwrap
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai
from io import BytesIO
from discord.commands import SlashCommandGroup, Option
import re
import urllib.parse as up
from supabase import create_client, Client
import os

import asyncpg # New import for asynchronous PostgreSQL

# Get your Supabase credentials from environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Create the Supabase client instance
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- Configuration & Setup ---
load_dotenv()
TOKEN = os.getenv('TOKEN')
WOM_CLAN_ID = os.getenv('WOM_CLAN_ID')
WOM_VERIFICATION_CODE = os.getenv('WOM_VERIFICATION_CODE')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DEBUG_GUILD_ID = int(os.getenv('DEBUG_GUILD_ID'))
DATABASE_URL = os.getenv('DATABASE_URL')
TASKS_FILE = "tasks.json"

# Channel IDs
SOTW_CHANNEL_ID = int(os.getenv('SOTW_CHANNEL_ID'))
BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID'))
RAFFLE_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID'))
RECAP_CHANNEL_ID = int(os.getenv('RECAP_CHANNEL_ID'))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))
GIVEAWAY_CHANNEL_ID = ANNOUNCEMENTS_CHANNEL_ID # Often the same as announcements
PVM_EVENT_CHANNEL_ID = int(os.getenv('PVM_EVENT_CHANNEL_ID')) # New: for PVM events

# Configure the Gemini AI (for text)
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-1.0-pro')

# Define WOM skill metrics & Bot Intents
WOM_SKILLS = ["overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herblore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"]

# Define a list of common OSRS activities/bosses that are typically found on Hiscores.
# This list's order should ideally match the Hiscores API output for robust parsing.
# For a more dynamic solution, one might fetch this list from a data source.
OSRS_ACTIVABLE_HISCORE_ORDER = [
    "Clue Scrolls (all)", "Clue Scrolls (beginner)", "Clue Scrolls (easy)", "Clue Scrolls (medium)", "Clue Scrolls (hard)", "Clue Scrolls (elite)", "Clue Scrolls (master)",
    "LMS - Rank", "Bounty Hunter - Hunter", "Bounty Hunter - Rogue", # Note: BH does not have a rank, only score/kc
    "Barrows Chests", "Boss Kills (Total)", "Abyssal Sire", "Alchemical Hydra", "Artio", "Bryophyta", "Callisto", "Calvar'ion", "Cerberus", "Chambers of Xeric", "Chambers of Xeric: Challenge Mode", "Chaos Elemental", "Chaos Fanatic", "Commander Zilyana", "Corporeal Beast", "Crazy Archaeologist", "Dagannoth Prime", "Dagannoth Rex", "Dagannoth Supreme", "Deranged Archaeologist", "General Graardor", "Giant Mole", "Grotesque Guardians", "Hespori", "Kalphite Queen", "King Black Dragon", "Kraken", "Kree'arra", "K'ril Tsutsaroth", "Mimic", "Nex", "Nightmare", "Phosani's Nightmare", "Obor", "Sarachnis", "Scorpia", "Skotizo", "Tempoross", "The Gauntlet", "The Corrupted Gauntlet", "Theatre of Blood", "Theatre of Blood: Hard Mode", "Thermonuclear Smoke Devil", "Tombs of Amascut", "Tombs of Amascut: Expert Mode", "TzKal-Zuk", "TzTok-Jad", "Venenatis", "Vet'ion", "Vorkath", "Wintertodt", "Zalcano", "Zulrah" 
    # This list is illustrative; a complete and accurate list would be much longer and precise with Hiscores indices.
]

intents = discord.Intents.default()
intents.members = True
bot = discord.Bot(intents=intents, debug_guilds=[DEBUG_GUILD_ID])
bot.item_mapping = {}
bot.active_polls = {}
bot.db_pool = None

# --- Database Setup ---
async def setup_database_pool():
    """Initializes the database connection pool and sets up schema."""
    global bot # Access the global bot object
    up.uses_netloc.append("postgres")
    url = up.urlparse(os.environ["DATABASE_URL"]) # Ensure DATABASE_URL is set
    try:
        bot.db_pool = await asyncpg.create_pool(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            ssl='require', # Use ssl='require' for asyncpg with external DB
            min_size=5,  # Minimum connections in pool
            max_size=10, # Maximum connections in pool
            timeout=60   # Max time to wait for a connection acquisition
        )
        print("Database connection pool created successfully.")

        # Run schema setup using the acquired connection from the pool
        async with bot.db_pool.acquire() as conn:
            # Use conn.execute for DDL statements
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS active_competitions (
                id INTEGER PRIMARY KEY, title TEXT, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
                midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE, winners_awarded BOOLEAN DEFAULT FALSE
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS raffles (
                id SERIAL PRIMARY KEY, 
                prize TEXT NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                winner_id BIGINT DEFAULT NULL, 
                message_id BIGINT DEFAULT NULL, 
                channel_id BIGINT DEFAULT NULL
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS raffle_entries (
                entry_id SERIAL PRIMARY KEY,
                raffle_id INTEGER NOT NULL REFERENCES raffles(id) ON DELETE CASCADE, 
                user_id BIGINT NOT NULL,
                source TEXT DEFAULT 'self',
                UNIQUE (raffle_id, user_id) 
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS bingo_events (
                id SERIAL PRIMARY KEY, 
                ends_at TIMESTAMPTZ NOT NULL,
                board_json TEXT NOT NULL,
                message_id BIGINT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE 
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS bingo_submissions (
                id SERIAL PRIMARY KEY,
                event_id INTEGER NOT NULL REFERENCES bingo_events(id) ON DELETE CASCADE, 
                user_id BIGINT NOT NULL,
                task_name TEXT NOT NULL,
                proof_url TEXT NOT NULL,
                status TEXT DEFAULT 'pending'
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS bingo_completed_tiles (
                event_id INTEGER NOT NULL REFERENCES bingo_events(id) ON DELETE CASCADE,
                task_name TEXT NOT NULL,
                PRIMARY KEY (event_id, task_name) 
            )""")
            await conn.execute("CREATE TABLE IF NOT EXISTS user_links (discord_id BIGINT PRIMARY KEY, osrs_name TEXT NOT NULL)")
            await conn.execute("CREATE TABLE IF NOT EXISTS clan_points (discord_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0)")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS rewards (
                id SERIAL PRIMARY KEY,
                reward_name TEXT NOT NULL UNIQUE,
                point_cost INTEGER NOT NULL,
                description TEXT,
                is_active BOOLEAN DEFAULT TRUE
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS role_rewards (
                reward_id INTEGER PRIMARY KEY,
                role_id BIGINT NOT NULL,
                FOREIGN KEY (reward_id) REFERENCES rewards(id) ON DELETE CASCADE
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS redeem_transactions (
                transaction_id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                reward_id INTEGER NOT NULL,
                reward_name TEXT NOT NULL,
                point_cost INTEGER NOT NULL,
                redeemed_at TIMESTAMPTZ DEFAULT NOW()
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                message_id BIGINT PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                prize TEXT NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                winner_count INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                role_id BIGINT
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                entry_id SERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                UNIQUE (message_id, user_id)
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS pvm_events (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                starts_at TIMESTAMPTZ NOT NULL,
                duration_minutes INTEGER,
                message_id BIGINT,
                channel_id BIGINT,
                signup_message_id BIGINT DEFAULT NULL,
                reminder_sent BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS pvm_event_signups (
                event_id INTEGER REFERENCES pvm_events(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                PRIMARY KEY (event_id, user_id)
            )""")
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)
            """)
            await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('last_recap_sent', $1) ON CONFLICT (key) DO NOTHING", datetime.min.replace(tzinfo=timezone.utc).isoformat())
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS boss_pbs (
                discord_id BIGINT NOT NULL,
                boss_name TEXT NOT NULL,
                pb_time_ms INTEGER NOT NULL,
                proof_url TEXT,
                logged_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (discord_id, boss_name)
            )""")

    except Exception as e:
        print(f"Error setting up database pool or schema: {e}")
        # In a production environment, you might want to exit or raise here
        raise

# --- All View Classes ---
class SotwPollView(discord.ui.View):
    def __init__(self, author):
        super().__init__(timeout=86400)
        self.author = author
        self.votes = {}

    async def create_embed(self):
        ai_embed_data = await generate_announcement_json("sotw_poll")
        vote_description = "\n\n**Current Votes:**\n"
        for skill, voters in self.votes.items(): vote_description += f"**{skill.capitalize()}**: {len(voters)} vote(s)\n"
        embed = discord.Embed.from_dict(ai_embed_data)
        embed.description += vote_description
        embed.set_footer(text=f"Poll started by {self.author.display_name}", icon_url=self.author.display_avatar.url)
        return embed

    def add_buttons(self, skills):
        for skill in skills: self.votes[skill] = []; self.add_item(SotwButton(label=skill.capitalize(), custom_id=skill))
        self.add_item(FinishButton(label="Finish Poll & Start SOTW", custom_id="finish_poll"))

class SotwButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        skill_voted_for = self.custom_id
        
        current_vote_skill = None
        for skill_key, voters in self.view.votes.items():
            if user in voters:
                current_vote_skill = skill_key
                break

        response_message = ""

        if current_vote_skill == skill_voted_for:
            # User clicked the button for the skill they already voted for -> remove vote
            self.view.votes[skill_voted_for].remove(user)
            response_message = f"Your vote for **{self.label}** has been removed."
        elif current_vote_skill is not None:
            # User had a vote for a different skill -> change vote
            self.view.votes[current_vote_skill].remove(user) # Remove old vote
            self.view.votes[skill_voted_for].append(user)    # Add new vote
            response_message = f"Your vote has been changed to **{self.label}**."
        else:
            # User clicked for the first time -> add vote
            self.view.votes[skill_voted_for].append(user)
            response_message = f"Your vote for **{self.label}** has been counted."

        new_embed = await self.view.create_embed()
        await interaction.response.edit_message(embed=new_embed, view=self.view)
        await interaction.followup.send(response_message, ephemeral=True)

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
            await send_global_announcement("sotw_start", {"skill": winner.capitalize()}, sotw_message.jump_url)
            await interaction.followup.send("Competition created in the SOTW channel!", ephemeral=True)
        for item in view.children: item.disabled = True
        await interaction.message.edit(view=view)
        bot.active_polls.pop(interaction.guild.id, None)

class SubmissionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_submission")
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
        async with bot.db_pool.acquire() as conn:
            # FIX: Select event_id from submissions
            submission_data = await conn.fetchrow("SELECT user_id, task_name, event_id FROM bingo_submissions WHERE id = $1", submission_id)
            if not submission_data:
                return await interaction.response.send_message("This submission was already handled.", ephemeral=True)
            user_id, task_name, event_id = submission_data
            
            await conn.execute("UPDATE bingo_submissions SET status = 'approved' WHERE id = $1", submission_id)
            # FIX: Insert into bingo_completed_tiles with event_id
            await conn.execute("INSERT INTO bingo_completed_tiles (event_id, task_name) VALUES ($1, $2) ON CONFLICT (event_id, task_name) DO NOTHING",
                               event_id, task_name)
        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} approved.", ephemeral=True)
        member = interaction.guild.get_member(user_id) # Use interaction.guild as context
        if member:
            await award_points(member, 25, f"completing the bingo task: '{task_name}'")
        await update_bingo_board_post()

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_submission")
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])
        async with bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE bingo_submissions SET status = 'denied' WHERE id = $1", submission_id)
        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} denied.", ephemeral=True)

class GiveawayView(discord.ui.View):
    def __init__(self, message_id):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="\n\n\nEnter Giveaway", style=discord.ButtonStyle.primary, custom_id="giveaway_entry_button")
    async def enter_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        async with bot.db_pool.acquire() as conn:
            try:
                # Check if user already entered
                existing_entry = await conn.fetchrow("SELECT entry_id FROM giveaway_entries WHERE message_id = $1 AND user_id = $2", self.message_id, interaction.user.id)
                if existing_entry:
                    await interaction.response.send_message("You have already entered this giveaway.", ephemeral=True)
                else:
                    await conn.execute("INSERT INTO giveaway_entries (message_id, user_id) VALUES ($1, $2)", self.message_id, interaction.user.id)
                    await interaction.response.send_message("You have successfully entered the giveaway!", ephemeral=True)
            except Exception as e:
                print(f"Error adding giveaway entry: {e}")
                await interaction.response.send_message("An error occurred while entering the giveaway.", ephemeral=True)

# NEW: PvmEventView for event sign-ups
class PvmEventView(discord.ui.View):
    def __init__(self, event_id: int):
        super().__init__(timeout=None)
        self.event_id = event_id

    @discord.ui.button(label="\n\n\n Sign Up", style=discord.ButtonStyle.success, custom_id="pvm_signup_button")
    async def signup_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        async with bot.db_pool.acquire() as conn:
            try:
                # Check if user already signed up
                existing_signup = await conn.fetchrow("SELECT user_id FROM pvm_event_signups WHERE event_id = $1 AND user_id = $2", self.event_id, interaction.user.id)
                if existing_signup:
                    await interaction.response.send_message("You are already signed up for this event.", ephemeral=True)
                else:
                    await conn.execute("INSERT INTO pvm_event_signups (event_id, user_id) VALUES ($1, $2)", self.event_id, interaction.user.id)
                    await interaction.response.send_message("You have signed up for this PVM event!", ephemeral=True)
            except Exception as e:
                print(f"Error signing up for PVM event: {e}")
                await interaction.response.send_message("An error occurred while signing up for the event.", ephemeral=True)
    
    @discord.ui.button(label="\n\n\n Withdraw", style=discord.ButtonStyle.danger, custom_id="pvm_withdraw_button")
    async def withdraw_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        async with bot.db_pool.acquire() as conn:
            try:
                result = await conn.execute("DELETE FROM pvm_event_signups WHERE event_id = $1 AND user_id = $2", self.event_id, interaction.user.id)
                if 'DELETE 1' in result:
                    await interaction.response.send_message("You have withdrawn from this PVM event.", ephemeral=True)
                else:
                    await interaction.response.send_message("You were not signed up for this event.", ephemeral=True)
            except Exception as e:
                print(f"Error withdrawing from PVM event: {e}")
                await interaction.response.send_message("An error occurred while withdrawing from the event.", ephemeral=True)

# --- Helper Functions ---
async def load_item_mapping():
    """Fetches the item name-to-ID mapping from the OSRS Cloud API on startup."""
    url = "https://prices.osrs.cloud/api/v1/latest/mapping"
    headers = {'User-Agent': 'GrazyBot/1.0'}
    # Adding a 30-second timeout to prevent the bot from hanging forever
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    bot.item_mapping = {item['name'].lower(): item for item in data}
                    print(f"Successfully loaded {len(bot.item_mapping)} items in the background.")
                else:
                    print(f"Error loading item mapping: API returned status {response.status}")
        except asyncio.TimeoutError:
            print("Error loading item mapping: The request timed out.")
        except Exception as e:
            print(f"An exception occurred while loading item mapping: {e}")

def format_price_timestamp(ts: int) -> str:
    """Formats a UNIX timestamp into a human-readable relative time string."""
    if not ts: return "N/A"
    dt_object = datetime.fromtimestamp(ts, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - dt_object
    if delta.total_seconds() < 60: return "just now"
    elif delta.total_seconds() < 3600:
        minutes = int(delta.total_seconds() / 60)
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    elif delta.total_seconds() < 86400:
        hours = int(delta.total_seconds() / 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    else:
        days = delta.days
        return f"{days} day{'s' if days > 1 else ''} ago"

def get_wom_metric_url(metric):
    """Generates a URL for a specific OSRS Wiki skill/activity icon."""
    base_url = "https://oldschool.runescape.wiki/images/"
    icon_map = {"attack": "Attack_icon.png", "strength": "Strength_icon.png", "defence": "Defence_icon.png", "hitpoints": "Hitpoints_icon.png", "ranged": "Ranged_icon.png", "magic": "Magic_icon.png", "prayer": "Prayer_icon.png", "vorkath": "Vorkath.png", "zulrah": "Zulrah.png", "chambers_of_xeric": "Olmlet.png", "tombs_of_amascut": "Tumeken's_guardian.png"}
    filename = icon_map.get(metric.lower().replace(" ", "_"), "Coins_10000.png")
    return f"{base_url}{filename}"

async def award_points(member: discord.Member, amount: int, reason: str):
    if not member or member.bot: return
    async with bot.db_pool.acquire() as conn:
        # Ensure discord_id exists or create with 0 points
        await conn.execute("INSERT INTO clan_points (discord_id, points) VALUES ($1, 0) ON CONFLICT (discord_id) DO NOTHING", member.id)
        # Update points and return new balance
        new_balance = await conn.fetchval("UPDATE clan_points SET points = points + $1 WHERE discord_id = $2 RETURNING points", amount, member.id)
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
                async with bot.db_pool.acquire() as conn:
                    await conn.execute("INSERT INTO active_competitions (id, title, starts_at, ends_at) VALUES ($1, $2, $3, $4)", comp_data['competition']['id'], comp_data['competition']['title'], comp_data['competition']['startsAt'], comp_data['competition']['endsAt'])
                return comp_data, None
            else: return None, f"API Error: {(await response.json()).get('message', 'Failed to create competition.')}"

async def create_competition_embed(data, author, poll_winner=False):
    comp = data['competition']; comp_id = comp['id']
    details = {"skill": comp['metric'].capitalize()}
    ai_embed_data = await generate_announcement_json("sotw_start", details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.url = f"https://wiseoldman.net/competitions/{comp_id}"
    start_dt = datetime.fromisoformat(comp['startsAt'].replace('Z', '+00:00')); end_dt = datetime.fromisoformat(comp['endsAt'].replace('Z', '+00:00'))
    embed.add_field(name="Skill", value=comp['metric'].capitalize(), inline=True); embed.add_field(name="Duration", value=f"{(end_dt - start_dt).days} days", inline=True); embed.add_field(name="\n\n", value="\n\n", inline=True); embed.add_field(name="Start Time", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True); embed.add_field(name="End Time", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
    embed.set_footer(text=f"Competition started by {author.display_name}", icon_url=author.display_avatar.url)
    return embed

async def generate_recap_text(gains_data: list):
    data_summary = ""
    for i, player in enumerate(gains_data[:10]):
        rank = i + 1; username = player['player']['displayName']; gained = player.get('gained', 0)
        data_summary += f"{rank}. {username}: {gained:,} XP\n"
    prompt = f"You are the Taskmaster for an Old School RuneScape clan. Your tone is formal and encouraging. Write a weekly recap based on the following data. Announce the top 3 with extra flair. Keep it to a few short paragraphs. Do not use emojis or markdown. Data:\n{data_summary}"
    try:
        response = await ai_model.generate_content_async(prompt)
        return response.text
    except Exception as e:
        print(f"An error occurred with the Gemini API: {e}")
        return "The Taskmaster is currently reviewing the ledgers."

# Define fallbacks globally or in a separate configuration module
EMBED_FALLBACKS = {
    "sotw_poll": {"title": "\n\n A Council of Skills is Convened!", "description": "The time has come, warriors! The council is convened to determine our next great trial. Which skill shall we dedicate ourselves to mastering in the coming week? Lend your voice and cast your vote below, for your decision holds the power to shape our destiny and focus our collective might!", "color": 15105600},
    "sotw_start": {"title": "\n\n The Trial of {skill} Begins! \n\n", "description": "Hark, warriors! The clan has spoken, and the gauntlet is thrown! A grand trial of **{skill}** commences now, a test of endurance and mastery. Dedicate yourselves to the grind, for the gods of skill observe! Prove your worth, rise through the ranks, and claim the champion's glory that awaits the victor!", "color": 5763719},
    "raffle_start": {"title": "\n\n Fortune's Favor is Upon Us!", "description": "Tremble before the whims of fate! The gods of chance have smiled upon our clan, bestowing upon us a grand raffle! A magnificent prize of **{prize}** is at stake, a treasure worthy of legends. To claim your chance at this boon, simply utter the ancient command: `/raffle enter`. Your ticket to destiny awaits!", "color": 15844367},
    "giveaway_start": {"title": "\n\n A Gift to the Worthy! \n\n", "description": "To honor your dedication, a new giveaway has commenced! Press the button below for a chance to claim the prize of **{prize}**!", "color": 3066993},
    "bingo_start": {"title": "\n\n The Taskmaster's Gauntlet is Thrown! \n\n", "description": "Behold, warriors! The Taskmaster has unveiled a new challenge, a complex tapestry of trials designed to test the full breadth of your abilities! The clan bingo board awaits, filled with unique tasks that demand versatility and teamwork. Step forth, examine the challenges, and prove your mastery!", "color": 11027200},
    "points_award": {"title": "\n\n Your Renown Grows!", "description": "Hark! For your commendable dedication in *{reason}*, your standing within the clan has increased! You have been awarded a significant **{amount} Clan Points**! These points are a testament to your growing renown and can be exchanged for powerful boons and legendary artifacts within the clan's esteemed point store. Well done, warrior!", "color": 5763719},
    "pvm_event_start": {"title": "\n\n A Call to Arms: {title}! \n\n", "description": "Hear ye, hear ye! The time for battle is nigh! A new PVM event, **{title}**, has been declared! On <t:{start_time_unix}:F>, we shall embark on {description_text}. Gather your gear, sharpen your blades, and sign up below to join the ranks of heroes!", "color": 16711680},
    "default": {"title": "\n\n A New Calling!", "description": "A new event has begun! Answer the call.", "color": 3447003}
}

async def generate_announcement_json(event_type: str, details: dict = None) -> dict:
    details = details or {}
    persona_prompt = """
You are TaskmasterGPT, the grandmaster of clan events for a Discord server.
Your tone is epic, engaging, slightly cheeky, and highly detailed. You are here to build excitement, rally the members with compelling narratives, and provide all necessary information with flair.
Your task is to generate a JSON object for a Discord embed with "title", "description", and "color" keys.
Use vivid language and Discord markdown like **bold** or *italics*. Do not use emojis.
Make every announcement sound like a legendary event is unfolding, providing rich, descriptive text for the "description" field. Aim for a few sentences or a short paragraph for the description, not just one short line.
"""
    specific_prompts = {
        "sotw_poll": "Generate a detailed and engaging embed description for a new Skill of the Week poll. The description must implore the clan to lend their voice to the council, explaining that their choice will shape the clan's focus for the coming week. Frame it as a vital call to arms, emphasizing the importance of their vote in selecting the next skill challenge that will test their mettle and bring glory.",
        "sotw_start": f"Generate a detailed and engaging embed description announcing the triumphant start of a grand Skill of the Week competition for the skill: **{details.get('skill', 'a new skill')}**. Describe it as a demanding trial of dedication and perseverance. Encourage all warriors to hone their craft in this specific skill, declaring that the ancient gods of skill are watching their every action. Announce clearly that immense glory and recognition await the champion who rises to the top of the leaderboard.",
        "raffle_start": f"Generate a detailed and engaging embed description for a new clan raffle. Describe the grand prize of **{details.get('prize', 'a grand prize')}** as a magnificent treasure or a legendary boon from the gods of fortune. Clearly and enticingly instruct members on how to enter by simply using the /raffle enter command, framing it as claiming their single, precious ticket to destiny and a chance at immense luck.",
        "giveaway_start": f"Generate an embed announcing a new giveaway for **{details.get('prize', 'a fabulous prize')}**. Frame it as a token of appreciation from the clan leadership. State that {'a single victor' if details.get('winner_count', 1) == 1 else f'{details.get('winner_count', 1)} lucky victors'} will be chosen. Instruct members to click the button below to enter for a chance to win.",
        "bingo_start": "Generate a detailed and engaging embed description announcing the commencement of a new clan bingo event. Describe it as a complex tapestry of diverse trials and unique challenges woven by the Taskmaster himself to test the clan's versatility, skill, and teamwork. Issue a clear challenge to the clan to prove their adaptability and work together by completing the various tasks laid out on the ancient, mystical board.",
        "points_award": f"Generate a detailed and engaging embed description for a private message to a member. Announce they have been awarded **{details.get('amount', 'a number of')} Clan Points** specifically for *{details.get('reason', 'your excellent performance')}*. Explain that these points are not mere tokens, but a tangible measure of their growing renown, dedication, and value to the clan, and that they can be traded for legendary artifacts, powerful boons, and exclusive privileges within the clan's esteemed point store.",
        "pvm_event_start": f"Generate an epic and engaging embed description for a new PVM event titled: **{details.get('title', 'a grand PVM event')}**. Describe it as a critical expedition or a heroic stand against formidable foes. Emphasize the need for valor, strategy, and teamwork. Inform the warriors that it commences on <t:{details.get('start_time_unix')}:F> and urge them to sign up to secure their place in legend and claim their share of the glory.",
    }

    specific_prompt = specific_prompts.get(event_type, specific_prompts.get("default", "Generate a general clan announcement about a new event.")) # Added a default catch if specific_prompts has a 'default'
    fallback = EMBED_FALLBACKS.get(event_type, EMBED_FALLBACKS["default"])
    
    # Format the fallback description if it has placeholders
    if 'description' in fallback:
        try:
            fallback['description'] = fallback['description'].format(**details)
        except KeyError:
            pass # Fallback to original description if keys are missing
    if 'title' in fallback:
        try:
            fallback['title'] = fallback['title'].format(**details)
        except KeyError:
            pass

    full_prompt = f"{persona_prompt}\n\nRequest: {specific_prompt}\n\nJSON Output:"
    try:
        response = await ai_model.generate_content_async(full_prompt)
        clean_json_string = response.text.strip().lstrip("```json").rstrip("```")
        return json.loads(clean_json_string)
    except Exception as e:
        print(f"An error occurred during JSON generation for {event_type}: {e}")
        return fallback

# FIX: draw_raffle_winner to select an eligible raffle (oldest ended but not drawn)
async def draw_raffle_winner(raffle_channel: discord.TextChannel):
    async with bot.db_pool.acquire() as conn:
        # Find the oldest raffle that has ended and not yet drawn a winner
        raffle_data = await conn.fetchrow("SELECT * FROM raffles WHERE ends_at < NOW() AND winner_id IS NULL ORDER BY ends_at ASC LIMIT 1")
        if not raffle_data:
            return "No ended raffles to draw."
        
        raffle_id = raffle_data['id']
        prize = raffle_data['prize']
        message_id = raffle_data['message_id']
        
        entries = await conn.fetch("SELECT user_id FROM raffle_entries WHERE raffle_id = $1", raffle_id)
        
        if not entries:
            await raffle_channel.send(f"The raffle for **{prize}** (ID: {raffle_id}) has ended, but alas, no one entered the contest of fate.")
            await conn.execute("UPDATE raffles SET winner_id = 0 WHERE id = $1", raffle_id)
        else:
            winner_id = random.choice(entries)['user_id']
            winner_user = await bot.fetch_user(winner_id)
            await award_points(winner_user, 50, f"winning the raffle for {prize}")
            
            raffle_embed = discord.Embed(title="\n\n Raffle Winner Announcement! \n\n", description=f"The fates have chosen! Congratulations to {winner_user.mention}, you have won the raffle!", color=discord.Color.fuchsia())
            raffle_embed.add_field(name="Prize", value=f"**{prize}**", inline=False)
            raffle_embed.set_footer(text=f"Raffle ID: {raffle_id} | Thanks to everyone for participating!")
            raffle_embed.set_thumbnail(url=winner_user.display_avatar.url)
            await raffle_channel.send(embed=raffle_embed)
            
            announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
            if announcements_channel:
                announcement_embed = discord.Embed(title="\n\n A Champion of Fortune! \n\n", description=f"Let the entire clan celebrate! {winner_user.mention} has emerged victorious in the recent test of luck.", color=discord.Color.gold())
                announcement_embed.add_field(name="Prize Claimed", value=f"The grand prize of **{prize}** is now theirs!", inline=False)
                announcement_embed.add_field(name="Bonus Reward", value="For this victory, they have also been granted **50 Clan Points**!", inline=False)
                announcement_embed.set_thumbnail(url=winner_user.display_avatar.url)
                announcement_embed.set_footer(text=f"Raffle ID: {raffle_id} | May their luck inspire us all.")
                await announcements_channel.send(content=f"@everyone Congratulations to our winner, {winner_user.mention}!", embed=announcement_embed)
            
            await conn.execute("UPDATE raffles SET winner_id = $1 WHERE id = $2", winner_id, raffle_id)
        
        return f"Winner drawn for the '{prize}' raffle (ID: {raffle_id})."

async def end_giveaway(giveaway_data):
    message_id = giveaway_data['message_id']
    channel_id = giveaway_data['channel_id']
    prize = giveaway_data['prize']
    winner_count = giveaway_data['winner_count']
    role_id = giveaway_data.get('role_id')
    async with bot.db_pool.acquire() as conn:
        await conn.execute("UPDATE giveaways SET is_active = FALSE WHERE message_id = $1", message_id)
        entries = await conn.fetch("SELECT user_id FROM giveaway_entries WHERE message_id = $1", message_id)
    user_ids = [entry['user_id'] for entry in entries]
    channel = bot.get_channel(channel_id)
    guild = channel.guild if channel else None
    if not channel or not guild:
        print(f"Error: Could not find channel or guild for giveaway {message_id}")
        return
    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        message = None
    if not user_ids:
        no_entries_embed = discord.Embed(title="\n\n Giveaway Ended", description=f"The giveaway for **{prize}** has ended, but there were no entries.", color=discord.Color.dark_grey())
        await channel.send(embed=no_entries_embed)
        if message: await message.edit(view=None)
        return
    num_to_select = min(winner_count, len(user_ids))
    winner_ids = random.sample(user_ids, k=num_to_select)
    winner_mentions = [f"<@{winner_id}>" for winner_id in winner_ids]
    win_str = "Winner" if len(winner_mentions) == 1 else "Winners"
    announcement_embed = discord.Embed(title=f"\n\n Giveaway {win_str}! \n\n", description=f"Congratulations to {', '.join(winner_mentions)}! You have won the giveaway!", color=discord.Color.gold())
    announcement_embed.add_field(name="Prize", value=f"**{prize}**", inline=False)
    role_to_award = guild.get_role(role_id) if role_id else None
    if role_to_award:
        for winner_id in winner_ids:
            try:
                member = await guild.fetch_member(winner_id)
                await member.add_roles(role_to_award)
            except Exception as e:
                print(f"Failed to add role {role_to_award.name} to member {winner_id}: {e}")
        announcement_embed.description += f"\nThey have also been awarded the **{role_to_award.name}** role!"
    await channel.send(content=f"Congratulations {', '.join(winner_mentions)}!", embed=announcement_embed)
    if message:
        ended_embed = message.embeds[0]
        ended_embed.title = "\n\n Giveaway Ended \n\n"
        ended_embed.color = discord.Color.dark_red()
        field_indices_to_remove = []
        for i, field in enumerate(ended_embed.fields):
            if "Ends In" in field.name or "Entries" in field.name:
                field_indices_to_remove.append(i)
        for i in sorted(field_indices_to_remove, reverse=True):
            ended_embed.remove_field(index=i)
        ended_embed.add_field(name=f"{win_str}", value=', '.join(winner_mentions), inline=False)
        await message.edit(embed=ended_embed, view=None)

def parse_duration(duration_str: str) -> timedelta:
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

# FIX: generate_bingo_image for better font, sizing, and wrapping
def generate_bingo_image(tasks: list, completed_tasks: list = []):
    try:
        width, height = 1000, 1000
        background_color = (40, 26, 13) # Dark brown/gold-ish
        img = Image.new('RGB', (width, height), background_color)
        draw = ImageDraw.Draw(img)

        # Define a path for your fonts directory and a default font file
        FONT_DIR = "fonts" # Create a 'fonts' directory in your bot's root
        DEFAULT_FONT_FILENAME = "Roboto-Regular.ttf" # Example: you'd place this font file here
        DEFAULT_FONT_PATH = os.path.join(FONT_DIR, DEFAULT_FONT_FILENAME)
        
        # Ensure the font directory exists (helpful for local development, can be removed in deployment if directory is managed)
        # if not os.path.exists(FONT_DIR):
        #    os.makedirs(FONT_DIR)

        try:
            if os.path.exists(DEFAULT_FONT_PATH):
                title_font = ImageFont.truetype(DEFAULT_FONT_PATH, 60)
                task_font = ImageFont.truetype(DEFAULT_FONT_PATH, 28)
            else:
                print(f"Warning: Custom font '{DEFAULT_FONT_PATH}' not found. Falling back to default font. Consider adding a .ttf file.")
                title_font = ImageFont.load_default(size=60) 
                task_font = ImageFont.load_default(size=28)
        except Exception as e:
            print(f"Error loading custom font: {e}. Falling back to default font.")
            title_font = ImageFont.load_default(size=60) 
            task_font = ImageFont.load_default(size=28)
        
        title_text = "CLAN BINGO"
        text_bbox = draw.textbbox((0, 0), title_text, font=title_font)
        title_width = text_bbox[2] - text_bbox[0]
        draw.text(((width - title_width) / 2, 20), title_text, font=title_font, fill=(255, 215, 0)) # OSRS Gold color

        grid_size = 5
        grid_top_y = 120
        grid_bottom_y = height - 50
        
        cell_size = (grid_bottom_y - grid_top_y) / grid_size
        effective_grid_width = grid_size * cell_size
        left_margin = (width - effective_grid_width) / 2

        line_color = (255, 215, 0)
        line_width = 3

        for i in range(grid_size + 1):
            draw.line([(left_margin + i * cell_size, grid_top_y), (left_margin + i * cell_size, grid_bottom_y)], fill=line_color, width=line_width)
            draw.line([(left_margin, grid_top_y + i * cell_size), (left_margin + effective_grid_width, grid_top_y + i * cell_size)], fill=line_color, width=line_width)
        
        for i, task in enumerate(tasks):
            if i >= grid_size * grid_size: break

            row = i // grid_size
            col = i % grid_size
            
            cell_x_start = int(left_margin + col * cell_size)
            cell_y_start = int(grid_top_y + row * cell_size)
            
            if task['name'] in completed_tasks:
                overlay = Image.new('RGBA', (int(cell_size), int(cell_size)), (0, 255, 0, 90))
                img.paste(overlay, (cell_x_start, cell_y_start), overlay)
            
            task_name = task['name']
            
            # Dynamically calculate max_chars_per_line based on font metrics for better wrapping
            # A crude estimate: average character width (adjust if a more precise method is needed)
            avg_char_width = sum(task_font.getlength(c) for c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') / 62
            max_chars_per_line = int((cell_size - 10) / avg_char_width) # 10 pixels for padding
            
            wrapped_text = textwrap.fill(task_name, width=max_chars_per_line)
            
            text_bbox = draw.textbbox((0, 0), wrapped_text, font=task_font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            text_x = cell_x_start + (cell_size - text_width) / 2
            text_y = cell_y_start + (cell_size - text_height) / 2
            
            draw.text((text_x, text_y), wrapped_text, font=task_font, fill=(255, 255, 255))
        
        output_path = "bingo_board.png"
        img.save(output_path)
        return output_path, None
    except Exception as e:
        print(f"Error in generate_bingo_image: {e}")
        return None, f"An unexpected error occurred during image generation: {e}"

# FIX: update_bingo_board_post to filter completed tiles by active event
async def update_bingo_board_post():
    async with bot.db_pool.acquire() as conn:
        # Get the active event's ID and board data
        event_data = await conn.fetchrow("SELECT id, board_json, message_id FROM bingo_events WHERE is_active = TRUE LIMIT 1")
        if not event_data:
            print("No active bingo event to update.")
            return
        
        current_event_id, board_tasks_json, message_id = event_data
        board_tasks = json.loads(board_tasks_json)
        
        # FIX: Filter completed tiles by the current event_id
        completed_tiles = [row[0] for row in await conn.fetch("SELECT task_name FROM bingo_completed_tiles WHERE event_id = $1", current_event_id)]
    
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

# --- Event Manager & Periodic Reminder Tasks ---
@tasks.loop(hours=4)
async def periodic_event_reminder():
    await bot.wait_until_ready()
    announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not announcements_channel:
        print("Cannot send periodic reminder: Announcements channel not found.")
        return
    async with bot.db_pool.acquire() as conn:
        sotw = await conn.fetchrow("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        raffle = await conn.fetchrow("SELECT * FROM raffles WHERE ends_at > NOW() AND winner_id IS NULL ORDER BY ends_at DESC LIMIT 1")
        giveaway = await conn.fetchrow("SELECT * FROM giveaways WHERE ends_at > NOW() AND is_active = TRUE ORDER BY ends_at DESC LIMIT 1")
        pvm_event = await conn.fetchrow("SELECT * FROM pvm_events WHERE is_active = TRUE AND starts_at > NOW() ORDER BY starts_at ASC LIMIT 1")

    event_summary = ""
    if sotw: event_summary += f"- A Skill of the Week competition for **{sotw['title']}** is underway!\n"
    if raffle: event_summary += f"- A raffle for the legendary **{raffle['prize']}** is active! Use `/raffle enter`.\n"
    if giveaway: event_summary += f"- A giveaway for **{giveaway['prize']}** is happening now! Find the message and click the button to enter.\n"
    if pvm_event:
        event_summary += f"- A PVM event: **{pvm_event['title']}** starts <t:{int(pvm_event['starts_at'].timestamp())}:R>! Use `/pvm signup` to join.\n"

    if not event_summary:
        print("No active events for periodic reminder.")
        return
    prompt = """
    You are TaskmasterGPT, the wise and ancient lore-keeper for a clan of warriors.
    Your task is to write a bulletin summarizing the clan's active events. Your tone is epic, grand, and encouraging.
    Use the following information to compose your message. Frame it as a call to continue the good fight and remind everyone of the glories to be won.
    Active Events:
    {event_summary}
    Write a compelling summary in a few short paragraphs.
    """
    try:
        response = await ai_model.generate_content_async(prompt)
        description = response.text
        embed = discord.Embed(title="\n\n The Taskmaster's Bulletin \n\n", description=description, color=discord.Color.dark_gold())
        embed.set_footer(text="Seize the day, warriors!")
        await announcements_channel.send(embed=embed)
    except Exception as e:
        print(f"Failed to generate or send periodic reminder: {e}")

@tasks.loop(minutes=5)
async def event_manager():
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)
    recap_channel = bot.get_channel(RECAP_CHANNEL_ID)

    # Weekly Recap (Sunday 7 PM UTC)
    if recap_channel:
        async with bot.db_pool.acquire() as conn_recap:
            last_recap_timestamp_str = await conn_recap.fetchval("SELECT value FROM bot_settings WHERE key = 'last_recap_sent'")
            last_recap_dt = datetime.fromisoformat(last_recap_timestamp_str) if last_recap_timestamp_str else datetime.min.replace(tzinfo=timezone.utc)
            
            # Calculate the most recent past or current Sunday 7 PM UTC
            recap_trigger_time = now.replace(hour=19, minute=0, second=0, microsecond=0)
            
            # If current time is before Sunday 7 PM, or it's not Sunday at all,
            # then the `recap_trigger_time` is referring to a *future* Sunday or incorrect day.
            # Adjust it to the *previous* Sunday 7 PM.
            if now.weekday() != 6 or now < recap_trigger_time:
                # Calculate days to subtract to get to the previous Sunday (Sunday is weekday 6)
                # (now.weekday() - 6 + 7) % 7 will be 0 for Sunday, 1 for Monday, etc.
                days_to_subtract_for_last_sunday = (now.weekday() - 6 + 7) % 7 
                recap_trigger_time = (now - timedelta(days=days_to_subtract_for_last_sunday)).replace(hour=19, minute=0, second=0, microsecond=0)
                
                # If the current time is still before `recap_trigger_time` (e.g., if we're on a Monday and `recap_trigger_time` was computed as the *next* Sunday),
                # we need to push it back another week to ensure `recap_trigger_time` is always the *most recent passed or current* Sunday 7 PM UTC.
                if now < recap_trigger_time:
                    recap_trigger_time -= timedelta(weeks=1)

            # Check if we should run the recap:
            # 1. Is the current time past the trigger time for this week's recap? (`now >= recap_trigger_time`)
            # 2. Was the last recap sent *before* this week's trigger time? (`last_recap_dt < recap_trigger_time`)
            if now >= recap_trigger_time and last_recap_dt < recap_trigger_time:
                url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/gained?period=week&metric=overall"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            recap_text = await generate_recap_text(data)
                            embed = discord.Embed(title="\n\n Weekly Recap from the Taskmaster", description=recap_text, color=discord.Color.from_rgb(100, 150, 255))
                            embed.set_footer(text=f"Recap for the week ending {now.strftime('%B %d, %Y')}")
                            await recap_channel.send(embed=embed)
                            
                            # Update the last recap sent timestamp
                            await conn_recap.execute("INSERT INTO bot_settings (key, value) VALUES ('last_recap_sent', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", now.isoformat())
                        else:
                            print(f"Error fetching WOM data for weekly recap: {response.status}")
    
    # --- SOTW Processing ---
    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if sotw_channel:
        # Determine the guild context once for this channel
        current_guild = sotw_channel.guild 
        if not current_guild:
            print(f"Warning: Could not determine guild for SOTW channel {SOTW_CHANNEL_ID}. Skipping SOTW processing.")
            return # Exit if guild context is missing

        async with bot.db_pool.acquire() as conn:
            competitions = await conn.fetch("SELECT * FROM active_competitions")
            
            for comp in competitions:
                ends_at = comp['ends_at']; starts_at = comp['starts_at']
                
                # Award Winners
                if now > ends_at and not comp['winners_awarded']:
                    details_url = f"https://api.wiseoldman.net/v2/competitions/{comp['id']}"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(details_url) as response:
                            if response.status == 200:
                                comp_data = await response.json()
                                point_values = [100, 50, 25] # 1st, 2nd, 3rd
                                
                                for i, participant in enumerate(comp_data.get('participations', [])[:3]):
                                    osrs_name = participant['player']['displayName']
                                    user_data = await conn.fetchrow("SELECT discord_id FROM user_links WHERE osrs_name = $1", osrs_name)
                                    
                                    if user_data:
                                        # Use current_guild for member lookup
                                        member = current_guild.get_member(user_data['discord_id'])
                                        if member:
                                            await award_points(member, point_values[i], f"placing #{i+1} in the {comp['title']} SOTW")
                    
                    # Update competition status AFTER all awards are processed and external API calls completed
                    await conn.execute("UPDATE active_competitions SET winners_awarded = TRUE WHERE id = $1", comp['id'])
                
                # Send reminders (ensure await before DB update)
                if not comp['final_ping_sent'] and (ends_at - now) <= timedelta(hours=1):
                    reminder_embed = discord.Embed(title="\n\n Final Hour!", description=f"The **{comp['title']}** competition ends in less than an hour!", color=discord.Color.red(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                    await sotw_channel.send(content="@everyone", embed=reminder_embed)
                    await conn.execute("UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = $1", comp['id'])
                elif not comp['midway_ping_sent'] and now >= starts_at + ((ends_at - starts_at) / 2):
                    midway_embed = discord.Embed(title="\n\n Midway Point Reached!", description=f"The **{comp['title']}** competition is halfway through!", color=discord.Color.yellow(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                    await sotw_channel.send(embed=midway_embed)
                    await conn.execute("UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = $1", comp['id'])
        
    
    # --- Raffle Processing ---
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if raffle_channel:
        async with bot.db_pool.acquire() as conn_raffle:
            raffle_data = await conn_raffle.fetchrow("SELECT * FROM raffles WHERE ends_at < $1 AND winner_id IS NULL LIMIT 1", now)
            if raffle_data:
                await draw_raffle_winner(raffle_channel)

    # --- Giveaway Processing ---
    async with bot.db_pool.acquire() as conn_gw:
        ended_giveaways = await conn_gw.fetch("SELECT * FROM giveaways WHERE ends_at < $1 AND is_active = TRUE", now)
        for giveaway in ended_giveaways:
            await end_giveaway(giveaway)
        
        active_giveaways = await conn_gw.fetch("SELECT message_id, channel_id, ends_at FROM giveaways WHERE is_active = TRUE")
        for giveaway in active_giveaways:
            try:
                entry_count = await conn_gw.fetchval("SELECT COUNT(user_id) FROM giveaway_entries WHERE message_id = $1", giveaway['message_id'])
                channel = bot.get_channel(giveaway['channel_id'])
                if not channel: continue
                message = await channel.fetch_message(giveaway['message_id'])
                embed = message.embeds[0]
                
                # FIX: More robust logic for updating entry count field
                entry_field_index = -1
                for i, field in enumerate(embed.fields):
                    if "Entries" in field.name:
                        entry_field_index = i
                        break
                
                new_entry_value = f"\n\n **Entries:** {entry_count}"
                
                if entry_field_index != -1:
                    if embed.fields[entry_field_index].value != new_entry_value:
                        embed.set_field_at(entry_field_index, name="Entries", value=new_entry_value, inline=True)
                        await message.edit(embed=embed)
                else:
                    # Add new field. Can choose index if order is important.
                    embed.add_field(name="Entries", value=new_entry_value, inline=True)
                    await message.edit(embed=embed)

            except discord.NotFound:
                await conn_gw.execute("UPDATE giveaways SET is_active = FALSE WHERE message_id = $1", giveaway['message_id'])
            except Exception as e:
                print(f"Error updating giveaway entry count for {giveaway['message_id']}: {e}")

    # NEW: PVM Event reminders and cleanup
    pvm_channel = bot.get_channel(PVM_EVENT_CHANNEL_ID)
    if pvm_channel:
        async with bot.db_pool.acquire() as conn_pvm:
            upcoming_events = await conn_pvm.fetch("SELECT * FROM pvm_events WHERE is_active = TRUE AND reminder_sent = FALSE AND starts_at - INTERVAL '1 hour' <= $1", now)

            for event in upcoming_events:
                if event['starts_at'] > now:
                    # Send 1-hour reminder
                    event_embed = discord.Embed(
                        title=f"\n\n PVM Event Reminder: {event['title']}",
                        description=f"Our grand expedition to '{event['title']}' begins in less than an hour! Gather your comrades, prepare your gear, and brace yourselves for adventure!\n\nStarts: <t:{int(event['starts_at'].timestamp())}:R>\n[Event Details]({event['message_id']})",
                        color=discord.Color.orange()
                    )
                    event_embed.set_footer(text="May your adventures be glorious!")
                    await pvm_channel.send(content="@here", embed=event_embed)
                    await conn_pvm.execute("UPDATE pvm_events SET reminder_sent = TRUE WHERE id = $1", event['id'])
                else:
                    # Event has passed, mark as inactive
                    await conn_pvm.execute("UPDATE pvm_events SET is_active = FALSE WHERE id = $1", event['id'])

async def handle_http(request):
    """A simple HTTP handler for health checks."""
    return web.Response(text="Bot is running.")
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

    asyncio.create_task(load_item_mapping())

    # FIX: Corrected typo from awaitsetup_database()
    await setup_database_pool() 
    
    event_manager.start()
    periodic_event_reminder.start()

    # Re-register persistent views for giveaways
    async with bot.db_pool.acquire() as conn:
        active_giveaways = await conn.fetch("SELECT message_id FROM giveaways WHERE is_active = TRUE AND ends_at > NOW()")
        if active_giveaways:
            print(f"Re-registering {len(active_giveaways)} active giveaway view(s)...")
            for gw in active_giveaways:
                bot.add_view(GiveawayView(message_id=gw['message_id']))

        # NEW: Re-register persistent views for PVM events
        active_pvm_events = await conn.fetch("SELECT id, signup_message_id FROM pvm_events WHERE is_active = TRUE AND starts_at > NOW()")
        if active_pvm_events:
            print(f"Re-registering {len(active_pvm_events)} active PVM event view(s)...")
            for pvm_event in active_pvm_events:
                if pvm_event['signup_message_id']:
                    bot.add_view(PvmEventView(event_id=pvm_event['id']))
    
    print("Persistent views re-registered.")

# --- BOT COMMANDS ---
admin = bot.create_group("admin", "Admin-only commands for managing the bot and server.")
@admin.command(name="announce", description="Send a message as the bot to a specific channel.")
@discord.default_permissions(manage_guild=True)
async def announce(ctx: discord.ApplicationContext, message: discord.Option(str, "The message to send."), channel: discord.Option(discord.TextChannel, "The channel to send to."), ping_everyone: discord.Option(bool, "Whether to ping @everyone.", default=False)):
    await ctx.defer(ephemeral=True)
    content = "@everyone" if ping_everyone else ""
    embed = discord.Embed(title="\n\n Clan Announcement", description=message, color=discord.Color.orange())
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
async def manage_points(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member to manage points for."), action: discord.Option(str, "Whether to add or remove points.", choices=["add", "remove"]), amount: discord.Option(int, "The number of points to add or remove.", min_value=1), reason: discord.Option(str, "The reason for this point adjustment.")):
    await ctx.defer(ephemeral=True)
    if action == "add":
        await award_points(member, amount, reason)
    else: # remove
        async with bot.db_pool.acquire() as conn:
            await conn.execute("INSERT INTO clan_points (discord_id, points) VALUES ($1, 0) ON CONFLICT (discord_id) DO NOTHING", member.id)
            await conn.execute("UPDATE clan_points SET points = GREATEST(0, points - $1) WHERE discord_id = $2", amount, member.id)
    async with bot.db_pool.acquire() as conn:
        new_balance = await conn.fetchval("SELECT points FROM clan_points WHERE discord_id = $1", member.id)
    await ctx.respond(f"Successfully updated {member.display_name}'s points. Their new balance is {new_balance}.", ephemeral=True)

@admin.command(name="award_sotw_winners", description="Manually award points for a past SOTW competition.")
@discord.default_permissions(manage_guild=True)
async def award_sotw_winners(ctx: discord.ApplicationContext, competition_id: discord.Option(int, "The ID of the competition from Wise Old Man.")):
    await ctx.defer(ephemeral=True)
    details_url = f"https://api.wiseoldman.net/v2/competitions/{competition_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(details_url) as response:
            if response.status != 200: return await ctx.respond(f"Could not fetch details for competition ID {competition_id}.")
            comp_data = await response.json()
    awarded_to = []
    point_values = [100, 50, 25]
    for i, participant in enumerate(comp_data.get('participations', [])[:3]):
        osrs_name = participant['player']['displayName']
        async with bot.db_pool.acquire() as conn:
            user_data = await conn.fetchrow("SELECT discord_id FROM user_links WHERE osrs_name = $1", osrs_name)
        if user_data:
            # FIX: Use ctx.guild for member lookup
            member = ctx.guild.get_member(user_data['discord_id']) 
            if member:
                await award_points(member, point_values[i], f"placing #{i+1} in the {comp_data['title']} SOTW")
                awarded_to.append(f"#{i+1}: {member.display_name} ({point_values[i]} points)")
    if not awarded_to: return await ctx.respond("No winners could be found or linked for that competition.")
    await ctx.respond("Successfully awarded points to:\n" + "\n".join(awarded_to))

@admin.command(name="check_items", description="Check the status of the OSRS item mapping.")
@discord.default_permissions(manage_guild=True)
async def check_items(ctx: discord.ApplicationContext):
    if bot.item_mapping:
        await ctx.respond(f"\n\n The item list is loaded with **{len(bot.item_mapping)}** items.", ephemeral=True)
    else:
        await ctx.respond("\n\n The item list is not loaded yet. Please check the logs for errors.", ephemeral=True)

ge = bot.create_group("ge", "Commands for the Grand Exchange.")
async def item_autocomplete(ctx: discord.AutocompleteContext):
    """Provides autocomplete suggestions for OSRS items."""
    query = ctx.value.lower()
    if not query:
        popular_items = ["Twisted bow", "Scythe of vitur", "Abyssal whip", "Dragon claws"]
        return popular_items
    matches = [name.title() for name in bot.item_mapping.keys() if name.startswith(query)]
    return matches[:25]

@ge.command(name="price", description="Check the Grand Exchange price of an item.")
async def price(ctx: discord.ApplicationContext, item: discord.Option(str, "The name of the item to check.", autocomplete=item_autocomplete)):
    if not bot.item_mapping:
        return await ctx.respond("The item list is still loading from the server. Please wait a few more seconds and try again.", ephemeral=True)
    await ctx.defer()
    item_name_lower = item.lower()
    if item_name_lower not in bot.item_mapping:
        return await ctx.respond("Could not find this item. Please choose one from the list.", ephemeral=True)
    item_details = bot.item_mapping[item_name_lower]
    item_id = item_details['id']
    url = f"https://prices.osrs.cloud/api/v1/latest/item/{item_id}"
    headers = {'User-Agent': 'GrazyBot/1.0'}
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    return await ctx.respond(f"Error fetching price data (Status: {response.status}). Please try again later.", ephemeral=True)
                price_data = await response.json()
                embed = discord.Embed(title=f"Price Check: {item_details['name']}", color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
                icon_url = item_details.get('icon')
                if icon_url: embed.set_thumbnail(url=icon_url)
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
        except Exception as e:
            print(f"Error in /ge price command: {e}")
            await ctx.respond("An unexpected error occurred while fetching price data.", ephemeral=True)

# Add to the 'ge' SlashCommandGroup (after the existing /ge price command)

@ge.command(name="value", description="Calculate the total GE value of multiple items.")
async def calculate_value(
    ctx: discord.ApplicationContext,
    item_list: discord.Option(str, "List of items and quantities (e.g., '10k raw sharks, 2m runes, 1 twisted bow').")
):
    if not bot.item_mapping:
        return await ctx.respond("The item list is still loading from the server. Please wait a few more seconds and try again.", ephemeral=True)
    await ctx.defer()

    total_value = 0
    parsed_items_output = []
    unmatched_items = []

    # Regex to parse 'QUANTITY ITEM_NAME' (handles k for thousands, m for millions, and optional spaces/commas)
    # It finds patterns like '10k raw sharks', '2m runes', '1 twisted bow'.
    # The '(?:,|$)' at the end makes the comma optional or matches end of string.
    item_regex_pattern = r"(\d+(?:\.\d+)?[km]?)\s+([a-zA-Z0-9\s-]+?)(?:,|$)"
    
    # Find all matches in the input string
    matches = re.findall(item_regex_pattern, item_list.lower() + ',') # Add a comma to ensure last item is caught

    if not matches:
        return await ctx.respond("Invalid item list format. Please use 'QUANTITY ITEM_NAME, QUANTITY ITEM_NAME' (e.g., '10k raw sharks, 2m runes').", ephemeral=True)

    for quantity_str, item_name_raw in matches:
        item_name = item_name_raw.strip()
        quantity = 0.0

        # Parse quantity (e.g., "10k", "2m", "5")
        if 'k' in quantity_str:
            quantity = float(quantity_str.replace('k', '')) * 1_000
        elif 'm' in quantity_str:
            quantity = float(quantity_str.replace('m', '')) * 1_000_000
        else:
            quantity = float(quantity_str)

        # Find the item in the bot's loaded mapping
        matched_item = bot.item_mapping.get(item_name)
        if not matched_item:
            # Try more flexible matching (e.g., singular/plural, slightly different spacing)
            # This can be expanded significantly for better fuzzy matching
            matched_item_name = next((k for k in bot.item_mapping if item_name in k or k.startswith(item_name)), None)
            if matched_item_name:
                matched_item = bot.item_mapping[matched_item_name]
                item_name = matched_item_name # Use the canonical name

        if matched_item:
            item_id = matched_item['id']
            url = f"https://prices.osrs.cloud/api/v1/latest/item/{item_id}"
            headers = {'User-Agent': 'GrazyBot/1.0'}
            
            try:
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            price_data = await response.json()
                            # Use 'high' (instant buy) price for valuing items
                            current_price = price_data.get('high', 0)
                            item_value = current_price * quantity
                            total_value += item_value
                            parsed_items_output.append(f"{int(quantity):,} x {matched_item['name']} @ {current_price:,} gp each = {int(item_value):,} gp")
                        else:
                            unmatched_items.append(f"{int(quantity):,} x {matched_item['name']} (Price fetch failed: {response.status})")
            except Exception:
                unmatched_items.append(f"{int(quantity):,} x {matched_item['name']} (Price fetch error)")
        else:
            unmatched_items.append(f"{quantity_str} {item_name_raw.title()} (Item not found in database)")

    embed = discord.Embed(title="\n\n Grand Exchange Value Calculator", color=discord.Color.dark_teal())
    
    if parsed_items_output:
        embed.add_field(name="Items Valued", value="\n".join(parsed_items_output), inline=False)
        embed.add_field(name="Total Estimated Value", value=f"**{int(total_value):,} gp**", inline=False)
    else:
        embed.description = "No items could be valued or found."

    if unmatched_items:
        embed.add_field(name="Could Not Value (or Not Found)", value="\n".join(unmatched_items), inline=False)

    embed.set_footer(text="Prices from osrs.cloud | Calculated using instant buy prices.")
    await ctx.respond(embed=embed)
sotw = bot.create_group("sotw", "Commands for Skill of the Week")
@sotw.command(name="start", description="Manually start a new SOTW competition.")
async def start(ctx, skill: discord.Option(str, choices=WOM_SKILLS), duration_days: discord.Option(int, default=7)):
    await ctx.defer(ephemeral=True)
    data, error = await create_competition(WOM_CLAN_ID, skill, duration_days)
    if error: await ctx.respond(error); return
    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if sotw_channel:
        embed = await create_competition_embed(data, ctx.author)
        sotw_message = await sotw_channel.send(embed=embed)
        await send_global_announcement("sotw_start", {"skill": skill.capitalize()}, sotw_message.jump_url)
        await ctx.respond("SOTW started successfully in the designated channel!", ephemeral=True)
    else:
        await ctx.respond("Error: SOTW Channel ID not configured correctly.", ephemeral=True)

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
        rank_emoji = {1: "\n\n", 2: "\n\n", 3: "\n\n"}.get(i + 1, f"`{i + 1}.`")
        leaderboard_text += f"{rank_emoji} **{player['player']['displayName']}**: {player['progress']['gained']:,} XP\n"
    if not leaderboard_text: leaderboard_text = "No participants have gained XP yet."
    embed.add_field(name="Top 10", value=leaderboard_text, inline=False)
    end_dt = datetime.fromisoformat(data['endsAt'].replace('Z', '+00:00'))
    embed.set_footer(text="Competition ends"); embed.timestamp = end_dt
    await ctx.respond(embed=embed)

raffle = bot.create_group("raffle", "Commands for managing raffles.")
# FIX: Update raffle_start to create unique raffles
@raffle.command(name="start", description="Start a new raffle.")
@discord.default_permissions(manage_events=True)
async def start_raffle(ctx: discord.ApplicationContext, prize: discord.Option(str, "What is the prize?"), duration_days: discord.Option(float, "How many days will it last?")):
    await ctx.defer(ephemeral=True)
    
    ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    
    # Prepare embed and message first, to get message_id
    details = {"prize": prize}
    ai_embed_data = await generate_announcement_json("raffle_start", details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.add_field(name="How to Enter", value="Use `/raffle enter` to get a ticket! (Max 10 per person)", inline=False)
    embed.add_field(name="Raffle Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
    embed.set_footer(text=f"Raffle started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not raffle_channel:
        await ctx.respond("Error: Raffle Channel ID not configured correctly.", ephemeral=True); return

    raffle_message = await raffle_channel.send(embed=embed) # Send message to get its ID

    # FIX: Insert into raffles, get new ID
    async with bot.db_pool.acquire() as conn:
        new_raffle_id = await conn.fetchval("INSERT INTO raffles (prize, ends_at, message_id, channel_id) VALUES ($1, $2, $3, $4) RETURNING id",
                                            prize, ends_at, raffle_message.id, raffle_channel.id)
    
    # Update the embed with Raffle ID in footer for user reference
    updated_embed = raffle_message.embeds[0]
    updated_embed.set_footer(text=f"Raffle ID: {new_raffle_id} | Started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await raffle_message.edit(embed=updated_embed)
    
    await send_global_announcement("raffle_start", {"prize": prize}, raffle_message.jump_url)
    await ctx.respond(f"Raffle (ID: {new_raffle_id}) for **{prize}** created successfully!", ephemeral=True)

# FIX: Update enter_raffle to target the latest active raffle
@raffle.command(name="enter", description="Get one ticket for the current raffle (max 10).")
async def enter_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    async with bot.db_pool.acquire() as conn:
        # Find the latest active raffle
        raffle_data = await conn.fetchrow("SELECT id, prize FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        if not raffle_data:
            return await ctx.respond("There is no active raffle to enter right now.", ephemeral=True)
        
        raffle_id, prize = raffle_data
        self_entries = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2 AND source = 'self'", ctx.author.id, raffle_id)
        if self_entries >= 10:
            return await ctx.respond(f"You have already claimed your maximum of 10 tickets for the '{prize}' raffle!", ephemeral=True)
        
        await conn.execute("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES ($1, $2, 'self')", raffle_id, ctx.author.id)
        total_tickets = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", ctx.author.id, raffle_id)
    await ctx.respond(f"You have successfully claimed a ticket for the **{prize}** raffle! You now have a total of {total_tickets} ticket(s).", ephemeral=True)

@raffle.command(name="give_tickets", description="ADMIN: Give raffle tickets to a member for the active raffle.")
@discord.default_permissions(manage_events=True)
async def give_tickets(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member to give tickets to."), amount: discord.Option(int, "How many tickets to give.", min_value=1)):
    await ctx.defer(ephemeral=True)
    async with bot.db_pool.acquire() as conn:
        raffle_data = await conn.fetchrow("SELECT id FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        if not raffle_data:
            return await ctx.respond("There is no active raffle.", ephemeral=True)
        raffle_id = raffle_data['id']
        entries = [(raffle_id, member.id, 'admin') for _ in range(amount)]
        await conn.copy_records_to_table('raffle_entries', records=entries, columns=['raffle_id', 'user_id', 'source'])
        total_tickets = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", member.id, raffle_id)
    await ctx.respond(f"Successfully gave {amount} ticket(s) to {member.display_name}. They now have {total_tickets} ticket(s) for the active raffle.", ephemeral=True)

@raffle.command(name="edit_tickets", description="ADMIN: Set a member's total ticket count for the active raffle.")
@discord.default_permissions(manage_events=True)
async def edit_tickets(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member whose tickets you want to edit."), new_total: discord.Option(int, "The new total number of tickets they should have.", min_value=0)):
    await ctx.defer(ephemeral=True)
    async with bot.db_pool.acquire() as conn:
        raffle_data = await conn.fetchrow("SELECT id FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        if not raffle_data:
            return await ctx.respond("There is no active raffle.", ephemeral=True)
        raffle_id = raffle_data['id']
        await conn.execute("DELETE FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", member.id, raffle_id)
        if new_total > 0:
            entries = [(raffle_id, member.id, 'admin_edit') for _ in range(new_total)]
            await conn.copy_records_to_table('raffle_entries', records=entries, columns=['raffle_id', 'user_id', 'source'])
    await ctx.respond(f"Successfully set {member.display_name}'s ticket count for the active raffle to {new_total}.", ephemeral=True)

@raffle.command(name="view_tickets", description="View the current ticket count for all participants in the active raffle.")
async def view_tickets(ctx: discord.ApplicationContext):
    await ctx.defer()
    async with bot.db_pool.acquire() as conn:
        raffle_data = await conn.fetchrow("SELECT id, prize FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        if not raffle_data:
            return await ctx.respond("There is no active raffle.")
        raffle_id, prize = raffle_data
        entries = await conn.fetch("SELECT user_id, COUNT(user_id) FROM raffle_entries WHERE raffle_id = $1 GROUP BY user_id ORDER BY COUNT(user_id) DESC", raffle_id)
    embed = discord.Embed(title=f"\n\n Raffle Tickets for '{prize}'", color=discord.Color.gold())
    if not entries:
        embed.description = "No tickets have been given out yet for this raffle."
    else:
        description = ""
        for entry in entries[:20]: # Show top 20
            user_id, count = entry['user_id'], entry['count']
            try:
                member = await ctx.guild.fetch_member(user_id)
                description += f"**{member.display_name}**: {count} ticket(s)\n"
            except discord.NotFound:
                continue # Skip if member left the server
        embed.description = description
    await ctx.respond(embed=embed)

# FIX: draw_now to target a specific raffle (or the latest ended one)
@raffle.command(name="draw_now", description="ADMIN: Immediately ends a raffle and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now(ctx: discord.ApplicationContext, raffle_id: discord.Option(int, "The ID of the raffle to draw. Leave blank to draw the oldest ended raffle.", required=False)):
    await ctx.defer(ephemeral=True)
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not channel: return await ctx.respond("Error: Raffle channel not found.")

    if raffle_id:
        async with bot.db_pool.acquire() as conn:
            # Set ends_at to now for specific raffle if it's still active or not drawn
            updated_id = await conn.fetchval("UPDATE raffles SET ends_at = NOW() WHERE id = $1 AND winner_id IS NULL RETURNING id", raffle_id)
        if not updated_id:
            return await ctx.respond(f"Raffle ID {raffle_id} not found or already ended/drawn.", ephemeral=True)
    
    result = await draw_raffle_winner(channel) 
    await ctx.respond(f"Successfully triggered winner drawing: {result}", ephemeral=True)

# FIX: cancel_raffle to target a specific raffle
@raffle.command(name="cancel", description="ADMIN: Cancels a specific raffle without drawing a winner.")
@discord.default_permissions(manage_events=True)
async def cancel_raffle(ctx: discord.ApplicationContext, raffle_id: discord.Option(int, "The ID of the raffle to cancel.")):
    await ctx.defer(ephemeral=True)
    async with bot.db_pool.acquire() as conn:
        raffle_data = await conn.fetchrow("SELECT prize, message_id FROM raffles WHERE id = $1 AND winner_id IS NULL", raffle_id)
        
        if not raffle_data:
            return await ctx.respond(f"Raffle ID {raffle_id} not found or already ended/drawn.", ephemeral=True)
        
        prize, message_id = raffle_data['prize'], raffle_data['message_id']
        
        # Delete the raffle and its entries (CASCADE takes care of entries)
        await conn.execute("DELETE FROM raffles WHERE id = $1", raffle_id)
    
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if channel:
        try:
            original_message = await channel.fetch_message(message_id)
            await original_message.edit(content=f"**Raffle for {prize} (ID: {raffle_id}) has been cancelled!**", embed=None, view=None)
        except discord.NotFound:
            pass # Message already deleted
        await channel.send(f"The raffle for **{prize}** (ID: {raffle_id}) has been cancelled by an admin.")
    await ctx.respond(f"Raffle (ID: {raffle_id}) successfully cancelled.", ephemeral=True)

events = bot.create_group("events", "View all active clan events.")
@events.command(name="view", description="Shows all currently active competitions and raffles.")
async def view_events(ctx: discord.ApplicationContext):
    await ctx.defer()
    async with bot.db_pool.acquire() as conn:
        comp = await conn.fetchrow("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        raf = await conn.fetchrow("SELECT * FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        giveaway = await conn.fetchrow("SELECT * FROM giveaways WHERE is_active = TRUE AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        pvm_event = await conn.fetchrow("SELECT * FROM pvm_events WHERE is_active = TRUE AND starts_at > NOW() ORDER BY starts_at ASC LIMIT 1")

    embed = discord.Embed(title="\n\n Clan Event Status", description="Here's a look at all the events currently running.", color=discord.Color.blurple())
    
    if comp:
        comp_ends_dt = comp['ends_at']
        comp_info = (f"**Title:** [{comp['title']}](https://wiseoldman.net/competitions/{comp['id']})\n"
                     f"**Ends:** <t:{int(comp_ends_dt.timestamp())}:R>")
        embed.add_field(name="\n\n Active Competition", value=comp_info, inline=False)
    else:
        embed.add_field(name="\n\n Active Competition", value="There is no SOTW competition currently running.", inline=False)
    
    if raf:
        raf_ends_dt = raf['ends_at']
        raf_info = (f"**Prize:** {raf['prize']}\n"
                    f"**Ends:** <t:{int(raf_ends_dt.timestamp())}:R>\n[View Raffle]({bot.get_channel(RAFFLE_CHANNEL_ID).jump_url if bot.get_channel(RAFFLE_CHANNEL_ID) else '#'}) ")
        embed.add_field(name="\n\n Active Raffle", value=raf_info, inline=False)
    else:
        embed.add_field(name="\n\n Active Raffle", value="There is no raffle currently running.", inline=False)

    if giveaway:
        gw_ends_dt = giveaway['ends_at']
        gw_info = (f"**Prize:** {giveaway['prize']}\n"
                   f"**Ends:** <t:{int(gw_ends_dt.timestamp())}:R>\n[Enter Here]({bot.get_channel(giveaway['channel_id']).get_partial_message(giveaway['message_id']).jump_url})")
        embed.add_field(name="\n\n Active Giveaway", value=gw_info, inline=False)
    else:
        embed.add_field(name="\n\n Active Giveaway", value="There are no active giveaways.", inline=False)
    
    if pvm_event:
        pvm_starts_dt = pvm_event['starts_at']
        pvm_info = (f"**Event:** {pvm_event['title']}\n"
                    f"**Starts:** <t:{int(pvm_starts_dt.timestamp())}:F> (<t:{int(pvm_starts_dt.timestamp())}:R>)\n[View Event]({bot.get_channel(pvm_event['channel_id']).get_partial_message(pvm_event['message_id']).jump_url if pvm_event['channel_id'] and pvm_event['message_id'] else '#'}) ")
        embed.add_field(name="\n\n Upcoming PVM Event", value=pvm_info, inline=False)
    else:
        embed.add_field(name="\n\n Upcoming PVM Event", value="No PVM events scheduled.", inline=False)

    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.respond(embed=embed)

bingo = bot.create_group("bingo", "Commands for clan bingo events.")
# FIX: start_bingo to store and use the generated event ID, handle multiple events via is_active
@bingo.command(name="start", description="Start a new bingo event.")
@discord.default_permissions(manage_events=True)
async def start_bingo(ctx: discord.ApplicationContext, duration_days: discord.Option(int, "How many days the bingo event will last.")):
    await ctx.defer(ephemeral=True)
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
    
    async with bot.db_pool.acquire() as conn:
        # FIX: Deactivate existing bingo event instead of deleting
        await conn.execute("UPDATE bingo_events SET is_active = FALSE WHERE is_active = TRUE")
        
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        board_json = json.dumps(board_tasks)
        
        image_path, error = generate_bingo_image(board_tasks)
        if error: 
            return await ctx.edit(content=f"Failed to generate bingo image: {error}")
        
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if not bingo_channel: 
            return await ctx.edit(content="Error: Bingo Channel ID not configured correctly.")
        
        ai_embed_data = await generate_announcement_json("bingo_start")
        embed = discord.Embed.from_dict(ai_embed_data)
        file = discord.File(image_path, filename="bingo_board.png")
        embed.set_image(url="attachment://bingo_board.png")
        embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"Bingo started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        
        message = await bingo_channel.send(embed=embed, file=file)
        
        # FIX: Insert, and get the new event ID. is_active defaults to TRUE.
        current_bingo_event_id = await conn.fetchval("INSERT INTO bingo_events (ends_at, board_json, message_id) VALUES ($1, $2, $3) RETURNING id",
                                                   ends_at, board_json, message.id)
    
    await send_global_announcement("bingo_start", {}, message.jump_url)
    await ctx.edit(content=f"Bingo event (ID: {current_bingo_event_id}) created successfully!")

# FIX: complete_task to link submission to the active event
@bingo.command(name="complete", description="Submit a task for bingo completion.")
async def complete_task(ctx: discord.ApplicationContext, task: discord.Option(str, "The name of the task you completed."), proof: discord.Option(str, "A URL link to a screenshot or video proof.")):
    await ctx.defer(ephemeral=True)
    async with bot.db_pool.acquire() as conn:
        # Get the active bingo event ID and board
        event_data = await conn.fetchrow("SELECT id, board_json FROM bingo_events WHERE is_active = TRUE LIMIT 1")
        if not event_data:
            return await ctx.respond("There is no active bingo event.", ephemeral=True)
        
        current_event_id, board_json = event_data['id'], event_data['board_json']
        board_tasks = json.loads(board_json)
        task_names = [t['name'] for t in board_tasks]
        
        if task not in task_names:
            return await ctx.respond("That task is not on the current bingo board.", ephemeral=True)
        
        # FIX: Insert with event_id
        await conn.execute("INSERT INTO bingo_submissions (event_id, user_id, task_name, proof_url) VALUES ($1, $2, $3, $4)",
                           current_event_id, ctx.author.id, task, proof)
    await ctx.respond("Your submission has been sent to the admins for review!", ephemeral=True)

@bingo.command(name="submissions", description="ADMIN: View pending bingo task submissions.")
@discord.default_permissions(manage_events=True)
async def view_submissions(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    async with bot.db_pool.acquire() as conn:
        pending = await conn.fetch("SELECT bs.* FROM bingo_submissions bs JOIN bingo_events be ON bs.event_id = be.id WHERE bs.status = 'pending' AND be.is_active = TRUE")
    if not pending:
        return await ctx.respond("There are no pending bingo submissions for the active event.", ephemeral=True)
    await ctx.respond("Here are the pending submissions for the active bingo event:", ephemeral=True)
    for sub in pending:
        user = await bot.fetch_user(sub['user_id'])
        embed = discord.Embed(title="\n\n Bingo Submission", description=f"**Task:** {sub['task_name']}", color=discord.Color.yellow())
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.add_field(name="Proof", value=f"[Click to view]({sub['proof_url']})", inline=False)
        embed.set_footer(text=f"Submission ID: {sub['id']}")
        await ctx.channel.send(embed=embed, view=SubmissionView(), ephemeral=True)

@bingo.command(name="board", description="View the current bingo board.")
async def view_board(ctx: discord.ApplicationContext):
    await ctx.defer()
    async with bot.db_pool.acquire() as conn:
        event_data = await conn.fetchrow("SELECT message_id FROM bingo_events WHERE is_active = TRUE LIMIT 1")
    if not event_data or not event_data['message_id']:
        return await ctx.respond("There is no active bingo board to display.")
    bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
    if bingo_channel:
        try:
            message = await bingo_channel.fetch_message(event_data['message_id'])
            await ctx.respond(f"Here is the current bingo board: {message.jump_url}")
        except discord.NotFound:
            await ctx.respond("Could not find the original bingo board message.")
    else:
        await ctx.respond("Bingo channel not configured.")

pointstore = bot.create_group("pointstore", "Manage and redeem clan points.")
@pointstore.command(name="rewards", description="View available rewards in the point store.")
async def view_rewards(ctx: discord.ApplicationContext):
    await ctx.defer()
    if bot.db_pool is None:
        await ctx.respond("Database not initialized yet.", ephemeral=True)
        return
    async with bot.db_pool.acquire() as conn:
        try:
            rewards = await conn.fetch("SELECT * FROM rewards WHERE is_active = TRUE ORDER BY point_cost ASC")
            embed = discord.Embed(title="\n\n\n Clan Point Store Rewards \n\n\n", color=discord.Color.gold())
            if not rewards:
                embed.description = "There are currently no active rewards in the point store."
            else:
                for reward in rewards:
                    role_reward_text = ""
                    role_reward_data = await conn.fetchrow("SELECT role_id FROM role_rewards WHERE reward_id = $1", reward['id'])
                    if role_reward_data:
                        role_id = role_reward_data['role_id']
                        guild = ctx.guild # Use ctx.guild for dynamic role lookup
                        if guild:
                            role = guild.get_role(role_id)
                            if role:
                                role_reward_text = f"\n**Role:** {role.mention}"
                            else:
                                role_reward_text = f"\n**Role ID:** {role_id} (Role not found in this guild)"
                    embed.add_field(
                        name=f"{reward['reward_name']} ({reward['point_cost']} points)",
                        value=f"{reward['description'] or 'No description provided.'}{role_reward_text}",
                        inline=False
                    )
            embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
            embed.timestamp = datetime.now(timezone.utc)
            await ctx.respond(embed=embed)
        except Exception as e:
            print(f"Error fetching rewards: {e}")
            await ctx.respond("An error occurred while fetching rewards.", ephemeral=True)

@pointstore.command(name="redeem", description="Redeem a reward from the point store.")
async def redeem_reward(ctx: discord.ApplicationContext, reward_name: str):
    await ctx.defer(ephemeral=True)
    if bot.db_pool is None:
        await ctx.respond("Database not initialized yet.", ephemeral=True)
        return
    async with bot.db_pool.acquire() as conn:
        try:
            reward = await conn.fetchrow("SELECT * FROM rewards WHERE reward_name ILIKE $1 AND is_active = TRUE", reward_name)
            if not reward:
                await ctx.respond(f"Reward '{reward_name}' not found or is not currently active.", ephemeral=True)
                return
            user_points_data = await conn.fetchrow("SELECT points FROM clan_points WHERE discord_id = $1", ctx.user.id)
            current_points = user_points_data['points'] if user_points_data else 0
            if current_points < reward['point_cost']:
                await ctx.respond(f"You don't have enough points to redeem '{reward['reward_name']}'. You need {reward['point_cost']} points, but you only have {current_points}.", ephemeral=True)
                return
            new_balance = current_points - reward['point_cost']
            await conn.execute("UPDATE clan_points SET points = $1 WHERE discord_id = $2", new_balance, ctx.user.id)
            await conn.execute(
                "INSERT INTO redeem_transactions (user_id, reward_id, reward_name, point_cost) VALUES ($1, $2, $3, $4)",
                ctx.user.id, reward['id'], reward['reward_name'], reward['point_cost']
            )
            role_reward_data = await conn.fetchrow("SELECT role_id FROM role_rewards WHERE reward_id = $1", reward['id'])
            if role_reward_data:
                role_id = role_reward_data['role_id']
                guild = ctx.guild # FIX: Use ctx.guild for role lookup and member operations
                if guild:
                    role = guild.get_role(role_id)
                    if role:
                        member = guild.get_member(ctx.user.id) # Get member from current guild
                        if member:
                            try:
                                await member.add_roles(role)
                                await ctx.followup.send(f"You have successfully redeemed '{reward['reward_name']}'! The role **{role.name}** has been added to you. Your new point balance is **{new_balance}**.", ephemeral=False)
                            except discord.Forbidden:
                                print(f"Missing permissions to add role {role.name} to {member.display_name}")
                                await ctx.followup.send(f"You have successfully redeemed '{reward['reward_name']}'! Points deducted, but I lack permissions to assign the role **{role.name}**. Your new point balance is **{new_balance}**.", ephemeral=False)
                            except Exception as e:
                                print(f"Error adding role {role.name} to {member.display_name}: {e}")
                                await ctx.followup.send(f"You have successfully redeemed '{reward['reward_name']}'! Points deducted, but an error occurred while assigning the role. Your new point balance is **{new_balance}**.", ephemeral=False)
                        else:
                            print(f"Could not find member {ctx.user.id} in guild {guild.name} for role assignment.")
                            await ctx.followup.send(f"You have successfully redeemed '{reward['reward_name']}'! Points deducted, but I could not find your member profile in the guild to assign the role. Your new point balance is **{new_balance}**.", ephemeral=False)
                    else:
                        print(f"Role with ID {role_id} not found in guild {guild.name} for reward redemption.")
                        await ctx.followup.send(f"You have successfully redeemed '{reward['reward_name']}'! Points deducted, but the associated role was not found in the guild. Your new point balance is **{new_balance}**.", ephemeral=False)
                else:
                     print(f"Could not get guild {ctx.guild.id} for role assignment.")
                     await ctx.followup.send(f"You have successfully redeemed '{reward['reward_name']}'! Points deducted, but I could not access the guild to assign the role. Your new point balance is **{new_balance}**.", ephemeral=False)
            else:
                await ctx.followup.send(f"You have successfully redeemed '{reward['reward_name']}'! Your new point balance is **{new_balance}**. Please contact an admin for reward fulfillment.", ephemeral=False)
        except Exception as e:
            print(f"Error redeeming reward: {e}")
            await ctx.respond("An error occurred while redeeming the reward.", ephemeral=True)

@pointstore.command(name="addreward", description="ADMIN: Add a new reward to the point store.")
@discord.default_permissions(manage_guild=True)
async def add_reward(ctx: discord.ApplicationContext, name: str, cost: int, description: Option(str, "Optional description for the reward.", required=False), role_id: Option(str, "Optional Discord Role ID to link to this reward.", required=False)):
    await ctx.defer(ephemeral=True)
    if bot.db_pool is None:
        await ctx.respond("Database not initialized yet.", ephemeral=True)
        return
    async with bot.db_pool.acquire() as conn:
        try:
            reward_id = await conn.fetchval(
                "INSERT INTO rewards (reward_name, point_cost, description) VALUES ($1, $2, $3) RETURNING id",
                name, cost, description
            )
            if role_id:
                try:
                    role_id_int = int(role_id)
                    guild = ctx.guild
                    if guild and guild.get_role(role_id_int):
                        await conn.execute(
                            "INSERT INTO role_rewards (reward_id, role_id) VALUES ($1, $2)",
                            reward_id, role_id_int
                        )
                        await ctx.respond(f"Reward '{name}' added and linked to role <@&{role_id_int}>.", ephemeral=True)
                    elif guild and not guild.get_role(role_id_int):
                        await ctx.respond(f"Warning: Role with ID {role_id} not found in this guild. Reward added, but role not linked.", ephemeral=True)
                    else:
                         await ctx.respond(f"Warning: Could not access guild to validate role ID. Reward added, but role may not be linked correctly.", ephemeral=True)
                except ValueError:
                     await ctx.respond(f"Warning: Invalid Role ID '{role_id}'. Role not linked.", ephemeral=True)
                except Exception as e:
                     print(f"Error linking role reward: {e}")
                     await ctx.respond(f"Warning: An error occurred while linking the role reward. {e}", ephemeral=True)
            else:
                 await ctx.respond(f"Reward '{name}' added to the point store with a cost of {cost} points.", ephemeral=True)
        except asyncpg.exceptions.UniqueViolationError:
            await ctx.respond(f"A reward with the name '{name}' already exists.", ephemeral=True)
        except Exception as e:
            print(f"Error adding reward: {e}")
            await ctx.respond("An error occurred while adding the reward.", ephemeral=True)

@pointstore.command(name="removereward", description="ADMIN: Remove a reward from the point store.")
@discord.default_permissions(manage_guild=True)
async def remove_reward(ctx: discord.ApplicationContext, reward_name: str):
    await ctx.defer(ephemeral=True)
    if bot.db_pool is None:
        await ctx.respond("Database not initialized yet.", ephemeral=True)
        return
    async with bot.db_pool.acquire() as conn:
        try:
            result = await conn.execute("DELETE FROM rewards WHERE reward_name ILIKE $1", reward_name)
            if 'DELETE 1' in result:
                await ctx.respond(f"Reward '{reward_name}' removed from the point store.", ephemeral=True)
            else:
                await ctx.respond(f"Reward '{reward_name}' not found.", ephemeral=True)
        except Exception as e:
            print(f"Error removing reward: {e}")
            await ctx.respond("An error occurred while removing the reward.", ephemeral=True)

@pointstore.command(name="togglereward", description="ADMIN: Toggle the active status of a reward.")
@discord.default_permissions(manage_guild=True)
async def toggle_reward(ctx: discord.ApplicationContext, reward_name: str):
    await ctx.defer(ephemeral=True)
    if bot.db_pool is None:
        await ctx.respond("Database not initialized yet.", ephemeral=True)
        return
    async with bot.db_pool.acquire() as conn:
        try:
            reward_data = await conn.fetchrow("SELECT id, is_active FROM rewards WHERE reward_name ILIKE $1", reward_name)
            if not reward_data:
                await ctx.respond(f"Reward '{reward_name}' not found.", ephemeral=True)
                return
            new_status = not reward_data['is_active']
            await conn.execute("UPDATE rewards SET is_active = $1 WHERE id = $2", new_status, reward_data['id'])
            status_text = "active" if new_status else "inactive"
            await ctx.respond(f"Reward '{reward_name}' is now set to **{status_text}**.", ephemeral=True)
        except Exception as e:
            print(f"Error toggling reward status: {e}")
            await ctx.respond("An error occurred while toggling the reward status.", ephemeral=True)

giveaway = bot.create_group("giveaway", "Commands for managing giveaways.")
@giveaway.command(name="start", description="Start a new giveaway.")
@discord.default_permissions(manage_events=True)
async def start_giveaway(ctx: discord.ApplicationContext, prize: discord.Option(str, "What is the prize?"), duration: discord.Option(str, "How long? (e.g., 7d, 12h, 30m)"), winners: discord.Option(int, "How many winners?", min_value=1, default=1), reward_role: discord.Option(discord.Role, "Optional role for winner(s).", required=False)):
    await ctx.defer(ephemeral=True)
    delta = parse_duration(duration)
    if delta is None: return await ctx.respond("Invalid duration format.", ephemeral=True)
    ends_at = datetime.now(timezone.utc) + delta
    giveaway_channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if not giveaway_channel: return await ctx.respond("Giveaway channel not found.", ephemeral=True)
    details = {"prize": prize, "winner_count": winners}
    ai_embed_data = await generate_announcement_json("giveaway_start", details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.add_field(name="Ends In", value=f"<t:{int(ends_at.timestamp())}:R>", inline=True)
    embed.add_field(name="Winners", value=f"**{winners}**", inline=True)
    if reward_role: embed.add_field(name="\n\n Bonus Reward", value=f"Winner(s) will receive the {reward_role.mention} role!", inline=False)
    embed.set_footer(text=f"Giveaway started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    try:
        giveaway_message = await giveaway_channel.send(embed=embed)
        view = GiveawayView(message_id=giveaway_message.id)
        await giveaway_message.edit(view=view)
        async with bot.db_pool.acquire() as conn:
            role_id_to_save = reward_role.id if reward_role else None
            await conn.execute(
                "INSERT INTO giveaways (message_id, channel_id, prize, ends_at, winner_count, role_id) VALUES ($1, $2, $3, $4, $5, $6)",
                giveaway_message.id, giveaway_channel.id, prize, ends_at, winners, role_id_to_save
            )
        await ctx.respond(f"Giveaway for **{prize}** has been started!", ephemeral=True)
    except Exception as e:
        await ctx.respond(f"An unexpected error occurred: {e}", ephemeral=True)

@giveaway.command(name="entries", description="View the list of entrants for the current giveaway.")
@discord.default_permissions(manage_events=True)
async def view_entries(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    async with bot.db_pool.acquire() as conn:
        active_giveaway = await conn.fetchrow("SELECT * FROM giveaways WHERE is_active = TRUE ORDER BY ends_at DESC LIMIT 1")
        if not active_giveaway:
            await ctx.respond("There are no active giveaways.", ephemeral=True)
            return
        entries = await conn.fetch("SELECT user_id FROM giveaway_entries WHERE message_id = $1", active_giveaway['message_id'])
    embed = discord.Embed(
        title=f"\n\n Entries for '{active_giveaway['prize']}'",
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
                if member: entrant_list.append(f"- {member.display_name} (`{member.name}`)")
                else: entrant_list.append(f"- *User not in server? (ID: {entry['user_id']})*\n(This may be a user who left the server or has DMs disabled)")
            except Exception:
                entrant_list.append(f"- *Unknown User (ID: {entry['user_id']})*\n(Error fetching user details)")
        entrants_text = "\n".join(entrant_list)
        if len(entrants_text) > 4000:
             entrants_text = entrants_text[:4000] + "\n...and more."
        embed.description += f"\n\n{entrants_text}"
    await ctx.respond(embed=embed, ephemeral=True)

points = bot.create_group("points", "Commands related to Clan Points.")
@points.command(name="view", description="Check your current Clan Point balance.")
async def view_points(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    async with bot.db_pool.acquire() as conn:
        point_data = await conn.fetchrow("SELECT points FROM clan_points WHERE discord_id = $1", ctx.author.id)
    current_points = point_data['points'] if point_data else 0
    await ctx.followup.send(f"You currently have **{current_points}** Clan Points.")

@points.command(name="leaderboard", description="View the Clan Points leaderboard.")
async def leaderboard(ctx: discord.ApplicationContext):
    await ctx.defer()
    async with bot.db_pool.acquire() as conn:
        leaders = await conn.fetch("SELECT discord_id, points FROM clan_points ORDER BY points DESC LIMIT 10")
    embed = discord.Embed(title="\n\n\n Clan Points Leaderboard \n\n\n", color=discord.Color.gold())
    if not leaders:
        embed.description = "No one has earned any points yet."
    else:
        leaderboard_text = ""
        for i, entry in enumerate(leaders):
            user_id, points = entry['discord_id'], entry['points']
            rank_emoji = {1: "\n\n", 2: "\n\n", 3: "\n\n"}.get(i + 1, f"`{i + 1}.`")
            try:
                member = await ctx.guild.fetch_member(user_id)
                leaderboard_text += f"{rank_emoji} **{member.display_name}**: {points:,} points\n"
            except discord.NotFound:
                continue
        embed.description = leaderboard_text
    await ctx.respond(embed=embed)

# NEW: OSRS command group and commands
osrs = bot.create_group("osrs", "Commands for Old School RuneScape integration.")

@osrs.command(name="link", description="Link your Discord account to your OSRS name.")
async def link_osrs_name(ctx: discord.ApplicationContext, osrs_name: discord.Option(str, "Your Old School RuneScape username.")):
    await ctx.defer(ephemeral=True)
    
    # Basic validation for OSRS name (can be more robust with API call)
    if not re.match(r"^[a-zA-Z0-9\s-]{1,12}$", osrs_name):
        return await ctx.respond("Invalid OSRS username format. Names are 1-12 characters, alphanumeric, spaces, or hyphens.", ephemeral=True)

    async with bot.db_pool.acquire() as conn:
        try:
            # Use ON CONFLICT to update if already exists
            await conn.execute("INSERT INTO user_links (discord_id, osrs_name) VALUES ($1, $2) ON CONFLICT (discord_id) DO UPDATE SET osrs_name = EXCLUDED.osrs_name",
                           ctx.author.id, osrs_name)
            await ctx.respond(f"Your Discord account has been linked to OSRS name: **{osrs_name}**.", ephemeral=True)
        except Exception as e:
            print(f"Error linking OSRS name: {e}")
            await ctx.respond("An error occurred while linking your OSRS name.", ephemeral=True)

# Modify the existing view_osrs_profile command to include AI summary

@osrs.command(name="profile", description="View your (or another member's) OSRS stats from the Hiscores.")
async def view_osrs_profile(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member to view. Defaults to yourself.", required=False)):
    await ctx.defer()
    target_member = member or ctx.author
    
    # Using the new asyncpg connection pool
    async with bot.db_pool.acquire() as conn:
        osrs_name_data = await conn.fetchrow("SELECT osrs_name FROM user_links WHERE discord_id = $1", target_member.id)

    if not osrs_name_data:
        if target_member == ctx.author:
            return await ctx.respond("You have not linked your OSRS name yet. Use `/osrs link <your_rsn>`.", ephemeral=True)
        else:
            return await ctx.respond(f"{target_member.display_name} has not linked their OSRS name yet.", ephemeral=True)

    osrs_name = osrs_name_data['osrs_name']
    hiscores_url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={osrs_name}"
    headers = {'User-Agent': 'GrazyBot/1.0 OSRS Clan Bot'}

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(hiscores_url) as response:
                if response.status == 200:
                    data = await response.text()
                    lines = data.strip().split('\n')

                    skills_data = {}
                    for i, skill_name in enumerate(WOM_SKILLS): # WOM_SKILLS has 23 skills
                        if i < len(lines): # Ensure line exists
                            parts = lines[i].split(',')
                            if len(parts) >= 3: # Rank, Level, XP
                                skills_data[skill_name] = {
                                    "rank": int(parts[0]),
                                    "level": int(parts[1]),
                                    "xp": int(parts[2])
                                }
                    
                    embed = discord.Embed(
                        title=f"\n\n OSRS Profile: {osrs_name}",
                        url=f"https://secure.runescape.com/m=hiscore_oldschool/hiscorepersonal?user1={up.quote(osrs_name)}", 
                        color=discord.Color.blue()
                    )
                    embed.set_thumbnail(url="https://oldschool.runescape.wiki/images/thumb/Old_School_RuneScape_logo.png/1200px-Old_School_RuneScape_logo.png")
                    
                    # Prepare data for AI summary
                    overall_level = skills_data.get('overall', {}).get('level', 'N/A')
                    top_skills_list = []
                    if skills_data:
                        # Sort skills by level, excluding 'overall'
                        sorted_skills = sorted([ 
                            (skill, data['level']) for skill, data in skills_data.items() if skill != 'overall' 
                        ], key=lambda x: x[1], reverse=True)[:3]
                        top_skills_list = [f"{s.capitalize()} (Lv{l})" for s, l in sorted_skills]

                    ai_profile_prompt = f"""
You are a wise and observant OSRS character, providing a brief, engaging summary of a player's profile.
Highlight their overall level and their top 3 skills. Keep it to 1-2 sentences. Do not use emojis or markdown.
Player: {osrs_name}
Overall Level: {overall_level}
Top 3 Skills: {', '.join(top_skills_list) if top_skills_list else 'None yet'}
"""
                    
                    profile_summary = ""
                    try:
                        response = await ai_model.generate_content_async(ai_profile_prompt)
                        profile_summary = response.text
                    except Exception as e:
                        print(f"Error generating AI profile summary for {osrs_name}: {e}")
                        profile_summary = "The ancient scrolls whisper of a warrior whose legend is still being written."
                    
                    embed.description = profile_summary + "\n\n" # Add AI summary at the top of the embed

                    overall = skills_data.get('overall', {})
                    if overall:
                        embed.add_field(name="Overall", value=f"Rank: {overall['rank']:,}\nLevel: {overall['level']}\nXP: {overall['xp']:,}", inline=False)
                    
                    combat_skills = ["attack", "strength", "defence", "ranged", "prayer", "magic", "hitpoints"]
                    combat_value = ""
                    for skill in combat_skills:
                        s_data = skills_data.get(skill)
                        if s_data:
                            combat_value += f"**{skill.capitalize()}**: {s_data['level']}\n"

                    if combat_value: 
                        embed.add_field(name="Combat Skills", value=combat_value, inline=True)
                    
                    skilling_skills = ["cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herblore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"]
                    skilling_value = ""
                    for skill in skilling_skills:
                        s_data = skills_data.get(skill)
                        if s_data:
                            skilling_value += f"**{skill.capitalize()}**": {s_data['level']}\n"
                    if skilling_value: 
                        embed.add_field(name="Other Skills", value=skilling_value, inline=True)
                    
                    embed.set_footer(text=f"Data from OSRS Hiscores | Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
                    await ctx.respond(embed=embed)

                elif response.status == 404:
                    await ctx.respond(f"OSRS name **{osrs_name}** not found on the Hiscores. Please check spelling or ensure it's a valid RSN.", ephemeral=True)
                else:
                    await ctx.respond(f"Error fetching Hiscores data for **{osrs_name}**. Status: {response.status}", ephemeral=True)

        except aiohttp.ClientError as e:
            print(f"HTTP client error fetching Hiscores for {osrs_name}: {e}")
            await ctx.respond("A network error occurred while fetching Hiscores data. Please try again later.", ephemeral=True)
        except Exception as e:
            print(f"Unexpected error in /osrs profile: {e}")
            await ctx.respond("An unexpected error occurred while processing Hiscores data.", ephemeral=True)

# Add to the 'osrs' SlashCommandGroup (after /osrs profile)

@osrs.command(name="kc", description="View your (or another member's) OSRS boss kill counts.")
async def view_osrs_kc(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member to view. Defaults to yourself.", required=False)):
    await ctx.defer()
    target_member = member or ctx.author
    
    # Using the new asyncpg connection pool
    async with bot.db_pool.acquire() as conn:
        osrs_name_data = await conn.fetchrow("SELECT osrs_name FROM user_links WHERE discord_id = $1", target_member.id)

    if not osrs_name_data:
        if target_member == ctx.author:
            return await ctx.respond("You have not linked your OSRS name yet. Use `/osrs link <your_rsn>`.", ephemeral=True)
        else:
            return await ctx.respond(f"{target_member.display_name} has not linked their OSRS name yet.", ephemeral=True)

    osrs_name = osrs_name_data['osrs_name']
    hiscores_url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={osrs_name}"
    headers = {'User-Agent': 'GrazyBot/1.0 OSRS Clan Bot'}

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(hiscores_url) as response:
                if response.status == 200:
                    data = await response.text()
                    lines = data.strip().split('\n')

                    # Skills occupy the first 23 lines
                    activities_data = {}
                    start_index_for_activities = len(WOM_SKILLS) # Index 23 onwards
                    
                    for i, activity_name in enumerate(OSRS_ACTIVABLE_HISCORE_ORDER):
                        line_index = start_index_for_activities + i
                        if line_index < len(lines):
                            parts = lines[line_index].split(',')
                            if len(parts) >= 2: # Rank and Score/KC
                                # Hiscores provides rank, and then killcount/score for activities
                                activities_data[activity_name.lower()] = {
                                    "rank": int(parts[0]),
                                    "killcount": int(parts[1])
                                }

                    embed = discord.Embed(
                        title=f"\n\n OSRS Kill Counts: {osrs_name}",
                        url=f"https://secure.runescape.com/m=hiscore_oldschool/hiscorepersonal?user1={up.quote(osrs_name)}", 
                        color=discord.Color.dark_red()
                    )
                    embed.set_thumbnail(url="https://oldschool.runescape.wiki/images/Slayer_helmet.png") 

                    kc_text = ""
                    # Prioritize displaying common PvM bosses
                    notable_bosses = ["Vorkath", "Zulrah", "Cerberus", "Chambers of Xeric", "Theatre of Blood", "Tombs of Amascut", "General Graardor", "K'ril Tsutsaroth", "Kree'arra", "Commander Zilyana", "Nex", "Phosani's Nightmare", "Nightmare"]

                    for boss in notable_bosses:
                        kc_entry = activities_data.get(boss.lower())
                        if kc_entry and kc_entry['killcount'] > 0:
                            kc_text += f"**{boss}**: {kc_entry['killcount']:,} (Rank: {kc_entry['rank']:,})\n"
                    
                    if not kc_text:
                        kc_text = "No notable boss kill counts found. Keep grinding!"
                    
                    embed.add_field(name="PvM Kill Counts", value=kc_text, inline=False)
                    embed.set_footer(text=f"Data from OSRS Hiscores | Requested by {ctx.author.display_name}")
                    await ctx.respond(embed=embed)

                elif response.status == 404:
                    await ctx.respond(f"OSRS name **{osrs_name}** not found on the Hiscores. Please check spelling or ensure it's a valid RSN.", ephemeral=True)
                else:
                    await ctx.respond(f"Error fetching Hiscores data for **{osrs_name}**. Status: {response.status}", ephemeral=True)

        except aiohttp.ClientError as e:
            print(f"HTTP client error fetching Hiscores for {osrs_name}: {e}")
            await ctx.respond("A network error occurred while fetching Hiscores data. Please try again later.", ephemeral=True)
        except Exception as e:
            print(f"Unexpected error in /osrs kc: {e}")
            await ctx.respond("An unexpected error occurred while processing Hiscores data.", ephemeral=True)

# NEW: PVM Event commands
pvm = bot.create_group("pvm", "Commands for PVM events.")

@pvm.command(name="schedule", description="ADMIN: Schedule a new PVM event.")
@discord.default_permissions(manage_events=True)
async def schedule_pvm_event(ctx: discord.ApplicationContext,
                             title: discord.Option(str, "Title of the event."),
                             description: discord.Option(str, "Description of the event.", max_length=1000),
                             start_time: discord.Option(str, "Start time (e.g., '2023-12-31 20:00 UTC')."),
                             duration_minutes: discord.Option(int, "Duration in minutes.", min_value=10, default=60)):
    await ctx.defer(ephemeral=True)
    
    try:
        # Attempt to parse start_time, e.g., 'YYYY-MM-DD HH:MM UTC'
        event_start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
    except ValueError:
        return await ctx.respond("Invalid start time format. Please use 'YYYY-MM-DD HH:MM UTC'.", ephemeral=True)

    if event_start_dt <= datetime.now(timezone.utc):
        return await ctx.respond("Start time must be in the future.", ephemeral=True)

    pvm_channel = bot.get_channel(PVM_EVENT_CHANNEL_ID)
    if not pvm_channel:
        return await ctx.respond("PVM Event Channel ID not configured correctly.", ephemeral=True)
    
    async with bot.db_pool.acquire() as conn:
        try:
            details = {'title': title, 'description': description, 'start_time_unix': int(event_start_dt.timestamp())}
            ai_embed_data = await generate_announcement_json("pvm_event_start", details)
            event_embed = discord.Embed.from_dict(ai_embed_data)
            event_embed.add_field(name="Starts At", value=f"<t:{int(event_start_dt.timestamp())}:F> (<t:{int(event_start_dt.timestamp())}:R>)", inline=False)
            event_embed.add_field(name="Expected Duration", value=f"{duration_minutes} minutes", inline=False)
            event_embed.set_footer(text=f"Event organized by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

            # Send a placeholder message first to get message_id for the view
            event_message = await pvm_channel.send(embed=event_embed)
            
            event_id = await conn.fetchval("INSERT INTO pvm_events (title, description, starts_at, duration_minutes, message_id, channel_id) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                                   title, description, event_start_dt, duration_minutes, event_message.id, pvm_channel.id)

            # Create and attach the view
            signup_view = PvmEventView(event_id=event_id)
            await event_message.edit(view=signup_view)

            # Store the signup message ID (which is the main event message ID here)
            await conn.execute("UPDATE pvm_events SET signup_message_id = $1 WHERE id = $2", event_message.id, event_id)

            await ctx.respond(f"PVM event '{title}' scheduled successfully! (ID: {event_id})", ephemeral=True)
            await send_global_announcement("pvm_event_start", details, event_message.jump_url)

        except Exception as e:
            print(f"Error scheduling PVM event: {e}")
            await ctx.respond(f"An error occurred while scheduling the event: {e}", ephemeral=True)

@pvm.command(name="participants", description="View participants for a PVM event.")
async def view_pvm_participants(ctx: discord.ApplicationContext, event_id: discord.Option(int, "The ID of the PVM event.")):
    await ctx.defer()

    async with bot.db_pool.acquire() as conn:
        event_data = await conn.fetchrow("SELECT title, starts_at FROM pvm_events WHERE id = $1 AND is_active = TRUE", event_id)
        
        if not event_data:
            return await ctx.respond(f"PVM event with ID {event_id} not found or is inactive.")

        signups = await conn.fetch("SELECT user_id FROM pvm_event_signups WHERE event_id = $1", event_id)

    embed = discord.Embed(
        title=f"\n\n Participants for '{event_data['title']}'",
        description=f"Starts: <t:{int(event_data['starts_at'].timestamp())}:F>\nTotal Signed Up: **{len(signups)}**",
        color=discord.Color.green()
    )

    if not signups:
        embed.add_field(name="No Sign-ups Yet", value="Be the first to join this epic adventure!")
    else:
        participant_list = []
        for entry in signups:
            try:
                member = await ctx.guild.fetch_member(entry['user_id'])
                if member: participant_list.append(member.display_name)
                else: participant_list.append(f"*Unknown User (ID: {entry['user_id']})*")
            except discord.NotFound:
                participant_list.append(f"*User Left (ID: {entry['user_id']})*")
        
        participants_text = textwrap.fill(', '.join(participant_list), width=500)
        embed.add_field(name="Signed-Up Warriors", value=participants_text, inline=False)
    
    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.respond(embed=embed)

@pvm.command(name="cancel", description="ADMIN: Cancel an upcoming PVM event.")
@discord.default_permissions(manage_events=True)
async def cancel_pvm_event(ctx: discord.ApplicationContext, event_id: discord.Option(int, "The ID of the PVM event to cancel.")):
    await ctx.defer(ephemeral=True)
    async with bot.db_pool.acquire() as conn:
        try:
            event_data = await conn.fetchrow("SELECT title, message_id, channel_id FROM pvm_events WHERE id = $1 AND is_active = TRUE", event_id)
            
            if not event_data:
                return await ctx.respond(f"PVM event with ID {event_id} not found or already inactive.", ephemeral=True)
            
            title, message_id, channel_id = event_data['title'], event_data['message_id'], event_data['channel_id']

            # Deactivate the event (CASCADE will handle signups)
            await conn.execute("UPDATE pvm_events SET is_active = FALSE WHERE id = $1", event_id)

            event_channel = bot.get_channel(channel_id)
            if event_channel:
                try:
                    original_message = await event_channel.fetch_message(message_id)
                    await original_message.edit(content=f"**PVM Event '{title}' (ID: {event_id}) has been CANCELLED!**", embed=None, view=None)
                except discord.NotFound:
                    pass # Message already deleted
                await event_channel.send(f"The PVM event **{title}** (ID: {event_id}) has been cancelled by an admin.")

            await ctx.respond(f"PVM event '{title}' (ID: {event_id}) successfully cancelled.", ephemeral=True)

        except Exception as e:
            print(f"Error cancelling PVM event: {e}")
            await ctx.respond(f"An error occurred while cancelling the event: {e}", ephemeral=True)

# NEW: Boss Personal Bests (PB) commands
pb = bot.create_group("pb", "Commands for tracking Boss Personal Bests.")

@pb.command(name="log", description="Log or update your Personal Best time for a boss.")
async def log_pb(ctx: discord.ApplicationContext,
                   boss_name: discord.Option(str, "Name of the boss."),
                   time_in_seconds: discord.Option(float, "Your PB time in seconds (e.g., 123.45)."),
                   proof_url: discord.Option(str, "URL to proof (e.g., screenshot, video).")):
    await ctx.defer(ephemeral=True)
    
    if not proof_url.startswith(('http://', 'https://')):
        return await ctx.respond("Proof URL must be a valid web link.", ephemeral=True)

    pb_time_ms = int(time_in_seconds * 1000) # Convert to milliseconds for storage
    
    async with bot.db_pool.acquire() as conn:
        try:
            # Check if existing PB is better
            existing_pb = await conn.fetchrow("SELECT pb_time_ms FROM boss_pbs WHERE discord_id = $1 AND boss_name ILIKE $2", ctx.author.id, boss_name)

            if existing_pb and pb_time_ms >= existing_pb['pb_time_ms']:
                return await ctx.respond(f"Your submitted time ({time_in_seconds:.2f}s) is not faster than your current PB ({existing_pb['pb_time_ms']/1000:.2f}s) for **{boss_name.title()}**.", ephemeral=True)

            await conn.execute("INSERT INTO boss_pbs (discord_id, boss_name, pb_time_ms, proof_url) VALUES ($1, $2, $3, $4) ON CONFLICT (discord_id, boss_name) DO UPDATE SET pb_time_ms = EXCLUDED.pb_time_ms, proof_url = EXCLUDED.proof_url, logged_at = NOW()",
                           ctx.author.id, boss_name.title(), pb_time_ms, proof_url)
            await ctx.respond(f"Your Personal Best for **{boss_name.title()}** has been logged/updated to **{time_in_seconds:.2f} seconds**!", ephemeral=True)
        except Exception as e:
            print(f"Error logging PB: {e}")
            await ctx.respond(f"An error occurred while logging your PB: {e}", ephemeral=True)

@pb.command(name="my", description="View your Personal Best for a specific boss.")
async def my_pb(ctx: discord.ApplicationContext, boss_name: discord.Option(str, "Name of the boss.")):
    await ctx.defer()
    async with bot.db_pool.acquire() as conn:
        pb_data = await conn.fetchrow("SELECT pb_time_ms, proof_url, logged_at FROM boss_pbs WHERE discord_id = $1 AND boss_name ILIKE $2", ctx.author.id, boss_name)

    if not pb_data:
        return await ctx.respond(f"You have no logged Personal Best for **{boss_name.title()}**.", ephemeral=True)

    pb_time_seconds = pb_data['pb_time_ms'] / 1000
    embed = discord.Embed(
        title=f"\n\n {ctx.author.display_name}'s PB for {boss_name.title()}",
        color=discord.Color.gold()
    )
    embed.add_field(name="Time", value=f"**{pb_time_seconds:.2f} seconds**", inline=False)
    embed.add_field(name="Proof", value=f"[View Proof]({pb_data['proof_url']})", inline=False)
    embed.set_footer(text=f"Logged on: {pb_data['logged_at'].strftime('%Y-%m-%d %H:%M UTC')}")
    await ctx.respond(embed=embed)

@pb.command(name="clan", description="View the clan leaderboard for a specific boss PB.")
async def clan_pb(ctx: discord.ApplicationContext, boss_name: discord.Option(str, "Name of the boss.")):
    await ctx.defer()
    async with bot.db_pool.acquire() as conn:
        leaderboard_data = await conn.fetch("SELECT discord_id, pb_time_ms FROM boss_pbs WHERE boss_name ILIKE $1 ORDER BY pb_time_ms ASC LIMIT 10", boss_name)

    embed = discord.Embed(
        title=f"\n\n Clan PB Leaderboard: {boss_name.title()}",
        color=discord.Color.blue()
    )

    if not leaderboard_data:
        embed.description = f"No Personal Bests logged for **{boss_name.title()}** yet."
    else:
        leaderboard_text = ""
        for i, entry in enumerate(leaderboard_data):
            rank_emoji = {1: "\n\n", 2: "\n\n", 3: "\n\n"}.get(i + 1, f"`{i + 1}.`")
            try:
                member = await ctx.guild.fetch_member(entry['discord_id'])
                if member:
                    leaderboard_text += f"{rank_emoji} **{member.display_name}**: {entry['pb_time_ms']/1000:.2f}s\n"
            except discord.NotFound:
                leaderboard_text += f"{rank_emoji} *User Left (ID: {entry['discord_id']})*: {entry['pb_time_ms']/1000:.2f}s\n"
        embed.description = leaderboard_text
    
    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.respond(embed=embed)

@bot.slash_command(name="help", description="Shows a list of all available commands.")
async def help(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    embed = discord.Embed(
        title="\n\n GrazyBot Command List \n\n",
        description="Here are all the commands you can use to manage clan events.",
        color=discord.Color.blurple()
    )
    member_commands = """
    `/ge price` - Check the Grand Exchange price of an item.
    `/ge value` - Calculate the total GE value of multiple items.
    `/osrs link` - Link your Discord account to your OSRS name.
    `/osrs profile` - View your linked OSRS account's stats and boss kills.
    `/osrs kc` - View your (or another member's) OSRS boss kill counts.
    `/points view` - Check your current Clan Point balance.
    `/points leaderboard` - View the Clan Points leaderboard.
    `/sotw view` - View the leaderboard for the current Skill of the Week.
    `/raffle enter` - Get one ticket for the current active raffle (max 10).
    `/raffle view_tickets` - See how many tickets everyone has for the active raffle.
    `/bingo board` - Get a link to the current active bingo board.
    `/bingo complete` - Submit a task for bingo completion.
    `/pointstore rewards` - See what you can buy with your points.
    `/pointstore redeem` - Spend your points on a reward.
    `/events view` - See all currently active events.
    `/pvm participants` - View who has signed up for a PVM event.
    `/pb log` - Log or update your Personal Best time for a boss.
    `/pb my` - View your Personal Best for a specific boss.
    `/pb clan` - View the clan leaderboard for a specific boss.
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
    `/raffle give_tickets` - Give raffle tickets to a member for the active raffle.
    `/raffle edit_tickets` - Set a member's total ticket count for the active raffle.
    `/raffle draw_now` - End a raffle and draw a winner immediately.
    `/raffle cancel` - Cancel a specific raffle.
    `/bingo start` - Start a new clan bingo event.
    `/bingo submissions` - View and manage pending bingo submissions.
    `/pointstore addreward` - Add a new reward to the store.
    `/pointstore removereward` - Remove a reward from the store.
    `/pointstore togglereward` - Activate or deactivate a reward.
    `/pvm schedule` - Schedule a new PVM event.
    `/pvm cancel` - Cancel an upcoming PVM event.
    """
    embed.add_field(name="\n\n Member Commands", value=textwrap.dedent(member_commands), inline=False)
    embed.add_field(name="\n\n Admin Commands", value=textwrap.dedent(admin_commands), inline=False)
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