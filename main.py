# main.py
# The main entry point for starting the GrazyBot.

import asyncio
import logging
import discord
import sys

from core import config
from core.bot import GrazyBot
from core.database import create_db_pool

# --- Logging Setup ---
# Set up a basic logging configuration.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# Suppress noisy discord.py logs
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

logger = logging.getLogger("main")

async def main():
    """The main function to initialize and run the bot."""
    
    # --- Configuration Validation ---
    # Perform a check of the environment variables before starting.
    try:
        config.validate_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # --- Bot Intents ---
    # Define the permissions the bot needs to function.
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True

    # --- Initialize Bot ---
    # Create an instance of our custom bot class.
    bot = GrazyBot(
        command_prefix=config.BOT_PREFIX,
        intents=intents,
        help_command=None  # We will use a custom slash command for help
    )

    # --- Database Connection ---
    # Create the database pool and attach it to the bot instance.
    # This makes the pool accessible in all cogs via `self.bot.db_pool`.
    db_pool = await create_db_pool()
    if not db_pool:
        logger.critical("Database connection failed. The bot cannot start.")
        sys.exit(1)
    bot.db_pool = db_pool

    # --- Start Bot ---
    # The `setup_hook` in the bot class will handle loading cogs.
    try:
        await bot.start(config.TOKEN)
    except discord.LoginFailure:
        logger.critical("Failed to log in. Please check your bot token.")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"An unexpected error occurred while running the bot: {e}")
    finally:
        # Ensure resources are cleaned up on exit.
        if not bot.is_closed():
            await bot.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shut down by user.")