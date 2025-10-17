# core/bot_base.py
# Defines the custom GrazyBot class.

import discord
from discord.ext import commands
import asyncpg
import logging
import os

from . import config

logger = logging.getLogger(__name__)

class GrazyBot(commands.Bot):
    """
    A custom bot subclass to hold shared state like the database pool.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # --- Application State ---
        self.db_pool: asyncpg.Pool | None = None
        self.item_mapping: dict[str, dict] = {}
        self.active_polls: dict[int, int] = {} # mapping of message_id to poll_id

    async def setup_hook(self):
        """
        The setup_hook is called after the bot logs in but before it connects
        to the websocket. This is the ideal place to load cogs and extensions.
        """
        logger.info("Running setup_hook...")

        # --- Defer View Imports to prevent circular dependencies ---
        from utils.views import GiveawayView, PvmEventView, SubmissionView

        # --- Load Cogs ---
        cogs_dir = "cogs"
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                try:
                    await self.load_extension(f"{cogs_dir}.{filename[:-3]}")
                    logger.info(f"Successfully loaded extension: {filename}")
                except Exception as e:
                    logger.error(f"Failed to load extension {filename}: {e}", exc_info=True)

        # --- Re-register Persistent Views ---
        if self.db_pool:
            async with self.db_pool.acquire() as conn:
                active_giveaways = await conn.fetch("SELECT message_id, prize FROM giveaways WHERE is_active = TRUE")
                for gw in active_giveaways:
                    self.add_view(GiveawayView(message_id=gw['message_id'], prize=gw['prize']))
                logger.info(f"Re-registered {len(active_giveaways)} active giveaway view(s).")

                active_pvm_events = await conn.fetch("SELECT id FROM pvm_events WHERE is_active = TRUE")
                for pvm in active_pvm_events:
                    self.add_view(PvmEventView(event_id=pvm['id']))
                logger.info(f"Re-registered {len(active_pvm_events)} active PVM event view(s).")

                self.add_view(SubmissionView())
                logger.info("Re-registered generic SubmissionView.")

        # --- Sync Slash Commands ---
        if config.DEBUG_GUILD_ID:
            # If a debug guild is specified, sync commands there immediately.
            # This is ideal for testing.
            guild = discord.Object(id=config.DEBUG_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Commands synced to debug guild: {config.DEBUG_GUILD_ID}")
        else:
            # If no debug guild is set, sync globally.
            # This can take up to an hour to propagate to all servers.
            await self.tree.sync()
            logger.info("No debug guild set. Commands synced globally. This may take up to an hour to reflect in Discord.")

    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        logger.info(f"Connected to {len(self.guilds)} guilds.")
        logger.info('Bot is ready and online.')

    async def close(self):
        """Ensure the database connection is closed gracefully."""
        logger.info("Closing bot...")
        if self.db_pool:
            await self.db_pool.close()
            logger.info("Database connection pool closed.")
        await super().close()