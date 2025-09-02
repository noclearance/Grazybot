import os
import asyncio
import asyncpg
from dotenv import load_dotenv

# This script will connect to your database and add the missing columns.
# It is safe to run even if the columns already exist.

async def main():
    """Connects to the database and adds the necessary columns."""
    load_dotenv()
    database_url = os.getenv('DATABASE_URL')

    if not database_url:
        print("🔴 ERROR: DATABASE_URL environment variable not found.")
        return

    print("Connecting to the database...")
    try:
        conn = await asyncpg.connect(database_url)
        print("✅ Successfully connected.")

        # Add 'final_ping_sent' to 'raffles' table if it doesn't exist
        print("Checking 'raffles' table...")
        await conn.execute("""
            ALTER TABLE raffles ADD COLUMN IF NOT EXISTS final_ping_sent BOOLEAN DEFAULT FALSE;
        """)
        print(" -> 'raffles' table updated.")


        # Add 'winner_id' and 'winning_number' to 'giveaways' table if they don't exist
        print("Checking 'giveaways' table...")
        await conn.execute("""
            ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS winner_id BIGINT;
        """)
        await conn.execute("""
            ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS winning_number INTEGER;
        """)
        print(" -> 'giveaways' table updated.")

        await conn.close()
        print("\n✅ Database schema is now up to date!")
        print("You can now delete this script from your repository and restart your bot service.")

    except Exception as e:
        print(f"\n🔴 An error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(main())

