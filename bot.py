# bot.py
# Grazybot — refactored to be async-safe and to always acknowledge interactions
# Preserves original features: SOTW, Raffles, Bingo, Giveaways, Gemini prompts, DB schema, tasks.

import os
import sys
import asyncio
import logging
import json
import random
import textwrap
from functools import partial
from io import BytesIO
from datetime import datetime, timedelta, timezone, time

import discord
from discord.ext import tasks
from discord import app_commands
import aiohttp
from aiohttp import web
import asyncpg
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# -------------------------
# Logging & env
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s:%(name)s: %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

load_dotenv()

# -------------------------
# Config / Env validation
# -------------------------
TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
WOM_CLAN_ID = os.getenv("WOM_CLAN_ID")
WOM_VERIFICATION_CODE = os.getenv("WOM_VERIFICATION_CODE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEBUG_GUILD_ID_STR = os.getenv("DEBUG_GUILD_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
TASKS_FILE = os.getenv("TASKS_FILE", "tasks.json")
BINGO_FONT_FILE = os.getenv("BINGO_FONT_FILE", "arial.ttf")
PORT = int(os.getenv("PORT", "10000"))

required_env = [TOKEN, WOM_CLAN_ID, WOM_VERIFICATION_CODE, GEMINI_API_KEY, DEBUG_GUILD_ID_STR, DATABASE_URL]
if not all(required_env):
    # Don't exit here; instead log critical and continue — diagnostics command will show what's missing.
    log.critical("One or more critical environment variables are missing. Bot will still start in degraded mode; run /admin diagnostics to check details.")

DEBUG_GUILD_ID = int(DEBUG_GUILD_ID_STR) if DEBUG_GUILD_ID_STR else None

# Channel IDs (0 if not set)
SOTW_CHANNEL_ID = int(os.getenv("SOTW_CHANNEL_ID", 0))
BINGO_CHANNEL_ID = int(os.getenv("BINGO_CHANNEL_ID", 0))
RAFFLE_CHANNEL_ID = int(os.getenv("RAFFLE_CHANNEL_ID", 0))
GIVEAWAY_CHANNEL_ID = int(os.getenv("GIVEAWAY_CHANNEL_ID", 0))
RECAP_CHANNEL_ID = int(os.getenv("RECAP_CHANNEL_ID", 0))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv("ANNOUNCEMENTS_CHANNEL_ID", 0))
PVM_EVENT_CHANNEL_ID = int(os.getenv("PVM_EVENT_CHANNEL_ID", 0))

# -------------------------
# AI setup (Gemini)
# -------------------------
try:
    import google.generativeai as genai
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        ai_model = genai.GenerativeModel("gemini-1.0-pro")
    else:
        ai_model = None
except Exception as e:
    log.warning("Gemini lib not available or failed to init: %s", e)
    ai_model = None

# -------------------------
# Bot and intents
# -------------------------
WOM_SKILLS = [
    "overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic",
    "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing",
    "mining", "herblore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"
]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = discord.Bot(intents=intents)
bot.db_pool = None
bot.active_polls = {}

# Admin slash group
admin = discord.SlashCommandGroup("admin", "Admin-only commands")
bot.add_application_command(admin)

# -------------------------
# Utilities for blocking tasks + safe executor
# -------------------------
async def run_in_executor(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

def utcnow():
    return datetime.now(timezone.utc)

# -------------------------
# Database schema + setup
# -------------------------
SCHEMA_SQLS = [
    """CREATE TABLE IF NOT EXISTS active_competitions (
        id INTEGER PRIMARY KEY,
        title TEXT,
        starts_at TIMESTAMPTZ,
        ends_at TIMESTAMPTZ,
        midway_ping_sent BOOLEAN DEFAULT FALSE,
        final_ping_sent BOOLEAN DEFAULT FALSE,
        winners_awarded BOOLEAN DEFAULT FALSE
    )""",
    """CREATE TABLE IF NOT EXISTS raffles (
        id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
        prize TEXT,
        ends_at TIMESTAMPTZ,
        winner_id BIGINT,
        final_ping_sent BOOLEAN DEFAULT FALSE
    )""",
    """CREATE TABLE IF NOT EXISTS raffle_entries (
        entry_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
        raffle_id INTEGER REFERENCES raffles(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL,
        source TEXT DEFAULT 'user'
    )""",
    """CREATE TABLE IF NOT EXISTS bingo_events (
        id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
        starts_at TIMESTAMPTZ,
        ends_at TIMESTAMPTZ,
        board_json TEXT,
        message_id BIGINT,
        midway_ping_sent BOOLEAN DEFAULT FALSE,
        final_ping_sent BOOLEAN DEFAULT FALSE
    )""",
    """CREATE TABLE IF NOT EXISTS bingo_submissions (
        id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
        user_id BIGINT,
        task_name TEXT,
        proof_url TEXT,
        status TEXT DEFAULT 'pending',
        bingo_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS bingo_completed_tiles (
        bingo_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE,
        task_name TEXT,
        PRIMARY KEY (bingo_id, task_name)
    )""",
    "CREATE TABLE IF NOT EXISTS user_links (discord_id BIGINT PRIMARY KEY, osrs_name TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS clan_points (discord_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0)",
    """CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
        prize TEXT NOT NULL,
        ends_at TIMESTAMPTZ NOT NULL,
        max_number INTEGER NOT NULL,
        winner_id BIGINT,
        winning_number INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS giveaway_entries (
        entry_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
        giveaway_id INTEGER REFERENCES giveaways(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL,
        chosen_number INTEGER NOT NULL,
        UNIQUE (giveaway_id, chosen_number),
        UNIQUE (giveaway_id, user_id)
    )""",
    "CREATE TABLE IF NOT EXISTS pvm_guides (boss_name TEXT PRIMARY KEY, guide_text TEXT NOT NULL)"
]

async def init_db_pool():
    if not DATABASE_URL:
        log.warning("DATABASE_URL not set. DB features disabled.")
        return None
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, command_timeout=60)
    async with pool.acquire() as conn:
        for sql in SCHEMA_SQLS:
            await conn.execute(sql)
    log.info("Database schema ensured.")
    return pool

async def migrate_db(pool):
    if not pool:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute("ALTER TABLE raffles ADD COLUMN IF NOT EXISTS final_ping_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS winner_id BIGINT")
            await conn.execute("ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS winning_number INTEGER")
        log.info("DB migrations checked.")
    except Exception:
        log.exception("Error during DB migrations")

# -------------------------
# Helper functions (preserve logic)
# -------------------------
async def award_points(member: discord.Member, amount: int, reason: str):
    if not member or member.bot:
        return
    try:
        async with bot.db_pool.acquire() as conn:
            await conn.execute("INSERT INTO clan_points (discord_id, points) VALUES ($1, 0) ON CONFLICT (discord_id) DO NOTHING", member.id)
            new_balance = await conn.fetchval("UPDATE clan_points SET points = points + $1 WHERE discord_id = $2 RETURNING points", amount, member.id)
        # DM the user (non-blocking)
        dm_embed = discord.Embed(
            title="🏆 Points Awarded!",
            description=f"You have been awarded **{amount} Clan Points** for *{reason}*.",
            color=5763719
        )
        dm_embed.add_field(name="New Balance", value=f"You now have **{new_balance}** Clan Points.")
        try:
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            log.warning("Could not DM %s (they may have DMs disabled)", member)
    except Exception:
        log.exception("Failed to award points")

