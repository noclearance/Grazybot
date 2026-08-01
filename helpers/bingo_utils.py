# Utility functions specifically for the bingo cog.

import asyncio
import json
import os
import textwrap

import discord
from PIL import Image, ImageDraw, ImageFont

from config import BINGO_CHANNEL_ID


def _generate_bingo_image_sync(tasks: list, completed_tasks: list = None) -> tuple[str | None, str | None]:
    """Synchronous image generation, run in a worker thread."""
    if completed_tasks is None:
        completed_tasks = []
    try:
        width, height = 1000, 1000
        background_color = (40, 26, 13)
        img = Image.new("RGB", (width, height), background_color)
        draw = ImageDraw.Draw(img)

        font_dir = "fonts"
        default_font_path = os.path.join(font_dir, "Roboto-Regular.ttf")

        try:
            if os.path.exists(default_font_path):
                title_font = ImageFont.truetype(default_font_path, 60)
                task_font = ImageFont.truetype(default_font_path, 28)
            else:
                title_font = ImageFont.load_default()
                task_font = ImageFont.load_default()
        except Exception as e:
            print(f"Error loading custom font: {e}. Falling back to default font.")
            title_font = ImageFont.load_default()
            task_font = ImageFont.load_default()

        title_text = "CLAN BINGO"
        text_bbox = draw.textbbox((0, 0), title_text, font=title_font)
        title_width = text_bbox[2] - text_bbox[0]
        draw.text(((width - title_width) / 2, 20), title_text, font=title_font, fill=(255, 215, 0))

        grid_size = 5
        grid_top_y = 120
        grid_bottom_y = height - 50
        cell_size = (grid_bottom_y - grid_top_y) / grid_size
        effective_grid_width = grid_size * cell_size
        left_margin = (width - effective_grid_width) / 2
        line_color = (255, 215, 0)
        line_width = 3

        for i in range(grid_size + 1):
            draw.line(
                [(left_margin + i * cell_size, grid_top_y), (left_margin + i * cell_size, grid_bottom_y)],
                fill=line_color,
                width=line_width,
            )
            draw.line(
                [(left_margin, grid_top_y + i * cell_size), (left_margin + effective_grid_width, grid_top_y + i * cell_size)],
                fill=line_color,
                width=line_width,
            )

        for i, task in enumerate(tasks):
            if i >= grid_size * grid_size:
                break

            row = i // grid_size
            col = i % grid_size
            cell_x_start = int(left_margin + col * cell_size)
            cell_y_start = int(grid_top_y + row * cell_size)

            task_name = task["name"] if isinstance(task, dict) else str(task)

            if task_name in completed_tasks:
                overlay = Image.new("RGBA", (int(cell_size), int(cell_size)), (0, 255, 0, 90))
                img.paste(overlay, (cell_x_start, cell_y_start), overlay)

            avg_char_width_approx = 15
            max_chars_per_line = int((cell_size - 20) / avg_char_width_approx)
            if max_chars_per_line < 1:
                max_chars_per_line = 1

            wrapped_text = textwrap.fill(
                task_name, width=max_chars_per_line, break_long_words=False, replace_whitespace=False
            )
            lines_of_text = wrapped_text.split("\n")
            total_text_height = sum(
                task_font.getbbox(line)[3] - task_font.getbbox(line)[1] for line in lines_of_text
            )
            text_y_start = cell_y_start + (cell_size - total_text_height) / 2
            current_y = text_y_start
            for line in lines_of_text:
                text_bbox = draw.textbbox((0, 0), line, font=task_font)
                line_width_px = text_bbox[2] - text_bbox[0]
                line_height = text_bbox[3] - text_bbox[1]
                text_x = cell_x_start + (cell_size - line_width_px) / 2
                draw.text((text_x, current_y), line, font=task_font, fill=(255, 255, 255))
                current_y += line_height

        output_path = "bingo_board.png"
        img.save(output_path)
        return output_path, None
    except Exception as e:
        print(f"Error in generate_bingo_image: {e}")
        return None, f"An unexpected error occurred during image generation: {e}"


async def generate_bingo_image(tasks: list, completed_tasks: list = None) -> tuple[str | None, str | None]:
    """Asynchronously generates the bingo image in a thread."""
    if completed_tasks is None:
        completed_tasks = []
    return await asyncio.to_thread(_generate_bingo_image_sync, tasks, completed_tasks)


async def update_bingo_board_post(bot):
    """Fetches the latest bingo data and updates the Discord message image."""
    async with bot.db_pool.acquire() as conn:
        event = await conn.fetchrow("SELECT * FROM bingo_events WHERE is_active = TRUE LIMIT 1")
        if not event:
            return
        completed = [
            r["task_name"]
            for r in await conn.fetch(
                "SELECT task_name FROM bingo_completed_tiles WHERE event_id = $1", event["id"]
            )
        ]

    image_path, error = await generate_bingo_image(json.loads(event["board_json"]), completed)
    if error:
        print(f"Failed to update bingo board image: {error}")
        return

    channel = bot.get_channel(BINGO_CHANNEL_ID)
    if not channel:
        return
    try:
        message = await channel.fetch_message(event["message_id"])
        with open(image_path, "rb") as f:
            new_file = discord.File(f, filename="bingo_board.png")
            embed = message.embeds[0]
            embed.set_image(url="attachment://bingo_board.png")
            await message.edit(embed=embed, file=new_file)
    except discord.NotFound:
        print(f"Could not find bingo message {event['message_id']} to update.")
    except Exception as e:
        print(f"Error updating bingo board: {e}")
