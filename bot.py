
# bot.py 

````python
import discord
from discord.ext import commands
import asyncio
import random
import json
from datetime import datetime, timedelta, timezone
import psycopg2
import psycopg2.extras
import aiohttp
import textwrap
import os

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
RAFFLE_CHANNEL_ID = int(os.getenv("RAFFLE_CHANNEL_ID", "0"))
BINGO_CHANNEL_ID = int(os.getenv("BINGO_CHANNEL_ID", "0"))
TASKS_FILE = "tasks.json"

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Bot(intents=intents)

def get_db_connection():
    # Your DB connection function, unchanged
    return psycopg2.connect(os.getenv("DATABASE_URL"))

# --- Helper: generate_announcement_json (AI embed generator) ---
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
        # Here you would call your AI model, but since it's custom, fallback for now:
        # response = await ai_model.generate_content_async(full_prompt)
        # clean_json_string = response.text.strip().lstrip("```json").rstrip("```")
        # return json.loads(clean_json_string)
        return fallback
    except Exception as e:
        print(f"An error occurred during JSON generation: {e}")
        return fallback

# --- Helper: award_points ---
async def award_points(member: discord.Member, amount: int, reason: str):
    if not member or member.bot:
        return
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
        print(f"Could not send DM to {member.display_name} (may have DMs disabled).")
    except Exception as e:
        print(f"Failed to send points DM: {e}")

# --- Raffle commands group ---
raffle = bot.create_group("raffle", "Commands for clan raffles.")

