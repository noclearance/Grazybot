# bot.py

import discord
from discord.ext import tasks
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
DEBUG_GUILD_ID = int(os.getenv('DEBUG_GUILD_ID'))
DATABASE_URL = os.getenv('DATABASE_URL')
TASKS_FILE = "tasks.json"

# Channel IDs
SOTW_CHANNEL_ID = int(os.getenv('SOTW_CHANNEL_ID'))
BINGO_CHANNEL_ID = int(os.getenv('BINGO_CHANNEL_ID'))
RAFFLE_CHANNEL_ID = int(os.getenv('RAFFLE_CHANNEL_ID'))
RECAP_CHANNEL_ID = int(os.getenv('RECAP_CHANNEL_ID'))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))

# Configure the Gemini AI (for text)
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-1.0-pro')

# Define WOM skill metrics & Bot Intents
WOM_SKILLS = ["overall", "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic", "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herblore", "agility", "thieving", "slayer", "farming", "runecraft", "hunter", "construction"]
intents = discord.Intents.default()
intents.members = True
bot = discord.Bot(intents=intents, debug_guilds=[DEBUG_GUILD_ID])
bot.active_polls = {}

# --- Database Setup ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def setup_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS active_competitions (
        id INTEGER PRIMARY KEY, title TEXT, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, 
        midway_ping_sent BOOLEAN DEFAULT FALSE, final_ping_sent BOOLEAN DEFAULT FALSE, winners_awarded BOOLEAN DEFAULT FALSE
    )""")
    cursor.execute("CREATE TABLE IF NOT EXISTS raffles (id SERIAL PRIMARY KEY, prize TEXT, ends_at TIMESTAMPTZ, winner_id BIGINT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS raffle_entries (entry_id SERIAL PRIMARY KEY, raffle_id INTEGER, user_id BIGINT NOT NULL, source TEXT DEFAULT 'self')")
    cursor.execute("CREATE TABLE IF NOT EXISTS bingo_events (id SERIAL PRIMARY KEY, ends_at TIMESTAMPTZ, board_json TEXT, message_id BIGINT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS bingo_submissions (id SERIAL PRIMARY KEY, user_id BIGINT, task_name TEXT, proof_url TEXT, status TEXT DEFAULT 'pending', bingo_id INTEGER)")
    cursor.execute("CREATE TABLE IF NOT EXISTS bingo_completed_tiles (bingo_id INTEGER, task_name TEXT, PRIMARY KEY (bingo_id, task_name))")
    cursor.execute("CREATE TABLE IF NOT EXISTS user_links (discord_id BIGINT PRIMARY KEY, osrs_name TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS clan_points (discord_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0)")
    conn.commit()
    cursor.close()
    conn.close()

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
    prompt = f"You are the Taskmaster for an Old School RuneScape clan. Your tone is formal and encouraging. Write a weekly recap based on the following data. Announce the top 3 with extra flair. Keep it to a few short paragraphs. Do not use emojis or markdown. Data:\n{data_summary}"
    try:
        response = await ai_model.generate_content_async(prompt); return response.text
    except Exception as e:
        print(f"An error occurred with the Gemini API: {e}"); return "The Taskmaster is currently reviewing the ledgers."

async def generate_announcement_json(event_type: str, details: dict = None) -> dict:
    details = details or {}
    
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
        return {"title": "🎉 New Event!", "description": "A new event has started!", "color": 3447003}

    full_prompt = f"{persona_prompt}\n\nRequest: {specific_prompt}\n\nJSON Output:"
    
    try:
        response = await ai_model.generate_content_async(full_prompt)
        clean_json_string = response.text.strip().lstrip("```json").rstrip("```")
        return json.loads(clean_json_string)
    except Exception as e:
        print(f"An error occurred during JSON generation: {e}")
        return fallback

async def draw_raffle_winner(channel: discord.TextChannel, raffle_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE id = %s", (raffle_id,))
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close()
        return "Could not find the specified raffle to draw."
    prize = raffle_data['prize']
    cursor.execute("SELECT user_id FROM raffle_entries WHERE raffle_id = %s", (raffle_id,))
    entries = cursor.fetchall()
    if not entries:
        await channel.send(f"The raffle for **{prize}** has ended, but unfortunately, no one entered.")
    else:
        winner_id = random.choice(entries)['user_id']
        winner_user = await bot.fetch_user(winner_id)
        
        await award_points(winner_user, 50, f"winning the raffle for {prize}")

        embed = discord.Embed(title="🎉 Raffle Winner Announcement! 🎉", description=f"Congratulations to {winner_user.mention}, you have won the raffle!", color=discord.Color.fuchsia())
        embed.add_field(name="Prize", value=f"**{prize}**", inline=False)
        embed.add_field(name="Bonus Reward", value="You have also been awarded **50 Clan Points**!", inline=False)
        embed.set_footer(text="Thanks to everyone for participating!")
        embed.set_thumbnail(url=winner_user.display_avatar.url)
        await channel.send(content=f"Congratulations {winner_user.mention}!", embed=embed)
        cursor.execute("UPDATE raffles SET winner_id = %s WHERE id = %s", (winner_id, raffle_id))
    conn.commit()
    cursor.close()
    conn.close()
    return f"Winner drawn for the '{prize}' raffle."

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
            draw.line([(margin + i * cell_size, margin + 100), (margin + i * cell_size, height - margin)], fill=line_color, width=3)
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
    cursor = conn.cursor()
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
                    await recap_channel.send(embed=embed)

    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if sotw_channel:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM active_competitions")
        competitions = cursor.fetchall()
        for comp in competitions:
            ends_at = comp['ends_at']; starts_at = comp['starts_at']
            
            if now > ends_at and not comp['winners_awarded']:
                details_url = f"https://api.wiseoldman.net/v2/competitions/{comp['id']}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(details_url) as response:
                        if response.status == 200:
                            comp_data = await response.json()
                            point_values = [100, 50, 25] # 1st, 2nd, 3rd
                            for i, participant in enumerate(comp_data.get('participations', [])[:3]):
                                osrs_name = participant['player']['displayName']
                                conn_inner = get_db_connection()
                                cursor_inner = conn_inner.cursor()
                                cursor_inner.execute("SELECT discord_id FROM user_links WHERE osrs_name = %s", (osrs_name,))
                                user_data = cursor_inner.fetchone()
                                conn_inner.close()
                                if user_data:
                                    member = bot.get_guild(DEBUG_GUILD_ID).get_member(user_data[0])
                                    if member:
                                        await award_points(member, point_values[i], f"placing #{i+1} in the {comp['title']} SOTW")
                cursor.execute("UPDATE active_competitions SET winners_awarded = TRUE WHERE id = %s", (comp['id'],))

            if not comp['final_ping_sent'] and (ends_at - now) <= timedelta(hours=1):
                reminder_embed = discord.Embed(title="⏳ Final Hour!", description=f"The **{comp['title']}** competition ends in less than an hour!", color=discord.Color.red(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                await sotw_channel.send(content="@everyone", embed=reminder_embed); cursor.execute("UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = %s", (comp['id'],))
            elif not comp['midway_ping_sent'] and now >= starts_at + ((ends_at - starts_at) / 2):
                midway_embed = discord.Embed(title="½ Midway Point Reached!", description=f"The **{comp['title']}** competition is halfway through!", color=discord.Color.yellow(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                await sotw_channel.send(embed=midway_embed); cursor.execute("UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = %s", (comp['id'],))
        conn.commit(); cursor.close(); conn.close()
    
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if raffle_channel:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM raffles WHERE winner_id IS NULL AND ends_at <= NOW()")
        ended_raffles = cursor.fetchall()
        for raffle_data in ended_raffles:
            await draw_raffle_winner(raffle_channel, raffle_data['id'])
        conn.commit(); cursor.close(); conn.close()

# --- Web Server for Hosting ---
async def handle_http(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        print(f"Web server started on port {port}")
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    print(f"{bot.user} is online and ready!")
    setup_database()
    event_manager.start()
    bot.add_view(SubmissionView())

# --- BOT COMMANDS ---
sotw = bot.create_group("sotw", "Commands for Skill of the Week")
@sotw.command(name="start", description="Manually start a new SOTW competition.")
async def start(ctx, skill: discord.Option(str, choices=WOM_SKILLS), duration_days: discord.Option(int, default=7)):
    await ctx.defer(ephemeral=True)
    data, error = await create_competition(WOM_CLAN_ID, skill, duration_days)
    if error: await ctx.respond(error); return
    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if sotw_channel:
        embed = await create_competition_embed(data, ctx.author)
        sotw_message = await sotw_channel.send(embed=embed)
        await send_global_announcement("sotw_start", {"skill": skill.capitalize()}, sotw_message.jump_url)
        await ctx.respond("SOTW started successfully in the designated channel!", ephemeral=True)
    else:
        await ctx.respond("Error: SOTW Channel ID not configured correctly.", ephemeral=True)

@sotw.command(name="poll", description="Start a poll to choose the next SOTW.")
@discord.default_permissions(manage_events=True)
async def poll(ctx: discord.ApplicationContext):
    if ctx.guild.id in bot.active_polls: return await ctx.respond("There is already an active SOTW poll.", ephemeral=True)
    poll_skills = random.sample(WOM_SKILLS, 6); view = SotwPollView(ctx.author); view.add_buttons(poll_skills)
    embed = await view.create_embed();
    sotw_channel = bot.get_channel(SOTW_CHANNEL_ID)
    if sotw_channel:
        poll_message = await sotw_channel.send(embed=embed, view=view)
        await ctx.respond("SOTW Poll created!", ephemeral=True); view.message_id = poll_message.id
        bot.active_polls[ctx.guild.id] = view
    else:
        await ctx.respond("Error: SOTW Channel ID not configured correctly.", ephemeral=True)

@sotw.command(name="view", description="View the leaderboard for the current SOTW.")
async def view(ctx: discord.ApplicationContext):
    await ctx.defer()
    list_url = f"https://api.wiseoldman.net/v2/groups/{WOM_CLAN_ID}/competitions"
    async with aiohttp.ClientSession() as session:
        async with session.get(list_url) as response:
            if response.status != 200: return await ctx.respond("Could not fetch competition list.")
            competitions = await response.json()
            if not competitions: return await ctx.respond("This clan has no competitions on Wise Old Man.")
            latest_comp_id = competitions[0]['id']
    details_url = f"https://api.wiseoldman.net/v2/competitions/{latest_comp_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(details_url) as response:
            if response.status != 200: return await ctx.respond(f"Could not fetch details for competition ID {latest_comp_id}.")
            data = await response.json()
    embed = discord.Embed(title=f"Leaderboard: {data['title']}", description=f"Current standings for the **{data['metric'].capitalize()}** competition.", color=discord.Color.purple(), url=f"https://wiseoldman.net/competitions/{data['id']}")
    leaderboard_text = ""
    for i, player in enumerate(data['participations'][:10]):
        rank_emoji = {1: "🏆", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
        leaderboard_text += f"{rank_emoji} **{player['player']['displayName']}**: {player['progress']['gained']:,} XP\n"
    if not leaderboard_text: leaderboard_text = "No participants have gained XP yet."
    embed.add_field(name="Top 10", value=leaderboard_text, inline=False)
    end_dt = datetime.fromisoformat(data['endsAt'].replace('Z', '+00:00'))
    embed.set_footer(text=f"Competition ends"); embed.timestamp = end_dt
    await ctx.respond(embed=embed)

raffle = bot.create_group("raffle", "Commands for managing raffles.")
@raffle.command(name="start", description="Start a new raffle.")
@discord.default_permissions(manage_events=True)
async def start_raffle(ctx: discord.ApplicationContext, prize: discord.Option(str, "What is the prize?"), duration_days: discord.Option(float, "How many days will it last?")):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor()
    ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    cursor.execute("INSERT INTO raffles (prize, ends_at) VALUES (%s, %s) RETURNING id", (prize, ends_at.isoformat()))
    raffle_id = cursor.fetchone()[0]
    conn.commit(); cursor.close(); conn.close()
    
    details = {"prize": prize}
    ai_embed_data = await generate_announcement_json("raffle_start", details)
    embed = discord.Embed.from_dict(ai_embed_data)
    
    embed.add_field(name="How to Enter", value="Use `/raffle enter` to get a ticket! (Max 10 per person)", inline=False)
    embed.add_field(name="Raffle Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
    embed.set_footer(text=f"Raffle ID: {raffle_id}")
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if raffle_channel:
        raffle_message = await raffle_channel.send(embed=embed)
        await send_global_announcement("raffle_start", {"prize": prize}, raffle_message.jump_url)
        await ctx.respond("Raffle created successfully!", ephemeral=True)
    else:
        await ctx.respond("Error: Raffle Channel ID not configured correctly.", ephemeral=True)

@raffle.command(name="enter", description="Get one ticket for the current raffle (max 10).")
async def enter_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close(); return await ctx.respond("There is no active raffle to enter right now.", ephemeral=True)
    
    raffle_id = raffle_data['id']
    cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND raffle_id = %s AND source = 'self'", (ctx.author.id, raffle_id))
    self_entries = cursor.fetchone()[0]
    if self_entries >= 10:
        cursor.close(); conn.close(); return await ctx.respond("You have already claimed your maximum of 10 tickets for this raffle!", ephemeral=True)
    
    cursor.execute("INSERT INTO raffle_entries (user_id, source, raffle_id) VALUES (%s, 'self', %s)", (ctx.author.id, raffle_id))
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND raffle_id = %s", (ctx.author.id, raffle_id))
    total_tickets = cursor.fetchone()[0]
    cursor.close(); conn.close()
    await ctx.respond(f"You have successfully claimed a ticket for the **{raffle_data['prize']}** raffle! You now have a total of {total_tickets} ticket(s).", ephemeral=True)

@raffle.command(name="give_tickets", description="ADMIN: Give raffle tickets to a member.")
@discord.default_permissions(manage_events=True)
async def give_tickets(ctx: discord.ApplicationContext, member: discord.Option(discord.Member, "The member to give tickets to."), amount: discord.Option(int, "How many tickets to give.", min_value=1)):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close(); return await ctx.respond("There is no active raffle.", ephemeral=True)
    
    raffle_id = raffle_data['id']
    entries = [(raffle_id, member.id, 'admin') for _ in range(amount)]
    cursor.executemany("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES (%s, %s, %s)", entries)
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND raffle_id = %s", (member.id, raffle_id))
    total_tickets = cursor.fetchone()[0]
    cursor.close(); conn.close()
    
    await ctx.respond(f"Successfully gave {amount} ticket(s) to {member.display_name} for the '{raffle_data['prize']}' raffle. They now have {total_tickets} ticket(s).", ephemeral=True)

@raffle.command(name="edit_tickets", description="ADMIN: Set a member's total ticket count for the active raffle.")
@discord.default_permissions(manage_events=True)
async def edit_tickets(
    ctx: discord.ApplicationContext,
    member: discord.Option(discord.Member, "The member whose tickets you want to edit."),
    new_total: discord.Option(int, "The new total number of tickets they should have.", min_value=0)
):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close(); return await ctx.respond("There is no active raffle.", ephemeral=True)
        
    raffle_id = raffle_data['id']
    cursor.execute("DELETE FROM raffle_entries WHERE user_id = %s AND raffle_id = %s", (member.id, raffle_id))
    
    if new_total > 0:
        entries = [(raffle_id, member.id, 'admin_edit') for _ in range(new_total)]
        cursor.executemany("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES (%s, %s, %s)", entries)
    
    conn.commit()
    cursor.close(); conn.close()
    
    await ctx.respond(f"Successfully set {member.display_name}'s ticket count to {new_total} for the '{raffle_data['prize']}' raffle.", ephemeral=True)

@raffle.command(name="view_tickets", description="View the current ticket count for all participants.")
async def view_tickets(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT prize FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close(); conn.close(); return await ctx.respond("There is no active raffle.")
    
    cursor.execute("SELECT user_id, COUNT(user_id) FROM raffle_entries GROUP BY user_id ORDER BY COUNT(user_id) DESC")
    entries = cursor.fetchall()
    cursor.close(); conn.close()

    embed = discord.Embed(title=f"🎟️ Raffle Tickets for '{raffle_data[0]}'", color=discord.Color.gold())
    if not entries:
        embed.description = "No tickets have been given out yet."
    else:
        description = ""
        for user_id, count in entries[:20]: # Show top 20
            try:
                member = await ctx.guild.fetch_member(user_id)
                description += f"**{member.display_name}**: {count} ticket(s)\n"
            except discord.NotFound:
                continue # Skip if member left the server
        embed.description = description
    
    await ctx.respond(embed=embed)

@raffle.command(name="draw_now", description="ADMIN: Immediately ends the raffle and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not channel: return await ctx.respond("Error: Raffle channel not found.")
    
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    cursor.close(); conn.close()
    if not raffle_data:
        return await ctx.respond("There is no active raffle to draw.", ephemeral=True)
        
    result = await draw_raffle_winner(channel, raffle_data['id'])
    await ctx.respond(f"Successfully triggered winner drawing: {result}")

@raffle.command(name="cancel", description="ADMIN: Cancels the current raffle without drawing a winner.")
@discord.default_permissions(manage_events=True)
async def cancel_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data: cursor.close(); conn.close(); return await ctx.respond("There is no active raffle to cancel.")
    
    raffle_id = raffle_data[0]
    prize = raffle_data[1]
    
    cursor.execute("DELETE FROM raffle_entries WHERE raffle_id = %s", (raffle_id,))
    cursor.execute("DELETE FROM raffles WHERE id = %s", (raffle_id,))
    conn.commit(); cursor.close(); conn.close()
    
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if channel: await channel.send(f"The raffle for **{prize}** has been cancelled by an admin.")
    await ctx.respond("Raffle successfully cancelled.")

events = bot.create_group("events", "View all active clan events.")
@events.command(name="view", description="Shows all currently active competitions and raffles.")
async def view_events(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
    competitions = cursor.fetchall()
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
    raffles = cursor.fetchall()
    cursor.close(); conn.close()
    
    embed = discord.Embed(title="📅 Clan Event Status", description="Here's a look at all the events currently running.", color=discord.Color.blurple())
    
    if competitions:
        comp_info = ""
        for comp in competitions:
            comp_ends_dt = comp['ends_at']
            comp_info += f"**Title:** [{comp['title']}](https://wiseoldman.net/competitions/{comp['id']})\n**Ends:** <t:{int(comp_ends_dt.timestamp())}:R>\n\n"
        embed.add_field(name="⚔️ Active Competitions", value=comp_info, inline=False)
    else:
        embed.add_field(name="⚔️ Active Competitions", value="There are no SOTW competitions currently running.", inline=False)
        
    if raffles:
        raffle_info = ""
        for raf in raffles:
            raf_ends_dt = raf['ends_at']
            raffle_info += f"**Prize:** {raf['prize']}\n**Ends:** <t:{int(raf_ends_dt.timestamp())}:R>\n\n"
        embed.add_field(name="🎟️ Active Raffles", value=raffle_info, inline=False)
    else:
        embed.add_field(name="🎟️ Active Raffles", value="There are no raffles currently running.", inline=False)
        
    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.respond(embed=embed)

bingo = bot.create_group("bingo", "Commands for clan bingo events.")
@bingo.command(name="start", description="Start a new bingo event.")
@discord.default_permissions(manage_events=True)
async def start_bingo(ctx: discord.ApplicationContext, duration_days: discord.Option(int, "How many days the bingo event will last.")):
    await ctx.defer(ephemeral=True)
    await ctx.respond("The Taskmaster is forging a new challenge... This may take a moment.", ephemeral=True)
    try:
        with open(TASKS_FILE, 'r') as f: all_tasks = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return await ctx.edit(content="Error: `tasks.json` not found or is invalid.")
    tasks_by_difficulty = {"common": [], "uncommon": [], "rare": []}
    for task in all_tasks: tasks_by_difficulty.setdefault(task['difficulty'], []).append(task)
    board_composition = {"common": 15, "uncommon": 7, "rare": 3}
    board_tasks = []
    for difficulty, count in board_composition.items():
        if len(tasks_by_difficulty.get(difficulty, [])) < count:
            return await ctx.edit(content=f"Error: Not enough '{difficulty}' tasks in `tasks.json`.")
        board_tasks.extend(random.sample(tasks_by_difficulty[difficulty], count))
    if len(board_tasks) < 25:
        return await ctx.edit(content="Error: Not enough tasks in total to create a 25-slot board.")
    random.shuffle(board_tasks); board_tasks = board_tasks[:25]
    
    image_path, error = generate_bingo_image(board_tasks)
    if error: return await ctx.edit(content=f"Failed to generate bingo image: {error}")
    
    bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
    if not bingo_channel: return await ctx.edit(content="Error: Bingo Channel ID not configured correctly.")
    
    ai_embed_data = await generate_announcement_json("bingo_start")
    embed = discord.Embed.from_dict(ai_embed_data)
    file = discord.File(image_path, filename="bingo_board.png")
    embed.set_image(url="attachment://bingo_board.png")
    ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
    embed.set_footer(text=f"Bingo started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    message = await bingo_channel.send(embed=embed, file=file)
    
    conn = get_db_connection(); cursor = conn.cursor()
    board_json = json.dumps(board_tasks)
    cursor.execute("INSERT INTO bingo_events (ends_at, board_json, message_id) VALUES (%s, %s, %s) RETURNING id", (ends_at, board_json, message.id))
    bingo_id = cursor.fetchone()[0]
    conn.commit(); cursor.close(); conn.close()
    
    await send_global_announcement("bingo_start", {}, message.jump_url)
    await ctx.edit(content=f"Bingo event #{bingo_id} created successfully!")

@bingo.command(name="complete", description="Submit a task for bingo completion.")
async def complete_task(ctx: discord.ApplicationContext, task: discord.Option(str, "The name of the task you completed."), proof: discord.Option(str, "A URL link to a screenshot or video proof.")):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
    event_data = cursor.fetchone()
    if not event_data:
        cursor.close(); conn.close(); return await ctx.respond("There is no active bingo event.", ephemeral=True)
    
    bingo_id = event_data['id']
    board_tasks = json.loads(event_data['board_json'])
    task_names = [t['name'] for t in board_tasks]
    if task not in task_names:
        cursor.close(); conn.close(); return await ctx.respond("That task is not on the current bingo board.", ephemeral=True)

    cursor.execute("INSERT INTO bingo_submissions (user_id, task_name, proof_url, bingo_id) VALUES (%s, %s, %s, %s)", (ctx.author.id, task, proof, bingo_id))
    conn.commit(); cursor.close(); conn.close()
    await ctx.respond("Your submission has been sent to the admins for review!", ephemeral=True)

@bingo.command(name="submissions", description="ADMIN: View pending bingo task submissions.")
@discord.default_permissions(manage_events=True)
async def view_submissions(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM bingo_submissions WHERE status = 'pending'")
    pending = cursor.fetchall()
    cursor.close(); conn.close()
    if not pending:
        return await ctx.respond("There are no pending bingo submissions.", ephemeral=True)
    
    await ctx.respond("Here are the pending submissions:", ephemeral=True)
    for sub in pending:
        user = await bot.fetch_user(sub['user_id'])
        embed = discord.Embed(title="📝 Bingo Submission", description=f"**Task:** {sub['task_name']}", color=discord.Color.yellow())
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.add_field(name="Proof", value=f"[Click to view]({sub['proof_url']})", inline=False)
        embed.set_footer(text=f"Submission ID: {sub['id']}")
        await ctx.channel.send(embed=embed, view=SubmissionView(), ephemeral=True)

@bingo.command(name="board", description="View the current bingo board.")
async def view_board(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT message_id FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
    event_data = cursor.fetchone()
    cursor.close(); conn.close()
    if not event_data or not event_data[0]:
        return await ctx.respond("There is no active bingo board to display.")
    
    bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
    if bingo_channel:
        try:
            message = await bingo_channel.fetch_message(event_data[0])
            await ctx.respond(f"Here is the current bingo board: {message.jump_url}")
        except discord.NotFound:
            await ctx.respond("Could not find the original bingo board message.")
    else:
        await ctx.respond("Bingo channel not configured.")

admin = bot.create_group("admin", "Admin-only commands for managing the bot and server.")
@admin.command(name="announce", description="Send a message as the bot to a specific channel.")
@discord.default_permissions(manage_guild=True)
async def announce(ctx: discord.ApplicationContext, message: discord.Option(str, "The message to send."), channel: discord.Option(discord.TextChannel, "The channel to send to."), ping_everyone: discord.Option(bool, "Whether to ping @everyone.", default=False)):
    await ctx.defer(ephemeral=True)
    content = "@everyone" if ping_everyone else ""
    embed = discord.Embed(title="📢 Clan Announcement", description=message, color=discord.Color.orange())
    embed.set_footer(text=f"Message sent by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    try:
        await channel.send(content=content, embed=embed)
        await ctx.respond("Announcement sent successfully!", ephemeral=True)
    except discord.Forbidden:
        await ctx.respond("Error: I don't have permission to send messages in that channel.", ephemeral=True)
    except Exception as e:
        await ctx.respond(f"An unexpected error occurred: {e}", ephemeral=True)

@admin.command(name="manage_points", description="Add or remove Clan Points from a member.")
@discord.default_permissions(manage_guild=True)
async def manage_points(
    ctx: discord.ApplicationContext,
    member: discord.Option(discord.Member, "The member to manage points for."),
    action: discord.Option(str, "Whether to add or remove points.", choices=["add", "remove"]),
    amount: discord.Option(int, "The number of points to add or remove.", min_value=1),
    reason: discord.Option(str, "The reason for this point adjustment.")
):
    await ctx.defer(ephemeral=True)
    
    if action == "add":
        await award_points(member, amount, reason)
    else: # remove
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO clan_points (discord_id, points) VALUES (%s, 0) ON CONFLICT (discord_id) DO NOTHING", (member.id,))
        cursor.execute("UPDATE clan_points SET points = GREATEST(0, points - %s) WHERE discord_id = %s", (amount, member.id))
        conn.commit()
    
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT points FROM clan_points WHERE discord_id = %s", (member.id,))
    new_balance = cursor.fetchone()[0]
    cursor.close(); conn.close()

    await ctx.respond(f"Successfully updated {member.display_name}'s points. Their new balance is {new_balance}.", ephemeral=True)

@admin.command(name="award_sotw_winners", description="Manually award points for a past SOTW competition.")
@discord.default_permissions(manage_guild=True)
async def award_sotw_winners(ctx: discord.ApplicationContext, competition_id: discord.Option(int, "The ID of the competition from Wise Old Man.")):
    await ctx.defer(ephemeral=True)
    details_url = f"https://api.wiseoldman.net/v2/competitions/{competition_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(details_url) as response:
            if response.status != 200:
                return await ctx.respond(f"Could not fetch details for competition ID {competition_id}.")
            comp_data = await response.json()

    awarded_to = []
    point_values = [100, 50, 25]
    for i, participant in enumerate(comp_data.get('participations', [])[:3]):
        osrs_name = participant['player']['displayName']
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT discord_id FROM user_links WHERE osrs_name = %s", (osrs_name,))
        user_data = cursor.fetchone()
        conn.close()
        if user_data:
            member = ctx.guild.get_member(user_data[0])
            if member:
                await award_points(member, point_values[i], f"placing #{i+1} in the {comp_data['title']} SOTW")
                awarded_to.append(f"#{i+1}: {member.display_name} ({point_values[i]} points)")

    if not awarded_to:
        return await ctx.respond("No winners could be found or linked for that competition.")

    await ctx.respond("Successfully awarded points to:\n" + "\n".join(awarded_to))

osrs = bot.create_group("osrs", "Commands related to your OSRS account.")
@osrs.command(name="link", description="Link your Discord account to your OSRS username.")
async def link(ctx: discord.ApplicationContext, username: discord.Option(str, "Your in-game RuneScape name.")):
    await ctx.defer(ephemeral=True)
    url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={username.replace(' ', '_')}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return await ctx.respond(f"Could not find '{username}' on the OSRS HiScores.", ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO user_links (discord_id, osrs_name) VALUES (%s, %s) ON CONFLICT (discord_id) DO UPDATE SET osrs_name = EXCLUDED.osrs_name", (ctx.author.id, username))
    conn.commit(); cursor.close(); conn.close()
    await ctx.respond(f"Success! Your Discord account has been linked to the OSRS name: **{username}**.", ephemeral=True)

points = bot.create_group("points", "Commands related to Clan Points.")
@points.command(name="view", description="Check your current Clan Point balance.")
async def view_points(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT points FROM clan_points WHERE discord_id = %s", (ctx.author.id,))
    point_data = cursor.fetchone()
    cursor.close(); conn.close()
    
    current_points = point_data[0] if point_data else 0
    await ctx.respond(f"You currently have **{current_points}** Clan Points.", ephemeral=True)

@points.command(name="leaderboard", description="View the Clan Points leaderboard.")
async def leaderboard(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT discord_id, points FROM clan_points ORDER BY points DESC LIMIT 10")
    leaders = cursor.fetchall()
    cursor.close(); conn.close()

    embed = discord.Embed(title="🏆 Clan Points Leaderboard 🏆", color=discord.Color.gold())
    if not leaders:
        embed.description = "No one has earned any points yet."
    else:
        leaderboard_text = ""
        for i, (user_id, points) in enumerate(leaders):
            rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
            try:
                member = await ctx.guild.fetch_member(user_id)
                leaderboard_text += f"{rank_emoji} **{member.display_name}**: {points:,} points\n"
            except discord.NotFound:
                continue
        embed.description = leaderboard_text
    
    await ctx.respond(embed=embed)

@bot.slash_command(name="help", description="Shows a list of all available commands.")
async def help(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    
    embed = discord.Embed(
        title="📜 GrazyBot Command List 📜",
        description="Here are all the commands you can use to manage clan events.",
        color=discord.Color.blurple()
    )

    member_commands = """
    `/sotw view` - View the leaderboard for the current Skill of the Week.
    `/raffle enter` - Get one ticket for the current raffle (max 10).
    `/raffle view_tickets` - See how many tickets everyone has.
    `/bingo board` - Get a link to the current bingo board.
    `/bingo complete` - Submit a task for bingo completion.
    `/points view` - Check your current Clan Point balance.
    `/points leaderboard` - View the Clan Points leaderboard.
    `/osrs link` - Link your Discord account to your OSRS name.
    `/events view` - See all currently active events.
    """
    
    admin_commands = """
    `/sotw start` - Manually start a new SOTW competition.
    `/sotw poll` - Start a poll to choose the next SOTW.
    `/raffle start` - Start a new raffle.
    `/raffle give_tickets` - Give raffle tickets to a member.
    `/raffle edit_tickets` - Set a member's total ticket count.
    `/raffle draw_now` - End the raffle and draw a winner immediately.
    `/raffle cancel` - Cancel the current raffle.
    `/bingo start` - Start a new clan bingo event.
    `/bingo submissions` - View and manage pending bingo submissions.
    `/admin announce` - Send a global announcement as the bot.
    `/admin manage_points` - Add or remove Clan Points from a member.
    `/admin award_sotw_winners` - Manually award points for a past SOTW.
    """
    
    embed.add_field(name="✅ Member Commands", value=textwrap.dedent(member_commands), inline=False)
    embed.add_field(name="👑 Admin Commands", value=textwrap.dedent(admin_commands), inline=False)
    embed.set_footer(text="Let the games begin!")
    
    await ctx.respond(embed=embed, ephemeral=True)

# --- Main Execution Block ---
async def run_bot():
    """A resilient function to start the bot and handle rate limits."""
    while True:
        try:
            await bot.start(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print("BOT is being rate-limited by Discord. Retrying in 5 minutes...")
                await asyncio.sleep(300) # Wait 5 minutes before trying to reconnect
            else:
                print(f"An unexpected HTTP error occurred with the bot: {e}")
                break # Exit on other HTTP errors
        except Exception as e:
            print(f"An unexpected error occurred while running the bot: {e}")
            break # Exit on other errors

async def main():
    web_task = asyncio.create_task(start_web_server())
    bot_task = asyncio.create_task(run_bot())
    await asyncio.gather(web_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())