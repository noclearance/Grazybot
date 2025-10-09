# bot/main.py
# Entry point for the Discord bot.
import os
import sys
# Add the project root to sys.path to resolve imports from the "cog" module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import asyncio
import logging
import discord
from discord.ext import commands
# cog/config.py
TOKEN = "YOUR_DISCORD_TOKEN"
BOT_PREFIX = "/"
DEBUG_GUILD_ID = 1234567890  # Replace with your debug guild ID
DATABASE_URL = "postgresql://user:password@localhost/database"  # Update as needed
from cog.db import setup_database_pool
from cog.helpers.utils import load_item_mapping
from cog.views import PvmEventView, GiveawayView, SubmissionView
import importlib
import sys
import traceback

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grazybot")

# Required environment variables
REQUIRED_ENV_VARS = [
    'TOKEN', 'WOM_CLAN_ID', 'WOM_VERIFICATION_CODE', 'GEMINI_API_KEY',
    'DEBUG_GUILD_ID', 'DATABASE_URL', 'SOTW_ROLE_ID', 'SOTW_CHANNEL_ID',
    'BINGO_CHANNEL_ID', 'RAFFLE_CHANNEL_ID', 'RECAP_CHANNEL_ID',
    'ANNOUNCEMENTS_CHANNEL_ID', 'PVM_EVENT_CHANNEL_ID'
]

# Bot initialization
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Required for some commands
bot = commands.Bot(command_prefix="/", intents=intents, debug_guilds=[DEBUG_GUILD_ID])  # Changed to /

# Bot state
bot.item_mapping = {}
bot.active_polls = {}
bot.db_pool = None

