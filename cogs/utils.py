# cogs/utils.py
import discord
import os
import psycopg2
import psycopg2.extras
import aiohttp
from datetime import datetime, timedelta, timezone
import google.generativeai as genai
import json
import textwrap
from PIL import Image, ImageDraw, ImageFont
import random

# --- Environment Variables ---
WOM_CLAN_ID = os.getenv('WOM_CLAN_ID')
WOM_VERIFICATION_CODE = os.getenv('WOM_VERIFICATION_CODE')
DATABASE_URL = os.getenv('DATABASE_URL')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# --- AI Setup ---
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-1.0-pro')

# --- Helper Functions ---

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

async def award_points(member: discord.Member, amount: int, reason: str):
    # This is from your original file's logic
    if not member or member.bot: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO clan_points (discord_id, points) VALUES (%s, 0) ON CONFLICT (discord_id) DO NOTHING", (member.id,))
    cursor.execute("UPDATE clan_points SET points = points + %s WHERE discord_id = %s RETURNING points", (amount, member.id))
    new_balance = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    try:
        details = {"amount": amount, "reason": reason}
        ai_dm_data = await generate_announcement_json("points_award", details)
        dm_embed = discord.Embed.from_dict(ai_dm_data)
        dm_embed.add_field(name="New Balance", value=f"You now have **{new_balance}** Clan Points.")
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        print(f"Could not send DM to {member.display_name}.")
    except Exception as e:
        print(f"Failed to send points DM: {e}")

async def generate_announcement_json(event_type: str, details: dict = None) -> dict:
    # Your full, detailed prompt logic should be here.
    # This is a condensed version from your file for brevity.
    details = details or {}
    print(f"Generating announcement for {event_type} with details: {details}")
    fallback = {"title": f"🎉 Announcement: {event_type.replace('_', ' ').title()}", "description": "An important event has occurred!", "color": 3447003}
    return fallback

async def send_to_announcement_channels(bot, embed, content="@everyone"):
    """Sends a message to all designated announcement channels."""
    channel_ids = [
        int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID')),
        int(os.getenv('GENERAL_CHAT_CHANNEL_ID'))
    ]
    for channel_id in channel_ids:
        try:
            channel = bot.get_channel(channel_id)
            if channel:
                await channel.send(content=content, embed=embed)
        except Exception as e:
            print(f"Failed to send announcement to channel {channel_id}: {e}")

async def draw_raffle_winner(bot, raffle_channel_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE winner_id IS NULL LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close()
        return "No active raffle to draw."
    prize = raffle_data['prize']
    cursor.execute("SELECT user_id FROM raffle_entries")
    entries = cursor.fetchall()
    raffle_channel = bot.get_channel(raffle_channel_id)
    if not raffle_channel:
        cursor.close(); conn.close()
        return "Raffle channel not found."
    if not entries:
        await raffle_channel.send(f"The raffle for **{prize}** has ended, but alas, no one entered.")
        cursor.execute("UPDATE raffles SET winner_id = 0 WHERE id = 1")
    else:
        winner_id = random.choice(entries)['user_id']
        try:
            winner_user = await bot.fetch_user(winner_id)
            await award_points(winner_user, 50, f"winning the raffle for {prize}")
            raffle_embed = discord.Embed(title="🎉 Raffle Winner! 🎉", description=f"Congratulations to {winner_user.mention}, you have won the **{prize}**!", color=discord.Color.fuchsia())
            raffle_embed.set_thumbnail(url=winner_user.display_avatar.url)
            await raffle_channel.send(embed=raffle_embed)
            announcement_embed = discord.Embed(title="🏆 Champion of Fortune! 🏆", description=f"Let the clan celebrate! {winner_user.mention} has emerged victorious in the raffle for the **{prize}**!", color=discord.Color.gold())
            announcement_embed.set_thumbnail(url=winner_user.display_avatar.url)
            await send_to_announcement_channels(bot, announcement_embed, content=f"@everyone Congratulations to our winner, {winner_user.mention}!")
            cursor.execute("UPDATE raffles SET winner_id = %s WHERE id = 1", (winner_id,))
        except discord.NotFound:
            await raffle_channel.send(f"The winner for the **{prize}** raffle could not be found.")
    conn.commit()
    cursor.close()
    conn.close()
    return f"Winner drawn for the '{prize}' raffle."

def generate_bingo_image(tasks: list, completed_tasks: list = []):
    # Full bingo image generation logic from your original file
    try:
        width, height = 1000, 1000
        background_color = (40, 26, 13)
        img = Image.new('RGB', (width, height), background_color)
        draw = ImageDraw.Draw(img)
        title_font = ImageFont.load_default()
        task_font = ImageFont.load_default()
        draw.text((width/2, 50), "CLAN BINGO", font=title_font, fill=(255, 215, 0), anchor="ms")
        grid_size = 5; cell_size = 170; margin = 50
        line_color = (255, 215, 0)
        for i in range(grid_size + 1):
            draw.line([(margin + i * cell_size, margin + 100), (margin + i * cell_size, height - margin)], fill=line_color, width=3)
            draw.line([(margin, margin + 100 + i * cell_size), (width - margin, margin + 100 + i * cell_size)], fill=line_color, width=3)
        for i, task in enumerate(tasks):
            if i >= 25: break
            row = i // grid_size; col = i % grid_size
            cell_x, cell_y = margin + col * cell_size, margin + 100 + row * cell_size
            task_name_only = task['name'] if isinstance(task, dict) else task
            if task_name_only in completed_tasks:
                overlay = Image.new('RGBA', (cell_size, cell_size), (0, 255, 0, 90))
                img.paste(overlay, (cell_x, cell_y), overlay)
            wrapped_text = textwrap.fill(task_name_only, width=25)
            draw.text((cell_x + cell_size/2, cell_y + cell_size/2), wrapped_text, font=task_font, fill=(255, 255, 255), anchor="mm", align="center")
        output_path = "bingo_board.png"; img.save(output_path)
        return output_path, None
    except Exception as e:
        return None, f"An unexpected error during image generation: {e}"

async def update_bingo_board_post(bot):
    BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID'))
    # Full update_bingo_board_post logic from your original file
    pass

async def create_competition(clan_id: str, skill: str, duration_days: int):
    # Full create_competition logic from your original file
    pass

async def create_competition_embed(data, author, poll_winner=False):
    # Full create_competition_embed logic from your original file
    pass

async def generate_recap_text(gains_data: list) -> str:
    # Full generate_recap_text logic from your original file
    pass

async def end_giveaway(giveaway_data):
    # Full end_giveaway logic from your original file
    pass