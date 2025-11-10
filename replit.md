# GrazyBot - OSRS Clan Discord Bot

## Overview
GrazyBot is a Discord bot designed for an Old School RuneScape (OSRS) clan. It provides features like:
- Clan member management and points tracking
- PVM (Player vs Monster) event coordination
- SOTW (Skill of the Week) competitions
- Giveaways and raffles
- Bingo events
- Boss personal best tracking
- Point store rewards system
- Integration with WiseOldMan (WOM) API for player stats
- AI-powered features using Google Gemini

## Tech Stack
- **Language**: Python 3.11
- **Framework**: Discord.py / py-cord
- **Database**: PostgreSQL (asyncpg)
- **APIs**: 
  - Discord Bot API
  - WiseOldMan API (OSRS clan tracking)
  - Google Gemini API (AI features)

## Project Structure
- `core/` - Core bot functionality and configuration
  - `bot.py` - Main entry point
  - `bot_base.py` - Base bot class
  - `config.py` - Environment configuration
  - `database.py` - Database connection management
- `cogs/` - Bot command modules (admin, bingo, events, giveaway, osrs, pvm, raffle, sotw, etc.)
- `utils/` - Utility functions (AI, clan management, OSRS data, time helpers, etc.)
- `tests/` - Test suite
- `schema.sql` - Database schema definition

## Required Environment Variables
**Critical (Bot won't start without these):**
- `TOKEN` - Discord bot token
- `DATABASE_URL` - PostgreSQL database connection URL
- `DEBUG_GUILD_ID` - Discord server ID for testing
- `WOM_CLAN_ID` - WiseOldMan clan ID
- `WOM_VERIFICATION_CODE` - WiseOldMan API verification code
- `GEMINI_API_KEY` - Google Gemini API key

**Optional (Channel/Role IDs for specific features):**
- `ANNOUNCEMENTS_CHANNEL_ID`
- `BINGO_CHANNEL_ID`
- `PVM_EVENT_CHANNEL_ID`
- `RAFFLE_CHANNEL_ID`
- `RECAP_CHANNEL_ID`
- `SOTW_CHANNEL_ID`
- `SOTW_ROLE_ID`

## Setup Notes (Replit Import - November 10, 2025)
This project was imported from GitHub into Replit. The following setup was completed:
- Installed Python 3.11 with all dependencies from requirements.txt
- Database URL is already configured as a secret
- Bot requires Discord token and other API keys to be configured by the user
- Database schema will be auto-applied on first run from schema.sql

## Running the Bot
The bot is started with: `python -m core.bot`

It will:
1. Load environment variables
2. Validate configuration
3. Connect to PostgreSQL database
4. Apply database schema if needed
5. Load all cogs (command modules)
6. Connect to Discord and start listening for commands

## Deployment
This is a long-running bot process that needs to stay active. It's not a web server, so it runs as a console application.
