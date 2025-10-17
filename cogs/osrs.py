# cogs/osrs.py
# Contains commands for Old School RuneScape integration.

import discord
import aiohttp
import re
import urllib.parse as up
import logging
from discord import SlashCommandGroup, Option
from discord.ext import commands

from core.bot import GrazyBot
from utils import osrs as osrs_utils, ai

logger = logging.getLogger(__name__)

class OSRS(commands.Cog):
    """Cog for OSRS-related commands like stats, kc, and linking accounts."""
    
    def __init__(self, bot: GrazyBot):
        self.bot = bot
        self.hiscores_url = "https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws"
        self.session = aiohttp.ClientSession(headers={'User-Agent': 'GrazyBot/2.0'})

    def cog_unload(self):
        self.bot.loop.create_task(self.session.close())

    osrs_group = SlashCommandGroup("osrs", "Commands for Old School RuneScape integration.")

    @osrs_group.command(name="link", description="Link your Discord account to your OSRS name.")
    async def link_osrs_name(self, ctx: discord.ApplicationContext, 
                             osrs_name: Option(str, "Your Old School RuneScape username.")):
        """Links a user's Discord ID to their OSRS username."""
        await ctx.defer(ephemeral=True)
        
        if not re.match(r"^[a-zA-Z0-9 _-]{1,12}$", osrs_name):
            return await ctx.respond("Invalid OSRS username format.", ephemeral=True)

        try:
            async with self.bot.db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_links (discord_id, osrs_name) VALUES ($1, $2) "
                    "ON CONFLICT (discord_id) DO UPDATE SET osrs_name = EXCLUDED.osrs_name",
                    ctx.author.id, osrs_name
                )
            await ctx.respond(f"Your account has been linked to OSRS name: **{osrs_name}**.", ephemeral=True)
            logger.info(f"User {ctx.author} linked their OSRS name to '{osrs_name}'.")
        except Exception as e:
            logger.error(f"Error linking OSRS name for {ctx.author}: {e}", exc_info=True)
            await ctx.respond("An error occurred while linking your account.", ephemeral=True)

    @osrs_group.command(name="profile", description="View a member's OSRS stats from the Hiscores.")
    async def view_osrs_profile(self, ctx: discord.ApplicationContext, 
                                member: Option(discord.Member, "The member to view. Defaults to yourself.", required=False)):
        """Fetches and displays a user's OSRS skills from the official hiscores."""
        await ctx.defer()
        target_member = member or ctx.author
        
        async with self.bot.db_pool.acquire() as conn:
            osrs_name = await conn.fetchval("SELECT osrs_name FROM user_links WHERE discord_id = $1", target_member.id)

        if not osrs_name:
            return await ctx.respond(f"{'You have' if target_member == ctx.author else f'{target_member.display_name} has'} not linked an OSRS name yet. Use `/osrs link`.", ephemeral=True)

        try:
            async with self.session.get(f"{self.hiscores_url}?player={up.quote(osrs_name)}") as response:
                if response.status == 404:
                    return await ctx.respond(f"OSRS name **{osrs_name}** not found on the Hiscores.", ephemeral=True)
                response.raise_for_status()
                data = await response.text()
        except aiohttp.ClientError as e:
            logger.error(f"Hiscores fetch failed for {osrs_name}: {e}")
            return await ctx.respond(f"Error fetching Hiscores data for **{osrs_name}**.", ephemeral=True)

        skills_data, _ = osrs_utils.parse_hiscores_data(data)
        
        if not skills_data:
            return await ctx.respond(f"Could not parse any skill data for **{osrs_name}**.", ephemeral=True)

        profile_summary = await ai.generate_osrs_profile_summary(osrs_name, skills_data)
        
        embed = discord.Embed(
            title=f"OSRS Profile: {osrs_name}",
            url=f"https://secure.runescape.com/m=hiscore_oldschool/hiscorepersonal?user1={up.quote(osrs_name)}",
            description=f"*{profile_summary}*",
            color=discord.Color.dark_green()
        )
        embed.set_thumbnail(url="https://oldschool.runescape.wiki/images/thumb/Old_School_RuneScape_logo.png/1200px-Old_School_RuneScape_logo.png")
        
        if 'overall' in skills_data:
            overall = skills_data['overall']
            embed.add_field(name="Overall", value=f"Rank: `{overall['rank']:,}`\nLevel: `{overall['level']}`\nXP: `{overall['xp']:,}`", inline=False)
        
        combat_skills = ["attack", "strength", "defence", "ranged", "prayer", "magic", "hitpoints"]
        for i, block in enumerate(osrs_utils.format_skill_list(combat_skills, skills_data)):
            embed.add_field(name="Combat Skills", value=block, inline=True)
            
        skilling_skills = [s for s in osrs_utils.WOM_SKILLS if s not in combat_skills and s != 'overall']
        for i, block in enumerate(osrs_utils.format_skill_list(skilling_skills, skills_data)):
            embed.add_field(name="Skilling", value=block, inline=True)
            
        await ctx.respond(embed=embed)

    @osrs_group.command(name="kc", description="View a member's OSRS boss kill counts.")
    async def view_osrs_kc(self, ctx: discord.ApplicationContext, 
                           member: Option(discord.Member, "The member to view. Defaults to yourself.", required=False)):
        """Fetches and displays a user's boss kill counts from the hiscores."""
        await ctx.defer()
        target_member = member or ctx.author
        
        async with self.bot.db_pool.acquire() as conn:
            osrs_name = await conn.fetchval("SELECT osrs_name FROM user_links WHERE discord_id = $1", target_member.id)

        if not osrs_name:
            return await ctx.respond(f"{'You have' if target_member == ctx.author else f'{target_member.display_name} has'} not linked an OSRS name yet.", ephemeral=True)

        try:
            async with self.session.get(f"{self.hiscores_url}?player={up.quote(osrs_name)}") as response:
                if response.status == 404:
                    return await ctx.respond(f"OSRS name **{osrs_name}** not found on the Hiscores.", ephemeral=True)
                response.raise_for_status()
                data = await response.text()
        except aiohttp.ClientError:
            return await ctx.respond(f"Error fetching Hiscores data for **{osrs_name}**.", ephemeral=True)
        
        _, activities_data = osrs_utils.parse_hiscores_data(data)

        embed = discord.Embed(title=f"OSRS Kill Counts: {osrs_name}", color=discord.Color.dark_red())
        embed.set_thumbnail(url="https://oldschool.runescape.wiki/images/Slayer_helmet.png")

        if not activities_data:
            embed.description = "No notable boss kill counts found on the Hiscores."
        else:
            kc_text = []
            for name, data in activities_data.items():
                kc_text.append(f"**{name.replace('_', ' ').title()}**: `{data['score']:,}`")

            # Paginate if needed
            current_field = ""
            field_count = 1
            for line in kc_text:
                if len(current_field) + len(line) + 1 > 1024:
                    embed.add_field(name=f"PvM Kills (Part {field_count})", value=current_field, inline=False)
                    current_field = line + "\n"
                    field_count += 1
                else:
                    current_field += line + "\n"
            if current_field:
                embed.add_field(name=f"PvM Kills (Part {field_count})", value=current_field, inline=False)
        
        await ctx.respond(embed=embed)

async def setup(bot: GrazyBot):
    await bot.add_cog(OSRS(bot))