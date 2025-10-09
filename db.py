# bot/db.py
# Handles database connection and schema setup.

import asyncpg
import os
import urllib.parse as up
from bot.config import DATABASE_URL
from datetime import datetime, timezone

async def setup_database_pool():
    """
    Initializes the database connection pool and sets up the schema if it doesn't exist.
    This function is called once when the bot starts up.
    """
    up.uses_netloc.append("postgres")
    url = up.urlparse(DATABASE_URL)
    
    try:
        pool = await asyncpg.create_pool(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            ssl='require',
            min_size=5,
            max_size=10,
            timeout=60
        )
        print("Attempting to connect and set up database schema...")
        async with pool.acquire() as conn:
            # All CREATE TABLE statements are executed here, ensuring the database is ready.
            # This makes the bot more robust, as it can set itself up on a fresh database.
            await conn.execute("CREATE TABLE IF NOT EXISTS active_competitions (id INTEGER PRIMARY KEY, title TEXT, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE, winners_awarded BOOLEAN DEFAULT FALSE)")
            await conn.execute("CREATE TABLE IF NOT EXISTS raffles (id SERIAL PRIMARY KEY, prize TEXT NOT NULL, ends_at TIMESTAMPTZ NOT NULL, winner_id BIGINT DEFAULT NULL, message_id BIGINT DEFAULT NULL, channel_id BIGINT DEFAULT NULL)")
            await conn.execute("CREATE TABLE IF NOT EXISTS raffle_entries (entry_id SERIAL PRIMARY KEY, raffle_id INTEGER NOT NULL REFERENCES raffles(id) ON DELETE CASCADE, user_id BIGINT NOT NULL, source TEXT DEFAULT 'self')")
            await conn.execute("CREATE TABLE IF NOT EXISTS bingo_events (id SERIAL PRIMARY KEY, ends_at TIMESTAMPTZ NOT NULL, board_json TEXT NOT NULL, message_id BIGINT NOT NULL, is_active BOOLEAN DEFAULT TRUE)")
            await conn.execute("CREATE TABLE IF NOT EXISTS bingo_submissions (id SERIAL PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES bingo_events(id) ON DELETE CASCADE, user_id BIGINT NOT NULL, task_name TEXT NOT NULL, proof_url TEXT NOT NULL, status TEXT DEFAULT 'pending')")
            await conn.execute("CREATE TABLE IF NOT EXISTS bingo_completed_tiles (event_id INTEGER NOT NULL REFERENCES bingo_events(id) ON DELETE CASCADE, task_name TEXT NOT NULL, PRIMARY KEY (event_id, task_name))")
            await conn.execute("CREATE TABLE IF NOT EXISTS user_links (discord_id BIGINT PRIMARY KEY, osrs_name TEXT NOT NULL)")
            await conn.execute("CREATE TABLE IF NOT EXISTS clan_points (discord_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0)")
            await conn.execute("CREATE TABLE IF NOT EXISTS rewards (id SERIAL PRIMARY KEY, reward_name TEXT NOT NULL UNIQUE, point_cost INTEGER NOT NULL, description TEXT, is_active BOOLEAN DEFAULT TRUE)")
            await conn.execute("CREATE TABLE IF NOT EXISTS role_rewards (reward_id INTEGER PRIMARY KEY, role_id BIGINT NOT NULL, FOREIGN KEY (reward_id) REFERENCES rewards(id) ON DELETE CASCADE)")
            await conn.execute("CREATE TABLE IF NOT EXISTS redeem_transactions (transaction_id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, reward_id INTEGER NOT NULL, reward_name TEXT NOT NULL, point_cost INTEGER NOT NULL, redeemed_at TIMESTAMPTZ DEFAULT NOW())")
            await conn.execute("CREATE TABLE IF NOT EXISTS giveaways (message_id BIGINT PRIMARY KEY, channel_id BIGINT NOT NULL, prize TEXT NOT NULL, ends_at TIMESTAMPTZ NOT NULL, winner_count INTEGER NOT NULL, is_active BOOLEAN DEFAULT TRUE, role_id BIGINT)")
            await conn.execute("CREATE TABLE IF NOT EXISTS giveaway_entries (entry_id SERIAL PRIMARY KEY, message_id BIGINT NOT NULL, user_id BIGINT NOT NULL, UNIQUE (message_id, user_id))")
            await conn.execute("CREATE TABLE IF NOT EXISTS pvm_events (id SERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT, starts_at TIMESTAMPTZ NOT NULL, duration_minutes INTEGER, message_id BIGINT, channel_id BIGINT, signup_message_id BIGINT DEFAULT NULL, reminder_sent BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE)")
            await conn.execute("CREATE TABLE IF NOT EXISTS pvm_event_signups (event_id INTEGER REFERENCES pvm_events(id) ON DELETE CASCADE, user_id BIGINT NOT NULL, PRIMARY KEY (event_id, user_id))")
            await conn.execute("CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)")
            await conn.execute("CREATE TABLE IF NOT EXISTS boss_pbs (discord_id BIGINT NOT NULL, boss_name TEXT NOT NULL, pb_time_ms INTEGER NOT NULL, proof_url TEXT, logged_at TIMESTAMPTZ DEFAULT NOW(), PRIMARY KEY (discord_id, boss_name))")
            
            # Set default value for recap tracker if not present
            await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('last_recap_sent', $1) ON CONFLICT (key) DO NOTHING", datetime.min.replace(tzinfo=timezone.utc).isoformat())
        
        print("Database schema verified/created successfully.")
        return pool
    except Exception as e:
        print(f"FATAL: Error setting up database pool or schema: {e}")
        return None