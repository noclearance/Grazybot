# bot.py

import discord
from discord.ext import tasks, commands # Import commands
import os
from dotenv import load_dotenv
import aiohttp
from aiohttp import web
import asyncio
from datetime import datetime, timedelta, timezone
import random
import psycopg2
import psycopg2.extras
import json
import textwrap
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai
from io import BytesIO

# --- Configuration & Setup ---
load_dotenv()
TOKEN = os.getenv('TOKEN')
WOM_CLAN_ID = os.getenv('WOM_CLAN_ID')
WOM_VERIFICATION_CODE = os.getenv('WOM_VERIFICATION_CODE')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DEBUG_GUILD_ID = int(os.getenv('DEBUG_GUILD_ID', '0'))
DATABASE_URL = os.getenv('DATABASE_URL')
TASKS_FILE = "tasks.json"

# Channel IDs - Ensure default '0' is provided for robustness
SOTW_CHANNEL_ID = int(os.getenv('SOTW_CHANNEL_ID', '0'))
BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID', '0'))
RAFFLE_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID', '0'))
RECAP_CHANNEL_ID = int(os.getenv('RECAP_CHANNEL_ID', '0'))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID', '0'))

# Configure the Gemini AI (for text)
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-1.0-pro')
else:
    print("GEMINI_API_KEY not set. AI functionalities will be limited.")
    ai_model = None


# Define WOM skill metrics & Bot Intents
WOM_SKILLS = ["overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herblore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"]
intents = discord.Intents.default()
intents.members = True
bot = discord.Bot(intents=intents, debug_guilds=[DEBUG_GUILD_ID])
bot.active_polls = {}

# --- Database Setup (Simplified for this block) ---
# Assumes setup_database() is called elsewhere in your bot's startup
def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL environment variable not set.")
        return None
    try:
        conn = psycopg2.connect(db_url)
        return conn
    except Exception as e:
        print(f"Database connection failed: {e}")
        return None

# --- SOTW Poll View ---
class SotwPollView(discord.ui.View):
    def __init__(self, author):
        super().__init__(timeout=86400); self.author = author; self.votes = {};

    async def create_embed(self):
        ai_embed_data = await generate_announcement_json("sotw_poll")
        vote_description = "\n\n**Current Votes:**\n"
        for skill, voters in self.votes.items(): vote_description += f"**{skill.capitalize()}**: {len(voters)} vote(s)\n"

        embed = discord.Embed.from_dict(ai_embed_data)
        embed.description += vote_description
        embed.set_footer(text=f"Poll started by {self.author.display_name}", icon_url=self.author.display_avatar.url);
        return embed

    def add_buttons(self, skills):
        for skill in skills: self.votes[skill] = []; self.add_item(SotwButton(label=skill.capitalize(), custom_id=skill))
        self.add_item(FinishButton(label="Finish Poll & Start SOTW", custom_id="finish_poll"))

class SotwButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction):
        voted = False
        for skill_key, voters in self.view.votes.items():
            if interaction.user in voters:
                if skill_key == self.custom_id: voters.remove(interaction.user); voted = False
                else: voters.remove(interaction.user); self.view.votes[self.custom_id].append(interaction.user); voted = True
                break
        else: self.view.votes[self.custom_id].append(interaction.user); voted = True

        new_embed = await self.view.create_embed()
        await interaction.response.edit_message(embed=new_embed, view=self.view)

        if voted: await interaction.followup.send(f"Your vote for **{self.label}** has been counted.", ephemeral=True)
        else: await interaction.followup.send("Your vote has been removed.", ephemeral=True)

class FinishButton(discord.ui.Button):
    def __init__(self, label, custom_id): super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id=custom_id)
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.author.id: return await interaction.response.send_message("Only the poll starter can finish it.", ephemeral=True)
        view = self.view
        if not any(v for v in view.votes.values()): return await interaction.response.send_message("No votes cast yet.", ephemeral=True)
        winner = max(view.votes, key=lambda k: len(view.votes[k])); await interaction.response.defer(ephemeral=True)
        data, error = await create_competition(WOM_CLAN_ID, winner, 7)
        if error: await interaction.followup.send(f"Poll finished, but failed to start for **{winner.capitalize()}**: {error}", ephemeral=True); return

        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            embed = await create_competition_embed(data, interaction.user, poll_winner=True)
            sotw_message = await sotw_channel.send(embed=embed)
            await send_global_announcement("sotw_start", {"skill": winner.capitalize()}, sotw_message.jump_url)
            await interaction.followup.send("Competition created in the SOTW channel!", ephemeral=True)

        for item in view.children: item.disabled = True
        await interaction.message.edit(view=view)
        bot.active_polls.pop(interaction.guild.id, None)