async def create_competition(clan_id: str, skill: str, duration_days: int):
    url = "https://api.wiseoldman.net/v2/competitions"
    start_date = utcnow() + timedelta(minutes=1)
    end_date = start_date + timedelta(days=duration_days)
    payload = {
        "title": f"{skill.capitalize()} SOTW ({duration_days} days)",
        "metric": skill,
        "startsAt": start_date.isoformat(),
        "endsAt": end_date.isoformat(),
        "groupId": int(clan_id),
        "groupVerificationCode": WOM_VERIFICATION_CODE
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status == 201:
                    comp_data = await response.json()
                    c = comp_data["competition"]
                    async with bot.db_pool.acquire() as conn:
                        await conn.execute(
                            "INSERT INTO active_competitions (id, title, starts_at, ends_at) VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING",
                            c["id"], c["title"], c["startsAt"], c["endsAt"]
                        )
                    return comp_data, None
                else:
                    try:
                        err = await response.json()
                        return None, f"API Error: {err.get('message', 'Failed')}"
                    except Exception:
                        return None, f"API Error: Status {response.status}"
    except Exception:
        log.exception("Error calling create_competition")
        return None, "Failed to contact WiseOldMan API."

# -------------------------
# Gemini embed generation (offload to executor)
# -------------------------
async def generate_embed_from_prompt(prompt: str):
    if not ai_model:
        log.debug("Gemini not configured; skipping embed generation")
        return None
    full_prompt = f"""
You are TaskmasterGPT, the official announcer for an Old School RuneScape clan.
Tone: epic, engaging, a little cheeky. Output: single valid JSON object representing a Discord embed.
Required: title, description, color (decimal). Optional: fields.
User Request: "{prompt}"
JSON Output:
"""
    try:
        # The ai_model.generate_content is synchronous in this environment; offload to executor safely.
        response = await run_in_executor(ai_model.generate_content, full_prompt)
        if not response or not getattr(response, "text", None):
            return None
        clean_json_string = response.text.strip().lstrip("```json").rstrip("```")
        ai_data = json.loads(clean_json_string)
        # discord.Embed.from_dict requires valid embed dict; catch problems
        try:
            return discord.Embed.from_dict(ai_data)
        except Exception:
            # try to build a minimal embed if dictionary mapping differs
            embed = discord.Embed(
                title=ai_data.get("title", "Announcement"),
                description=ai_data.get("description", ""),
                color=ai_data.get("color", 0)
            )
            if "fields" in ai_data:
                for f in ai_data["fields"]:
                    embed.add_field(name=f.get("name", ""), value=f.get("value", ""), inline=f.get("inline", False))
            return embed
    except Exception:
        log.exception("Gemini embed generation failed")
        return None

# -------------------------
# Bingo image generation (synchronous) — keep as-is but offload
# -------------------------
def generate_bingo_image(tasks: list, completed_tasks: list = []):
    try:
        width, height = 1000, 1000
        background_color = (40, 26, 13)
        img = Image.new("RGB", (width, height), background_color)
        draw = ImageDraw.Draw(img)
        try:
            title_font = ImageFont.truetype(BINGO_FONT_FILE, size=70)
            task_font = ImageFont.truetype(BINGO_FONT_FILE, size=22)
        except Exception:
            title_font = ImageFont.load_default()
            task_font = ImageFont.load_default()

        draw.text((width / 2, 60), "CLAN BINGO", font=title_font, fill=(255, 215, 0), anchor="ms")
        grid_size = 5
        cell_size = 170
        line_width = 4
        grid_start_x, grid_start_y = 75, 125
        grid_end_x, grid_end_y = grid_start_x + grid_size * cell_size, grid_start_y + grid_size * cell_size
        line_color = (255, 215, 0)
        for i in range(grid_size + 1):
            draw.line([(grid_start_x + i * cell_size, grid_start_y), (grid_start_x + i * cell_size, grid_end_y)], fill=line_color, width=line_width)
            draw.line([(grid_start_x, grid_start_y + i * cell_size), (grid_end_x, grid_start_y + i * cell_size)], fill=line_color, width=line_width)

        for i, task in enumerate(tasks):
            if i >= 25: break
            row, col = divmod(i, grid_size)
            cell_x = grid_start_x + col * cell_size
            cell_y = grid_start_y + row * cell_size
            if task["name"] in completed_tasks:
                overlay = Image.new("RGBA", (cell_size - line_width, cell_size - line_width), (0, 255, 0, 90))
                img.paste(overlay, (cell_x + line_width // 2, cell_y + line_width // 2), overlay)

            lines = textwrap.wrap(task["name"], width=15)
            line_heights = [task_font.getbbox(line)[3] for line in lines]
            total_text_height = sum(line_heights) + (len(lines) - 1) * 2
            current_y = (cell_y + (cell_size / 2)) - (total_text_height / 2)
            for line in lines:
                text_width = draw.textbbox((0, 0), line, font=task_font)[2]
                draw.text(((cell_x + (cell_size / 2) - text_width / 2), current_y), line, font=task_font, fill=(255, 255, 255))
                current_y += task_font.getbbox(line)[3] + 2

        image_bytes = BytesIO()
        img.save(image_bytes, format="PNG")
        image_bytes.seek(0)
        return image_bytes, None
    except Exception as e:
        log.exception("generate_bingo_image failed")
        return None, str(e)

# -------------------------
# Announcement helper
# -------------------------
async def send_global_announcement(event_type: str, details: dict, message_url: str):
    channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not channel:
        log.error("Announcements channel missing for global announcement.")
        return
    if event_type == "sotw_start":
        title = f"⚔️ New SOTW: {details.get('skill', 'Unknown')}!"
        desc = f"A new Skill of the Week has begun! It will last for **{details.get('duration', 'a while')}**."
    elif event_type == "raffle_start":
        title = "🎟️ New Raffle Started!"
        desc = f"A raffle for a **{details.get('prize', 'mystery prize')}** has started!"
    elif event_type == "bingo_start":
        title = "🧩 New Clan Bingo!"
        desc = f"A new bingo event started lasting **{details.get('duration', 'a while')}**."
    else:
        title = "🎉 New Event!"
        desc = "A new clan event has started!"

    embed = discord.Embed(title=title, description=desc, color=discord.Color.blue())
    embed.url = message_url
    embed.add_field(name="Details", value=f"[Click here to view the event]({message_url})")
    embed.set_footer(text="A new clan event has started!")
    try:
        await channel.send(content="@everyone", embed=embed)
    except Exception:
        log.exception("Failed to send global announcement")

# -------------------------
# Raffle / Giveaway drawing tasks (safe)
# -------------------------
async def draw_raffle_winner(channel: discord.TextChannel, raffle_id: int):
    try:
        async with bot.db_pool.acquire() as conn:
            raffle_data = await conn.fetchrow("SELECT * FROM raffles WHERE id = $1", raffle_id)
            if not raffle_data:
                log.error("Raffle ID %s not found", raffle_id)
                return
            entries = await conn.fetch("SELECT user_id FROM raffle_entries WHERE raffle_id = $1", raffle_id)
            if not entries:
                await channel.send(f"The raffle for **{raffle_data['prize']}** ended with no entries.")
                return
            winner = random.choice(entries)
            winner_id = winner["user_id"]
            await conn.execute("UPDATE raffles SET winner_id = $1 WHERE id = $2", winner_id, raffle_id)

        winner_user = await bot.fetch_user(winner_id)
        # Award points asynchronously but do not block drawing
        try:
            asyncio.create_task(award_points(winner_user, 50, f"winning the raffle for {raffle_data['prize']}"))
        except Exception:
            log.exception("award_points task failed")

        prompt = f"Create a Discord embed JSON announcing the winner of a raffle. The winner is <@{winner_id}> and they won **{raffle_data['prize']}**."
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title="🎉 Raffle Winner!", description=f"Congratulations to <@{winner_id}> — you won **{raffle_data['prize']}**!", color=discord.Color.magenta())
        embed.add_field(name="Prize", value=f"**{raffle_data['prize']}**", inline=False)
        embed.set_thumbnail(url=winner_user.display_avatar.url if winner_user else None)
        await channel.send(content=f"Congratulations <@{winner_id}>!", embed=embed)
    except Exception:
        log.exception("draw_raffle_winner failed")

async def draw_giveaway_winner(channel: discord.TextChannel, giveaway_id: int):
    try:
        async with bot.db_pool.acquire() as conn:
            giveaway = await conn.fetchrow("SELECT * FROM giveaways WHERE id = $1", giveaway_id)
            if not giveaway:
                return
            prize = giveaway["prize"]
            max_number = giveaway["max_number"]
            win_number = random.randint(1, max_number)
            winner_data = await conn.fetchrow("SELECT user_id FROM giveaway_entries WHERE giveaway_id = $1 AND chosen_number = $2", giveaway_id, win_number)

            if winner_data:
                winner_id = winner_data["user_id"]
                await conn.execute("UPDATE giveaways SET winner_id = $1, winning_number = $2 WHERE id = $3", winner_id, win_number, giveaway_id)
                winner_user = await bot.fetch_user(winner_id)
                prompt = f"Create a Discord embed JSON for a giveaway result where {winner_user.mention} won the prize: **{prize}**. Winning number: **{win_number}**."
                embed = await generate_embed_from_prompt(prompt)
                if not embed:
                    embed = discord.Embed(title=f"🎉 Giveaway Winner: {prize}!", description=f"Congrats to {winner_user.mention} who guessed **{win_number}**!", color=discord.Color.gold())
                embed.set_thumbnail(url=winner_user.display_avatar.url)
                await channel.send(content=f"Congratulations {winner_user.mention}!", embed=embed)
            else:
                await conn.execute("UPDATE giveaways SET winning_number = $1 WHERE id = $2", win_number, giveaway_id)
                prompt = f"Create a Discord embed JSON for giveaway results: nobody guessed the winning number **{win_number}**. Prize: **{prize}**."
                embed = await generate_embed_from_prompt(prompt)
                if not embed:
                    embed = discord.Embed(title=f"🎉 Giveaway Results for {prize}!", description=f"No one picked **{win_number}**. Prize remains in the vault.", color=discord.Color.dark_gold())
                await channel.send(embed=embed)
    except Exception:
        log.exception("draw_giveaway_winner failed")

# -------------------------
# Views / Buttons
# -------------------------
class SotwPollView(discord.ui.View):
    def __init__(self, author: discord.Member):
        super().__init__(timeout=86400)
        self.author = author
        self.votes: dict[str, list[discord.Member]] = {}

    async def create_embed(self):
        prompt = "Create a Discord embed JSON for a new 'Skill of the Week' poll. Encourage everyone to vote to determine the clan's next challenge."
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title="📊 Skill of the Week Poll", description="The time has come to choose our next battleground! Cast your vote to determine the clan's next great challenge.", color=15105600)
        vote_description = "\n\n**Current Votes:**\n"
        for skill, voters in self.votes.items():
            vote_description += f"**{skill.capitalize()}**: {len(voters)} vote(s)\n"
        embed.description = (embed.description or "") + vote_description
        embed.set_footer(text=f"Poll started by {self.author.display_name}", icon_url=self.author.display_avatar.url)
        return embed

    def add_buttons(self, skills: list[str]):
        for skill in skills:
            self.votes[skill] = []
            self.add_item(SotwButton(label=skill.capitalize(), custom_id=skill))
        self.add_item(FinishButton(label="Finish Poll & Start SOTW", custom_id="finish_poll"))

class SotwButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction):
        # toggle user vote
        for skill_key, voters in self.view.votes.items():
            if interaction.user in voters:
                voters.remove(interaction.user)
        self.view.votes[self.custom_id].append(interaction.user)
        new_embed = await self.view.create_embed()
        # respond with an edit (counts as initial response)
        await interaction.response.edit_message(embed=new_embed, view=self.view)
        # ephemeral courtesy message
        try:
            await interaction.followup.send(f"Your vote for **{self.label}** has been counted.", ephemeral=True)
        except Exception:
            # followup could fail if something odd; ignore
            pass

