# bot_introspect.py
import asyncio
from bot import bot  # import your bot instance

async def main():
    await bot.wait_until_ready()

    print("\n=== EVENTS ===")
    # bot._listeners is a dict of event_name -> list of callbacks
    for event_name, listeners in bot._listeners.items():
        print(f"- {event_name}: {len(listeners)} listener(s)")

    print("\n=== BACKGROUND TASKS ===")
    # list all asyncio tasks started by the bot
    for task in asyncio.all_tasks():
        print(f"- {task.get_name()} | {task._coro}")

asyncio.run(main())
