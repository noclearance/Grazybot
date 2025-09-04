; ----------GrazyDiagnose ----
# diagnose_bot.py
import os
import asyncio
import discord
from discord.ext import commands

async def main():
    token = os.getenv("DISCORD_TOKEN")
    print("Token loaded:", bool(token))
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    try:
        await bot.login(token)
        print("Logged into Discord gateway successfully.")
    except Exception as e:
        print("Login error:", e)
        return
    try:
        # Check command sync if using slash
        synced_commands = await bot.tree.fetch_commands()
        print(f"Slash commands registered: {len(synced_commands)}")
    except Exception as e:
        print("Slash command fetch error:", e)
    await bot.close()

if _name_ == "_main_":
    asyncio.run(main())