# Central configuration file for loading environment variables and defining constants.
import os
import re
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grazybot")

load_dotenv()


def _clean_str(value: str | None) -> str | None:
    """Strip whitespace, wrapping quotes, and accidental surrounding parentheses."""
    if value is None:
        return None
    s = value.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    # Discord snowflakes sometimes pasted as (1234567890)
    if s.startswith("(") and s.endswith(")") and re.fullmatch(r"\(\d+\)", s):
        s = s[1:-1]
    return s or None


def _env_str(key: str, default: str = "") -> str:
    return _clean_str(os.getenv(key)) or default


def _env_int(key: str) -> int | None:
    raw = _clean_str(os.getenv(key))
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.error("Invalid integer for %s (got non-numeric after cleanup)", key)
        raise ValueError(f"{key} must be a numeric Discord ID, not {raw!r}")


# --- Core Bot Settings ---
TOKEN = _env_str("TOKEN")
if not TOKEN:
    logger.error("Missing required environment variable: TOKEN")
    raise ValueError("TOKEN is required")
BOT_PREFIX = "/"
DEBUG_GUILD_ID = _env_int("DEBUG_GUILD_ID")
if not DEBUG_GUILD_ID:
    logger.warning("DEBUG_GUILD_ID not set; slash command sync may fail")

# --- API Keys & IDs ---
WOM_CLAN_ID = _env_str("WOM_CLAN_ID")
WOM_VERIFICATION_CODE = _env_str("WOM_VERIFICATION_CODE")
GEMINI_API_KEY = _env_str("GEMINI_API_KEY")
DATABASE_URL = _env_str("DATABASE_URL")
if not DATABASE_URL:
    logger.error("Missing required environment variable: DATABASE_URL")
    raise ValueError("DATABASE_URL is required")

# --- Discord Channel and Role IDs ---
SOTW_ROLE_ID = _env_int("SOTW_ROLE_ID")
SOTW_CHANNEL_ID = _env_int("SOTW_CHANNEL_ID")
BINGO_CHANNEL_ID = _env_int("BINGO_CHANNEL_ID")
RAFFLE_CHANNEL_ID = _env_int("RAFFLE_CHANNEL_ID")
RECAP_CHANNEL_ID = _env_int("RECAP_CHANNEL_ID")
ANNOUNCEMENTS_CHANNEL_ID = _env_int("ANNOUNCEMENTS_CHANNEL_ID")
GIVEAWAY_CHANNEL_ID = ANNOUNCEMENTS_CHANNEL_ID
PVM_EVENT_CHANNEL_ID = _env_int("PVM_EVENT_CHANNEL_ID")

# Optional Base44 hub push (no-op when unset)
BASE44_HUB_URL = _env_str("BASE44_HUB_URL").rstrip("/")
BASE44_HUB_TOKEN = _env_str("BASE44_HUB_TOKEN")

required_ids = [
    ("SOTW_CHANNEL_ID", SOTW_CHANNEL_ID),
    ("BINGO_CHANNEL_ID", BINGO_CHANNEL_ID),
    ("RAFFLE_CHANNEL_ID", RAFFLE_CHANNEL_ID),
    ("RECAP_CHANNEL_ID", RECAP_CHANNEL_ID),
    ("ANNOUNCEMENTS_CHANNEL_ID", ANNOUNCEMENTS_CHANNEL_ID),
    ("PVM_EVENT_CHANNEL_ID", PVM_EVENT_CHANNEL_ID),
]
for name, value in required_ids:
    if value is None:
        logger.error("Missing required environment variable: %s", name)
        raise ValueError(f"{name} is required")
if SOTW_ROLE_ID is None:
    logger.warning("SOTW_ROLE_ID not set; SOTW role pings will be skipped if used")

# --- Static Data & Game Constants ---
TASKS_FILE = "tasks.json"
MAX_FIELD_LENGTH = 1024

WOM_SKILLS = [
    "overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer",
    "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking",
    "crafting", "smithing", "mining", "herblore", "agility", "thieving",
    "slayer", "farming", "runecraft", "hunter", "construction",
]

OSRS_ACTIVABLE_HISCORE_ORDER = [
    "Clue Scrolls (all)", "Clue Scrolls (beginner)", "Clue Scrolls (easy)", "Clue Scrolls (medium)",
    "Clue Scrolls (hard)", "Clue Scrolls (elite)", "Clue Scrolls (master)",
    "LMS - Rank", "Bounty Hunter - Hunter", "Bounty Hunter - Rogue",
    "Barrows Chests", "Boss Kills (Total)", "Abyssal Sire", "Alchemical Hydra", "Artio", "Bryophyta",
    "Callisto", "Calvar'ion", "Cerberus", "Chambers of Xeric", "Chambers of Xeric: Challenge Mode",
    "Chaos Elemental", "Chaos Fanatic", "Commander Zilyana", "Corporeal Beast", "Crazy Archaeologist",
    "Dagannoth Prime", "Dagannoth Rex", "Dagannoth Supreme", "Deranged Archaeologist", "General Graardor",
    "Giant Mole", "Grotesque Guardians", "Hespori", "Kalphite Queen", "King Black Dragon", "Kraken",
    "Kree'arra", "K'ril Tsutsaroth", "Mimic", "Nex", "Nightmare", "Phosani's Nightmare", "Obor",
    "Sarachnis", "Scorpia", "Skotizo", "Tempoross", "The Gauntlet", "The Corrupted Gauntlet",
    "Theatre of Blood", "Theatre of Blood: Hard Mode", "Thermonuclear Smoke Devil", "Tombs of Amascut",
    "Tombs of Amascut: Expert Mode", "TzKal-Zuk", "TzTok-Jad", "Venenatis", "Vet'ion", "Vorkath",
    "Wintertodt", "Zalcano", "Zulrah",
]
