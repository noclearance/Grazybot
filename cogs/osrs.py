# bot/cogs/osrs.py
# Contains commands for Old School RuneScape integration.

import discord
import aiohttp
import re
import urllib.parse as up
from discord.commands import SlashCommandGroup
from discord.ext import commands

from bot.config import WOM_SKILLS, OSRS_ACTIVABLE_HISCORE_ORDER, MAX_FIELD_LENGTH
from bot.helpers.ai import generate_osrs_profile_summary
from bot.helpers.embeds import format_skill_list

class OSRS(commands.Cog):
    """Cog for OSRS-related commands like stats, kc, and linking accounts."""
    
    def __init__(self, bot):
        self.bot = bot

    osrs = SlashCommandGroup("osrs", "Commands for Old School RuneScape integration.")

    @osrs.command(name="link", description="Link your Discord account to your OSRS name.")
    async def link_osrs_name(self, ctx: discord.ApplicationContext, 
                             osrs_name: discord.Option(str, "Your Old School RuneScape username.")):
        await ctx.defer(ephemeral=True)
        
        if not re.match(r"^[a-zA-Z0-9\\s-]{1,12}$", osrs_name):
            return await ctx.respond("Invalid OSRS username format.", ephemeral=True)

        async with self.bot.db_pool.acquire() as conn:
            try:
                # Use ON CONFLICT to handle both new links and updates seamlessly
                await conn.execute("INSERT INTO user_links (discord_id, osrs_name) VALUES ($1, $2) ON CONFLICT (discord_id) DO UPDATE SET osrs_name = EXCLUDED.osrs_name",
                                   ctx.author.id, osrs_name)
                await ctx.respond(f"Your Discord account has been linked to OSRS name: **{osrs_name}**.", ephemeral=True)
            except Exception as e:
                await ctx.respond("An error occurred while linking your OSRS name.", ephemeral=True)

    @osrs.command(name="profile", description="View a member's OSRS stats from the Hiscores.")
    async def view_osrs_profile(self, ctx: discord.ApplicationContext, 
                                member: discord.Option(discord.Member, "The member to view. Defaults to yourself.", required=False)):
        await ctx.defer()
        target_member = member or ctx.author
        
        async with self.bot.db_pool.acquire() as conn:
            osrs_name_data = await conn.fetchrow("SELECT osrs_name FROM user_links WHERE discord_id = $1", target_member.id)

        if not osrs_name_data:
            return await ctx.respond(f"{'You have' if target_member == ctx.author else f'{target_member.display_name} has'} not linked an OSRS name yet.", ephemeral=True)

        osrs_name = osrs_name_data['osrs_name']
        hiscores_url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={up.quote(osrs_name)}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(hiscores_url) as response:
                    if response.status == 404:
                        return await ctx.respond(f"OSRS name **{osrs_name}** not found on the Hiscores.", ephemeral=True)
                    response.raise_for_status()
                    data = await response.text()
            except aiohttp.ClientError as e:
                return await ctx.respond(f"Error fetching Hiscores data for **{osrs_name}**.", ephemeral=True)

        lines = data.strip().split('\n')
        skills_data = {}
        for i, skill_name in enumerate(WOM_SKILLS):
            if i < len(lines):
                parts = lines[i].split(',')
                if len(parts) >= 3:
                    skills_data[skill_name] = {"rank": int(parts[0]), "level": int(parts[1]), "xp": int(parts[2])}
        
        profile_summary = await generate_osrs_profile_summary(osrs_name, skills_data)
        
        embed = discord.Embed(
            title=f"OSRS Profile: {osrs_name}",
            url=f"https://secure.runescape.com/m=hiscore_oldschool/hiscorepersonal?user1={up.quote(osrs_name)}",
            description=profile_summary,
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url="https://oldschool.runescape.wiki/images/thumb/Old_School_RuneScape_logo.png/1200px-Old_School_RuneScape_logo.png")
        
        if 'overall' in skills_data:
            overall = skills_data['overall']
            embed.add_field(name="Overall", value=f"Rank: {overall['rank']:,}\nLevel: {overall['level']}\nXP: {overall['xp']:,}", inline=False)
        
        combat_skills = ["attack", "strength", "defence", "ranged", "prayer", "magic", "hitpoints"]
        for i, block in enumerate(format_skill_list(combat_skills, skills_data)):
            embed.add_field(name=f"Combat Skills{' part ' + str(i+1) if i else ''}", value=block, inline=True)
            
        skilling_skills = [s for s in WOM_SKILLS if s not in combat_skills and s != 'overall']
        for i, block in enumerate(format_skill_list(skilling_skills, skills_data)):
            embed.add_field(name=f"Other Skills{' part ' + str(i+1) if i else ''}", value=block, inline=True)
            
        await ctx.respond(embed=embed)

    @osrs.command(name="kc", description="View a member's OSRS boss kill counts.")
    async def view_osrs_kc(self, ctx: discord.ApplicationContext, 
                           member: discord.Option(discord.Member, "The member to view. Defaults to yourself.", required=False)):
        await ctx.defer()
        target_member = member or ctx.author
        
        async with self.bot.db_pool.acquire() as conn:
            osrs_name_data = await conn.fetchrow("SELECT osrs_name FROM user_links WHERE discord_id = $1", target_member.id)

        if not osrs_name_data:
            return await ctx.respond(f"{'You have' if target_member == ctx.author else f'{target_member.display_name} has'} not linked an OSRS name yet.", ephemeral=True)

        osrs_name = osrs_name_data['osrs_name']
        hiscores_url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={up.quote(osrs_name)}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(hiscores_url) as response:
                    if response.status == 404:
                        return await ctx.respond(f"OSRS name **{osrs_name}** not found on the Hiscores.", ephemeral=True)
                    response.raise_for_status()
                    data = await response.text()
            except aiohttp.ClientError:
                return await ctx.respond(f"Error fetching Hiscores data for **{osrs_name}**.", ephemeral=True)
        
        lines = data.strip().split('\n')
        start_index = len(WOM_SKILLS)
        
        kc_lines = []
        for i, activity_name in enumerate(OSRS_ACTIVABLE_HISCORE_ORDER):
            line_index = start_index + i
            if line_index < len(lines):
                parts = lines[line_index].split(',')
                if len(parts) >= 2 and int(parts[1]) > 0:
                    kc_lines.append(f"**{activity_name}**: {int(parts[1]):,} (Rank: {int(parts[0]):,})")

        embed = discord.Embed(title=f"OSRS Kill Counts: {osrs_name}", color=discord.Color.dark_red())
        embed.set_thumbnail(url="https://oldschool.runescape.wiki/images/Slayer_helmet.png")

        if not kc_lines:
            embed.description = "No notable boss kill counts found on the Hiscores."
        else:
            # Paginate the KC list into multiple embed fields if it's too long
            current_field = ""
            field_count = 1
            for line in kc_lines:
                if len(current_field) + len(line) + 1 > MAX_FIELD_LENGTH:
                    embed.add_field(name=f"PvM Kill Counts (Part {field_count})", value=current_field, inline=False)
                    current_field = line + "\n"
                    field_count += 1
                else:
                    current_field += line + "\n"
            if current_field:
                embed.add_field(name=f"PvM Kill Counts (Part {field_count})", value=current_field, inline=False)
        
        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(OSRS(bot))