import discord
from discord import app_commands
from discord.ext import commands
import logging
import json
from core.bot_base import BotBase
from utils import wom

logger = logging.getLogger(__name__)

class Clan(commands.Cog):
    """Cog for clan-related commands."""

    def __init__(self, bot: BotBase):
        self.bot = bot

    @app_commands.command(name="saveclan", description="Save or update clan data.")
    @app_commands.describe(data="Data to save (e.g., 'level:10, members:5' or JSON format)")
    async def save_clan_data(self, interaction: discord.Interaction, data: str):
        """Saves or updates clan data in the Supabase table."""
        await interaction.response.defer(ephemeral=True)

        # Fetch player details from Wise Old Man
        player_details, error = await wom.get_player_details(osrs_name)
        if error:
            return await interaction.followup.send(f"Error fetching player details from Wise Old Man: {error}", ephemeral=True)

        # Prepare data for Supabase
        data = {
            "user_id": interaction.user.id,
            "username": osrs_name,
            "role": "member",
            "join_date": discord.utils.utcnow().isoformat(),
            "stats": player_details,
        }

        try:
            # Insert data into Supabase
            response = self.bot.supabase.table("members").insert(data).execute()

            # Check for errors in the response
            if response.data:
                await interaction.followup.send(f"Member **{osrs_name}** has been added to the clan.", ephemeral=True)
                logger.info(f"Member {osrs_name} added to the clan by {interaction.user}.")
            else:
                 await interaction.followup.send("Failed to add member to the clan. Please check the logs.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error adding member to Supabase: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while adding the member to the clan.", ephemeral=True)

    @clan_group.command(name="view", description="View a member's clan profile.")
    async def view_member(self, interaction: discord.Interaction, osrs_name: str):
        """Views a member's profile from the clan database."""
        await interaction.response.defer()

        try:
            # Fetch data from Supabase
            response = self.bot.supabase.table("members").select("*").eq("username", osrs_name).execute()

            if response.data:
                member_data = response.data[0]
                embed = discord.Embed(title=f"Clan Profile: {member_data['username']}", color=discord.Color.blue())
                embed.add_field(name="Role", value=member_data['role'], inline=True)
                embed.add_field(name="Join Date", value=member_data['join_date'], inline=True)

                stats = member_data.get('stats', {})
                if stats:
                    embed.add_field(name="Overall Level", value=stats.get('overall', {}).get('level', 'N/A'), inline=False)

                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(f"Member **{osrs_name}** not found in the clan database.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error fetching member from Supabase: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching the member's profile.", ephemeral=True)

    @clan_group.command(name="save_data", description="Save or update clan data.")
    @app_commands.describe(data="Data to save (e.g., 'level:10, members:5' or JSON format)")
    async def save_clan_data(self, interaction: discord.Interaction, data: str):
        """Saves or updates clan data in the Supabase table."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Attempt to parse the input string as JSON
            try:
                clan_data = json.loads(data)
            except json.JSONDecodeError:
                # Fallback for key-value string format
                clan_data = {}
                for item in data.split(','):
                    if ':' in item:
                        key, value = item.split(':', 1)
                        clan_data[key.strip()] = value.strip()
        except Exception as e:
            await interaction.followup.send(
                "Invalid data format. Please use 'key:value, key2:value2' or a valid JSON string.",
                ephemeral=True
            )
            logger.warning(f"Invalid data format from {interaction.user}: {data}")
            return

        # Prepare the data for Supabase
        payload = {
            "clan_name": interaction.guild.name,
            "data": clan_data
        }

        try:
            # Insert the data into the 'clan_data' table
            response = self.bot.supabase.table("clan_data").insert(payload).execute()

            if response.data:
                await interaction.followup.send("Clan data has been successfully saved.", ephemeral=True)
                logger.info(f"Clan data saved by {interaction.user} for guild {interaction.guild.id}.")
            else:
                await interaction.followup.send("Failed to save clan data. Please check logs.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error saving clan data to Supabase: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while saving clan data.", ephemeral=True)

async def setup(bot: BotBase):
    await bot.add_cog(Clan(bot))