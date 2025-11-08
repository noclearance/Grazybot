import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import discord
from cogs.clan import Clan

@pytest.fixture
def bot():
    """Fixture to create a mock bot."""
    return MagicMock()

@pytest.fixture
def clan_cog(bot):
    """Fixture to create an instance of the Clan cog."""
    return Clan(bot)

@pytest.mark.asyncio
async def test_add_duplicate_member(clan_cog):
    """
    Test that adding a duplicate member is handled correctly.
    """
    # Arrange
    mock_interaction = MagicMock(spec=discord.ApplicationContext)
    mock_interaction.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()
    osrs_name = "TestUser"

    # Mock the database response to simulate the member already existing
    clan_cog.bot.supabase.table().select().eq().execute.return_value.data = [{"username": osrs_name}]

    # Act
    await clan_cog.add_member.callback(clan_cog, mock_interaction, osrs_name)

    # Assert
    clan_cog.bot.supabase.table().insert().execute.assert_not_called()
    mock_interaction.followup.send.assert_called_with(
        f"Member **{osrs_name}** already exists in the clan.",
        ephemeral=True
    )

@pytest.mark.asyncio
async def test_add_new_member(clan_cog):
    """
    Test that a new member can be added successfully.
    """
    # Arrange
    mock_interaction = MagicMock(spec=discord.ApplicationContext)
    mock_interaction.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()
    osrs_name = "TestUser"

    # Mock the database response to simulate the member not existing
    clan_cog.bot.supabase.table().select().eq().execute.return_value.data = []
    clan_cog.bot.supabase.table().insert().execute.return_value.data = [{"username": osrs_name}]

    # Act
    with patch("utils.wom.get_player_details", new=AsyncMock(return_value=({}, None))):
        await clan_cog.add_member.callback(clan_cog, mock_interaction, osrs_name)

    # Assert
    clan_cog.bot.supabase.table().insert().execute.assert_called_once()
    mock_interaction.followup.send.assert_called_with(
        f"Member **{osrs_name}** has been added to the clan.",
        ephemeral=True
    )
