# utils/bingo.py
# Utility functions specifically for the bingo cog.

import asyncio
from PIL import Image, ImageDraw, ImageFont
import textwrap
import os
import discord
import json
import logging

from core import config

logger = logging.getLogger(__name__)

def _generate_bingo_image_sync(tasks: list, completed_tasks: list = []) -> tuple[str | None, str | None]:
    """
    Synchronous function to generate the bingo board image.
    Designed to be run in a separate thread to avoid blocking the bot.
    """
    try:
        width, height = 1200, 1200
        cell_size = width // 5
        padding = 10
        img = Image.new('RGB', (width, height), (28, 28, 28)) # Dark grey background
        draw = ImageDraw.Draw(img)

        try:
            # Assumes a font file is available. If not, Pillow's default will be used.
            font_path = "assets/fonts/Roboto-Regular.ttf"
            title_font = ImageFont.truetype(font_path, 60) if os.path.exists(font_path) else ImageFont.load_default()
            task_font = ImageFont.truetype(font_path, 22) if os.path.exists(font_path) else ImageFont.load_default()
        except IOError:
            logger.warning("Font file not found. Falling back to default font.")
            title_font = ImageFont.load_default()
            task_font = ImageFont.load_default()

        # Title
        draw.text((width / 2, 40), "CLAN BINGO", font=title_font, fill="#FFD700", anchor="mt")

        for i, task in enumerate(tasks):
            row, col = i // 5, i % 5
            x0, y0 = col * cell_size, (row * cell_size) + 100
            x1, y1 = x0 + cell_size, y0 + cell_size

            # Draw cell with border
            draw.rectangle([x0, y0, x1, y1], outline="#4A4A4A", width=2)

            # Cell background color based on difficulty
            difficulty_colors = {"common": "#2E7D32", "uncommon": "#1565C0", "rare": "#C2185B"}
            cell_color = difficulty_colors.get(task.get('difficulty', 'common'), "#333333")
            draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], fill=cell_color)

            # Check if task is completed
            if task['name'] in completed_tasks:
                # Add a semi-transparent green overlay for completed tasks
                overlay = Image.new('RGBA', (cell_size, cell_size), (0, 255, 0, 100))
                img.paste(overlay, (x0, y0), overlay)
                # Draw a checkmark
                draw.text((x0 + cell_size - 30, y0 + 10), "✔", font=title_font, fill="#FFFFFF")

            # Wrap text and draw
            wrapped_text = textwrap.fill(task['name'], width=20)
            text_bbox = draw.textbbox((0, 0), wrapped_text, font=task_font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            text_x = x0 + (cell_size - text_width) / 2
            text_y = y0 + (cell_size - text_height) / 2
            draw.text((text_x, text_y), wrapped_text, font=task_font, fill="#FFFFFF", align="center")

        output_path = "bingo_board.png"
        img.save(output_path)
        return output_path, None
    except Exception as e:
        logger.error(f"Error during bingo image generation: {e}", exc_info=True)
        return None, f"Error during image generation: {e}"

async def generate_bingo_image(tasks: list, completed_tasks: list = []) -> tuple[str | None, str | None]:
    """Asynchronously generates the bingo image by running the sync function in a thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _generate_bingo_image_sync, tasks, completed_tasks)

async def update_bingo_board_post(bot):
    """Fetches the latest bingo data and updates the message with a new image."""
    async with bot.db_pool.acquire() as conn:
        event = await conn.fetchrow("SELECT * FROM bingo_events WHERE is_active = TRUE LIMIT 1")
        if not event: return

        completed_records = await conn.fetch("SELECT task_name FROM bingo_completed_tiles WHERE event_id = $1", event['id'])
        completed_tasks = [r['task_name'] for r in completed_records]

    board_tasks = json.loads(event['board_json'])
    image_path, error = await generate_bingo_image(board_tasks, completed_tasks)
    if error:
        logger.error(f"Failed to generate updated bingo image for event {event['id']}: {error}")
        return

    channel = bot.get_channel(config.BINGO_CHANNEL_ID)
    if not channel:
        logger.error(f"Bingo channel ID {config.BINGO_CHANNEL_ID} not found.")
        return

    try:
        message = await channel.fetch_message(event['message_id'])
        with open(image_path, 'rb') as f:
            new_file = discord.File(f, filename="bingo_board.png")
            embed = message.embeds[0]
            embed.set_image(url="attachment://bingo_board.png")
            await message.edit(embed=embed, attachments=[new_file])
        logger.info(f"Successfully updated bingo board for event {event['id']}.")
    except discord.NotFound:
        logger.warning(f"Could not find bingo message {event['message_id']} in channel {channel.id} to update.")
    except Exception as e:
        logger.error(f"Error updating bingo board post: {e}", exc_info=True)