# Grazybot

Grazybot is a Python-based Discord bot designed for clan management and community engagement. It integrates with Supabase to store and retrieve data, and it offers a variety of slash commands for interacting with the bot.

## Setup

To run Grazybot, you need to set up the following environment variables:

- `DISCORD_BOT_TOKEN`: Your Discord bot token.
- `DATABASE_URL`: The connection string for your PostgreSQL database.
- `SUPABASE_URL`: The URL for your Supabase project.
- `SUPABASE_KEY`: The anonymous key for your Supabase project.

## Supabase Integration

Grazybot uses Supabase to store data in a PostgreSQL database. The following table schema is required for the `/saveclan` command:

```sql
CREATE TABLE clan_data (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  clan_name TEXT NOT NULL,
  data JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

## Commands

### `/saveclan`

The `/saveclan` command allows you to save or update clan data.

**Usage:** `/saveclan data:<data>`

The `<data>` parameter can be a simple string in `key:value` format or a JSON object. For example:

- `/saveclan data:level:10, members:5`
- `/saveclan data:{"level": 10, "members": 5}`
