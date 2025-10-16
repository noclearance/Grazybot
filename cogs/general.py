# cogs/general.py
# Contains general user-facing commands like help, points, and profile.

import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup
import logging

from core.bot import GrazyBot

logger = logging.getLogger(__name__)

class General(commands.Cog):
    """Cog for general, non-admin commands."""

    def __init__(self, bot: GrazyBot):
        self.bot = bot

    @commands.slash_command(name="help", description="Shows a list of all available commands.")
    async def help(self, ctx: discord.ApplicationContext):
        """
        Dynamically generates a help message showing all available commands,
        separated into Admin and Member categories.
        """
        await ctx.defer(ephemeral=True)

        embed = discord.Embed(
            title="GrazyBot Command List",
            description="Here are all the commands you can use. Commands marked with 🔒 are for admins only.",
            color=discord.Color.blurple()
        )

        categorized_commands = {'Member': [], 'Admin': []}

        for command in self.bot.walk_application_commands():
            if isinstance(command, discord.SlashCommand) and command.parent:
                continue

            is_admin = False
            if hasattr(command, 'default_member_permissions') and command.default_member_permissions is not None:
                 if command.default_member_permissions.manage_guild:
                     is_admin = True

            category = 'Admin' if is_admin else 'Member'

            if isinstance(command, SlashCommandGroup):
                for subcommand in command.walk_commands():
                     categorized_commands[category].append(f"`/{command.name} {subcommand.name}` - {subcommand.description}")
            else:
                categorized_commands[category].append(f"`/{command.name}` - {command.description}")

        if categorized_commands['Member']:
            embed.add_field(name="Member Commands", value="\n".join(sorted(categorized_commands['Member'])), inline=False)

        if categorized_commands['Admin']:
            embed.add_field(name="🔒 Admin Commands", value="\n".join(sorted(categorized_commands['Admin'])), inline=False)

        embed.set_footer(text="Let the games begin!")
        await ctx.respond(embed=embed, ephemeral=True)

    points_group = SlashCommandGroup("points", "Commands related to Clan Points.")

    @points_group.command(name="view", description="Check your current Clan Point balance.")
    async def view_points(self, ctx: discord.ApplicationContext):
        """Displays the calling user's current clan point balance."""
        await ctx.defer(ephemeral=True)
        try:
            async with self.bot.db_pool.acquire() as conn:
                point_data = await conn.fetchval(
                    "SELECT points FROM clan_points WHERE discord_id = $1", ctx.author.id
                )

            current_points = point_data if point_data is not None else 0
            await ctx.respond(f"You currently have **{current_points:,}** Clan Points.")
        except Exception as e:
            logger.error(f"Error fetching points for user {ctx.author.id}: {e}", exc_info=True)
            await ctx.respond("Could not fetch your points balance. Please try again later.", ephemeral=True)

    @points_group.command(name="leaderboard", description="View the Clan Points leaderboard.")
    async def leaderboard(self, ctx: discord.ApplicationContext):
        """Shows the top 10 members with the most clan points."""
        await ctx.defer()
        try:
            async with self.bot.db_pool.acquire() as conn:
                leaders = await conn.fetch(
                    "SELECT discord_id, points FROM clan_points WHERE points > 0 ORDER BY points DESC LIMIT 10"
                )

            embed = discord.Embed(title="🏆 Clan Points Leaderboard", color=discord.Color.gold())

            if not leaders:
                embed.description = "The leaderboard is empty. Go earn some points!"
            else:
                leaderboard_text = []
                for i, record in enumerate(leaders):
                    member = ctx.guild.get_member(record['discord_id'])
                    member_name = member.display_name if member else f"User ID: {record['discord_id']}"
                    rank_emoji = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"**#{i + 1}**")
                    leaderboard_text.append(f"{rank_emoji} {member_name}: `{record['points']:,}` points")
                embed.description = "\n".join(leaderboard_text)

            await ctx.respond(embed=embed)
        except Exception as e:
            logger.error(f"Error generating points leaderboard: {e}", exc_info=True)
            await ctx.respond("Could not retrieve the leaderboard. Please try again later.")

async def setup(bot: GrazyBot):
    await bot.add_cog(General(bot))