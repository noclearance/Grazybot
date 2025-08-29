import asyncio
import os
import discord
import psycopg2
from discord.ext import commands
from dotenv import load_dotenv  # Import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Define global variables
# DATABASE_URL is now accessed within the functions that need it
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Check if tokens and API keys are loaded
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable not set.")
if not OPENAI_API_KEY and not GOOGLE_API_KEY:
    print("Warning: Neither OPENAI_API_KEY nor GOOGLE_API_KEY are set. AI features may not work.")

# Define the bot
bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())

# Database setup function
def setup_database():
    """Sets up the PostgreSQL database and creates the necessary tables."""
    DATABASE_URL = os.getenv('DATABASE_URL') # Access DATABASE_URL here
    print(f"DEBUG: DATABASE_URL as seen by bot is: {DATABASE_URL}") # Keep this for debugging
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable not set.")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Create users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                discord_id BIGINT UNIQUE,
                graze_count INTEGER DEFAULT 0
            );
        """)

        # Create items table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) UNIQUE,
                description TEXT,
                cost INTEGER
            );
        """)

        # Create user_items table (many-to-many relationship)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_items (
                user_id INTEGER REFERENCES users(id),
                item_id INTEGER REFERENCES items(id),
                quantity INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, item_id)
            );
        """)

        conn.commit()
        cur.close()
        conn.close()
        print("Database setup complete.")
    except psycopg2.Error as e:
        print(f"Database error during setup: {e}")
        # Depending on your needs, you might want to re-raise the exception
        # or handle it differently. For now, just printing the error.

# Function to get or create a user
def get_or_create_user(discord_id):
    """Gets a user from the database or creates a new one if they don't exist."""
    DATABASE_URL = os.getenv('DATABASE_URL') # Access DATABASE_URL here
    if not DATABASE_URL:
        print("Error: DATABASE_URL not set in get_or_create_user.")
        return None # Or raise an exception

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        cur.execute("SELECT id FROM users WHERE discord_id = %s;", (discord_id,))
        user = cur.fetchone()

        if user:
            user_id = user[0]
        else:
            cur.execute("INSERT INTO users (discord_id) VALUES (%s) RETURNING id;", (discord_id,))
            user_id = cur.fetchone()[0]
            conn.commit()

        cur.close()
        conn.close()
        return user_id
    except psycopg2.Error as e:
        print(f"Database error in get_or_create_user: {e}")
        return None # Or raise an exception

# Function to update graze count
def update_graze_count(user_id, count):
    """Updates the graze count for a given user."""
    DATABASE_URL = os.getenv('DATABASE_URL') # Access DATABASE_URL here
    if not DATABASE_URL:
        print("Error: DATABASE_URL not set in update_graze_count.")
        return

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        cur.execute("UPDATE users SET graze_count = graze_count + %s WHERE id = %s;", (count, user_id))
        conn.commit()

        cur.close()
        conn.close()
    except psycopg2.Error as e:
        print(f"Database error in update_graze_count: {e}")

# Function to get graze count
def get_graze_count(user_id):
    """Gets the current graze count for a user."""
    DATABASE_URL = os.getenv('DATABASE_URL') # Access DATABASE_URL here
    if not DATABASE_URL:
        print("Error: DATABASE_URL not set in get_graze_count.")
        return 0 # Return a default or indicate error

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        cur.execute("SELECT graze_count FROM users WHERE id = %s;", (user_id,))
        result = cur.fetchone()

        cur.close()
        conn.close()

        return result[0] if result else 0
    except psycopg2.Error as e:
        print(f"Database error in get_graze_count: {e}")
        return 0 # Return a default or indicate error

# Example command: !graze
@bot.command(name='graze')
async def graze(ctx):
    user_id = get_or_create_user(ctx.author.id)
    if user_id:
        update_graze_count(user_id, 1)
        count = get_graze_count(user_id)
        await ctx.send(f"{ctx.author.display_name} grazed! Your graze count is now {count}.")
    else:
        await ctx.send("Could not process your request. Database error.")


@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    setup_database() # Ensure database is set up when the bot is ready

# Main execution
async def main():
    # setup_database() # Removed from here to be called on_ready
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
