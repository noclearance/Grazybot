import asyncpg
import os
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

async def create_db_pool():
    """
    Creates and returns a connection pool to the PostgreSQL database.
    Applies the database schema from schema.sql.
    Returns the pool if successful, raises an exception on failure.
    """
    load_dotenv()
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

        # Apply schema
        try:
            async with pool.acquire() as conn:
                with open('schema.sql', 'r') as f:
                    await conn.execute(f.read())
            logger.info("Database schema applied successfully")
        except FileNotFoundError:
            logger.error("schema.sql not found. Cannot apply database schema")
            raise
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to apply database schema: {e}")
            raise

        return pool
    except (asyncpg.PostgresError, OSError) as e:
        logger.error(f"Failed to create database connection pool: {e}")
        raise
