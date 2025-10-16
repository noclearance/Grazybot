# core/database.py
# Manages the database connection pool.

import asyncpg
import logging
from . import config

logger = logging.getLogger(__name__)

async def create_db_pool():
    """
    Creates and returns a connection pool to the PostgreSQL database.
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
        return pool
    except (asyncpg.PostgresError, OSError) as e:
        logger.error(f"Failed to create database connection pool: {e}")
        return None