class FinishButton(discord.ui.Button):
    def __init__(self, label, custom_id):
        super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        # defer because this will call external API and DB
        await interaction.response.defer(ephemeral=True)
        try:
            if interaction.user.id != self.view.author.id:
                return await interaction.followup.send("Only the poll starter can finish it.", ephemeral=True)
            view: SotwPollView = self.view  # type: ignore
            if not any(v for v in view.votes.values()):
                return await interaction.followup.send("No votes cast yet.", ephemeral=True)
            winner = max(view.votes, key=lambda k: len(view.votes[k]))
            data, error = await create_competition(WOM_CLAN_ID, winner, 7)
            if error:
                return await interaction.followup.send(f"Poll finished, but failed to start competition: {error}", ephemeral=True)
            # announce in SOTW channel
            sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
            if sotw_channel:
                comp = data["competition"]
                prompt = f"Write a Discord embed JSON announcing a Skill of the Week for **{winner.capitalize()}**, lasting 7 days."
                embed = await generate_embed_from_prompt(prompt)
                if not embed:
                    embed = discord.Embed(title=f"⚔️ SOTW Started: {winner.capitalize()}! ⚔️", description=f"The clan has spoken! The grind for **{winner.capitalize()}** begins now!", color=5763719)
                start_dt = datetime.fromisoformat(comp["startsAt"].replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(comp["endsAt"].replace("Z", "+00:00"))
                embed.url = f"https://wiseoldman.net/competitions/{comp['id']}"
                embed.add_field(name="Start Time", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True)
                embed.add_field(name="End Time", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
                embed.set_footer(text=f"Competition started by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
                sotw_message = await sotw_channel.send(embed=embed)
                await send_global_announcement("sotw_start", {"skill": winner.capitalize(), "duration": "7 days"}, sotw_message.jump_url)
                await interaction.followup.send("Competition created in the SOTW channel!", ephemeral=True)
            # disable buttons
            for item in view.children:
                item.disabled = True
            await interaction.message.edit(view=view)
            bot.active_polls.pop(interaction.guild.id, None)
        except Exception:
            log.exception("Error finishing SOTW poll")
            await interaction.followup.send("An error occurred while finishing the poll.", ephemeral=True)

class SubmissionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_submission")
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            footer = interaction.message.embeds[0].footer.text
            submission_id = int(footer.split(": ")[1])
            async with bot.db_pool.acquire() as conn:
                async with conn.transaction():
                    rec = await conn.fetchrow("SELECT user_id, task_name, bingo_id FROM bingo_submissions WHERE id = $1 AND status = 'pending'", submission_id)
                    if not rec:
                        await interaction.message.delete()
                        return await interaction.followup.send("This submission was already handled or does not exist.", ephemeral=True)
                    await conn.execute("UPDATE bingo_submissions SET status = 'approved' WHERE id = $1", submission_id)
                    await conn.execute("INSERT INTO bingo_completed_tiles (bingo_id, task_name) VALUES ($1, $2) ON CONFLICT (bingo_id, task_name) DO NOTHING", rec["bingo_id"], rec["task_name"])
            await interaction.message.delete()
            await interaction.followup.send(f"Submission #{submission_id} approved.", ephemeral=True)
            member = interaction.guild.get_member(rec["user_id"])
            if member:
                asyncio.create_task(award_points(member, 25, f"completing the bingo task: '{rec['task_name']}'"))
            await update_bingo_board_post(rec["bingo_id"])
        except Exception:
            log.exception("approve_button failed")
            await interaction.followup.send("An error occurred while approving the submission.", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_submission")
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            footer = interaction.message.embeds[0].footer.text
            submission_id = int(footer.split(": ")[1])
            res = await bot.db_pool.execute("UPDATE bingo_submissions SET status = 'denied' WHERE id = $1 AND status = 'pending'", submission_id)
            if res == "UPDATE 0":
                await interaction.message.delete()
                return await interaction.followup.send("This submission was already handled.", ephemeral=True)
            await interaction.message.delete()
            await interaction.followup.send(f"Submission #{submission_id} denied.", ephemeral=True)
        except Exception:
            log.exception("deny_button failed")
            await interaction.followup.send("An error occurred while denying the submission.", ephemeral=True)

# -------------------------
# Bingo update helper (offload image generation)
# -------------------------
async def update_bingo_board_post(bingo_id: int):
    try:
        async with bot.db_pool.acquire() as conn:
            event_data = await conn.fetchrow("SELECT board_json, message_id FROM bingo_events WHERE id = $1", bingo_id)
            if not event_data:
                return
            completed_recs = await conn.fetch("SELECT task_name FROM bingo_completed_tiles WHERE bingo_id = $1", bingo_id)
            completed_tasks = [r["task_name"] for r in completed_recs]
        board_tasks = json.loads(event_data["board_json"])
        image_bytes, error = await run_in_executor(generate_bingo_image, board_tasks, completed_tasks)
        if error:
            log.error("Could not generate bingo image: %s", error)
            return
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if not bingo_channel:
            log.warning("Bingo channel not found when updating board.")
            return
        try:
            message = await bingo_channel.fetch_message(event_data["message_id"])
        except discord.NotFound:
            log.warning("Original bingo message not found to update.")
            return
        file = discord.File(image_bytes, filename="bingo_board.png")
        embed = message.embeds[0] if message.embeds else discord.Embed(title="Clan Bingo")
        embed.set_image(url="attachment://bingo_board.png")
        await message.edit(embed=embed, attachments=[file])
    except Exception:
        log.exception("update_bingo_board_post failed")

# -------------------------
# Scheduled tasks: daily summary, event manager
# -------------------------
daily_summary_time = time(hour=12, minute=0, tzinfo=timezone.utc)

@tasks.loop(time=daily_summary_time)
async def daily_event_summary():
    await bot.wait_until_ready()
    try:
        ann = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
        if not ann:
            log.debug("Announcements channel not configured for daily summary")
            return
        async with bot.db_pool.acquire() as conn:
            competitions = await conn.fetch("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
            raffles = await conn.fetch("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
            bingo = await conn.fetchrow("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        if not competitions and not raffles and not bingo:
            log.info("No active events for daily summary")
            return
        s = ""
        if competitions:
            s += "Skill of the Week Competitions:\n" + "".join([f"- {c['title']} (Ends <t:{int(c['ends_at'].timestamp())}:R>)\n" for c in competitions])
        if raffles:
            s += "\nRaffles:\n" + "".join([f"- Prize: {r['prize']} (Ends <t:{int(r['ends_at'].timestamp())}:R>)\n" for r in raffles])
        if bingo:
            s += f"\nBingo Event:\nA clan-wide bingo is active! (Ends <t:{int(bingo['ends_at'].timestamp())}:R>)\n"
        prompt = f"Create a Discord embed JSON for a daily summary of active clan events. Data:\n{s}"
        embed = await generate_embed_from_prompt(prompt)
        if not embed:
            embed = discord.Embed(title="📅 Daily Clan Events Summary", description=s or "No events", color=10181046)
        embed.set_footer(text="Good luck, have fun!")
        embed.timestamp = utcnow()
        await ann.send(embed=embed)
    except Exception:
        log.exception("daily_event_summary failed")

@tasks.loop(minutes=5)
async def event_manager():
    await bot.wait_until_ready()
    async def safe_run(handler):
        try:
            await handler()
        except Exception:
            log.exception("event_manager handler failed: %s", handler.__name__)
    await asyncio.gather(
        safe_run(handle_weekly_recap),
        safe_run(handle_sotw_management),
        safe_run(handle_raffle_management),
        safe_run(handle_bingo_management),
        safe_run(handle_giveaway_management)
    )

# --- event handlers used by event_manager ---
async def handle_weekly_recap():
    now = utcnow()
    recap_channel = bot.get_channel(RECAP_CHANNEL_ID)
    if not (recap_channel and now.weekday() == 6 and now.hour == 19 and now.minute < 5):
        return
    url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/gained?period=week&metric=overall"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return
                data = await r.json()
                if not data:
                    embed = discord.Embed(title="📈 Weekly Recap", description="No XP gains to report this week.", color=discord.Color.blue())
                else:
                    ds = ""
                    for i, player in enumerate(data[:10]):
                        ds += f"{i+1}. {player['player']['displayName']}: {player.get('gained', 0):,} XP\n"
                    prompt = f"Create a Discord embed JSON for our clan's weekly recap. Data:\n{ds}"
                    embed = await generate_embed_from_prompt(prompt) or discord.Embed(title="📈 Weekly Recap", description=ds, color=discord.Color.blue())
                embed.set_footer(text=f"Recap for week ending {now.strftime('%Y-%m-%d')}")
                await recap_channel.send(embed=embed)
    except Exception:
        log.exception("handle_weekly_recap failed")

async def handle_sotw_management():
    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if not sotw_channel or not bot.db_pool:
        return
    async with bot.db_pool.acquire() as conn:
        now = utcnow()
        competitions = await conn.fetch("SELECT * FROM active_competitions WHERE ends_at < NOW() + interval '7 days'")
        for comp in competitions:
            ends_at, starts_at = comp["ends_at"], comp["starts_at"]
            if now > ends_at and not comp["winners_awarded"]:
                await conn.execute("UPDATE active_competitions SET winners_awarded = TRUE WHERE id = $1", comp["id"])
                asyncio.create_task(award_sotw_winners_for_comp(comp))
            elif not comp["final_ping_sent"] and (ends_at - now) <= timedelta(hours=1) and now < ends_at:
                await conn.execute("UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = $1", comp["id"])
                await sotw_channel.send(content="@everyone", embed=discord.Embed(title="⏳ Final Hour!", description=f"The **{comp['title']}** competition ends in less than an hour!", color=discord.Color.red(), url=f"https://wiseoldman.net/competitions/{comp['id']}"))
            elif not comp["midway_ping_sent"] and now >= starts_at + ((ends_at - starts_at) / 2) and now < ends_at:
                await conn.execute("UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = $1", comp["id"])
                await sotw_channel.send(embed=discord.Embed(title="½ Midway Point Reached!", description=f"The **{comp['title']}** competition is halfway through!", color=discord.Color.yellow(), url=f"https://wiseoldman.net/competitions/{comp['id']}"))

async def award_sotw_winners_for_comp(comp):
    details_url = f"https://api.wiseoldman.net/v2/competitions/{comp['id']}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(details_url) as r:
                if r.status != 200:
                    return
                comp_data = await r.json()
                for i, participant in enumerate(comp_data.get("participations", [])[:3]):
                    await award_sotw_winner_points(participant, i, comp["title"])
    except Exception:
        log.exception("award_sotw_winners_for_comp failed")

async def award_sotw_winner_points(participant, rank, title):
    osrs_name = participant["player"]["displayName"]
    if not bot.db_pool:
        return
    discord_id = await bot.db_pool.fetchval("SELECT discord_id FROM user_links WHERE osrs_name = $1", osrs_name)
    if discord_id and bot.get_guild(DEBUG_GUILD_ID):
        member = bot.get_guild(DEBUG_GUILD_ID).get_member(discord_id)
        if member:
            values = [100, 50, 25]
            asyncio.create_task(award_points(member, values[rank], f"placing #{rank+1} in the {title} SOTW"))

async def handle_raffle_management():
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not raffle_channel or not bot.db_pool:
        return
    now = utcnow()
    async with bot.db_pool.acquire() as conn:
        raffles_to_draw = await conn.fetch("SELECT id FROM raffles WHERE winner_id IS NULL AND ends_at <= $1", now)
        raffles_to_remind = await conn.fetch("SELECT * FROM raffles WHERE winner_id IS NULL AND final_ping_sent = FALSE AND ends_at - $1 <= interval '1 day'", now)
        for r in raffles_to_remind:
            await conn.execute("UPDATE raffles SET final_ping_sent = TRUE WHERE id = $1", r["id"])
            embed = discord.Embed(title="🎟️ Raffle Ending Soon!", description=f"24 hours left to enter the raffle for **{r['prize']}**!", color=discord.Color.orange())
            await raffle_channel.send(content="@everyone", embed=embed)
    for r in raffles_to_draw:
        asyncio.create_task(draw_raffle_winner(raffle_channel, r["id"]))

async def handle_bingo_management():
    # placeholder: your original file left this as pass
    return

async def handle_giveaway_management():
    if not bot.db_pool:
        return
    giveaway_channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if not giveaway_channel:
        return
    try:
        ended = await bot.db_pool.fetch("SELECT * FROM giveaways WHERE winner_id IS NULL AND ends_at <= NOW()")
        for g in ended:
            asyncio.create_task(draw_giveaway_winner(giveaway_channel, g["id"]))
    except Exception:
        log.exception("handle_giveaway_management failed")

# -------------------------
# Web server — non-blocking runner for Render
# -------------------------
async def health_handler(request):
    return web.Response(text="OK", status=200)

async def start_web_runner():
    app = web.Application()
    app.add_routes([web.get("/", health_handler), web.head("/", health_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web server started on port %s", PORT)
    return runner

# -------------------------
# Bot events & commands (preserve original command patterns but safe)
# -------------------------
@bot.event
async def on_ready():
    log.info("%s is online and ready!", bot.user)

@bot.event
async def setup_hook():
    # sync commands and start tasks
    try:
        await bot.sync_commands()  # keeps it simple; per-guild sync optional
    except Exception:
        log.exception("Failed to sync commands")
    if not event_manager.is_running():
        event_manager.start()
    if not daily_event_summary.is_running():
        daily_event_summary.start()
    bot.add_view(SubmissionView())
    log.info("Setup hook complete")

@bot.event
async def on_message(message):
    # preserve the helpful PVM guide trigger behavior but ensure non-blocking
    if message.author.bot:
        return
    trigger_phrases = ["what gear for", "setup for", "inventory for"]
    if any(message.content.lower().startswith(t) for t in trigger_phrases):
        prompt = f"You are an expert Old School RuneScape (OSRS) player. Respond to: \"{message.content}\""
        async with message.channel.typing():
            try:
                if not ai_model:
                    await message.reply("Sorry, AI features are currently disabled.")
                    return
                # offload to executor
                response = await run_in_executor(ai_model.generate_content, prompt)
                embed = discord.Embed(title="Gear & Inventory Guide", description=getattr(response, "text", str(response)), color=discord.Color.blue())
                embed.set_footer(text=f"Guide for: {message.content}")
                await message.reply(embed=embed)
            except Exception:
                log.exception("on_message AI reply failed")
                await message.reply("Sorry, I couldn't fetch a guide for that right now.")

# -------------------------
# Slash commands & groups
# -------------------------
@bot.slash_command(name="ping", description="Check bot responsiveness.")
async def _ping(ctx: discord.ApplicationContext):
    # if fast, respond immediately
    await ctx.respond(f"Pong! Latency: {round(bot.latency * 1000)}ms", ephemeral=True)

# Admin diagnostics (mirrors your original but safe)
@admin.command(name="diagnostics", description="Runs a full system check to ensure the bot is configured correctly.")
async def diagnostics(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    lines = ["**--- Bot Diagnostics Report ---**"]
    lines.append("\n**Environment Variables:**")
    envs = ["TOKEN", "WOM_CLAN_ID", "WOM_VERIFICATION_CODE", "GEMINI_API_KEY", "DEBUG_GUILD_ID", "DATABASE_URL"]
    all_ok = True
    for e in envs:
        if os.getenv(e):
            lines.append(f"✅ `{e}` is set.")
        else:
            lines.append(f"❌ `{e}` is MISSING.")
            all_ok = False
    lines.append("\n**Database Connection:**")
    if bot.db_pool and all_ok:
        try:
            async with bot.db_pool.acquire() as conn:
                v = await conn.fetchval("SELECT 1")
                lines.append("✅ Successfully connected to the database and executed a query." if v == 1 else "❌ Connected to DB but query returned unexpected.")
        except Exception as e:
            lines.append(f"❌ Failed to query DB: `{e}`")
    else:
        lines.append("❌ Database pool unavailable.")
    lines.append("\n**Gemini AI API:**")
    if ai_model:
        try:
            test_embed = await generate_embed_from_prompt("test")
            lines.append("✅ Gemini responded." if test_embed else "❌ Gemini returned no usable embed.")
        except Exception as e:
            lines.append(f"❌ Error contacting Gemini: `{e}`")
    else:
        lines.append("❌ Gemini not configured.")
    lines.append("\n**File Access:**")
    try:
        with open(TASKS_FILE, "r") as f:
            json.load(f)
        lines.append(f"✅ Read `{TASKS_FILE}` successfully.")
    except FileNotFoundError:
        lines.append(f"❌ `{TASKS_FILE}` not found.")
    except json.JSONDecodeError:
        lines.append(f"❌ `{TASKS_FILE}` is invalid JSON.")
    await ctx.followup.send("\n".join(lines), ephemeral=True)

# -------------------------
# SOTW commands
# -------------------------
sotw = bot.create_group("sotw", "Skill of the Week commands")

@sotw.command(name="start", description="Manually start a new SOTW competition.")
@discord.default_permissions(manage_events=True)
async def sotw_start(ctx: discord.ApplicationContext, skill: discord.Option(str, choices=WOM_SKILLS), duration_days: discord.Option(int, default=7)):
    await ctx.defer(ephemeral=True)
    try:
        data, error = await create_competition(WOM_CLAN_ID, skill, duration_days)
        if error:
            return await ctx.followup.send(error, ephemeral=True)
        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            comp = data["competition"]
            prompt = f"Write a Discord embed JSON announcing a Skill of the Week: **{skill.capitalize()}**, lasting **{duration_days}** days."
            embed = await generate_embed_from_prompt(prompt) or discord.Embed(title=f"⚔️ SOTW Started: {skill.capitalize()}! ⚔️", description=f"The great grind for **{skill.capitalize()}** begins now!", color=5763719)
            start_dt = datetime.fromisoformat(comp["startsAt"].replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(comp["endsAt"].replace("Z", "+00:00"))
            embed.url = f"https://wiseoldman.net/competitions/{comp['id']}"
            embed.add_field(name="Start Time", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True)
            embed.add_field(name="End Time", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
            embed.set_footer(text=f"Competition started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
            sotw_message = await sotw_channel.send(embed=embed)
            await send_global_announcement("sotw_start", {"skill": skill.capitalize(), "duration": f"{duration_days} days"}, sotw_message.jump_url)
            await ctx.followup.send(f"SOTW for {skill.capitalize()} created! [Jump]({sotw_message.jump_url})", ephemeral=True)
        else:
            await ctx.followup.send("SOTW channel not configured.", ephemeral=True)
    except Exception:
        log.exception("sotw_start failed")
        await ctx.followup.send("An error occurred starting SOTW.", ephemeral=True)

@sotw.command(name="poll", description="Start a poll to choose the next SOTW.")
@discord.default_permissions(manage_events=True)
async def sotw_poll(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        if ctx.guild.id in bot.active_polls:
            return await ctx.followup.send("There is already an active SOTW poll.", ephemeral=True)
        poll_skills = random.sample(WOM_SKILLS, 6)
        view = SotwPollView(ctx.author)
        view.add_buttons(poll_skills)
        embed = await view.create_embed()
        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if not sotw_channel:
            return await ctx.followup.send("SOTW channel not configured.", ephemeral=True)
        poll_message = await sotw_channel.send(embed=embed, view=view)
        view.message_id = poll_message.id
        bot.active_polls[ctx.guild.id] = view
        await ctx.followup.send("SOTW poll created!", ephemeral=True)
    except Exception:
        log.exception("sotw_poll failed")
        await ctx.followup.send("An error occurred creating the poll.", ephemeral=True)

@sotw.command(name="view", description="View the leaderboard for the current SOTW.")
async def sotw_view(ctx: discord.ApplicationContext):
    await ctx.defer()
    try:
        list_url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/competitions"
        async with aiohttp.ClientSession() as s:
            async with s.get(list_url) as r:
                if r.status != 200:
                    return await ctx.followup.send("Could not fetch competition list.")
                competitions = await r.json()
                if not competitions:
                    return await ctx.followup.send("No competitions found.")
                latest_comp_id = competitions[0]["id"]
        details_url = f"https://api.wiseoldman.net/v2/competitions/{latest_comp_id}"
        async with aiohttp.ClientSession() as s:
            async with s.get(details_url) as r:
                if r.status != 200:
                    return await ctx.followup.send("Could not fetch competition details.")
                data = await r.json()
        embed = discord.Embed(title=f"Leaderboard: {data['title']}", description=f"Current standings for the **{data['metric'].capitalize()}** competition.", color=discord.Color.purple(), url=f"https://wiseoldman.net/competitions/{data['id']}")
        leaderboard_text = ""
        for i, player in enumerate(data.get("participations", [])[:10]):
            rank_emoji = {1: "🏆", 2: "🥈", 3: "🥉"}.get(i + 1, f"{i+1}.")
            leaderboard_text += f"{rank_emoji} **{player['player']['displayName']}**: {player['progress']['gained']:,} XP\n"
        embed.add_field(name="Top 10", value=leaderboard_text or "No participants yet", inline=False)
        end_dt = datetime.fromisoformat(data["endsAt"].replace("Z", "+00:00"))
        embed.set_footer(text="Competition ends")
        embed.timestamp = end_dt
        await ctx.followup.send(embed=embed)
    except Exception:
        log.exception("sotw_view failed")
        await ctx.followup.send("An error occurred fetching the leaderboard.")

# -------------------------
# Raffle group (keeps your logic but ensures responses)
# -------------------------
raffle = bot.create_group("raffle", "Commands for managing raffles.")

@raffle.command(name="start", description="Start a new raffle.")
@discord.default_permissions(manage_events=True)
async def raffle_start(ctx: discord.ApplicationContext, prize: discord.Option(str, "What is the prize?"), duration_days: discord.Option(float, "How many days will it last?")):
    await ctx.defer(ephemeral=True)
    try:
        ends_at = utcnow() + timedelta(days=duration_days)
        duration_str = f"{int(duration_days)} day(s)" if duration_days >= 1 else f"{int(duration_days*24)} hours"
        prompt = f"Create a Discord embed JSON for a raffle starting now. Prize: **{prize}**. Duration: **{duration_str}**."
        embed = await generate_embed_from_prompt(prompt) or discord.Embed(title="🎟️ A New Raffle has Begun!", description=f"A new raffle is underway for a **{prize}**!", color=15844367)
        if not bot.db_pool:
            return await ctx.followup.send("Database not configured.", ephemeral=True)
        raffle_id = await bot.db_pool.fetchval("INSERT INTO raffles (prize, ends_at) VALUES ($1, $2) RETURNING id", prize, ends_at)
        embed.add_field(name="How to Enter", value="Use `/raffle enter` to get a ticket! (Max 10 per person)", inline=False)
        embed.add_field(name="Raffle Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"Raffle ID: {raffle_id}")
        raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
        if raffle_channel:
            raffle_message = await raffle_channel.send(embed=embed)
            await send_global_announcement("raffle_start", {"prize": prize, "duration": duration_str}, raffle_message.jump_url)
            await ctx.followup.send(f"Raffle for **{prize}** created! [Jump to message]({raffle_message.jump_url})", ephemeral=True)
        else:
            await ctx.followup.send("Raffle channel not configured.", ephemeral=True)
    except Exception:
        log.exception("raffle_start failed")
        await ctx.followup.send("An error occurred starting the raffle.", ephemeral=True)

@raffle.command(name="enter", description="Get one ticket for the current raffle (max 10).")
async def raffle_enter(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        if not bot.db_pool:
            return await ctx.followup.send("Database not configured.", ephemeral=True)
        async with bot.db_pool.acquire() as conn:
            raffle = await conn.fetchrow("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
            if not raffle:
                return await ctx.followup.send("No active raffle to enter.", ephemeral=True)
            count = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2 AND source = 'user'", ctx.author.id, raffle["id"])
            if count >= 10:
                return await ctx.followup.send("You have already claimed your maximum of 10 tickets.", ephemeral=True)
            await conn.execute("INSERT INTO raffle_entries (user_id, source, raffle_id) VALUES ($1, 'user', $2)", ctx.author.id, raffle["id"])
            total_tickets = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", ctx.author.id, raffle["id"])
        await ctx.followup.send(f"You claimed a ticket for the **{raffle['prize']}** raffle. Total: {total_tickets}", ephemeral=True)
    except Exception:
        log.exception("raffle_enter failed")
        await ctx.followup.send("An error occurred while entering the raffle.", ephemeral=True)

@raffle.command(name="give_tickets", description="ADMIN: Give raffle tickets to a member.")
@discord.default_permissions(manage_events=True)
async def raffle_give_tickets(ctx: discord.ApplicationContext, member: discord.Member, amount: int):
    await ctx.defer(ephemeral=True)
    try:
        if not bot.db_pool:
            return await ctx.followup.send("Database not configured.", ephemeral=True)
        async with bot.db_pool.acquire() as conn:
            raffle = await conn.fetchrow("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
            if not raffle:
                return await ctx.followup.send("No active raffle.", ephemeral=True)
            entries = [(raffle["id"], member.id, "admin") for _ in range(amount)]
            await conn.copy_records_to_table("raffle_entries", records=entries, columns=["raffle_id", "user_id", "source"])
            total = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", member.id, raffle["id"])
        await ctx.followup.send(f"Gave {amount} tickets to {member.display_name}. They now have {total} tickets.", ephemeral=True)
    except Exception:
        log.exception("raffle_give_tickets failed")
        await ctx.followup.send("An error occurred granting tickets.", ephemeral=True)

@raffle.command(name="view_tickets", description="View the current ticket count for all participants.")
async def raffle_view_tickets(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        if not bot.db_pool:
            return await ctx.followup.send("Database not configured.", ephemeral=True)
        raffle = await bot.db_pool.fetchrow("SELECT id, prize FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        if not raffle:
            return await ctx.followup.send("No active raffle.", ephemeral=True)
        entries = await bot.db_pool.fetch("SELECT user_id, COUNT(user_id) as count FROM raffle_entries WHERE raffle_id = $1 GROUP BY user_id ORDER BY count DESC", raffle["id"])
        embed = discord.Embed(title=f"🎟️ Raffle Tickets for '{raffle['prize']}'", color=discord.Color.gold())
        if not entries:
            embed.description = "No tickets yet."
        else:
            lines = []
            for entry in entries[:20]:
                m = ctx.guild.get_member(entry["user_id"])
                name = m.display_name if m else f"User ID: {entry['user_id']}"
                lines.append(f"**{name}**: {entry['count']} ticket(s)")
            embed.description = "\n".join(lines)
        await ctx.followup.send(embed=embed, ephemeral=True)
    except Exception:
        log.exception("raffle_view_tickets failed")
        await ctx.followup.send("An error occurred fetching tickets.", ephemeral=True)

@raffle.command(name="draw_now", description="ADMIN: Immediately ends the raffle and draws a winner.")
@discord.default_permissions(manage_events=True)
async def raffle_draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        ch = bot.get_channel(RAFFLE_CHANNEL_ID)
        if not ch:
            return await ctx.followup.send("Raffle channel not found.", ephemeral=True)
        if not bot.db_pool:
            return await ctx.followup.send("Database not configured.", ephemeral=True)
        raffle = await bot.db_pool.fetchrow("SELECT * FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        if not raffle:
            return await ctx.followup.send("No raffle to draw.", ephemeral=True)
        await draw_raffle_winner(ch, raffle["id"])
        await ctx.followup.send("Triggered raffle drawing.", ephemeral=True)
    except Exception:
        log.exception("raffle_draw_now failed")
        await ctx.followup.send("An error occurred drawing raffle.", ephemeral=True)

@raffle.command(name="cancel", description="ADMIN: Cancels the current raffle without drawing a winner.")
@discord.default_permissions(manage_events=True)
async def raffle_cancel(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        if not bot.db_pool:
            return await ctx.followup.send("Database not configured.", ephemeral=True)
        raffle = await bot.db_pool.fetchrow("SELECT id, prize FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        if not raffle:
            return await ctx.followup.send("No active raffle to cancel.", ephemeral=True)
        await bot.db_pool.execute("DELETE FROM raffles WHERE id = $1", raffle["id"])
        ch = bot.get_channel(RAFFLE_CHANNEL_ID)
        if ch:
            await ch.send(f"The raffle for **{raffle['prize']}** has been cancelled by an admin.")
        await ctx.followup.send("Raffle cancelled.", ephemeral=True)
    except Exception:
        log.exception("raffle_cancel failed")
        await ctx.followup.send("An error occurred cancelling raffle.", ephemeral=True)

# -------------------------
# Giveaway commands (pick-a-number)
# -------------------------
giveaway = bot.create_group("giveaway", "Pick-a-number giveaway commands.")

@giveaway.command(name="start", description="ADMIN: Start a pick-a-number giveaway.")
@discord.default_permissions(manage_events=True)
async def giveaway_start(ctx: discord.ApplicationContext, prize: discord.Option(str), max_number: discord.Option(int), duration_days: discord.Option(float)):
    await ctx.defer(ephemeral=True)
    try:
        if not bot.db_pool:
            return await ctx.followup.send("Database not configured.", ephemeral=True)
        active = await bot.db_pool.fetchval("SELECT id FROM giveaways WHERE ends_at > NOW()")
        if active:
            return await ctx.followup.send("There is already an active giveaway.", ephemeral=True)
        ends_at = utcnow() + timedelta(days=duration_days)
        await bot.db_pool.execute("INSERT INTO giveaways (prize, ends_at, max_number) VALUES ($1, $2, $3)", prize, ends_at, max_number)
        duration_str = f"{int(duration_days)} day(s)" if duration_days >= 1 else f"{int(duration_days*24)} hours"
        prompt = f"Create a Discord embed JSON for a 'pick-a-number' giveaway. Prize: **{prize}**. Range: 1 to {max_number}. Duration: {duration_str}."
        embed = await generate_embed_from_prompt(prompt) or discord.Embed(title="🎉 Giveaway!", description=f"A giveaway for **{prize}** is live!", color=discord.Color.dark_magenta())
        embed.add_field(name="How to Enter", value=f"Pick a number between 1 and {max_number} using `/giveaway enter`.", inline=False)
        embed.add_field(name="Giveaway Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        ch = bot.get_channel(GIVEAWAY_CHANNEL_ID)
        if ch:
            await ch.send(embed=embed)
            await ctx.followup.send("Giveaway created.", ephemeral=True)
        else:
            await ctx.followup.send("Giveaway channel not configured.", ephemeral=True)
    except Exception:
        log.exception("giveaway_start failed")
        await ctx.followup.send("An error occurred starting giveaway.", ephemeral=True)

@giveaway.command(name="enter", description="Enter the current giveaway by picking a number.")
async def giveaway_enter(ctx: discord.ApplicationContext, number: discord.Option(int, required=False) = None):
    await ctx.defer(ephemeral=True)
    try:
        if not bot.db_pool:
            return await ctx.followup.send("Database not configured.", ephemeral=True)
        async with bot.db_pool.acquire() as conn:
            giveaway = await conn.fetchrow("SELECT * FROM giveaways WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
            if not giveaway:
                return await ctx.followup.send("No active giveaway.", ephemeral=True)
            gid, max_num = giveaway["id"], giveaway["max_number"]
            if number is None:
                taken = {r["chosen_number"] for r in await conn.fetch("SELECT chosen_number FROM giveaway_entries WHERE giveaway_id = $1", gid)}
                available = list(set(range(1, max_num + 1)) - taken)
                if not available:
                    return await ctx.followup.send("All numbers are taken.", ephemeral=True)
                number = random.choice(available)
            if not (1 <= number <= max_num):
                return await ctx.followup.send(f"Pick a number between 1 and {max_num}.", ephemeral=True)
            try:
                await conn.execute("INSERT INTO giveaway_entries (giveaway_id, user_id, chosen_number) VALUES ($1, $2, $3)", gid, ctx.author.id, number)
                await ctx.followup.send(f"Your entry for number **{number}** is locked in. Good luck!", ephemeral=True)
            except asyncpg.UniqueViolationError as e:
                # constraint_name may not be present depending on driver; do generic message
                await ctx.followup.send("You or this number is already taken.", ephemeral=True)
    except Exception:
        log.exception("giveaway_enter failed")
        await ctx.followup.send("An error occurred entering the giveaway.", ephemeral=True)

@giveaway.command(name="draw_now", description="ADMIN: Immediately draw a giveaway winner.")
@discord.default_permissions(manage_events=True)
async def giveaway_draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        ch = bot.get_channel(GIVEAWAY_CHANNEL_ID)
        if not ch:
            return await ctx.followup.send("Giveaway channel not found.", ephemeral=True)
        if not bot.db_pool:
            return await ctx.followup.send("DB not configured.", ephemeral=True)
        g = await bot.db_pool.fetchrow("SELECT * FROM giveaways WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        if not g:
            return await ctx.followup.send("No active giveaway.", ephemeral=True)
        await draw_giveaway_winner(ch, g["id"])
        await ctx.followup.send("Triggered giveaway draw.", ephemeral=True)
    except Exception:
        log.exception("giveaway_draw_now failed")
        await ctx.followup.send("An error occurred drawing the giveaway.", ephemeral=True)

# -------------------------
# Events viewing & Bingo commands
# -------------------------
events = bot.create_group("events", "View active clan events")

@events.command(name="view", description="Shows all currently active competitions, raffles, and bingo events.")
async def events_view(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        if not bot.db_pool:
            return await ctx.followup.send("DB not configured.", ephemeral=True)
        async with bot.db_pool.acquire() as conn:
            competitions = await conn.fetch("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
            raffles = await conn.fetch("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
            bingo = await conn.fetchrow("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
        embed = discord.Embed(title="📅 Clan Event Status", description="Active events", color=discord.Color.blurple())
        if competitions:
            comp_info = "".join([f"**Title:** [{c['title']}](https://wiseoldman.net/competitions/{c['id']})\n**Ends:** <t:{int(c['ends_at'].timestamp())}:R>\n\n" for c in competitions])
            embed.add_field(name="⚔️ Active Competitions", value=comp_info, inline=False)
        else:
            embed.add_field(name="⚔️ Active Competitions", value="No SOTW competitions currently.", inline=False)
        if raffles:
            raffle_info = "".join([f"**Prize:** {r['prize']}\n**Ends:** <t:{int(r['ends_at'].timestamp())}:R>\n\n" for r in raffles])
            embed.add_field(name="🎟️ Active Raffles", value=raffle_info, inline=False)
        else:
            embed.add_field(name="🎟️ Active Raffles", value="No raffles currently.", inline=False)
        if bingo:
            bingo_url = f"https://discord.com/channels/{ctx.guild.id}/{BINGO_CHANNEL_ID}/{bingo['message_id']}"
            embed.add_field(name="🧩 Active Bingo", value=f"A bingo is live — [View Board]({bingo_url})\nEnds: <t:{int(bingo['ends_at'].timestamp())}:R>", inline=False)
        else:
            embed.add_field(name="🧩 Active Bingo", value="No bingo event currently.", inline=False)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.followup.send(embed=embed, ephemeral=True)
    except Exception:
        log.exception("events_view failed")
        await ctx.followup.send("An error occurred fetching events.", ephemeral=True)

# Bingo group (start, complete, submissions, board)
bingo = bot.create_group("bingo", "Clan bingo commands")

@bingo.command(name="start", description="Start a new bingo event.")
@discord.default_permissions(manage_events=True)
async def bingo_start(ctx: discord.ApplicationContext, duration_days: int):
    await ctx.defer(ephemeral=True)
    try:
        # Read tasks file
        try:
            with open(TASKS_FILE, "r") as f:
                all_tasks = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return await ctx.followup.send(f"Error: `{TASKS_FILE}` missing or invalid.", ephemeral=True)
        tasks_by_difficulty = {"common": [], "uncommon": [], "rare": []}
        for task in all_tasks:
            tasks_by_difficulty.setdefault(task["difficulty"], []).append(task)
        composition = {"common": 15, "uncommon": 7, "rare": 3}
        board_tasks = []
        for diff, count in composition.items():
            if len(tasks_by_difficulty.get(diff, [])) < count:
                return await ctx.followup.send(f"Not enough {diff} tasks in {TASKS_FILE}", ephemeral=True)
            board_tasks.extend(random.sample(tasks_by_difficulty[diff], count))
        if len(board_tasks) < 25:
            return await ctx.followup.send("Not enough tasks to form a 25-slot board.", ephemeral=True)
        random.shuffle(board_tasks)
        board = board_tasks[:25]
        image_bytes, error = await run_in_executor(generate_bingo_image, board)
        if error:
            return await ctx.followup.send(f"Failed to generate bingo image: {error}", ephemeral=True)
        bingo_ch = bot.get_channel(BINGO_CHANNEL_ID)
        if not bingo_ch:
            return await ctx.followup.send("Bingo channel not configured.", ephemeral=True)
        duration_str = f"{duration_days} day(s)"
        prompt = f"Create a Discord embed JSON for a new clan bingo event lasting {duration_str}."
        embed = await generate_embed_from_prompt(prompt) or discord.Embed(title="🧩 New Clan Bingo!", description=f"A new bingo event for {duration_str}!", color=11027200)
        file = discord.File(image_bytes, filename="bingo_board.png")
        embed.set_image(url="attachment://bingo_board.png")
        ends_at = utcnow() + timedelta(days=duration_days)
        embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"Bingo started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        message = await bingo_ch.send(embed=embed, file=file)
        if not bot.db_pool:
            return await ctx.followup.send("DB not configured — bingo saved locally only.", ephemeral=True)
        bingo_id = await bot.db_pool.fetchval("INSERT INTO bingo_events (starts_at, ends_at, board_json, message_id) VALUES ($1, $2, $3, $4) RETURNING id", utcnow(), ends_at, json.dumps(board), message.id)
        await send_global_announcement("bingo_start", {"duration": duration_str}, message.jump_url)
        await ctx.followup.send(f"Bingo event #{bingo_id} created!", ephemeral=True)
    except Exception:
        log.exception("bingo_start failed")
        await ctx.followup.send("An error occurred starting bingo.", ephemeral=True)

@bingo.command(name="complete", description="Submit a task for bingo completion.")
async def bingo_complete(ctx: discord.ApplicationContext, task: str, proof: str):
    await ctx.defer(ephemeral=True)
    try:
        if not bot.db_pool:
            return await ctx.followup.send("DB not configured.", ephemeral=True)
        async with bot.db_pool.acquire() as conn:
            event = await conn.fetchrow("SELECT id, board_json FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
            if not event:
                return await ctx.followup.send("No active bingo event.", ephemeral=True)
            board_tasks = json.loads(event["board_json"])
            if task not in [t["name"] for t in board_tasks]:
                return await ctx.followup.send("That task isn't on the current board.", ephemeral=True)
            await conn.execute("INSERT INTO bingo_submissions (user_id, task_name, proof_url, bingo_id) VALUES ($1, $2, $3, $4)", ctx.author.id, task, proof, event["id"])
        await ctx.followup.send("Submission sent to admins for review.", ephemeral=True)
    except Exception:
        log.exception("bingo_complete failed")
        await ctx.followup.send("An error occurred submitting your task.", ephemeral=True)

@bingo.command(name="submissions", description="ADMIN: View pending bingo task submissions.")
@discord.default_permissions(manage_events=True)
async def bingo_submissions(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        pending = await bot.db_pool.fetch("SELECT * FROM bingo_submissions WHERE status = 'pending'")
        if not pending:
            return await ctx.followup.send("No pending submissions.", ephemeral=True)
        await ctx.followup.send("Posting pending submissions in-channel for review.", ephemeral=True)
        for p in pending:
            user = await bot.fetch_user(p["user_id"])
            embed = discord.Embed(title="📝 Bingo Submission", description=f"**Task:** {p['task_name']}", color=discord.Color.yellow())
            embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
            embed.add_field(name="Proof", value=f"[Open]({p['proof_url']})", inline=False)
            embed.set_footer(text=f"Submission ID: {p['id']}")
            await ctx.channel.send(embed=embed, view=SubmissionView())
    except Exception:
        log.exception("bingo_submissions failed")
        await ctx.followup.send("An error occurred fetching submissions.", ephemeral=True)

@bingo.command(name="board", description="View the current bingo board.")
async def bingo_board(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        message_id = await bot.db_pool.fetchval("SELECT message_id FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        if not message_id:
            return await ctx.followup.send("No active bingo board.", ephemeral=True)
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if bingo_channel:
            try:
                message = await bingo_channel.fetch_message(message_id)
                await ctx.followup.send(f"Here is the current bingo board: {message.jump_url}", ephemeral=True)
            except discord.NotFound:
                await ctx.followup.send("Could not find original bingo board message.", ephemeral=True)
        else:
            await ctx.followup.send("Bingo channel not configured.", ephemeral=True)
    except Exception:
        log.exception("bingo_board failed")
        await ctx.followup.send("An error occurred fetching the board.", ephemeral=True)

# -------------------------
# Bootstrapping: start web server and bot cleanly
# -------------------------
async def main():
    # Start web runner (non-blocking)
    web_runner = None
    try:
        web_runner = await start_web_runner()
    except Exception:
        log.exception("Failed to start web server")

    # Init DB pool
    try:
        bot.db_pool = await init_db_pool()
        await migrate_db(bot.db_pool)
    except Exception:
        log.exception("DB init failed")

    # Start the bot
    try:
        await bot.start(TOKEN)
    finally:
        # cleanup
        if bot.db_pool:
            await bot.db_pool.close()
        if web_runner:
            try:
                await web_runner.cleanup()
            except Exception:
                log.exception("Failed to cleanup web runner")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutdown requested. Exiting.")
    except Exception:
        log.exception("Fatal error in main loop")
