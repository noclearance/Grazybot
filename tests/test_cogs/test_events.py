# tests/test_cogs/test_events.py
# Tests for events.py cog command registration.

import pytest
from discord.ext import commands
from cogs.events import Events


class TestEventsCog:
    """Test suite for Events cog command registration."""

    def test_cog_has_events_group(self):
        """Test that Events cog has events command group."""
        assert hasattr(Events, 'events_group')

    def test_cog_has_view_events_command(self):
        """Test that Events cog has view_events command."""
        assert hasattr(Events, 'view_events')
