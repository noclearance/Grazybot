-- schema.sql
-- This file contains the database schema for the GrazyBot.

-- Table to store links between Discord users and OSRS usernames
CREATE TABLE IF NOT EXISTS user_links (
    discord_id BIGINT PRIMARY KEY,
    osrs_name VARCHAR(12) NOT NULL
);

-- Table to store clan points for each member
CREATE TABLE IF NOT EXISTS clan_points (
    discord_id BIGINT PRIMARY KEY,
    points INTEGER NOT NULL DEFAULT 0
);

-- Table to store information about active SOTW competitions
CREATE TABLE IF NOT EXISTS active_competitions (
    id SERIAL PRIMARY KEY,
    competition_id INTEGER NOT NULL,
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Table to store information about giveaways
CREATE TABLE IF NOT EXISTS giveaways (
    id SERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    prize VARCHAR(255) NOT NULL,
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
    winner_count INTEGER NOT NULL DEFAULT 1,
    role_id BIGINT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Table to store giveaway entries
CREATE TABLE IF NOT EXISTS giveaway_entries (
    id SERIAL PRIMARY KEY,
    giveaway_id INTEGER REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL
);

-- Table to store information about raffles
CREATE TABLE IF NOT EXISTS raffles (
    id SERIAL PRIMARY KEY,
    prize VARCHAR(255) NOT NULL,
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    winner_id BIGINT
);

-- Table to store raffle entries
CREATE TABLE IF NOT EXISTS raffle_entries (
    id SERIAL PRIMARY KEY,
    raffle_id INTEGER REFERENCES raffles(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    source VARCHAR(20) NOT NULL -- 'self' or 'admin'
);

-- Table to store information about bingo events
CREATE TABLE IF NOT EXISTS bingo_events (
    id SERIAL PRIMARY KEY,
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
    board_json TEXT NOT NULL,
    message_id BIGINT,
    channel_id BIGINT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Table to store bingo submissions
CREATE TABLE IF NOT EXISTS bingo_submissions (
    id SERIAL PRIMARY KEY,
    event_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    task_name VARCHAR(255) NOT NULL,
    proof_url VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' -- 'pending', 'approved', 'rejected'
);

-- Table to store completed bingo tiles
CREATE TABLE IF NOT EXISTS bingo_completed_tiles (
    id SERIAL PRIMARY KEY,
    event_id INTEGER REFERENCES bingo_events(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    task_name VARCHAR(255) NOT NULL
);

-- Table to store information about PVM events
CREATE TABLE IF NOT EXISTS pvm_events (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
    duration_minutes INTEGER NOT NULL,
    message_id BIGINT,
    channel_id BIGINT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Table to store PVM event signups
CREATE TABLE IF NOT EXISTS pvm_event_signups (
    id SERIAL PRIMARY KEY,
    event_id INTEGER REFERENCES pvm_events(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL
);

-- Table to store boss personal bests
CREATE TABLE IF NOT EXISTS boss_pbs (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT NOT NULL,
    boss_name VARCHAR(255) NOT NULL,
    pb_time_ms INTEGER NOT NULL,
    proof_url VARCHAR(255) NOT NULL,
    logged_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE (discord_id, boss_name)
);

-- Table to store available rewards in the point store
CREATE TABLE IF NOT EXISTS rewards (
    id SERIAL PRIMARY KEY,
    reward_name VARCHAR(255) UNIQUE NOT NULL,
    point_cost INTEGER NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Table to link rewards to roles
CREATE TABLE IF NOT EXISTS role_rewards (
    reward_id INTEGER PRIMARY KEY REFERENCES rewards(id) ON DELETE CASCADE,
    role_id BIGINT NOT NULL
);

-- Table to log point store transactions
CREATE TABLE IF NOT EXISTS redeem_transactions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    reward_id INTEGER NOT NULL,
    reward_name VARCHAR(255) NOT NULL,
    point_cost INTEGER NOT NULL,
    redeemed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);