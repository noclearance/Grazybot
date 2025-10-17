# core/database.py
# Manages the database connection pool.

import asyncpg
import logging
from . import config

logger = logging.getLogger(__name__)

async def create_db_pool():
    """
    Creates and returns a connection pool to the PostgreSQL database.
    Also ensures the database schema is up to date.
    Returns None if the connection fails.
    """
    try:
        pool = await asyncpg.create_pool(
            dsn=config.DATABASE_URL,
            min_size=5,
            max_size=20,
            command_timeout=60
        )
        logger.info("Database connection pool created successfully.")

        # Apply schema
        async with pool.acquire() as conn:
            with open('schema.sql', 'r') as f:
                await conn.execute(f.read())
        logger.info("Database schema applied successfully.")

        return pool
    except (asyncpg.PostgresError, OSError) as e:
        logger.error(f"Failed to create database connection pool: {e}")
        return None
    except FileNotFoundError:
        logger.error("schema.sql not found. Cannot apply database schema.")
        return None