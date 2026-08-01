# Background tasks that run periodically.

from datetime import datetime, timezone, timedelta
import asyncio
import logging

import discord
from discord.ext import tasks, commands

from config import RECAP_CHANNEL_ID, SOTW_CHANNEL_ID, ANNOUNCEMENTS_CHANNEL_ID, PVM_EVENT_CHANNEL_ID
from helpers.ai import generate_recap_text, ai_model
from helpers.giveaway_utils import end_giveaway
from helpers.raffle_utils import draw_raffle_winner
from helpers.utils import award_points
from helpers.wom import get_weekly_gains, get_competition_details

logger = logging.getLogger("grazybot.tasks")


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
        """Lifecycle manager for SOTW, raffles, giveaways, PVM, weekly recap."""
        if not self.bot.db_pool:
            return
        now = datetime.now(timezone.utc)

        # --- Weekly Recap (Sundays after 19:00 UTC) ---
        try:
            async with self.bot.db_pool.acquire() as conn:
                last_recap_str = await conn.fetchval(
                    "SELECT value FROM bot_settings WHERE key = 'last_recap_sent'"
                )
                last_recap_dt = (
                    datetime.fromisoformat(last_recap_str)
                    if last_recap_str
                    else datetime.min.replace(tzinfo=timezone.utc)
                )
                if now.weekday() == 6 and now.hour >= 19 and (now - last_recap_dt) > timedelta(days=6):
                    recap_channel = self.bot.get_channel(RECAP_CHANNEL_ID)
                    if recap_channel:
                        gains_data, error = await get_weekly_gains()
                        if not error and gains_data:
                            recap_text = await generate_recap_text(gains_data)
                            embed = discord.Embed(
                                title="Weekly Recap from the Taskmaster",
                                description=recap_text,
                                color=discord.Color.blue(),
                            )
                            await recap_channel.send(embed=embed)
                            await conn.execute(
                                "INSERT INTO bot_settings (key, value) VALUES ('last_recap_sent', $1) "
                                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                                now.isoformat(),
                            )
        except Exception as e:
            logger.error("Weekly recap error: %s", e)

        await self.process_sotw(now)
        await self.process_raffles(now)
        await self.process_giveaways(now)
        await self.process_pvm_events(now)

    async def process_sotw(self, now):
        sotw_channel = self.bot.get_channel(SOTW_CHANNEL_ID)
        if not sotw_channel:
            return

        try:
            async with self.bot.db_pool.acquire() as conn:
                comps = await conn.fetch("SELECT * FROM active_competitions")
                for comp in comps:
                    ends_at = comp["ends_at"]
                    starts_at = comp["starts_at"]

                    if now > ends_at and not comp["winners_awarded"]:
                        details, error = await get_competition_details(comp["id"])
                        if not error and details:
                            point_values = [100, 50, 25]
                            for i, p in enumerate(details.get("participations", [])[:3]):
                                user_id = await conn.fetchval(
                                    "SELECT discord_id FROM user_links WHERE osrs_name = $1",
                                    p["player"]["displayName"],
                                )
                                if user_id:
                                    member = sotw_channel.guild.get_member(user_id)
                                    if member:
                                        await award_points(
                                            self.bot,
                                            member,
                                            point_values[i],
                                            f"placing #{i + 1} in SOTW",
                                        )
                        await conn.execute(
                            "UPDATE active_competitions SET winners_awarded = TRUE WHERE id = $1",
                            comp["id"],
                        )

                    if not comp["final_ping_sent"] and (ends_at - now) <= timedelta(hours=1):
                        embed = discord.Embed(
                            title="Final Hour!",
                            description=f"The **{comp['title']}** competition ends in less than an hour!",
                            color=discord.Color.red(),
                            url=f"https://wiseoldman.net/competitions/{comp['id']}",
                        )
                        await sotw_channel.send(content="@everyone", embed=embed)
                        await conn.execute(
                            "UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = $1",
                            comp["id"],
                        )
                    elif (
                        not comp["midway_ping_sent"]
                        and now >= starts_at + ((ends_at - starts_at) / 2)
                    ):
                        embed = discord.Embed(
                            title="Midway Point Reached!",
                            description=f"The **{comp['title']}** competition is halfway through!",
                            color=discord.Color.yellow(),
                            url=f"https://wiseoldman.net/competitions/{comp['id']}",
                        )
                        await sotw_channel.send(embed=embed)
                        await conn.execute(
                            "UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = $1",
                            comp["id"],
                        )
        except Exception as e:
            logger.error("SOTW processing error: %s", e)

    async def process_raffles(self, now):
        try:
            async with self.bot.db_pool.acquire() as conn:
                ended_raffles = await conn.fetch(
                    "SELECT id FROM raffles WHERE ends_at < $1 AND winner_id IS NULL", now
                )
            for raffle in ended_raffles:
                await draw_raffle_winner(self.bot, raffle["id"])
        except Exception as e:
            logger.error("Raffle processing error: %s", e)

    async def process_giveaways(self, now):
        try:
            async with self.bot.db_pool.acquire() as conn:
                ended_giveaways = await conn.fetch(
                    "SELECT * FROM giveaways WHERE ends_at < $1 AND is_active = TRUE", now
                )
                active_giveaways = await conn.fetch(
                    "SELECT message_id, channel_id FROM giveaways WHERE is_active = TRUE AND ends_at > $1",
                    now,
                )

            for gw in ended_giveaways:
                await end_giveaway(self.bot, dict(gw))

            # Update entry counts on active giveaway embeds
            for giveaway in active_giveaways:
                try:
                    async with self.bot.db_pool.acquire() as conn:
                        entry_count = await conn.fetchval(
                            "SELECT COUNT(user_id) FROM giveaway_entries WHERE message_id = $1",
                            giveaway["message_id"],
                        )
                    channel = self.bot.get_channel(giveaway["channel_id"])
                    if not channel:
                        continue
                    message = await channel.fetch_message(giveaway["message_id"])
                    if not message.embeds:
                        continue
                    embed = message.embeds[0]
                    new_entry_value = f"**Entries:** {entry_count}"
                    entry_field_index = next(
                        (i for i, field in enumerate(embed.fields) if "Entries" in field.name),
                        -1,
                    )
                    if entry_field_index != -1:
                        if embed.fields[entry_field_index].value != new_entry_value:
                            embed.set_field_at(
                                entry_field_index, name="Entries", value=new_entry_value, inline=True
                            )
                            await message.edit(embed=embed)
                    else:
                        embed.add_field(name="Entries", value=new_entry_value, inline=True)
                        await message.edit(embed=embed)
                except discord.NotFound:
                    async with self.bot.db_pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE giveaways SET is_active = FALSE WHERE message_id = $1",
                            giveaway["message_id"],
                        )
                except Exception as e:
                    logger.error(
                        "Error updating giveaway %s: %s", giveaway["message_id"], e
                    )
        except Exception as e:
            logger.error("Giveaway processing error: %s", e)

    async def process_pvm_events(self, now):
        pvm_channel = self.bot.get_channel(PVM_EVENT_CHANNEL_ID)
        if not pvm_channel:
            return
        try:
            async with self.bot.db_pool.acquire() as conn:
                reminders_needed = await conn.fetch(
                    "SELECT * FROM pvm_events WHERE is_active = TRUE AND reminder_sent = FALSE "
                    "AND starts_at - INTERVAL '1 hour' <= $1 AND starts_at > $1",
                    now,
                )
                for event in reminders_needed:
                    embed = discord.Embed(
                        title=f"PVM Event Reminder: {event['title']}",
                        description=(
                            f"**{event['title']}** begins in less than an hour!\n"
                            f"Starts: <t:{int(event['starts_at'].timestamp())}:R>"
                        ),
                        color=discord.Color.orange(),
                    )
                    await pvm_channel.send(content="@here", embed=embed)
                    await conn.execute(
                        "UPDATE pvm_events SET reminder_sent = TRUE WHERE id = $1", event["id"]
                    )
                await conn.execute(
                    "UPDATE pvm_events SET is_active = FALSE WHERE starts_at < $1", now
                )
        except Exception as e:
            logger.error("PVM processing error: %s", e)

    @tasks.loop(hours=4)
    async def periodic_event_reminder(self):
        """AI bulletin summarizing active clan events."""
        if not self.bot.db_pool:
            return
        announcements_channel = self.bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
        if not announcements_channel:
            return

        try:
            async with self.bot.db_pool.acquire() as conn:
                sotw = await conn.fetchrow(
                    "SELECT title FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1"
                )
                raffle = await conn.fetchrow(
                    "SELECT prize FROM raffles WHERE ends_at > NOW() AND winner_id IS NULL ORDER BY ends_at DESC LIMIT 1"
                )
                giveaway = await conn.fetchrow(
                    "SELECT prize FROM giveaways WHERE ends_at > NOW() AND is_active = TRUE ORDER BY ends_at DESC LIMIT 1"
                )
                pvm_event = await conn.fetchrow(
                    "SELECT title, starts_at FROM pvm_events WHERE is_active = TRUE AND starts_at > NOW() ORDER BY starts_at ASC LIMIT 1"
                )

            event_summary = ""
            if sotw:
                event_summary += f"- A Skill of the Week competition for **{sotw['title']}** is underway!\n"
            if raffle:
                event_summary += f"- A raffle for **{raffle['prize']}** is active! Use `/raffle enter`.\n"
            if giveaway:
                event_summary += f"- A giveaway for **{giveaway['prize']}** is happening now!\n"
            if pvm_event:
                event_summary += (
                    f"- A PVM event: **{pvm_event['title']}** starts "
                    f"<t:{int(pvm_event['starts_at'].timestamp())}:R>!\n"
                )

            if not event_summary:
                return

            description = event_summary
            if ai_model:
                prompt = (
                    "You are TaskmasterGPT, the wise lore-keeper for an OSRS clan. "
                    "Write a short epic bulletin summarizing these active events. "
                    "No emojis. A few short paragraphs.\n\nActive Events:\n"
                    f"{event_summary}"
                )
                try:
                    response = await ai_model.generate_content_async(prompt)
                    description = response.text
                except Exception as e:
                    logger.warning("AI bulletin failed, using plain summary: %s", e)

            embed = discord.Embed(
                title="The Taskmaster's Bulletin",
                description=description,
                color=discord.Color.dark_gold(),
            )
            embed.set_footer(text="Seize the day, warriors!")
            await announcements_channel.send(embed=embed)
        except Exception as e:
            logger.error("Periodic reminder error: %s", e)

    @event_manager.before_loop
    async def before_event_manager(self):
        await self.bot.wait_until_ready()

    @periodic_event_reminder.before_loop
    async def before_periodic_reminder(self):
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(BackgroundTasks(bot))
