# tests/test_utils/test_time.py
# Unit tests for the time utility functions.

import unittest
from datetime import timedelta

# By running pytest from the root directory, it will automatically handle the pathing.
# We no longer need to modify sys.path here.
from utils.time import parse_duration

class TestTimeUtils(unittest.TestCase):
    """Test suite for time utility functions."""

    def test_parse_duration_days(self):
        """Test parsing durations in days."""
        self.assertEqual(parse_duration("7d"), timedelta(days=7))
        self.assertEqual(parse_duration("1d"), timedelta(days=1))
        self.assertEqual(parse_duration("30d"), timedelta(days=30))

    def test_parse_duration_hours(self):
        """Test parsing durations in hours."""
        self.assertEqual(parse_duration("12h"), timedelta(hours=12))
        self.assertEqual(parse_duration("1h"), timedelta(hours=1))
        self.assertEqual(parse_duration("24h"), timedelta(hours=24))

    def test_parse_duration_minutes(self):
        """Test parsing durations in minutes."""
        self.assertEqual(parse_duration("30m"), timedelta(minutes=30))
        self.assertEqual(parse_duration("1m"), timedelta(minutes=1))
        self.assertEqual(parse_duration("90m"), timedelta(minutes=90))

    def test_parse_duration_with_spaces(self):
        """Test parsing durations with extra whitespace."""
        self.assertEqual(parse_duration(" 5d "), timedelta(days=5))
        self.assertEqual(parse_duration("  10h"), timedelta(hours=10))

    def test_parse_duration_invalid_formats(self):
        """Test that invalid formats return None."""
        self.assertIsNone(parse_duration("1w"))  # Invalid unit
        self.assertIsNone(parse_duration("d"))   # Missing value
        self.assertIsNone(parse_duration("10"))  # Missing unit
        self.assertIsNone(parse_duration("abc")) # Gibberish
        self.assertIsNone(parse_duration(""))    # Empty string
        self.assertIsNone(parse_duration("1.5d"))# Floats not supported by this simple parser

if __name__ == '__main__':
    unittest.main()