# Diagnostic checks
def validate_config():
    """Check for missing or invalid environment variables."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        logger.error(f"Missing environment variables: {', '.join(missing)}")
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    try:
        int(os.getenv('DEBUG_GUILD_ID'))
        int(os.getenv('SOTW_ROLE_ID'))
        int(os.getenv('SOTW_CHANNEL_ID'))
        # Add more ID validations as needed
    except (TypeError, ValueError):
        logger.error("Invalid format for ID-based environment variables")
        raise ValueError("Invalid format for ID-based environment variables")
    logger.info("Configuration validated successfully")

def check_imports():
    """Verify all required modules are importable."""
    required_modules = [
        'discord', 'aiohttp', 'asyncio', 'asyncpg', 'google.generativeai',
        'PIL', 'dotenv', 're', 'urllib.parse', 'io', 'textwrap', 'json', 'random', 'datetime'
    ]
    errors = []
    for mod in required_modules:
        try:
            importlib.import_module(mod)
        except ImportError as e:
            errors.append(f"Missing module: {mod} ({e})")
    return errors or "All imports successful"

def check_cogs():
    """Simulate loading cogs to catch errors."""
    cogs_path = os.path.join(os.path.dirname(__file__), 'cogs')
    cogs = [f for f in os.listdir(cogs_path) if f.endswith('.py') and not f.startswith('__')]
    errors = []
    for cog in cogs:
        cog_name = f'bot.cogs.{cog[:-3]}'
        try:
            spec = importlib.util.find_spec(cog_name)
            if spec is None:
                errors.append(f"Cog module {cog_name} not found")
            else:
                module = importlib.util.module_from_spec(spec)
                sys.modules[cog_name] = module
                spec.loader.exec_module(module)
        except Exception as e:
            errors.append(f"Error loading cog {cog_name}: {traceback.format_exc()}")
    return errors or "All cogs loadable"



@bot.event
async def on_ready():
    """Handle bot startup and initialization."""
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    
    # Database initialization
    if not bot.db_pool:
        bot.db_pool = await setup_database_pool()
        if bot.db_pool:
            logger.info("Database connection pool established")
        else:
            logger.error("FATAL: Database connection failed. Bot cannot continue")
            await bot.close()
            return
    
    # Load item mapping
    await load_item_mapping(bot)
    
    # Re-register persistent views
    try:
        async with bot.db_pool.acquire() as conn:
            active_giveaways = await conn.fetch("SELECT message_id FROM giveaways WHERE is_active = TRUE AND ends_at > NOW()")
            if active_giveaways:
                logger.info(f"Re-registering {len(active_giveaways)} active giveaway view(s)")
                for gw in active_giveaways:
                    bot.add_view(GiveawayView(message_id=gw['message_id']))
            
            active_pvm_events = await conn.fetch("SELECT id FROM pvm_events WHERE is_active = TRUE AND starts_at > NOW()")
            if active_pvm_events:
                logger.info(f"Re-registering {len(active_pvm_events)} active PVM event view(s)")
                for pvm_event in active_pvm_events:
                    bot.add_view(PvmEventView(event_id=pvm_event['id']))
            
            bot.add_view(SubmissionView())
        logger.info("Persistent views re-registered")
    except Exception as e:
        logger.error(f"Failed to re-register views: {e}")
    
    # Sync slash commands
    try:
        await bot.tree.sync(guild=discord.Object(id=DEBUG_GUILD_ID))
        logger.info(f"Slash commands synced for guild {DEBUG_GUILD_ID}")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")

@bot.slash_command(name="help", description="Shows a list of all available commands.")
async def help(ctx: discord.ApplicationContext):
    """
    Central help command. It can be expanded to dynamically list commands
    from loaded cogs for better maintainability.
    """
    await ctx.defer(ephemeral=True)
    embed = discord.Embed(
        title="GrazyBot Command List",
        description="Here are all the commands you can use.",
        color=discord.Color.blurple()
    )
    member_commands = r"""
    `/ge price` - Check the Grand Exchange price of an item.
    `/ge value` - Calculate the total GE value of multiple items.
    `/osrs link` - Link your Discord account to your OSRS name.
    `/osrs profile` - View your linked OSRS account's stats.
    `/osrs kc` - View your OSRS boss kill counts.
    `/points view` - Check your current Clan Point balance.
    `/points leaderboard` - View the Clan Points leaderboard.
    `/sotw view` - View the leaderboard for the current Skill of the Week.
    `/raffle enter` - Get tickets for the current active raffle.
    `/raffle view_tickets` - See ticket counts for the active raffle.
    `/bingo board` - Get a link to the current bingo board.
    `/bingo complete` - Submit a task for bingo completion.
    `/pointstore rewards` - See what you can buy with your points.
    `/pointstore redeem` - Spend your points on a reward.
    `/events view` - See all currently active events.
    `/pvm participants` - View who has signed up for a PVM event.
    `/pb log` - Log or update your Personal Best time for a boss.
    `/pb my` - View your Personal Best for a specific boss.
    `/pb clan` - View the clan leaderboard for a specific boss.
    """
    admin_commands = r"""
    `/admin announce` - Send an announcement as the bot.
    `/admin manage_points` - Add or remove Clan Points from a member.
    `/admin award_sotw_winners` - Manually award points for a past SOTW.
    `/sotw start` - Manually start a new SOTW competition.
    `/sotw poll` - Start a poll to choose the next SOTW.
    `/giveaway start` - Start a new giveaway.
    `/giveaway entries` - View entrants for the current giveaway.
    `/raffle start` - Start a new raffle.
    `/raffle give_tickets` - Give raffle tickets to a member.
    `/raffle edit_tickets` - Set a member's total ticket count.
    `/raffle draw_now` - End a raffle and draw a winner now.
    `/raffle cancel` - Cancel a specific raffle.
    `/bingo start` - Start a new clan bingo event.
    `/bingo submissions` - View and manage pending bingo submissions.
    `/pointstore addreward` - Add a new reward to the store.
    `/pointstore removereward` - Remove a reward from the store.
    `/pointstore togglereward` - Activate or deactivate a reward.
    `/pvm schedule` - Schedule a new PVM event.
    `/pvm cancel` - Cancel an upcoming PVM event.
    """
    embed.add_field(name="Member Commands", value=member_commands, inline=False)
    embed.add_field(name="Admin Commands", value=admin_commands, inline=False)
    embed.set_footer(text="Let the games begin!")
    await ctx.respond(embed=embed, ephemeral=True)

def run_diagnostics():
    """Run pre-startup checks to catch critical issues."""
    logger.info("Running diagnostics...")
    
    # Env vars
    try:
        validate_config()
    except ValueError as e:
        logger.error(f"Config validation failed: {e}")
        sys.exit(1)
    
    # Imports
    import_results = check_imports()
    if isinstance(import_results, list):
        for err in import_results:
            logger.error(f"Imports: {err}")
        sys.exit(1)
    else:
        logger.info(f"Imports: {import_results}")
    
    # Cogs
    cog_results = check_cogs()
    if isinstance(cog_results, list):
        for err in cog_results:
            logger.error(f"Cogs: {err}")
        sys.exit(1)
    else:
        logger.info(f"Cogs: {cog_results}")
    
    # Intents
    if not bot.intents.message_content:
        logger.error("Message content intent disabled—required for some commands")
        sys.exit(1)
    logger.info("Intents: Configured correctly")

def run_bot():
    """Main function to load cogs and start the bot."""
    run_diagnostics()
    async def runner():
        async with bot:
            await load_cogs()
            try:
                await bot.start(TOKEN)
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    logger.error("Rate-limited by Discord. Exiting.")
                else:
                    logger.error(f"HTTP error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
    asyncio.run(runner())

if __name__ == "__main__":
    run_bot()