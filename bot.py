# bot.py - Grazybot fully patched for discord.py 2.x
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import os
import asyncio
import asyncpg
from datetime import datetime, timedelta
import random

# -----------------------
# Config
# -----------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
SOTW_PING_ROLE = "<@&ROLE_ID_FOR_SOTW_PING>"  # replace with actual
EVERYONE_PING = "@everyone"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------
# Database Pool
# -----------------------
db_pool: asyncpg.pool.Pool

async def get_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

@bot.event
async def on_ready():
    global db_pool
    db_pool = await get_db_pool()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    if not weekly_tasks.is_running():
        weekly_tasks.start()

# -----------------------
# Admin Commands
# -----------------------
admin = app_commands.Group(name="admin", description="Admin-only commands")

@app_commands.checks.has_permissions(administrator=True)
@admin.command(name="shutdown", description="Shut down the bot")
async def shutdown(interaction: discord.Interaction):
    await interaction.response.send_message("Shutting down...")
    await bot.close()

@app_commands.checks.has_permissions(administrator=True)
@admin.command(name="sync", description="Sync slash commands")
async def sync(interaction: discord.Interaction):
    await interaction.response.send_message("Syncing commands...")
    await bot.tree.sync()
    await interaction.followup.send("Commands synced!")

bot.tree.add_command(admin)

# -----------------------
# General Commands
# -----------------------
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! {round(bot.latency*1000)}ms")

@bot.tree.command(name="info", description="Bot info")
async def info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Grazybot",
        description="OSRS Clan Bot - Slash Commands Only",
        color=discord.Color.green()
    )
    embed.add_field(name="Latency", value=f"{round(bot.latency*1000)}ms")
    embed.add_field(name="Developer", value="Caleb")
    await interaction.response.send_message(embed=embed)

# -----------------------
# Events: SOTW / Raffle / Bingo
# -----------------------
events = app_commands.Group(name="events", description="Clan events commands")

@events.command(name="sotw", description="Show current Skill of the Week")
async def sotw(interaction: discord.Interaction):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT skill, date FROM sotw ORDER BY date DESC LIMIT 1;")
    skill = row["skill"] if row else "Not set"
    embed = discord.Embed(
        title="Skill of the Week",
        description=f"{SOTW_PING_ROLE} Current SOTW: **{skill}**",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

@events.command(name="raffle", description="Start a raffle")
async def raffle(interaction: discord.Interaction):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO raffles (started_at) VALUES ($1)", datetime.utcnow())
    embed = discord.Embed(
        title="Raffle Live!",
        description=f"{EVERYONE_PING} A raffle has started! Use `/raffle join` to enter!",
        color=discord.Color.purple()
    )
    await interaction.response.send_message(embed=embed)

@events.command(name="bingo", description="Show current Bingo board")
async def bingo(interaction: discord.Interaction):
    async with db_pool.acquire() as conn:
        board = await conn.fetch("SELECT square, completed_by FROM bingo_board ORDER BY square;")
    desc = "\n".join(f"{b['square']}: {b['completed_by'] or '❌'}" for b in board)
    embed = discord.Embed(title="Bingo Board", description=desc, color=discord.Color.orange())
    await interaction.response.send_message(embed=embed)

bot.tree.add_command(events)

# -----------------------
# Raffle Subcommands
# -----------------------
raffle_group = app_commands.Group(name="raffle", description="Raffle management")

@raffle_group.command(name="join", description="Join current raffle")
async def join_raffle(interaction: discord.Interaction):
    user_id = interaction.user.id
    async with db_pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM raffle_entries WHERE user_id=$1", user_id)
        if exists:
            await interaction.response.send_message("You already joined!", ephemeral=True)
            return
        await conn.execute("INSERT INTO raffle_entries (user_id) VALUES ($1)", user_id)
    await interaction.response.send_message("You joined the raffle!", ephemeral=True)

@raffle_group.command(name="draw", description="Draw a raffle winner")
@app_commands.checks.has_permissions(administrator=True)
async def draw_raffle(interaction: discord.Interaction):
    async with db_pool.acquire() as conn:
        entries = await conn.fetch("SELECT user_id FROM raffle_entries")
        if not entries:
            await interaction.response.send_message("No participants!", ephemeral=True)
            return
        winner = random.choice(entries)["user_id"]
        await conn.execute("DELETE FROM raffle_entries")
    await interaction.response.send_message(f"🎉 The winner is <@{winner}>!")

bot.tree.add_command(raffle_group)

# -----------------------
# Leaderboards
# -----------------------
leaderboards = app_commands.Group(name="leaderboards", description="Clan leaderboards")

@leaderboards.command(name="points", description="Top points leaderboard")
async def points(interaction: discord.Interaction):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT member_name, points FROM points ORDER BY points DESC LIMIT 10;")
    desc = "\n".join(f"**{r['member_name']}** — {r['points']} pts" for r in rows)
    embed = discord.Embed(title="Top Clan Points", description=desc or "No data yet", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)

bot.tree.add_command(leaderboards)

# -----------------------
# Utility
# -----------------------
@bot.tree.command(name="echo", description="Repeat a message")
async def echo(interaction: discord.Interaction, message: str):
    await interaction.response.send_message(message)

# -----------------------
# Weekly Automation (SOTW / Bingo / Raffle)
# -----------------------
@tasks.loop(hours=24)
async def weekly_tasks():
    now = datetime.utcnow()
    if now.weekday() == 0:  # Monday: Post SOTW
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT skill FROM sotw ORDER BY date DESC LIMIT 1;")
        skill = row["skill"] if row else "Not set"
        channel = bot.get_channel(CHANNEL_ID_FOR_SOTW)  # Replace with actual
        embed = discord.Embed(title="Skill of the Week", description=f"{SOTW_PING_ROLE} Current SOTW: **{skill}**", color=discord.Color.blue())
        await channel.send(embed=embed)
    if now.weekday() == 2:  # Wednesday: Post Bingo
        channel = bot.get_channel(CHANNEL_ID_FOR_BINGO)  # Replace with actual
        await channel.send(f"{EVERYONE_PING} Bingo board update coming soon!")  # Placeholder
    if now.weekday() == 4:  # Friday: Post Raffle
        channel = bot.get_channel(CHANNEL_ID_FOR_RAFFLE)  # Replace with actual
        await channel.send(f"{EVERYONE_PING} Friday raffle is live! Join now with `/raffle join`")

# -----------------------
# Error Handling
# -----------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Error: {error}", ephemeral=True)

# -----------------------
# Run Bot
# -----------------------
bot.run(TOKEN)
