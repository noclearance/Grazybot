# Command Registration Fix - Deployment Notes

## Changes Made

### 1. Fixed Command Registration in `core/config.py`
- Made `DEBUG_GUILD_ID` optional instead of mandatory
- Added clear documentation about guild-specific vs global sync
- Bot now supports both development (fast guild sync) and production (global sync) modes

### 2. Enhanced Command Sync Logging in `core/bot_base.py`
- Added detailed logging before, during, and after command sync
- Now logs the number of commands successfully registered
- Helps diagnose command registration issues in production

### 3. Updated `requirements.txt`
- Changed version pinning to flexible versioning (>=) for better compatibility
- Ensured all required dependencies are properly versioned
- Matches the requirements from the issue description

### 4. Created `.env.example`
- Provides template for all required environment variables
- Includes helpful comments about where to find Discord IDs
- Documents the `DEBUG_GUILD_ID` feature for faster command sync

### 5. Created `README.md`
- Complete setup and deployment instructions
- Lists all available commands
- Troubleshooting guide for command visibility issues
- Render deployment instructions with required environment variables

## How to Deploy

### For Development/Testing (Fast Command Sync)

1. Set `DEBUG_GUILD_ID` in your `.env` file:
   - Enable Developer Mode in Discord (User Settings → Advanced → Developer Mode)
   - Right-click your server icon → Copy ID
   - Add to `.env`: `DEBUG_GUILD_ID=your_guild_id_here`

2. Commands will appear instantly in that specific server

### For Production (Global Sync)

1. Remove or comment out `DEBUG_GUILD_ID` from your `.env` file
2. Commands will sync globally to all servers
3. **Note:** Global sync takes up to 1 hour to propagate

## Render Deployment Checklist

Set these environment variables in Render dashboard:
- ✅ `DISCORD_BOT_TOKEN`
- ✅ `SUPABASE_URL`
- ✅ `SUPABASE_KEY`
- ✅ `DATABASE_URL`
- ✅ `WOM_CLAN_ID`
- ✅ `WOM_VERIFICATION_CODE`
- ✅ `GEMINI_API_KEY`
- ✅ All channel and role IDs
- ⚠️ `DEBUG_GUILD_ID` (optional - only if you want instant sync to one server)

## Verifying Command Registration

Check your Render logs for these messages:
```
Starting command tree synchronization...
Syncing command tree to guild 123456789...
Command tree synced successfully! X command(s) registered to guild 123456789.
```

Or for global sync:
```
Starting command tree synchronization...
Syncing command tree globally (this may take up to 1 hour to appear in Discord)...
Command tree synced successfully! X command(s) registered globally.
```

## Troubleshooting

### Commands not appearing?

1. **Check bot permissions:**
   - Bot needs `applications.commands` scope
   - Needs Administrator or Manage Server permission in Discord

2. **Verify guild ID:**
   - Make sure `DEBUG_GUILD_ID` matches your server
   - Double-check it's set correctly in Render environment variables

3. **Re-invite bot:**
   - If bot was invited before slash commands were enabled
   - Use this URL format: `https://discord.com/api/oauth2/authorize?client_id=YOUR_BOT_ID&permissions=8&scope=bot%20applications.commands`

4. **Check logs:**
   - Look for errors during command sync in Render logs
   - Verify cogs loaded successfully

## Database Requirements

Ensure these tables exist in your Supabase database:
- `clan_points` (discord_id, points)
- `active_competitions` (id, title, ends_at)
- `raffles` (channel_id, message_id, prize, ends_at, winner_id)
- `giveaways` (channel_id, message_id, prize, ends_at, is_active)
- `pvm_events` (id, channel_id, message_id, title, starts_at, is_active)

## Next Steps

1. Deploy to Render with updated code
2. Set all required environment variables
3. Check logs to confirm command sync
4. Test commands in Discord:
   - `/help` - Should show all commands
   - `/points view` - Should fetch from Supabase
   - `/events view` - Should display active events
   - etc.

## Support

If commands still don't appear after following these steps:
- Check Render logs for errors
- Verify all environment variables are set
- Confirm Supabase tables exist and are accessible
- Ensure bot has proper Discord permissions
