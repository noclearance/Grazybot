import discord
from discord.ext import commands
import os
import asyncio
import logging
from dotenv import load_dotenv
from core.database import create_db_pool

class GrazyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = None

    async def setup_hook(self):
        logging.info("Running setup_hook...")
        self.db = await create_db_pool()
        cogs_dir = "cogs"
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and filename != "__init__.py":
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    logging.info(f"Successfully loaded extension: {filename}")
                except Exception as e:
                    logging.error(f"Failed to load extension {filename}: {e}")
        await self.tree.sync()

    async def close(self):
        logging.info("Closing bot...")
        if self.db:
            await self.db.close()
        await super().close()

async def main():
    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("DISCORD_BOT_TOKEN not set")
    bot = BotBase(
        command_prefix='/',
        intents=discord.Intents.default(),  # Adjust intents
        application_id=os.getenv("APPLICATION_ID")  # Optional
    )
    try:
        await bot.start(token)
    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}")
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())