# tests/test_cogs/test_raffle.py
# Tests for raffle.py cog command registration.

import pytest
from discord.ext import commands
from cogs.raffle import Raffle


class TestRaffleCog:
    """Test suite for Raffle cog command registration."""

    def test_cog_has_raffle_group(self):
        """Test that Raffle cog has raffle command group."""
        assert hasattr(Raffle, 'raffle_group')

    def test_cog_has_admin_group(self):
        """Test that Raffle cog has admin command group."""
        assert hasattr(Raffle, 'admin_group')

    def test_cog_has_start_raffle_command(self):
        """Test that Raffle cog has start_raffle command."""
        assert hasattr(Raffle, 'start_raffle')

    def test_cog_has_enter_raffle_command(self):
        """Test that Raffle cog has enter_raffle command."""
        assert hasattr(Raffle, 'enter_raffle')

    def test_cog_has_give_tickets_command(self):
        """Test that Raffle cog has give_tickets command."""
        assert hasattr(Raffle, 'give_tickets')

    def test_cog_has_view_tickets_command(self):
        """Test that Raffle cog has view_tickets command."""
        assert hasattr(Raffle, 'view_tickets')

    def test_cog_has_draw_now_command(self):
        """Test that Raffle cog has draw_now command."""
        assert hasattr(Raffle, 'draw_now')
