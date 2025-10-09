# bot/helpers/bingo_utils.py
# Utility functions specifically for the bingo cog.

import asyncio
from PIL import Image, ImageDraw, ImageFont
import textwrap
import os
import discord
import json

from bot.config import BINGO_CHANNEL_ID

def _generate_bingo_image_sync(tasks: list, completed_tasks: list = []) -> tuple[str | None, str | None]:
    """
    Synchronous function to generate the bingo board image.
    Designed to be run in a separate thread to avoid blocking the bot.
    """
    try:
        # Image generation logic from original bot.py
        width, height = 1000, 1000
        img = Image.new('RGB', (width, height), (40, 26, 13)) # Dark brown
        draw = ImageDraw.Draw(img)
        
        # Font handling
        try:
            title_font = ImageFont.truetype("fonts/Roboto-Regular.ttf", 60)
            task_font = ImageFont.truetype("fonts/Roboto-Regular.ttf", 28)
        except IOError:
            title_font = ImageFont.load_default()
            task_font = ImageFont.load_default()

        # ... (rest of the PIL drawing logic) ...
        title_text = "CLAN BINGO"
        draw.text((350, 20), title_text, font=title_font, fill=(255, 215, 0))

        # Grid and text drawing logic here
        # ...
        
        output_path = "bingo_board.png"
        img.save(output_path)
        return output_path, None
    except Exception as e:
        return None, f"Error during image generation: {e}"

async def generate_bingo_image(tasks: list, completed_tasks: list = []) -> tuple[str | None, str | None]:
    """Asynchronously generates the bingo image by running the sync function in a thread."""
    return await asyncio.to_thread(_generate_bingo_image_sync, tasks, completed_tasks)

async def update_bingo_board_post(bot):
    """Fetches the latest bingo data and updates the message with a new image."""
    async with bot.db_pool.acquire() as conn:
        event = await conn.fetchrow("SELECT * FROM bingo_events WHERE is_active = TRUE LIMIT 1")
        if not event: return
        
        completed = [r['task_name'] for r in await conn.fetch("SELECT task_name FROM bingo_completed_tiles WHERE event_id = $1", event['id'])]
        
    image_path, error = await generate_bingo_image(json.loads(event['board_json']), completed)
    if error: return
    
    channel = bot.get_channel(BINGO_CHANNEL_ID)
    if channel:
        try:
            message = await channel.fetch_message(event['message_id'])
            with open(image_path, 'rb') as f:
                new_file = discord.File(f, filename="bingo_board.png")
                embed = message.embeds[0]
                embed.set_image(url="attachment://bingo_board.png")
                await message.edit(embed=embed, attachments=[new_file])
        except discord.NotFound:
            print(f"Could not find bingo message {event['message_id']} to update.")