# utils/osrs.py
# OSRS-related constants and helper functions.

import discord

# --- Constants ---

WOM_SKILLS = [
    "overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer",
    "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking",
    "crafting", "smithing", "mining", "herblore", "agility", "thieving",
    "slayer", "farming", "runecrafting", "hunter", "construction"
]

OSRS_ACTIVITIES = [
    "league_points", "bounty_hunter_hunter", "bounty_hunter_rogue",
    "clue_scrolls_all", "clue_scrolls_beginner", "clue_scrolls_easy",
    "clue_scrolls_medium", "clue_scrolls_hard", "clue_scrolls_elite",
    "clue_scrolls_master", "lms_rank", "pvp_arena_rank", "soul_wars_zeal",
    "rifts_closed", "abyssal_sire", "alchemical_hydra", "barrows_chests",
    "bryophyta", "callisto", "cerberus", "chambers_of_xeric",
    "chambers_of_xeric_challenge_mode", "chaos_elemental", "chaos_fanatic",
    "commander_zilyana", "corporeal_beast", "crazy_archaeologist",
    "dagannoth_prime", "dagannoth_rex", "dagannoth_supreme",
    "deranged_archaeologist", "general_graardor", "giant_mole",
    "grotesque_guardians", "hespori", "kalphite_queen", "king_black_dragon",
    "kraken", "kree_arra", "kril_tsutsaroth", "mimic", "nex", "nightmare",
    "phosanis_nightmare", "obor", "sarachnis", "scorpia", "skotizo",
    "tempoross", "the_gauntlet", "the_corrupted_gauntlet", "theatre_of_blood",
    "theatre_of_blood_hard_mode", "thermonuclear_smoke_devil", "tombs_of_amascut",
    "tombs_of_amascut_expert", "tzkal_zuk", "tztok_jad", "venenatis", "vet_ion",
    "vorkath", "wintertodt", "zalcano", "zulrah"
]

MAX_FIELD_LENGTH = 1024

# --- Helper Functions ---

def format_skill_list(skills: list[str], skills_data: dict) -> list[str]:
    """Formats a list of skills into a string for an embed field."""
    output = []
    current_block = ""
    for skill_name in skills:
        if skill_name in skills_data:
            skill = skills_data[skill_name]
            line = f"**{skill_name.capitalize()}**: {skill['level']} (XP: {skill['xp']:,})\n"
            if len(current_block) + len(line) > MAX_FIELD_LENGTH:
                output.append(current_block)
                current_block = ""
            current_block += line
    if current_block:
        output.append(current_block)
    return output

def parse_hiscores_data(data: str) -> tuple[dict, dict]:
    """Parses the raw hiscores data into skills and activities dictionaries."""
    lines = data.strip().split('\\n')
    skills_data = {}
    activities_data = {}

    for i, skill_name in enumerate(WOM_SKILLS):
        if i < len(lines):
            parts = lines[i].split(',')
            if len(parts) >= 3:
                skills_data[skill_name] = {"rank": int(parts[0]), "level": int(parts[1]), "xp": int(parts[2])}

    start_index = len(WOM_SKILLS)
    for i, activity_name in enumerate(OSRS_ACTIVITIES):
        line_index = start_index + i
        if line_index < len(lines):
            parts = lines[line_index].split(',')
            if len(parts) >= 2 and int(parts[1]) > 0:
                activities_data[activity_name] = {"rank": int(parts[0]), "score": int(parts[1])}

    return skills_data, activities_data