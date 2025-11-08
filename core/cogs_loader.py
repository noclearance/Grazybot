# core/cogs_loader.py
import os
import logging

logger = logging.getLogger(__name__)

async def load_cogs(bot):
    """Loads all cogs from the cogs directory."""
    cogs_dir = "cogs"
    for filename in os.listdir(cogs_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            try:
                await bot.load_extension(f"cogs.{filename[:-3]}")
                logger.info(f"Successfully loaded extension: {filename}")
            except Exception as e:
                logger.error(f"Failed to load extension {filename}: {e}", exc_info=True)
