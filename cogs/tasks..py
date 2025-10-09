# bot/cogs/tasks.py
# Contains background tasks that run periodically.

from discord.ext import tasks, commands
from datetime import datetime, timezone, timedelta
import discord
import asyncio

from bot.config import RECAP_CHANNEL_ID, SOTW_CHANNEL_ID, ANNOUNCEMENTS_CHANNEL_ID, PVM_EVENT_CHANNEL_ID
from bot.helpers.ai import generate_recap_text
from bot.helpers.giveaway_utils import end_giveaway
from bot.helpers.raffle_utils import draw_raffle_winner
from bot.helpers.utils import award_points
from bot.helpers.wom import get_weekly_gains, get_competition_details

class BackgroundTasks(commands.Cog):
    """Cog for running background tasks like event management and reminders."""
    
    def __init__(self, bot):
        self.bot = bot
        self.event_manager.start()
        self.periodic_event_reminder.start()

    def cog_unload(self):
        self.event_manager.cancel()
        self.periodic_event_reminder.cancel()

    @tasks.loop(minutes=5)
    async def event_manager(self):
        """
        The main loop that manages the lifecycle of all events:
        - SOTW winner awarding and reminders
        - Raffle drawing
        - Giveaway ending and entry count updates
        - PVM event reminders
        - Weekly recaps
        """
        now = datetime.now(timezone.utc)
        
        # --- Weekly Recap (Runs on Sundays after 19:00 UTC) ---
        async with self.bot.db_pool.acquire() as conn:
            last_recap_str = await conn.fetchval("SELECT value FROM bot_settings WHERE key = 'last_recap_sent'")
            last_recap_dt = datetime.fromisoformat(last_recap_str)
            
            # Check if it's Sunday and past the trigger time, and if a recap hasn't been sent for this week
            if now.weekday() == 6 and now.hour >= 19 and (now - last_recap_dt) > timedelta(days=6):
                recap_channel = self.bot.get_channel(RECAP_CHANNEL_ID)
                if recap_channel:
                    gains_data, error = await get_weekly_gains()
                    if not error and gains_data:
                        recap_text = await generate_recap_text(gains_data)
                        embed = discord.Embed(title="Weekly Recap from the Taskmaster", description=recap_text, color=discord.Color.blue())
                        await recap_channel.send(embed=embed)
                        await conn.execute("UPDATE bot_settings SET value = $1 WHERE key = 'last_recap_sent'", now.isoformat())

        # --- SOTW, Raffle, Giveaway, PVM Processing ---
        await self.process_sotw(now)
        await self.process_raffles(now)
        await self.process_giveaways(now)
        await self.process_pvm_events(now)

    async def process_sotw(self, now):
        sotw_channel = self.bot.get_channel(SOTW_CHANNEL_ID)
        if not sotw_channel: return

        async with self.bot.db_pool.acquire() as conn:
            comps = await conn.fetch("SELECT * FROM active_competitions")
            for comp in comps:
                ends_at = comp['ends_at']
                
                # Award Winners if competition ended and winners not awarded
                if now > ends_at and not comp['winners_awarded']:
                    details, error = await get_competition_details(comp['id'])
                    if not error:
                        point_values = [100, 50, 25]
                        for i, p in enumerate(details.get('participations', [])[:3]):
                            user_id = await conn.fetchval("SELECT discord_id FROM user_links WHERE osrs_name = $1", p['player']['displayName'])
                            if user_id and (member := sotw_channel.guild.get_member(user_id)):
                                await award_points(self.bot, member, point_values[i], f"placing #{i+1} in SOTW")
                    await conn.execute("UPDATE active_competitions SET winners_awarded = TRUE WHERE id = $1", comp['id'])

                # Send reminders
                if not comp['final_ping_sent'] and (ends_at - now) <= timedelta(hours=1):
                    await sotw_channel.send(f"@everyone Final hour of the **{comp['title']}** competition!")
                    await conn.execute("UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = $1", comp['id'])

    async def process_raffles(self, now):
        async with self.bot.db_pool.acquire() as conn:
            ended_raffles = await conn.fetch("SELECT id FROM raffles WHERE ends_at < $1 AND winner_id IS NULL", now)
            for raffle in ended_raffles:
                await draw_raffle_winner(self.bot, raffle['id'])

    async def process_giveaways(self, now):
        async with self.bot.db_pool.acquire() as conn:
            ended_giveaways = await conn.fetch("SELECT * FROM giveaways WHERE ends_at < $1 AND is_active = TRUE", now)
            for gw in ended_giveaways:
                await end_giveaway(self.bot, gw)
            # You could add entry count updates here as well if desired

    async def process_pvm_events(self, now):
        pvm_channel = self.bot.get_channel(PVM_EVENT_CHANNEL_ID)
        if not pvm_channel: return
        
        async with self.bot.db_pool.acquire() as conn:
            # Send 1-hour reminders
            reminders_needed = await conn.fetch("SELECT * FROM pvm_events WHERE is_active = TRUE AND reminder_sent = FALSE AND starts_at - INTERVAL '1 hour' <= $1", now)
            for event in reminders_needed:
                await pvm_channel.send(f"@here PVM Event Reminder: **{event['title']}** begins in less than an hour!")
                await conn.execute("UPDATE pvm_events SET reminder_sent = TRUE WHERE id = $1", event['id'])

            # Deactivate past events
            await conn.execute("UPDATE pvm_events SET is_active = FALSE WHERE starts_at < $1", now)
    
    @tasks.loop(hours=4)
    async def periodic_event_reminder(self):
        """Sends a periodic summary of all active events."""
        # This function can be filled with the logic from the original bot.py
        # to generate an AI summary of active SOTW, raffles, and giveaways.
        pass

    @event_manager.before_loop
    @periodic_event_reminder.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()

def setup(bot):
    bot.add_cog(BackgroundTasks(bot))