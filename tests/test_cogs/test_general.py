# tests/test_cogs/test_general.py
# Tests for general.py cog command registration.

import pytest
from discord.ext import commands
from cogs.general import General


class TestGeneralCog:
    """Test suite for General cog command registration."""

    def test_cog_has_help_command(self):
        """Test that General cog has help command."""
        # Check that the help method exists
        assert hasattr(General, 'help')

    def test_cog_has_points_group(self):
        """Test that General cog has points command group."""
        assert hasattr(General, 'points_group')

    def test_cog_has_view_points_command(self):
        """Test that General cog has view_points command."""
        assert hasattr(General, 'view_points')

    def test_cog_has_leaderboard_command(self):
        """Test that General cog has leaderboard command."""
        assert hasattr(General, 'leaderboard')
