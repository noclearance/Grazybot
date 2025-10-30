import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import discord
from cogs.clan import Clan

class TestClanCog(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = AsyncMock()
        self.cog = Clan(self.bot)

    @patch('discord.Interaction')
    async def test_save_clan_data_success_json(self, mock_interaction):
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        mock_interaction.guild.name = "Test Guild"

        data_str = '{"level": 10, "members": 5}'

        with patch.object(self.cog.bot.supabase.table('clan_data'), 'insert') as mock_insert:
            mock_execute = MagicMock()
            mock_execute.data = [{"id": 1}]
            mock_insert.return_value.execute.return_value = mock_execute

            await self.cog.save_clan_data(mock_interaction, data=data_str)

            mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
            mock_insert.assert_called_once_with({
                "clan_name": "Test Guild",
                "data": {"level": 10, "members": 5}
            })
            mock_interaction.followup.send.assert_called_once_with(
                "Clan data has been successfully saved.", ephemeral=True
            )

    @patch('discord.Interaction')
    async def test_save_clan_data_success_kv_string(self, mock_interaction):
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        mock_interaction.guild.name = "Test Guild"

        data_str = "level:10, members:5"

        with patch.object(self.cog.bot.supabase.table('clan_data'), 'insert') as mock_insert:
            mock_execute = MagicMock()
            mock_execute.data = [{"id": 1}]
            mock_insert.return_value.execute.return_value = mock_execute

            await self.cog.save_clan_data(mock_interaction, data=data_str)

            mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
            mock_insert.assert_called_once_with({
                "clan_name": "Test Guild",
                "data": {"level": "10", "members": "5"}
            })
            mock_interaction.followup.send.assert_called_once_with(
                "Clan data has been successfully saved.", ephemeral=True
            )

    @patch('discord.Interaction')
    async def test_save_clan_data_supabase_error(self, mock_interaction):
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        mock_interaction.guild.name = "Test Guild"

        data_str = '{"level": 10, "members": 5}'

        with patch.object(self.cog.bot.supabase.table('clan_data'), 'insert') as mock_insert:
            mock_insert.return_value.execute.side_effect = Exception("Supabase error")

            await self.cog.save_clan_data(mock_interaction, data=data_str)

            mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
            mock_interaction.followup.send.assert_called_once_with(
                "An error occurred while saving clan data.", ephemeral=True
            )

    @patch('discord.Interaction')
    async def test_save_clan_data_invalid_format(self, mock_interaction):
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        data_str = "this is not valid data"

        await self.cog.save_clan_data(mock_interaction, data=data_str)

        mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
        mock_interaction.followup.send.assert_called_once_with(
            "Invalid data format. Please use 'key:value, key2:value2' or a valid JSON string.",
            ephemeral=True
        )

if __name__ == '__main__':
    unittest.main()