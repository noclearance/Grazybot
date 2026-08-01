# Entry point for the Discord bot (Pycord).
import os
import sys
import asyncio
import logging
import traceback

# Ensure project root is on sys.path when launched as `python main.py`
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import discord
from aiohttp import web

from config import TOKEN, DEBUG_GUILD_ID
from db import setup_database_pool
from helpers.utils import load_item_mapping
from views import PvmEventView, GiveawayView, SubmissionView

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grazybot")

REQUIRED_ENV_VARS = [
    "TOKEN",
    "WOM_CLAN_ID",
    "WOM_VERIFICATION_CODE",
    "GEMINI_API_KEY",
    "DEBUG_GUILD_ID",
    "DATABASE_URL",
    "SOTW_CHANNEL_ID",
    "BINGO_CHANNEL_ID",
    "RAFFLE_CHANNEL_ID",
    "RECAP_CHANNEL_ID",
    "ANNOUNCEMENTS_CHANNEL_ID",
    "PVM_EVENT_CHANNEL_ID",
]

COG_EXTENSIONS = [
    "cogs.admin",
    "cogs.bingo",
    "cogs.events",
    "cogs.ge",
    "cogs.giveaway",
    "cogs.osrs",
    "cogs.pb",
    "cogs.points",
    "cogs.pointstore",
    "cogs.pvm",
    "cogs.raffle",
    "cogs.sotw",
    "cogs.tasks",
]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = discord.Bot(intents=intents, debug_guilds=[DEBUG_GUILD_ID] if DEBUG_GUILD_ID else None)
bot.item_mapping = {}
bot.active_polls = {}
bot.db_pool = None


def validate_config():
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    logger.info("Configuration validated successfully")


def load_cogs():
    """Load all known cog extensions. Returns list of errors (empty if ok)."""
    errors = []
    for ext in COG_EXTENSIONS:
        try:
            bot.load_extension(ext)
            logger.info("Loaded extension: %s", ext)
        except Exception:
            errors.append(f"Failed to load {ext}:\n{traceback.format_exc()}")
    return errors


async def handle_http(_request: web.Request):
    return web.Response(text="GrazyBot is running.")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health web server listening on port %s", port)


@bot.event
async def on_ready():
    logger.info("Logged in as %s (ID: %s)", bot.user.name, bot.user.id)

    if not bot.db_pool:
        bot.db_pool = await setup_database_pool()
        if bot.db_pool:
            logger.info("Database connection pool established")
        else:
            logger.error("FATAL: Database connection failed. Bot cannot continue")
            await bot.close()
            return

    await load_item_mapping(bot)

    try:
        async with bot.db_pool.acquire() as conn:
            active_giveaways = await conn.fetch(
                "SELECT message_id FROM giveaways WHERE is_active = TRUE AND ends_at > NOW()"
            )
            if active_giveaways:
                logger.info("Re-registering %s active giveaway view(s)", len(active_giveaways))
                for gw in active_giveaways:
                    bot.add_view(GiveawayView(message_id=gw["message_id"]))

            active_pvm_events = await conn.fetch(
                "SELECT id FROM pvm_events WHERE is_active = TRUE AND starts_at > NOW()"
            )
            if active_pvm_events:
                logger.info("Re-registering %s active PVM event view(s)", len(active_pvm_events))
                for pvm_event in active_pvm_events:
                    bot.add_view(PvmEventView(event_id=pvm_event["id"]))

            bot.add_view(SubmissionView())
        logger.info("Persistent views re-registered")
    except Exception as e:
        logger.error("Failed to re-register views: %s", e)

    try:
        await bot.sync_commands()
        logger.info("Slash commands synced")
    except Exception as e:
        logger.error("Failed to sync slash commands: %s", e)


@bot.slash_command(name="help", description="Shows a list of all available commands.")
async def help_command(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    embed = discord.Embed(
        title="GrazyBot Command List",
        description="Here are all the commands you can use.",
        color=discord.Color.blurple(),
    )
    member_commands = (
        "`/ge price` · `/ge value` · `/osrs link` · `/osrs profile` · `/osrs kc`\n"
        "`/points view` · `/points leaderboard` · `/sotw view` · `/raffle enter`\n"
        "`/raffle view_tickets` · `/bingo board` · `/bingo complete` · `/pointstore rewards`\n"
        "`/pointstore redeem` · `/events view` · `/pvm participants` · `/pb log` · `/pb my` · `/pb clan`"
    )
    admin_commands = (
        "`/admin announce` · `/admin manage_points` · `/admin award_sotw_winners`\n"
        "`/sotw start` · `/sotw poll` · `/giveaway start` · `/raffle start` · `/bingo start`\n"
        "`/pointstore addreward` · `/pvm schedule` · `/pvm cancel`"
    )
    embed.add_field(name="Member Commands", value=member_commands, inline=False)
    embed.add_field(name="Admin Commands", value=admin_commands, inline=False)
    embed.set_footer(text="Let the games begin!")
    await ctx.respond(embed=embed, ephemeral=True)


def run_bot():
    try:
        validate_config()
    except ValueError as e:
        logger.error("Config validation failed: %s", e)
        sys.exit(1)

    errors = load_cogs()
    if errors:
        for err in errors:
            logger.error(err)
        sys.exit(1)

    async def runner():
        web_task = asyncio.create_task(start_web_server())
        try:
            await bot.start(TOKEN)
        except discord.HTTPException as e:
            if e.status == 429:
                logger.error("Rate-limited by Discord. Exiting.")
            else:
                logger.error("HTTP error: %s", e)
        except Exception as e:
            logger.error("Unexpected error: %s", e)
        finally:
            web_task.cancel()
            if bot.db_pool:
                await bot.db_pool.close()

    asyncio.run(runner())


if __name__ == "__main__":
    run_bot()
