import discord
from discord import app_commands
from discord.ext import commands
import logging
from core.bot_base import BotBase
from utils import wom

logger = logging.getLogger(__name__)

class Clan(commands.Cog):
    """Cog for clan-related commands."""

    def __init__(self, bot: BotBase):
        self.bot = bot

    clan_group = app_commands.Group(name="clan", description="Commands for managing the clan.")

    @clan_group.command(name="add_member", description="Add a new member to the clan.")
    async def add_member(self, interaction: discord.Interaction, osrs_name: str):
        """Adds a new member to the clan database."""
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

async def setup(bot: BotBase):
    await bot.add_cog(Clan(bot))