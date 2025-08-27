# bot_introspect.py
import asyncio
from bot import bot  # import your bot instance

async def main():
    await bot.wait_until_ready()
    
    print("=== BOT COMMANDS ===")
    for command in bot.commands:
        print(f"- {command.name} (cog: {command.cog_name})")

    print("\n=== COGS ===")
    for cog_name, cog in bot.cogs.items():
        print(f"- {cog_name}")
        for cmd in cog.get_commands():
            print(f"  - {cmd.name}")

    print("\n=== EVENTS ===")
    # bot._listeners is a dict of event_name -> list of callbacks
    for event_name, listeners in bot._listeners.items():
        print(f"- {event_name}: {len(listeners)} listener(s)")

    print("\n=== BACKGROUND TASKS ===")
    # list all asyncio tasks started by the bot
    for task in asyncio.all_tasks():
        print(f"- {task.get_name()} | {task._coro}")

asyncio.run(main())
