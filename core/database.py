import asyncpg
import os
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

async def create_db_pool():
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logging.error("DATABASE_URL not set in environment variables")
        raise ValueError("DATABASE_URL not set")
    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=10)
        logging.info("Database connection pool created successfully")

        # Apply schema
        async with pool.acquire() as conn:
            with open('schema.sql', 'r') as f:
                await conn.execute(f.read())
        logging.info("Database schema applied successfully.")

        return pool
    except (asyncpg.PostgresError, OSError) as e:
        logging.error(f"Failed to create database connection pool: {e}")
        raise
    except FileNotFoundError:
        logging.error("schema.sql not found. Cannot apply database schema.")
        raise