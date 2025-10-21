# core/bot.py
# The main entry point for starting the GrazyBot.

import asyncio
import logging
import discord
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from . import config
from .bot_base import GrazyBot
from .database import create_db_pool

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

logger = logging.getLogger("main")

async def main():
    """The main function to initialize and run the bot."""

    try:
        config.validate_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # --- Bot Intents ---
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True

    # --- Initialize Bot ---
    bot = GrazyBot(
        command_prefix=config.BOT_PREFIX,
        intents=intents,
        help_command=None
    )

    # --- Database Connection ---
    db_pool = await create_db_pool()
    if not db_pool:
        logger.critical("Database connection failed. The bot cannot start.")
        sys.exit(1)
    bot.db_pool = db_pool

    # --- Start Bot ---
    try:
        await bot.start(config.TOKEN)
    except discord.LoginFailure:
        logger.critical("Failed to log in. Please check your bot token.")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"An unexpected error occurred while running the bot: {e}")
    finally:
        if not bot.is_closed():
            await bot.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shut down by user.")