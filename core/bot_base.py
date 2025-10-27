import discord
from discord.ext import commands
import os
import asyncio
import logging
from core.database import create_db_pool
from supabase import create_client, Client
from . import config

class GrazyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = None
        self.supabase: Client = None
        self.item_mapping: dict[str, dict] = {}
        self.active_polls: dict[int, int] = {}

    async def setup_hook(self):
        logging.info("Running setup_hook...")
        self.db = await create_db_pool()
        self.supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

        # Load Cogs
        cogs_dir = "cogs"
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    logging.info(f"Successfully loaded extension: {filename}")
                except Exception as e:
                    logging.error(f"Failed to load extension {filename}: {e}", exc_info=True)

        # Defer View Imports to prevent circular dependencies
        from utils.views import GiveawayView, PvmEventView, SubmissionView

        # Re-register Persistent Views
        if self.db:
            async with self.db.acquire() as conn:
                active_giveaways = await conn.fetch("SELECT message_id, prize FROM giveaways WHERE is_active = TRUE")
                for gw in active_giveaways:
                    self.add_view(GiveawayView(message_id=gw['message_id'], prize=gw['prize']))
                logging.info(f"Re-registered {len(active_giveaways)} active giveaway view(s).")

                active_pvm_events = await conn.fetch("SELECT id FROM pvm_events WHERE is_active = TRUE")
                for pvm in active_pvm_events:
                    self.add_view(PvmEventView(event_id=pvm['id']))
                logging.info(f"Re-registered {len(active_pvm_events)} active PVM event view(s).")

                self.add_view(SubmissionView())
                logging.info("Re-registered generic SubmissionView.")

        # Sync Slash Commands
        logging.info("Starting command tree synchronization...")
        if config.DEBUG_GUILD_ID:
            guild = discord.Object(id=int(config.DEBUG_GUILD_ID))
            logging.info(f"Syncing command tree to guild {config.DEBUG_GUILD_ID}...")
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logging.info(f"Command tree synced successfully! {len(synced)} command(s) registered to guild {config.DEBUG_GUILD_ID}.")
        else:
            logging.info("Syncing command tree globally (this may take up to 1 hour to appear in Discord)...")
            synced = await self.tree.sync()
            logging.info(f"Command tree synced successfully! {len(synced)} command(s) registered globally.")

    async def on_ready(self):
        logging.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        logging.info(f"Connected to {len(self.guilds)} guilds.")
        logging.info('Bot is ready and online.')

    async def close(self):
        logging.info("Closing bot...")
        if self.db:
            await self.db.close()
        await super().close()
