import asyncio
import logging
import discord
from discord.ext import commands

from config import load_config, validate_config
from core.db import setup_database_pool

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grazybot")


def make_bot(config):
    intents = discord.Intents.default()
    intents.members = True
    bot = commands.Bot(command_prefix="/", intents=intents)
    return bot


async def main():
    config = load_config()
    validate_config(config)

    bot = make_bot(config)

    # Create DB pool and attach to bot
    try:
        bot.db_pool = await setup_database_pool(config.get("DATABASE_URL"))
        logger.info("Database pool created and attached to bot")
    except Exception as e:
        logger.exception("Failed to create DB pool: %s", e)
        raise

    # Extensions list — keep these names but tolerate missing modules so we can migrate gradually
    extensions = [
        "cogs.help",
        # other extensions can be added/kept; missing modules will be logged but won't stop startup
    ]

    for ext in extensions:
        try:
            await bot.load_extension(ext)
            logger.info(f"Loaded extension: {ext}")
        except Exception as e:
            logger.warning(f"Failed to load extension {ext}: {e}")

    # Start the bot
    await bot.start(config["TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