@raffle.command(name="view_tickets", description="View the current ticket count for all participants.")
async def view_tickets(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT prize FROM raffles LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close()
        conn.close()
        return await ctx.respond("There is no active raffle.")
    cursor.execute("SELECT user_id, COUNT(user_id) FROM raffle_entries GROUP BY user_id ORDER BY COUNT(user_id) DESC")
    entries = cursor.fetchall()
    cursor.close()
    conn.close()
    embed = discord.Embed(title=f"🎟️ Raffle Tickets for '{raffle_data[0]}'", color=discord.Color.gold())
    if not entries:
        embed.description = "No tickets have been given out yet."
    else:
        description = ""
        for user_id, count in entries[:20]:
            try:
                member = await ctx.guild.fetch_member(user_id)
                description += f"**{member.display_name}**: {count} ticket(s)\n"
            except discord.NotFound:
                continue
        embed.description = description
    await ctx.respond(embed=embed)

@raffle.command(name="draw_now", description="ADMIN: Immediately ends the raffle and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not channel:
        return await ctx.respond("Error: Raffle channel not found.")
    result = await draw_raffle_winner(channel)
    await ctx.respond(f"Successfully triggered winner drawing: {result}")

@raffle.command(name="cancel", description="ADMIN: Cancels the current raffle without drawing a winner.")
@discord.default_permissions(manage_events=True)
async def cancel_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT prize FROM raffles LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close()
        conn.close()
        return await ctx.respond("There is no active raffle to cancel.")
    prize = raffle_data[0]
    # Here is the fix: only delete the raffle and raffle_entries for this raffle, not ALL events
    cursor.execute("DELETE FROM raffles WHERE prize = %s", (prize,))
    cursor.execute("DELETE FROM raffle_entries WHERE raffle_id NOT IN (SELECT id FROM raffles)")
    conn.commit()
    cursor.close()
    conn.close()
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if channel:
        await channel.send(f"The raffle for **{prize}** has been cancelled by an admin.")
    await ctx.respond("Raffle successfully cancelled.")

# --- Events group ---
events = bot.create_group("events", "View all active clan events.")

@events.command(name="view", description="Shows all currently active competitions and raffles.")
async def view_events(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Fetch all active competitions (not just 1)
    cursor.execute("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at ASC")
    competitions = cursor.fetchall()

    # Fetch all active raffles (not just 1)
    cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC")
    raffles = cursor.fetchall()

    cursor.close()
    conn.close()

    embed = discord.Embed(title="📅 Clan Event Status", description="Here's a look at all the events currently running.", color=discord.Color.blurple())

    if competitions:
        for comp in competitions:
            comp_ends_dt = comp['ends_at']
            comp_info = (f"**Title:** [{comp['title']}](https://wiseoldman.net/competitions/{comp['id']})\n"
                         f"**Ends:** <t:{int(comp_ends_dt.timestamp())}:R>")
            embed.add_field(name="⚔️ Active Competition", value=comp_info, inline=False)
    else:
        embed.add_field(name="⚔️ Active Competition", value="There is no SOTW competition currently running.", inline=False)

    if raffles:
        for raf in raffles:
            raf_ends_dt = raf['ends_at']
            raf_info = (f"**Prize:** {raf['prize']}\n"
                        f"**Ends:** <t:{int(raf_ends_dt.timestamp())}:R>")
            embed.add_field(name="🎟️ Active Raffle", value=raf_info, inline=False)
    else:
        embed.add_field(name="🎟️ Active Raffle", value="There is no raffle currently running.", inline=False)

    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.respond(embed=embed)

# --- Bingo group ---
bingo = bot.create_group("bingo", "Commands for clan bingo events.")

@bingo.command(name="start", description="Start a new bingo event.")
@discord.default_permissions(manage_events=True)
async def start_bingo(ctx: discord.ApplicationContext, duration_days: discord.Option(int, "How many days the bingo event will last.")):
    await ctx.defer(ephemeral=True)
    await ctx.respond("The Taskmaster is forging a new challenge... This may take a moment.", ephemeral=True)
    try:
        with open(TASKS_FILE, 'r') as f:
            all_tasks = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return await ctx.edit(content="Error: `tasks.json` not found or is invalid.")

    tasks_by_difficulty = {"common": [], "uncommon": [], "rare": []}
    for task in all_tasks:
        tasks_by_difficulty.setdefault(task['difficulty'], []).append(task)

    board_composition = {"common": 15, "uncommon": 7, "rare": 3}
    board_tasks = []

    for difficulty, count in board_composition.items():
        if len(tasks_by_difficulty.get(difficulty, [])) < count:
            return await ctx.edit(content=f"Error: Not enough '{difficulty}' tasks in `tasks.json`.")
        board_tasks.extend(random.sample(tasks_by_difficulty[difficulty], count))

    if len(board_tasks) < 25:
        return await ctx.edit(content="Error: Not enough tasks in total to create a 25-slot board.")

    random.shuffle(board_tasks)
    board_tasks = board_tasks[:25]

    # Fix: DO NOT delete all bingo events to allow multiple active bingos.
    # Instead, insert new bingo event with unique id.
    conn = get_db_connection()
    cursor = conn.cursor()

    ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    board_json = json.dumps(board_tasks)

    image_path, error = generate_bingo_image(board_tasks)
    if error:
        cursor.close()
        conn.close()
        return await ctx.edit(content=f"Failed to generate bingo image: {error}")

    bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
    if not bingo_channel:
        cursor.close()
        conn.close()
        return await ctx.edit(content="Error: Bingo Channel ID not configured correctly.")

    ai_embed_data = await generate_announcement_json("bingo_start")
    embed = discord.Embed.from_dict(ai_embed_data)
    file = discord.File(image_path, filename="bingo_board.png")
    embed.set_image(url="attachment://bingo_board.png")
    embed.add_field(name="Event Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
    embed.set_footer(text=f"Bingo started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    message = await bingo_channel.send(embed=embed, file=file)

    # Insert new bingo event (auto increment ID assumed)
    cursor.execute("INSERT INTO bingo_events (ends_at, board_json, message_id) VALUES (%s, %s, %s)", (ends_at, board_json, message.id))
    conn.commit()
    cursor.close()
    conn.close()

    await send_global_announcement("bingo_start", {}, message.jump_url)
    await ctx.edit(content="Bingo event created successfully!")

@bingo.command(name="board", description="View the current bingo board.")
async def view_board(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Fix: fetch the latest active bingo event (not just 1)
    cursor.execute("SELECT * FROM bingo_events WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
    event_data = cursor.fetchone()

    cursor.close()
    conn.close()

    if not event_data or not event_data['message_id']:
        return await ctx.respond("There is no active bingo board to display.")

    bingo_channel = bot.get_channel(BINGO_CHANNEL_ID)
    if bingo_channel:
        try:
            message = await bingo_channel.fetch_message(event_data['message_id'])
            await ctx.respond(f"Here is the current bingo board: {message.jump_url}")
        except discord.NotFound:
            await ctx.respond("Could not find the original bingo board message.")
    else:
        await ctx.respond("Bingo channel not configured.")

# Add more bingo commands with similar fixes as needed...
# --- Raffle group ---
raffle = bot.create_group("raffle", "Commands for clan raffles.")

@raffle.command(name="view_tickets", description="View the current ticket count for all participants.")
async def view_tickets(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT prize FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close()
        conn.close()
        return await ctx.respond("There is no active raffle.")

    cursor.execute("SELECT user_id, COUNT(user_id) FROM raffle_entries GROUP BY user_id ORDER BY COUNT(user_id) DESC")
    entries = cursor.fetchall()
    cursor.close()
    conn.close()

    embed = discord.Embed(title=f"🎟️ Raffle Tickets for '{raffle_data[0]}'", color=discord.Color.gold())
    if not entries:
        embed.description = "No tickets have been given out yet."
    else:
        description = ""
        for user_id, count in entries[:20]:  # Show top 20
            try:
                member = await ctx.guild.fetch_member(user_id)
                description += f"**{member.display_name}**: {count} ticket(s)\n"
            except discord.NotFound:
                continue  # Skip if member left the server
        embed.description = description

    await ctx.respond(embed=embed)

@raffle.command(name="draw_now", description="ADMIN: Immediately ends the raffle and draws a winner.")
@discord.default_permissions(manage_events=True)
async def draw_now(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not channel:
        return await ctx.respond("Error: Raffle channel not found.")

    result = await draw_raffle_winner(channel)
    await ctx.respond(f"Successfully triggered winner drawing: {result}", ephemeral=True)

@raffle.command(name="cancel", description="ADMIN: Cancels the current raffle without drawing a winner.")
@discord.default_permissions(manage_events=True)
async def cancel_raffle(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT prize FROM raffles WHERE ends_at > NOW() ORDER BY ends_at ASC LIMIT 1")
    raffle_data = cursor.fetchone()
    if not raffle_data:
        cursor.close()
        conn.close()
        return await ctx.respond("There is no active raffle to cancel.")

    prize = raffle_data[0]

    # Fix: delete only raffles and entries that are active (not all)
    cursor.execute("DELETE FROM raffles WHERE ends_at > NOW()")
    cursor.execute("DELETE FROM raffle_entries WHERE raffle_id NOT IN (SELECT id FROM raffles)")
    conn.commit()
    cursor.close()
    conn.close()

    channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if channel:
        await channel.send(f"The raffle for **{prize}** has been cancelled by an admin.")

    await ctx.respond("Raffle successfully cancelled.", ephemeral=True)

# --- Admin group ---
admin = bot.create_group("admin", "Admin-only commands for managing the bot and server.")

@admin.command(name="announce", description="Send a message as the bot to a specific channel.")
@discord.default_permissions(manage_guild=True)
async def announce(
    ctx: discord.ApplicationContext,
    message: discord.Option(str, "The message to send."),
    channel: discord.Option(discord.TextChannel, "The channel to send to."),
    ping_everyone: discord.Option(bool, "Whether to ping @everyone.", default=False)
):
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
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO clan_points (discord_id, points) VALUES (%s, 0) ON CONFLICT (discord_id) DO NOTHING", (member.id,))
        cursor.execute("UPDATE clan_points SET points = GREATEST(0, points - %s) WHERE discord_id = %s", (amount, member.id))
        conn.commit()
        cursor.close()
        conn.close()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT points FROM clan_points WHERE discord_id = %s", (member.id,))
    new_balance = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    await ctx.respond(f"Successfully updated {member.display_name}'s points. Their new balance is {new_balance}.", ephemeral=True)

@admin.command(name="award_sotw_winners", description="Manually award points for a past SOTW competition.")
@discord.default_permissions(manage_guild=True)
async def award_sotw_winners(ctx: discord.ApplicationContext, competition_id: discord.Option(int, "The ID of the competition from Wise Old Man.")):
    await ctx.defer(ephemeral=True)
    details_url = f"https://api.wiseoldman.net/v2/competitions/{competition_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(details_url) as response:
            if response.status != 200:
                return await ctx.respond(f"Could not fetch details for competition ID {competition_id}.", ephemeral=True)
            comp_data = await response.json()

    awarded_to = []
    point_values = [100, 50, 25]

    for i, participant in enumerate(comp_data.get('participations', [])[:3]):
        osrs_name = participant['player']['displayName']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT discord_id FROM user_links WHERE osrs_name = %s", (osrs_name,))
        user_data = cursor.fetchone()
        conn.close()
        if user_data:
            member = ctx.guild.get_member(user_data[0])
            if member:
                await award_points(member, point_values[i], f"placing #{i+1} in the {comp_data['title']} SOTW")
                awarded_to.append(f"#{i+1}: {member.display_name} ({point_values[i]} points)")

    if not awarded_to:
        return await ctx.respond("No winners could be found or linked for that competition.", ephemeral=True)

    await ctx.respond("Successfully awarded points to:\n" + "\n".join(awarded_to), ephemeral=True)

# --- OSRS group ---
osrs = bot.create_group("osrs", "Commands related to your OSRS account.")

@osrs.command(name="link", description="Link your Discord account to your OSRS username.")
async def link(ctx: discord.ApplicationContext, username: discord.Option(str, "Your in-game RuneScape name.")):
    await ctx.defer(ephemeral=True)
    url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={username.replace(' ', '_')}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return await ctx.respond(f"Could not find '{username}' on the OSRS HiScores.", ephemeral=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO user_links (discord_id, osrs_name) VALUES (%s, %s) ON CONFLICT (discord_id) DO UPDATE SET osrs_name = EXCLUDED.osrs_name", (ctx.author.id, username))
    conn.commit()
    cursor.close()
    conn.close()
    await ctx.respond(f"Success! Your Discord account has been linked to the OSRS name: **{username}**.", ephemeral=True)

# --- Points group ---
points = bot.create_group("points", "Commands related to Clan Points.")

@points.command(name="view", description="Check your current Clan Point balance.")
async def view_points(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT points FROM clan_points WHERE discord_id = %s", (ctx.author.id,))
    point_data = cursor.fetchone()
    cursor.close()
    conn.close()

    current_points = point_data[0] if point_data else 0
    await ctx.respond(f"You currently have **{current_points}** Clan Points.", ephemeral=True)

@points.command(name="leaderboard", description="View the Clan Points leaderboard.")
async def leaderboard(ctx: discord.ApplicationContext):
    await ctx.defer()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT discord_id, points FROM clan_points ORDER BY points DESC LIMIT 10")
    leaders = cursor.fetchall()
    cursor.close()
    conn.close()

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
# --- Event helpers and background tasks ---

async def load_active_events():
    """Load all currently active events from the database into memory on startup."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM events WHERE ends_at > NOW()")
    events = cursor.fetchall()
    cursor.close()
    conn.close()
    for event in events:
        event_manager.add_event(event)
    print(f"Loaded {len(events)} active events from database.")

async def event_loop():
    """Background task that checks active events and triggers actions on expiry."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await event_manager.check_and_handle_expired_events()
        await asyncio.sleep(30)  # Adjust frequency as needed

# --- Custom Help Command ---

@bot.command(name="help")
async def help_command(ctx: discord.ApplicationContext):
    embed = discord.Embed(title="TaskmasterGPT Commands", color=discord.Color.blurple())
    embed.description = (
        "Here are some commands to keep your clan running smoothly:\n\n"
        "**/sotw start [skill]** - Start a new Skill of the Week event.\n"
        "**/raffle start [prize]** - Create a new raffle.\n"
        "**/raffle draw_now** - Draw the winner of the active raffle.\n"
        "**/bingo start** - Start a new Bingo event.\n"
        "**/points view** - Check your Clan Points balance.\n"
        "**/points leaderboard** - View the top Clan Points holders.\n"
        "**/osrs link [username]** - Link your OSRS account to your Discord.\n"
        "**/admin announce** - Send a server announcement.\n"
        "\nUse `/help [command]` for more details on a specific command."
    )
    await ctx.respond(embed=embed, ephemeral=True)

# --- Startup and Run ---

async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await load_active_events()
    bot.loop.create_task(event_loop())

bot.event(on_ready)

# --- Main Execution ---

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        sys.exit(1)
    bot.run(TOKEN)
