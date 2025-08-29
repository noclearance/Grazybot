# bot.py
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
import psycopg2
from aiohttp import web

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv("TOKEN")
DEBUG_GUILD_ID = int(os.getenv("DEBUG_GUILD_ID"))
DATABASE_URL = os.getenv('DATABASE_URL')

# --- NEW DIAGNOSTIC LINE ---
print(f"DEBUG: DATABASE_URL as seen by bot is: {DATABASE_URL}")
# --- END OF DIAGNOSTIC LINE ---

# Define bot intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# Create bot instance
bot = commands.Bot(intents=intents, debug_guilds=[DEBUG_GUILD_ID])

def setup_database():
    """
    Ensures all tables are created on startup.
    """
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    # Create all tables from your original script
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS active_competitions (
        id INTEGER PRIMARY KEY, title TEXT, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
        midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE, winners_awarded BOOLEAN DEFAULT FALSE
    )""")
    cursor.execute("CREATE TABLE IF NOT EXISTS raffles (id INTEGER PRIMARY KEY, prize TEXT, ends_at TIMESTAMPTZ, winner_id BIGINT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS raffle_entries (entry_id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, source TEXT DEFAULT 'self')")
    cursor.execute("CREATE TABLE IF NOT EXISTS bingo_events (id INTEGER PRIMARY KEY, ends_at TIMESTAMPTZ, board_json TEXT, message_id BIGINT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS bingo_submissions (id SERIAL PRIMARY KEY, user_id BIGINT, task_name TEXT, proof_url TEXT, status TEXT DEFAULT 'pending')")
    cursor.execute("CREATE TABLE IF NOT EXISTS bingo_completed_tiles (task_name TEXT PRIMARY KEY)")
    cursor.execute("CREATE TABLE IF NOT EXISTS user_links (discord_id BIGINT PRIMARY KEY, osrs_name TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS clan_points (discord_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0)")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rewards (
        id SERIAL PRIMARY KEY, reward_name TEXT NOT NULL UNIQUE, point_cost INTEGER NOT NULL,
        description TEXT, is_active BOOLEAN DEFAULT TRUE
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS role_rewards (
        reward_id INTEGER PRIMARY KEY, role_id BIGINT NOT NULL,
        FOREIGN KEY (reward_id) REFERENCES rewards(id) ON DELETE CASCADE
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS redeem_transactions (
        transaction_id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, reward_id INTEGER NOT NULL,
        reward_name TEXT NOT NULL, point_cost INTEGER NOT NULL, redeemed_at TIMESTAMPTZ DEFAULT NOW()
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS giveaways (
        message_id BIGINT PRIMARY KEY, channel_id BIGINT NOT NULL, prize TEXT NOT NULL,
        ends_at TIMESTAMPTZ NOT NULL, winner_count INTEGER NOT NULL, is_active BOOLEAN DEFAULT TRUE,
        role_id BIGINT
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS giveaway_entries (
        entry_id SERIAL PRIMARY KEY, message_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
        UNIQUE (message_id, user_id)
    )""")
    conn.commit()
    cursor.close()
    conn.close()
    print("Database setup checked and tables verified.")

# --- Web Server for Hosting ---
async def handle_http(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        print(f"Web server started on port {port}")
    except Exception as e:
        print(f"Error starting web server: {e}")

async def main():
    # Ensure database is set up before loading cogs
    setup_database()

    # Dynamically load all cogs
    print("Loading cogs...")
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and not filename.startswith('__'):
            try:
                bot.load_extension(f"cogs.{filename[:-3]}")
                print(f"✅ Loaded cog: {filename}")
            except Exception as e:
                print(f"❌ Failed to load cog {filename}: {e}")
    
    # Start the bot and the web server concurrently
    print("Starting bot and web server...")
    await asyncio.gather(
        bot.start(TOKEN),
        start_web_server()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot is shutting down.")