# --- Bingo Submission View ---
class SubmissionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_submission")
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, task_name, bingo_id FROM bingo_submissions WHERE id = %s", (submission_id,))
        submission_data = cursor.fetchone()
        if not submission_data:
            conn.close()
            return await interaction.response.send_message("This submission was already handled.", ephemeral=True)

        user_id, task_name, bingo_id = submission_data

        cursor.execute("UPDATE bingo_submissions SET status = 'approved' WHERE id = %s", (submission_id,))
        cursor.execute("INSERT INTO bingo_completed_tiles (bingo_id, task_name) VALUES (%s, %s) ON CONFLICT (bingo_id, task_name) DO NOTHING", (bingo_id, task_name))
        conn.commit()
        cursor.close()
        conn.close()

        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} approved.", ephemeral=True)

        member = interaction.guild.get_member(user_id)
        if member:
            await award_points(member, 25, f"completing the bingo task: '{task_name}'")

        await update_bingo_board_post(bingo_id)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_submission")
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        submission_id = int(interaction.message.embeds[0].footer.text.split(": ")[1])

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE bingo_submissions SET status = 'denied' WHERE id = %s", (submission_id,))
        conn.commit()
        cursor.close()
        conn.close()

        await interaction.message.delete()
        await interaction.response.send_message(f"Submission #{submission_id} denied.", ephemeral=True)

# --- Helper Functions ---
async def award_points(member: discord.Member, amount: int, reason: str):
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
        print(f"Could not send DM to {member.display_name} (they may have DMs disabled).")
    except Exception as e:
        print(f"Failed to send points DM: {e}")

async def create_competition(clan_id: str, skill: str, duration_days: int):
    url = "https://api.wiseoldman.net/v2/competitions"
    start_date = datetime.now(timezone.utc) + timedelta(minutes=1); end_date = start_date + timedelta(days=duration_days)
    payload = {"title": f"{skill.capitalize()} SOTW ({duration_days} days)","metric": skill,"startsAt": start_date.isoformat(),"endsAt": end_date.isoformat(),"groupId": int(clan_id),"groupVerificationCode": WOM_VERIFICATION_CODE}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status == 201:
                comp_data = await response.json()
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO active_competitions (id, title, starts_at, ends_at) VALUES (%s, %s, %s, %s)", (comp_data['competition']['id'], comp_data['competition']['title'], comp_data['competition']['startsAt'], comp_data['competition']['endsAt']))
                conn.commit(); cursor.close(); conn.close()
                return comp_data, None
            else: return None, f"API Error: {(await response.json()).get('message', 'Failed to create competition.')}"

