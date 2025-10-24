# init_db.py
# A script to initialize the database schema.
# This should be run once during the initial deployment setup.

import asyncio
import asyncpg
import os
from dotenv import load_dotenv

async def initialize_database():
    """Connects to the database and applies the schema from schema.sql."""
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not set in environment variables.")
        return

    conn = None
    try:
        conn = await asyncpg.connect(dsn=db_url)
        print("Successfully connected to the database.")

        try:
            with open('schema.sql', 'r') as f:
                await conn.execute(f.read())
            print("Database schema applied successfully.")
        except FileNotFoundError:
            print("Error: schema.sql not found. Cannot apply database schema.")
        except asyncpg.PostgresError as e:
            print(f"Error applying database schema: {e}")

    except (asyncpg.PostgresError, OSError) as e:
        print(f"Error connecting to the database: {e}")
    finally:
        if conn:
            await conn.close()
            print("Database connection closed.")

if __name__ == "__main__":
    asyncio.run(initialize_database())
