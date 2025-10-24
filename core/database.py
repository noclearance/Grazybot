import asyncpg
import os
import logging

logger = logging.getLogger(__name__)

async def create_db_pool():
    """
    Creates and returns a connection pool to the PostgreSQL database.
    Returns the pool if successful, otherwise raises an exception.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set in environment variables")
        raise ValueError("DATABASE_URL not set")

    logger.info(f"Attempting to connect to database with URL: {db_url.split('@')[1] if db_url else 'None'}")
    try:
        pool = await asyncpg.create_pool(
            dsn=db_url,
            min_size=5,
            max_size=20,
            command_timeout=60
        )
        logger.info("Database connection pool created successfully")
        return pool
    except (asyncpg.PostgresError, OSError) as e:
        logger.error(f"Failed to create database connection pool: {e}")
        raise