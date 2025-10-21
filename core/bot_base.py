import discord
from discord.ext import commands
import os
import asyncio
import logging
from dotenv import load_dotenv
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
        await self.tree.sync()
        logging.info("Commands synced globally.")

    async def on_ready(self):
        logging.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        logging.info(f"Connected to {len(self.guilds)} guilds.")
        logging.info('Bot is ready and online.')

    async def close(self):
        logging.info("Closing bot...")
        if self.db:
            await self.db.close()
        await super().close()

async def main():
    logging.basicConfig(level=logging.INFO)
    load_dotenv()

    # Use the config module for token to ensure consistency
    if not config.TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN not set in environment")

    bot = GrazyBot(
        command_prefix=config.BOT_PREFIX,
        intents=discord.Intents.default()
    )

    try:
        await bot.start(config.TOKEN)
    except Exception as e:
        logging.critical(f"An unexpected error occurred during bot startup: {e}", exc_info=True)
    finally:
        if not bot.is_closed():
            await bot.close()

if __name__ == "__main__":
    asyncio.run(main())