async def create_competition_embed(data, author, poll_winner=False):
    comp = data['competition']; comp_id = comp['id']

    details = {"skill": comp['metric'].capitalize()}
    ai_embed_data = await generate_announcement_json("sotw_start", details)

    embed = discord.Embed.from_dict(ai_embed_data)
    embed.url = f"https://wiseoldman.net/competitions/{comp_id}"
    start_dt = datetime.fromisoformat(comp['startsAt'].replace('Z', '+00:00')); end_dt = datetime.fromisoformat(comp['endsAt'].replace('Z', '+00:00'))
    embed.add_field(name="Skill", value=comp['metric'].capitalize(), inline=True); embed.add_field(name="Duration", value=f"{(end_dt - start_dt).days} days", inline=True); embed.add_field(name="\u200b", value="\u200b", inline=True); embed.add_field(name="Start Time", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True); embed.add_field(name="End Time", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
    embed.set_footer(text=f"Competition started by {author.display_name}", icon_url=author.display_avatar.url)
    return embed

async def generate_recap_text(gains_data: list) -> str:
    data_summary = ""
    for i, player in enumerate(gains_data[:10]):
        rank = i + 1; username = player['player']['displayName']; gained = player.get('gained', 0)
        data_summary += f"{rank}. {username}: {gained:,} XP\n"

    if ai_model:
        prompt = f"You are the Taskmaster for an Old School RuneScape clan. Your tone is formal and encouraging. Write a weekly recap based on the following data. Announce the top 3 with extra flair. Keep it to a few short paragraphs. Do not use emojis or markdown. Data:\n{data_summary}"
        try:
            response = await ai_model.generate_content_async(prompt); return response.text
        except Exception as e:
            print(f"An error occurred with the Gemini API: {e}"); return "The Taskmaster is currently reviewing the ledgers."
    else:
        return "Weekly recap unavailable (AI model not configured)."


async def generate_announcement_json(event_type: str, details: dict = None) -> dict:
    details = details or {}

    if ai_model:
        persona_prompt = """
        You are TaskmasterGPT, a clever and slightly cheeky Clan Discord events bot.
        Your task is to generate a JSON object for a Discord embed.
        The JSON must have the following keys: "title" (string), "description" (string), and "color" (integer).
        Maintain a confident but casual and epic tone when writing the text. Use Discord markdown like **bold** or *italics*. Do not use emojis.
        """

        if event_type == "sotw_poll":
            specific_prompt = "Generate an embed for a new Skill of the Week poll. The description should encourage members to vote."
            fallback = {"title": "📊 Skill of the Week Poll", "description": "The time has come to choose our next battleground! Cast your vote below.", "color": 15105600}
        elif event_type == "sotw_start":
            skill = details.get('skill', 'a new skill')
            specific_prompt = f"Generate an embed announcing the start of a Skill of the Week competition for the skill: **{skill}**."
            fallback = {"title": f"⚔️ SOTW Started: {skill}! ⚔️", "description": "The clan has spoken! The competition begins now. May the most dedicated warrior win!", "color": 5763719}
        elif event_type == "raffle_start":
            prize = details.get('prize', 'a grand prize')
            specific_prompt = f"Generate an embed for a new clan raffle. The prize is **{prize}**."
            fallback = {"title": "🎟️ A New Raffle has Begun!", "description": f"Fortune favors the bold! A new raffle has begun for a chance to win **{prize}**.", "color": 15844367}
        elif event_type == "bingo_start":
            specific_prompt = "Generate an embed announcing the start of a new clan bingo event."
            fallback = {"title": "🧩 A New Clan Bingo Has Started! 🧩", "description": "The Taskmaster has devised a new trial! A fresh board of challenges awaits. Let the games begin!", "color": 11027200}
        elif event_type == "points_award":
            amount = details.get('amount', 'a number of')
            reason = details.get('reason', 'your excellent performance')
            specific_prompt = f"Generate an embed for a private message notifying a member they have been awarded **{amount} Clan Points** for *{reason}*. Explain that points can be used for rewards like raffle tickets."
            fallback = {"title": "🏆 Points Awarded!", "description": f"You have been awarded **{amount} Clan Points** for *{reason}*! Clan Points are a measure of your dedication and can be used for rewards. Well done.", "color": 5763719}
        else:
            specific_prompt = f"Generate a generic announcement embed for event type: {event_type} with details: {details}"
            fallback = {"title": f"🎉 New {event_type.replace('_', ' ').title()}!", "description": "A new event has started!", "color": 3447003}

        full_prompt = f"{persona_prompt}\n\nRequest: {specific_prompt}\n\nJSON Output:"

        try:
            response = await ai_model.generate_content_async(full_prompt)
            clean_json_string = response.text.strip().lstrip("```json").rstrip("```")
            return json.loads(clean_json_string)
        except Exception as e:
            print(f"An error occurred during JSON generation: {e}")
            return fallback
    else:
        # Fallback when AI model is not configured
        if event_type == "sotw_poll":
            return {"title": "📊 Skill of the Week Poll", "description": "The time has come to choose our next battleground! Cast your vote below.", "color": 15105600}
        elif event_type == "sotw_start":
            skill = details.get('skill', 'a new skill')
            return {"title": f"⚔️ SOTW Started: {skill}! ⚔️", "description": "The clan has spoken! The competition begins now. May the most dedicated warrior win!", "color": 5763719}
        elif event_type == "raffle_start":
            prize = details.get('prize', 'a grand prize')
            return {"title": "🎟️ A New Raffle has Begun!", "description": f"Fortune favors the bold! A new raffle has begun for a chance to win **{prize}**.", "color": 15844367}
        elif event_type == "bingo_start":
            return {"title": "🧩 A New Clan Bingo Has Started! 🧩", "description": "The Taskmaster has devised a new trial! A fresh board of challenges awaits. Let the games begin!", "color": 11027200}
        elif event_type == "points_award":
            amount = details.get('amount', 'a number of')
            reason = details.get('reason', 'your excellent performance')
            return {"title": "🏆 Points Awarded!", "description": f"You have been awarded **{amount} Clan Points** for *{reason}*! Clan Points are a measure of your dedication and can be used for rewards. Well done.", "color": 5763719}
        else:
            return {"title": "🎉 New Event!", "description": "A new event has started!", "color": 3447003}


async def draw_raffle_winner(channel: discord.TextChannel, raffle_id: int):
    conn = get_db_connection()
    if conn is None:
        print("Database connection failed for draw_raffle_winner.")
        return "Error drawing raffle winner: Database connection failed."

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute("SELECT * FROM raffles WHERE id = %s AND winner_id IS NULL", (raffle_id,))
        raffle_data = cursor.fetchone()
        if not raffle_data:
            # This raffle might have already been drawn or doesn't exist
            cursor.close(); conn.close()
            return "Could not find the specified raffle to draw or it was already drawn."

        prize = raffle_data['prize']
        message_id = raffle_data['message_id'] # Retrieve message ID

        cursor.execute("SELECT user_id FROM raffle_entries WHERE raffle_id = %s", (raffle_id,))
        entries = cursor.fetchall()

        if not entries:
            await channel.send(f"The raffle for **{prize}** has ended, but unfortunately, no one entered.")
            cursor.execute("UPDATE raffles SET winner_id = 0 WHERE id = %s", (raffle_id,)) # Mark as processed even with no winner
        else:
            winner_id = random.choice(entries)['user_id']
            winner_user = await bot.fetch_user(winner_id)

            await award_points(winner_user, 50, f"winning the raffle for {prize}")

            embed = discord.Embed(title="🎉 Raffle Winner Announcement! 🎉", description=f"Congratulations to {winner_user.mention}! You have won the raffle for **{prize}**!", color=discord.Color.fuchsia())
            embed.add_field(name="Prize", value=f"**{prize}**", inline=False)
            embed.add_field(name="Bonus Reward", value="You have also been awarded **50 Clan Points**!", inline=False)
            embed.set_footer(text="Thanks to everyone for participating!")
            embed.set_thumbnail(url=winner_user.display_avatar.url)

            # Link to the original raffle message
            if message_id:
                 try:
                     original_message = await channel.fetch_message(message_id)
                     embed.add_field(name="Original Post", value=f"[View Raffle Post]({original_message.jump_url})", inline=False)
                 except discord.NotFound:
                     print(f"Original raffle message {message_id} not found.")
                 except Exception as e:
                     print(f"Error fetching original raffle message: {e}")


            await channel.send(content=f"Congratulations {winner_user.mention}!", embed=embed)
            cursor.execute("UPDATE raffles SET winner_id = %s WHERE id = %s", (winner_id, raffle_id))

        conn.commit()
        return f"Winner drawn for the '{prize}' raffle."
    except Exception as e:
        print(f"Error in draw_raffle_winner for raffle {raffle_id}: {e}")
        conn.rollback()
        return f"An error occurred while drawing the winner for the '{prize}' raffle."
    finally:
        cursor.close()
        conn.close()


def generate_bingo_image(tasks: list, completed_tasks: list = []):
    try:
        width, height = 1000, 1000
        background_color = (40, 26, 13) # Dark wood color
        img = Image.new('RGB', (width, height), background_color)
        draw = ImageDraw.Draw(img)

        # Use Pillow's built-in default font. It's guaranteed to work on any system.
        font = ImageFont.load_default()

        draw.text((width/2, 50), "CLAN BINGO", font=font, fill=(255, 215, 0), anchor="ms")

        grid_size = 5; cell_size = 170; margin = 50
        line_color = (255, 215, 0) # Gold color

        for i in range(grid_size + 1):
            # Draw vertical lines
            draw.line([(margin + i * cell_size, margin + 100), (margin + i * cell_size, height - margin)], fill=line_color, width=3)
            # Draw horizontal lines - Corrected end coordinate
            draw.line([(margin, margin + 100 + i * cell_size), (width - margin, margin + 100 + i * cell_size)], fill=line_color, width=3)


        for i, task in enumerate(tasks):
            if i >= 25: break
            row = i // grid_size; col = i % grid_size
            cell_x, cell_y = margin + col * cell_size, margin + 100 + row * cell_size

            if task['name'] in completed_tasks:
                overlay = Image.new('RGBA', (cell_size, cell_size), (0, 255, 0, 90))
                img.paste(overlay, (cell_x, cell_y), overlay)

            text_x = cell_x + (cell_size / 2); text_y = cell_y + (cell_size / 2)
            task_name = task['name']; wrapped_text = textwrap.fill(task_name, width=25) # Allow more width for default font
            draw.text((text_x, text_y), wrapped_text, font=font, fill=(255, 255, 255), anchor="mm", align="center")

        output_path = "bingo_board.png"; img.save(output_path)
        return output_path, None
    except Exception as e:
        print(f"An unexpected error occurred during image generation: {e}")
        return None, f"An unexpected error occurred during image generation: {e}"


async def update_bingo_board_post(bingo_id: int):
    conn = get_db_connection()
    if conn is None:
         print("Database connection failed for update_bingo_board_post.")
         return

    cursor = conn.cursor()
    try:
        cursor.execute("SELECT board_json, message_id FROM bingo_events WHERE id = %s", (bingo_id,))
        event_data = cursor.fetchone()
        if not event_data:
            cursor.close(); conn.close()
            return

        board_tasks = json.loads(event_data[0])
        message_id = event_data[1]

        cursor.execute("SELECT task_name FROM bingo_completed_tiles WHERE bingo_id = %s", (bingo_id,))
        completed_tiles = [row[0] for row in cursor.fetchall()]
        cursor.close(); conn.close()

        image_path, error = generate_bingo_image(board_tasks, completed_tiles)
        if error:
            print(f"Failed to update bingo board image: {error}")
            return

        try:
            bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
            if bingo_channel:
                message = await bingo_channel.fetch_message(message_id)
                with open(image_path, 'rb') as f:
                    new_file = discord.File(f, filename="bingo_board.png")
                    embed = message.embeds[0]
                    embed.set_image(url="attachment://bingo_board.png")
                    await message.edit(embed=embed, files=[new_file])
        except discord.NotFound:
            print(f"Could not find bingo message {message_id} to update.")
        except Exception as e:
            print(f"Error updating bingo board: {e}")
    except Exception as e:
        print(f"An unexpected error occurred in update_bingo_board_post: {e}")
    finally:
        if conn: conn.close()


async def send_global_announcement(event_type: str, details: dict, message_url: str):
    announcement_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    if not announcement_channel:
        print("Error: Global announcements channel not found.")
        return

    ai_embed_data = await generate_announcement_json(event_type, details)
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.url = message_url
    embed.add_field(name="Details", value=f"[Click here to view the event!]({message_url})")
    embed.set_footer(text="A new clan event has started!")

    await announcement_channel.send(content="@everyone", embed=embed)


# --- Event Manager Task ---
@tasks.loop(minutes=5)
async def event_manager():
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)
    print(f"Event manager running at {now}") # Debug print

    conn = get_db_connection()
    if conn is None:
        print("Skipping event manager due to database connection failure.")
        return

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    try:
        # Weekly Recap Logic (existing)
        recap_channel = bot.get_channel(RECAP_CHANNEL_ID)
        if recap_channel and now.weekday() == 6 and now.hour == 19 and now.minute < 5:
            url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/gained?period=week&metric=overall"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        recap_text = await generate_recap_text(data)
                        embed = discord.Embed(title="📈 Weekly Recap from the Taskmaster", description=recap_text, color=discord.Color.from_rgb(100, 150, 255))
                        embed.set_footer(text=f"Recap for the week ending {now.strftime('%B %d, %Y')}")
                        try:
                            await recap_channel.send(embed=embed)
                        except discord.Forbidden:
                            print(f"Missing permissions to send recap in {recap_channel.name}")
                        except Exception as e:
                            print(f"Error sending recap: {e}")


        # SOTW Logic (modified to include reminders)
        sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
        if sotw_channel:
            cursor.execute("SELECT * FROM active_competitions")
            competitions = cursor.fetchall()
            for comp in competitions:
                ends_at = comp['ends_at']; starts_at = comp['starts_at']

                # Award winners if competition ended and not yet awarded (existing)
                if now > ends_at and not comp['winners_awarded']:
                    details_url = f"https://api.wiseoldman.net/v2/competitions/{comp['id']}"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(details_url) as response:
                            if response.status == 200:
                                comp_data = await response.json()
                                point_values = [100, 50, 25] # 1st, 2nd, 3rd
                                guild = bot.get_guild(DEBUG_GUILD_ID)
                                if guild:
                                    for i, participant in enumerate(comp_data.get('participations', [])[:3]):
                                        osrs_name = participant['player']['displayName']
                                        conn_inner = get_db_connection()
                                        cursor_inner = conn_inner.cursor()
                                        cursor_inner.execute("SELECT discord_id FROM user_links WHERE osrs_name = %s", (osrs_name,))
                                        user_data = cursor_inner.fetchone()
                                        conn_inner.close()
                                        if user_data:
                                            member = guild.get_member(user_data[0])
                                            if member:
                                                await award_points(member, point_values[i], f"placing #{i+1} in the {comp['title']} SOTW")
                    cursor.execute("UPDATE active_competitions SET winners_awarded = TRUE WHERE id = %s", (comp['id'],))


                # Send final hour reminder
                if not comp['final_ping_sent'] and (ends_at - now) <= timedelta(hours=1) and now < ends_at:
                    print(f"Sending final hour reminder for SOTW ID: {comp['id']}") # Debug print
                    reminder_embed = discord.Embed(title="⏳ Final Hour!", description=f"The **{comp['title']}** competition ends in less than an hour!", color=discord.Color.red(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                    try:
                        await sotw_channel.send(content="@everyone", embed=reminder_embed)
                        cursor.execute("UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = %s", (comp['id'],))
                        conn.commit() # Commit after sending to avoid resending
                    except discord.Forbidden:
                        print(f"Missing permissions to send final SOTW reminder in {sotw_channel.name}")
                    except Exception as e:
                        print(f"Error sending final SOTW reminder: {e}")

                # Send midway reminder
                elif not comp['midway_ping_sent'] and now >= starts_at + ((ends_at - starts_at) / 2) and now < ends_at:
                    print(f"Sending midway reminder for SOTW ID: {comp['id']}") # Debug print
                    midway_embed = discord.Embed(title="½ Midway Point Reached!", description=f"The **{comp['title']}** competition is halfway through!", color=discord.Color.yellow(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                    try:
                        await sotw_channel.send(embed=midway_embed)
                        cursor.execute("UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = %s", (comp['id'],))
                        conn.commit() # Commit after sending to avoid resending
                    except discord.Forbidden:
                        print(f"Missing permissions to send midway SOTW reminder in {sotw_channel.name}")
                    except Exception as e:
                        print(f"Error sending midway SOTW reminder: {e}")

        # Raffle Logic (modified to trigger drawing and reminders)
        raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
        if raffle_channel:
            # Check for ended raffles and draw winners
            cursor.execute("SELECT id, message_id FROM raffles WHERE winner_id IS NULL AND ends_at <= %s", (now,))
            ended_raffles = cursor.fetchall()
            for raffle_data in ended_raffles:
                try:
                    print(f"Attempting to draw winner for raffle ID: {raffle_data['id']}") # Debug print
                    # Use the actual draw_raffle_winner function
                    await draw_raffle_winner(raffle_channel, raffle_data['id'])
                except Exception as e:
                    print(f"Error drawing raffle winner for raffle {raffle_data['id']}: {e}")


            # Add reminder logic for Raffles (Final Hour Reminder)
            cursor.execute("SELECT * FROM raffles WHERE winner_id IS NULL AND final_ping_sent IS FALSE AND ends_at > %s AND (ends_at - %s) <= INTERVAL '1 hour'", (now, now,))
            raffle_final_reminders = cursor.fetchall()
            for raffle_data in raffle_final_reminders:
                try:
                    print(f"Sending final hour reminder for Raffle ID: {raffle_data['id']}") # Debug print
                    reminder_embed = discord.Embed(title="⏳ Final Hour!", description=f"The raffle for **{raffle_data['prize']}** ends in less than an hour!", color=discord.Color.red())
                    # Link to the original raffle announcement message
                    if raffle_data['message_id']:
                         try:
                             original_message = await raffle_channel.fetch_message(raffle_data['message_id'])
                             reminder_embed.url = original_message.jump_url # Add link to the original message
                         except discord.NotFound:
                             print(f"Original raffle message {raffle_data['message_id']} not found for raffle {raffle_data['id']}")
                         except Exception as e:
                             print(f"Error fetching original raffle message for reminder: {e}")

                    await raffle_channel.send(content="@everyone", embed=reminder_embed)
                    # Mark reminder as sent
                    cursor.execute("UPDATE raffles SET final_ping_sent = TRUE WHERE id = %s", (raffle_data['id'],))
                    conn.commit() # Commit after sending to avoid resending
                except discord.Forbidden:
                    print(f"Missing permissions to send final raffle reminder in {raffle_channel.name}")
                    conn.rollback() # Rollback if send fails
                except Exception as e:
                    print(f"Error sending final raffle reminder for raffle {raffle_data['id']}: {e}")
                    conn.rollback()


        # Bingo Logic (modified to include reminders)
        bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
        if bingo_channel:
            cursor.execute("SELECT * FROM bingo_events WHERE ends_at <= NOW()")
            ended_bingo_events = cursor.fetchall()
            for bingo_data in ended_bingo_events:
                try:
                    # Placeholder for awarding points/roles for completed bingo events if needed
                    # ... logic to process ended bingo event ...
                    pass # No action needed here for reminders

                except Exception as e:
                    print(f"Error processing ended bingo event {bingo_data['id']}: {e}")


            # Add reminder logic for Bingo (Final Hour Reminder)
            cursor.execute("SELECT * FROM bingo_events WHERE final_ping_sent IS FALSE AND ends_at > %s AND (ends_at - %s) <= INTERVAL '1 hour'", (now, now,))
            bingo_final_reminders = cursor.fetchall()
            for bingo_data in bingo_final_reminders:
                 try:
                     print(f"Sending final hour reminder for Bingo ID: {bingo_data['id']}") # Debug print
                     reminder_embed = discord.Embed(title="⏳ Final Hour!", description=f"The current bingo event ends in less than an hour! Get your submissions in!", color=discord.Color.red())
                     # Fetch the original bingo board message to link to it
                     if bingo_data['message_id']:
                          try:
                              bingo_message = await bingo_channel.fetch_message(bingo_data['message_id'])
                              reminder_embed.url = bingo_message.jump_url # Add link to the original message
                          except discord.NotFound:
                             print(f"Bingo message {bingo_data['message_id']} not found for bingo event {bingo_data['id']}")
                          except Exception as e:
                             print(f"Error fetching original bingo message for reminder: {e}")

                     await bingo_channel.send(content="@everyone", embed=reminder_embed)
                     # Mark reminder as sent
                     cursor.execute("UPDATE bingo_events SET final_ping_sent = TRUE WHERE id = %s", (bingo_data['id'],))
                     conn.commit() # Commit after sending
                 except discord.Forbidden:
                    print(f"Missing permissions to send final bingo reminder in {bingo_channel.name}")
                    conn.rollback() # Rollback if send fails
                 except Exception as e:
                    print(f"Error sending final bingo reminder for bingo event {bingo_data['id']}: {e}")
                    conn.rollback()


    except Exception as e:
        print(f"An unexpected error occurred in event_manager: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


# --- Discord Bot Events and Startup ---

@bot.event
async def on_ready():
    """Logs when the bot is ready and starts the event manager task."""
    print(f'{bot.user} has connected to Discord!')
    print(f'Logged in as: {bot.user.name}')
    print(f'Bot ID: {bot.user.id}')

    # Start the event manager task
    if not event_manager.is_running():
        event_manager.start()
        print("Event manager task started.")

    # Add views that need to persist across restarts (like SubmissionView)
    # bot.add_view(SubmissionView()) # Uncomment and ensure SubmissionView is defined

@bot.event
async def on_reaction_add(reaction, user):
    """Handles reactions added to messages, specifically for raffle entries."""
    # Ignore bot's own reactions
    if user.bot:
        return

    # Check if the reaction is on a raffle message
    conn = get_db_connection()
    if conn is None:
        print("Database connection failed in on_reaction_add.")
        return

    cursor = conn.cursor()
    try:
        # Check if the message ID exists in the active raffles table
        cursor.execute("SELECT id, ends_at FROM raffles WHERE message_id = %s AND winner_id IS NULL", (reaction.message.id,))
        raffle_data = cursor.fetchone()

        if raffle_data:
            raffle_id = raffle_data[0]
            ends_at = raffle_data[1]
            now = datetime.now(timezone.utc)

            # Only allow entries before the raffle ends
            if now < ends_at:
                # Check if the user has already entered this raffle
                cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE raffle_id = %s AND user_id = %s", (raffle_id, user.id))
                entry_count = cursor.fetchone()[0]

                # Allow multiple entries, up to a limit (e.g., 10)
                MAX_ENTRIES_PER_USER = 10 # Define your desired limit
                if entry_count < MAX_ENTRIES_PER_USER:
                    cursor.execute("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES (%s, %s, 'reaction')", (raffle_id, user.id))
                    conn.commit()
                    print(f"User {user.id} entered raffle {raffle_id}.") # Debug print
                    # Optional: Provide feedback to the user via DM or ephemeral message
                    # try:
                    #     await user.send(f"You have successfully entered the raffle for **{reaction.message.embeds[0].fields[0].value}**! You now have {entry_count + 1} entries.")
                    # except discord.Forbidden:
                    #     print(f"Could not send raffle entry confirmation DM to {user.display_name}.")
                else:
                    print(f"User {user.id} tried to enter raffle {raffle_id} but reached the maximum entry limit.") # Debug print
                    # Optional: Remove their reaction or send ephemeral message
                    # try:
                    #      await reaction.message.remove_reaction(reaction.emoji, user)
                    # except discord.Forbidden:
                    #      print(f"Missing permissions to remove reaction for {user.display_name}.")
                    # await user.send("You have reached the maximum number of entries for this raffle.", ephemeral=True)


            else:
                 # Raffle has ended, remove reaction if added after end time
                 print(f"User {user.id} reacted to ended raffle {raffle_id}.") # Debug print
                 try:
                     await reaction.message.remove_reaction(reaction.emoji, user)
                 except discord.Forbidden:
                     print(f"Missing permissions to remove reaction for {user.display_name} on ended raffle.")
                 except Exception as e:
                     print(f"Error removing reaction from ended raffle: {e}")

    except Exception as e:
        print(f"An error occurred in on_reaction_add: {e}")
        conn.rollback()
    finally:
        if conn: conn.close()


@bot.event
async def on_command_error(ctx, error):
    """Handles command errors."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You don't have the necessary permissions to use this command.", ephemeral=True)
    elif isinstance(error, commands.MissingRequiredArgument):
         await ctx.respond(f"Missing required argument: {error.param}", ephemeral=True)
    elif isinstance(error, commands.CommandNotFound):
         pass # Ignore command not found errors
    else:
        print(f"Ignoring exception in command {ctx.command}:", error)
        # In a real bot, you'd want more robust logging
        await ctx.respond("An unexpected error occurred while running this command.", ephemeral=True)


@bot.event
async def on_error(event, *args, **kwargs):
    """Handles unexpected errors during bot operation."""
    print(f'Ignoring exception in {event}:', args, kwargs)
    # In a real bot, you'd want more robust logging

# --- Raffle Command ---
@bot.slash_command(name="raffle_start", description="Starts a raffle.")
async def raffle_start(ctx: discord.ApplicationContext, prize: str, duration: int):
    """Starts a raffle. Duration is in minutes."""
    # Ensure command is used in a guild
    if ctx.guild is None:
        await ctx.respond("This command can only be used in a server.", ephemeral=True)
        return

    # Ensure Raffle Channel is configured
    if RAFFLE_CHANNEL_ID == 0:
         await ctx.respond("The raffle channel is not configured.", ephemeral=True)
         return

    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if raffle_channel is None:
         await ctx.respond("Could not find the configured raffle channel.", ephemeral=True)
         return


    end_time = datetime.now(timezone.utc) + timedelta(minutes=duration)

    # Use AI to generate announcement embed
    ai_embed_data = await generate_announcement_json("raffle_start", {"prize": prize})
    embed = discord.Embed.from_dict(ai_embed_data)
    embed.add_field(name="Prize", value=prize, inline=False)
    embed.add_field(name="Ends", value=f"<t:{int(end_time.timestamp())}:F>", inline=False) # Use absolute time
    embed.add_field(name="Time Remaining", value=f"<t:{int(end_time.timestamp())}:R>", inline=False) # Use relative time
    embed.set_footer(text="React with 🎟️ to enter!")

    message = await raffle_channel.send(embed=embed) # Send to the designated raffle channel
    await message.add_reaction("🎟️")

    conn = get_db_connection()
    if conn is None:
        await ctx.respond("Failed to connect to database to save raffle.", ephemeral=True)
        # Consider deleting the message if DB save fails
        try: await message.delete()
        except: pass
        return

    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO raffles (prize, ends_at, message_id) VALUES (%s, %s, %s) RETURNING id",
            (prize, end_time, message.id)
        )
        raffle_id = cursor.fetchone()[0]
        conn.commit()
        await ctx.respond(f"Raffle for **{prize}** started in {raffle_channel.mention}!", ephemeral=True)
        print(f"Raffle {raffle_id} created with message ID {message.id}") # Debug print

        # Send global announcement
        await send_global_announcement("raffle_start", {"prize": prize}, message.jump_url)

    except Exception as e:
        print(f"Database error saving raffle: {e}")
        conn.rollback()
        await ctx.respond("An error occurred while saving the raffle to the database.", ephemeral=True)
        # Attempt to clean up the message if DB save failed
        try: await message.delete()
        except: pass
    finally:
        cursor.close()
        conn.close()


# --- Web Server to Keep Bot Alive ---
# This is useful for hosting environments like Heroku that require a web server.
# async def handler(request):
#     return web.Response(text="Bot is alive!")

# async def start_background_webserver():
#     runner = web.AppRunner(web.Application().router.add_get('/', handler).app)
#     await runner.setup()
#     site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 8080)))
#     await site.start()
#     print("Web server started.")


# --- Main Execution Block ---
# In your actual bot.py file, this is where you would typically call setup_database() and bot.run()

# Example (uncomment and adapt for your bot.py):
# if __name__ == "__main__":
#     print("Running database setup...")
#     setup_database() # Ensure database is set up on startup
#     print("Starting bot...")
#     # asyncio.run(start_background_webserver()) # Uncomment if you need the webserver
#     bot.run(TOKEN) # Replace TOKEN with your actual bot token variable