# cogs/admin.py
# Contains administrative commands for managing the bot and server.

# cogs/admin.py
# Contains administrative commands for managing the bot and server.

import discord
from discord import app_commands
from discord.ext import commands
import logging

from core.bot_base import GrazyBot
from utils import clan, wom

logger = logging.getLogger(__name__)

class Admin(commands.Cog):
    """Cog for admin-only commands."""

    def __init__(self, bot: GrazyBot):
        self.bot = bot

    # Create a slash command group using the app_commands.Group decorator
    admin_group = app_commands.Group(
        name="admin",
        description="Admin-only commands for managing the bot and server.",
        default_permissions=discord.Permissions(manage_guild=True)
    )

    @admin_group.command(name="announce", description="Send a message as the bot to a specific channel.")
    async def announce(self, interaction: discord.Interaction,
                       message: str,
                       channel: discord.TextChannel,
                       ping_everyone: bool = False):
        """Sends a formatted announcement embed to a specified channel."""
        await interaction.response.defer(ephemeral=True)

        content = "@everyone" if ping_everyone else ""
        embed = discord.Embed(
            title="Clan Announcement",
            description=message,
            color=discord.Color.orange()
        )
        embed.set_footer(text=f"Message sent by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

        try:
            await channel.send(content=content, embed=embed)
            await interaction.followup.send("Announcement sent successfully!", ephemeral=True)
            logger.info(f"Admin {interaction.user} sent an announcement to #{channel.name}.")
        except discord.Forbidden:
            await interaction.followup.send("Error: I don't have permission to send messages in that channel.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)
            logger.error(f"Error in /admin announce: {e}", exc_info=True)

    @admin_group.command(name="manage_points", description="Add or remove Clan Points from a member.")
    @app_commands.choices(action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Remove", value="remove")
    ])
    async def manage_points(self, interaction: discord.Interaction,
                            member: discord.Member,
                            action: str,
                            amount: int,
                            reason: str):
        """Adds or removes points from a user and logs the transaction."""
        await interaction.response.defer(ephemeral=True)
        
        if action == "add":
            await clan.award_points(self.bot, member, amount, reason)
        else: # remove
            try:
                async with self.bot.db_pool.acquire() as conn:
                    await conn.execute("INSERT INTO clan_points (discord_id, points) VALUES ($1, 0) ON CONFLICT (discord_id) DO NOTHING", member.id)
                    await conn.execute("UPDATE clan_points SET points = GREATEST(0, points - $1) WHERE discord_id = $2", amount, member.id)
                logger.info(f"Admin {interaction.user} removed {amount} points from {member.display_name} for: {reason}")
            except Exception as e:
                logger.error(f"Error removing points from {member.display_name}: {e}", exc_info=True)
                return await interaction.followup.send(f"An error occurred while updating points.", ephemeral=True)

        async with self.bot.db_pool.acquire() as conn:
            new_balance = await conn.fetchval("SELECT points FROM clan_points WHERE discord_id = $1", member.id) or 0
        
        await interaction.followup.send(f"Successfully {action}ed {amount} points for {member.display_name}. Their new balance is {new_balance:,}.", ephemeral=True)

    @admin_group.command(name="award_sotw_winners", description="Manually award points for a past SOTW competition.")
    async def award_sotw_winners(self, interaction: discord.Interaction,
                                 competition_id: int):
        """Fetches SOTW winners from WOM and awards them points."""
        await interaction.response.defer(ephemeral=True)
        
        comp_data, error = await wom.get_competition_details(competition_id)
        if error:
            return await interaction.followup.send(f"Could not fetch WOM details for competition ID {competition_id}. Error: {error}", ephemeral=True)

        awarded_to = []
        point_values = [100, 50, 25]
        
        async with self.bot.db_pool.acquire() as conn:
            participants = comp_data.get('participations', [])
            if not participants:
                return await interaction.followup.send("No participants found in the competition data.", ephemeral=True)

            for i, participant in enumerate(participants[:3]):
                osrs_name = participant['player']['displayName']
                user_data = await conn.fetchrow("SELECT discord_id FROM user_links WHERE osrs_name = $1", osrs_name)

                if user_data:
                    member = interaction.guild.get_member(user_data['discord_id'])
                    if member:
                        reason = f"placing #{i+1} in the '{comp_data['title']}' SOTW"
                        await clan.award_points(self.bot, member, point_values[i], reason)
                        awarded_to.append(f"#{i+1}: {member.display_name} ({point_values[i]} points)")

        if not awarded_to:
            return await interaction.followup.send("No winners could be found or linked for that competition.", ephemeral=True)
            
        await interaction.followup.send("Successfully awarded points to:\n" + "\n".join(awarded_to), ephemeral=True)
        logger.info(f"Admin {interaction.user} awarded SOTW points for competition {competition_id}.")

async def setup(bot: GrazyBot):
    await bot.add_cog(Admin(bot))