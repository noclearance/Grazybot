# tests/test_cogs/test_osrs.py
# Tests for osrs.py cog command registration.

import pytest
from discord.ext import commands
from cogs.osrs import OSRS


class TestOSRSCog:
    """Test suite for OSRS cog command registration."""

    def test_cog_has_osrs_group(self):
        """Test that OSRS cog has osrs command group."""
        assert hasattr(OSRS, 'osrs_group')

    def test_cog_has_link_command(self):
        """Test that OSRS cog has link_osrs_name command."""
        assert hasattr(OSRS, 'link_osrs_name')

    def test_cog_has_profile_command(self):
        """Test that OSRS cog has view_osrs_profile command."""
        assert hasattr(OSRS, 'view_osrs_profile')

    def test_cog_has_kc_command(self):
        """Test that OSRS cog has view_osrs_kc command."""
        assert hasattr(OSRS, 'view_osrs_kc')
