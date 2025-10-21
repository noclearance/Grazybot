# core/config.py
# Handles loading all settings from environment variables.

import os
from dotenv import load_dotenv

# Load environment variables from a .env file
load_dotenv()

# --- Discord Bot Token ---
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Missing required environment variable: DISCORD_BOT_TOKEN")

# --- Bot Configuration ---
BOT_PREFIX = os.getenv("BOT_PREFIX", "/")
try:
    DEBUG_GUILD_ID = int(os.getenv("DEBUG_GUILD_ID"))
except (ValueError, TypeError):
    raise ValueError("DEBUG_GUILD_ID must be a valid integer.")

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("Missing required environment variable: DATABASE_URL")

SUPABASE_URL = os.getenv("SUPABASE_URL")
if not SUPABASE_URL:
    raise ValueError("Missing required environment variable: SUPABASE_URL")

SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_KEY:
    raise ValueError("Missing required environment variable: SUPABASE_KEY")

# --- APIs ---
WOM_CLAN_ID = os.getenv("WOM_CLAN_ID")
WOM_VERIFICATION_CODE = os.getenv("WOM_VERIFICATION_CODE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Channel & Role IDs ---
# A helper to safely convert string IDs to integers
def _to_int(value: str | None) -> int | None:
    return int(value) if value else None

ANNOUNCEMENTS_CHANNEL_ID = _to_int(os.getenv("ANNOUNCEMENTS_CHANNEL_ID"))
BINGO_CHANNEL_ID = _to_int(os.getenv("BINGO_CHANNEL_ID"))
PVM_EVENT_CHANNEL_ID = _to_int(os.getenv("PVM_EVENT_CHANNEL_ID"))
RAFFLE_CHANNEL_ID = _to_int(os.getenv("RAFFLE_CHANNEL_ID"))
RECAP_CHANNEL_ID = _to_int(os.getenv("RECAP_CHANNEL_ID"))
SOTW_CHANNEL_ID = _to_int(os.getenv("SOTW_CHANNEL_ID"))
SOTW_ROLE_ID = _to_int(os.getenv("SOTW_ROLE_ID"))

# --- Validation ---
def validate_config():
    """Performs a comprehensive check of all loaded settings."""
    if not all([WOM_CLAN_ID, WOM_VERIFICATION_CODE, GEMINI_API_KEY]):
        raise ValueError("One or more API keys are missing from the environment.")

    if not all([
        ANNOUNCEMENTS_CHANNEL_ID, BINGO_CHANNEL_ID, PVM_EVENT_CHANNEL_ID,
        RAFFLE_CHANNEL_ID, RECAP_CHANNEL_ID, SOTW_CHANNEL_ID, SOTW_ROLE_ID
    ]):
        print("Warning: One or more optional Channel/Role IDs are not set.")

print("Configuration loaded successfully.")