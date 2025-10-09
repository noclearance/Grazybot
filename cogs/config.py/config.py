import os

def load_config():
    """Loads configuration from environment variables and returns as a dict."""
    return {
        "TOKEN": os.getenv("TOKEN"),
        "WOM_CLAN_ID": os.getenv("WOM_CLAN_ID"),
        "WOM_VERIFICATION_CODE": os.getenv("WOM_VERIFICATION_CODE"),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "DEBUG_GUILD_ID": int(os.getenv("DEBUG_GUILD_ID", "0")),
        "DATABASE_URL": os.getenv("DATABASE_URL"),
        "TASKS_FILE": os.getenv("TASKS_FILE", "tasks.json"),
        "SOTW_ROLE_ID": int(os.getenv("SOTW_ROLE_ID", "0")),
        "SOTW_CHANNEL_ID": int(os.getenv("SOTW_CHANNEL_ID", "0")),
        "BINGO_CHANNEL_ID": int(os.getenv("BINGO_CHANNEL_ID", "0")),
        "RAFFLE_CHANNEL_ID": int(os.getenv("RAFFLE_CHANNEL_ID", "0")),
        "RECAP_CHANNEL_ID": int(os.getenv("RECAP_CHANNEL_ID", "0")),
        "ANNOUNCEMENTS_CHANNEL_ID": int(os.getenv("ANNOUNCEMENTS_CHANNEL_ID", "0")),
        "GIVEAWAY_CHANNEL_ID": int(os.getenv("GIVEAWAY_CHANNEL_ID", "0")),
        "PVM_EVENT_CHANNEL_ID": int(os.getenv("PVM_EVENT_CHANNEL_ID", "0")),
        "MAX_FIELD_LENGTH": int(os.getenv("MAX_FIELD_LENGTH", "1024")),
    }

def validate_config(config):
    missing = [k for k, v in config.items() if v in (None, "", 0)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")