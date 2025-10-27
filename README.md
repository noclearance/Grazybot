# GrazyBot

A Discord bot for managing clan events, points, raffles, and OSRS integrations.

## Commands

### Member Commands
- `/help` - Lists all available commands
- `/points view` - Shows your Clan Points balance
- `/points leaderboard` - Displays top 10 point holders
- `/events view` - Shows active events (competitions, raffles, giveaways, PVM)

### Admin Commands
- Raffle commands (e.g., `/startraffle`) - Manage raffles
- OSRS commands (e.g., `/hiscores`, `/stats`) - Fetch OSRS stats
- Competition management commands

## Setup

### Prerequisites
- Python 3.11+
- PostgreSQL database
- Supabase account
- Discord Bot Token

### Installation

1. Clone the repository:
```bash
git clone https://github.com/noclearance/Grazybot.git
cd Grazybot
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

### Required Environment Variables

- `DISCORD_BOT_TOKEN` - Your Discord bot token
- `DATABASE_URL` - PostgreSQL connection string
- `SUPABASE_URL` - Your Supabase project URL
- `SUPABASE_KEY` - Your Supabase anon or service key
- `DEBUG_GUILD_ID` (optional) - Guild ID for fast command sync during development

See `.env.example` for a complete list of configuration options.

### Database Setup

Ensure the following tables exist in your Supabase/PostgreSQL database:
- `clan_points`
- `active_competitions`
- `raffles`
- `giveaways`
- `pvm_events`

## Deployment

### Render Deployment

1. Connect your GitHub repository to Render
2. Set environment variables in Render dashboard:
   - `DISCORD_BOT_TOKEN`
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
   - `DATABASE_URL`
   - All other required variables from `.env.example`

3. Deploy with start command: `python core/bot.py`

### Local Development

Run the bot locally:
```bash
source venv/bin/activate
python core/bot.py
```

## Command Registration

The bot uses Discord's slash command system. Commands are automatically synced on startup:

- **Guild-specific sync** (recommended for development): Set `DEBUG_GUILD_ID` in your `.env` file. Commands appear instantly.
- **Global sync** (production): Leave `DEBUG_GUILD_ID` unset. Commands may take up to 1 hour to appear across all servers.

### Bot Permissions Required

Ensure your bot has these permissions in Discord:
- `applications.commands` scope
- Administrator or Manage Server permission (for command registration)
- Send Messages, Embed Links, Add Reactions (for functionality)

### Troubleshooting Command Visibility

If commands don't appear:
1. Check bot has proper permissions in your server
2. Verify `DEBUG_GUILD_ID` is set correctly (right-click server → Copy ID)
3. Check Render logs for sync confirmation messages
4. Re-invite the bot with the `applications.commands` scope if needed

## License

[Add your license here]
