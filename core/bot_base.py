
import discord
from discord.ext import commands
import os
import logging
from .database import create_db_pool


class GrazyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db_pool = None
        self.item_mapping = {}  # For GE item autocomplete
        self.active_polls = {}  # For SOTW polls

    async def setup_hook(self):
        logging.info("Running setup_hook...")
        self.db_pool = await create_db_pool()
        if not self.db_pool:
            logging.critical("Failed to create database pool")
            raise RuntimeError("Database connection failed")
        cogs_dir = "cogs"
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and filename != "__init__.py":
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    logging.info(f"Successfully loaded extension: {filename}")
                except Exception as e:
                    logging.error(f"Failed to load extension {filename}: {e}")
        
        # Sync commands to debug guild for faster updates during development
        from core import config
        if config.DEBUG_GUILD_ID:
            guild = discord.Object(id=config.DEBUG_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logging.info(f"Synced commands to debug guild {config.DEBUG_GUILD_ID}")
        else:
            await self.tree.sync()
            logging.info("Synced commands globally")

    async def close(self):
        logging.info("Closing bot...")
        if self.db_pool:
            await self.db_pool.close()
        await super().close()
