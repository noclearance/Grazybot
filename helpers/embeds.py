# bot/helpers/embeds.py
# Contains helper functions for creating complex Discord embeds.

import discord
from discord import Embed
from datetime import datetime

from bot.config import MAX_FIELD_LENGTH

def format_skill_list(skills: list[str], skills_data: dict) -> list[str]:
    """
    Formats a list of skills into one or more strings that fit within
    Discord's embed field character limit.
    """
    lines = []
    current_block = ""
    for skill in skills:
        s_data = skills_data.get(skill)
        if not s_data: continue
        line = f"**{skill.capitalize()}**: {s_data['level']} ({s_data['xp']:,} XP)\n"
        if len(current_block) + len(line) > MAX_FIELD_LENGTH:
            lines.append(current_block)
            current_block = line
        else:
            current_block += line
    if current_block:
        lines.append(current_block)
    return lines

async def create_competition_embed(data: dict, author: discord.Member) -> discord.Embed:
    """Creates a standardized embed for a new SOTW competition."""
    from bot.helpers.ai import generate_announcement_json # Local import to avoid circular dependency
    
    comp = data['competition']
    details = {"skill": comp['metric'].capitalize()}
    ai_embed_data = await generate_announcement_json("sotw_start", details)
    
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.url = f"https://wiseoldman.net/competitions/{comp['id']}"
    
    start_dt = datetime.fromisoformat(comp['startsAt'].replace('Z', '+00:00'))
    end_dt = datetime.fromisoformat(comp['endsAt'].replace('Z', '+00:00'))
    
    embed.add_field(name="Skill", value=comp['metric'].capitalize(), inline=True)
    embed.add_field(name="Duration", value=f"{(end_dt - start_dt).days} days", inline=True)
    embed.add_field(name="Start Time", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True)
    embed.add_field(name="End Time", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
    embed.set_footer(text=f"Competition started by {author.display_name}", icon_url=author.display_avatar.url)
    
    return embed