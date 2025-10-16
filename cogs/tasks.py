# cogs/tasks.py
# Contains background tasks for managing events.

import logging
import discord
from discord.ext import tasks, commands
import random

from core.bot import GrazyBot
from utils import raffle as raffle_utils, wom as wom_utils, clan

logger = logging.getLogger(__name__)

class Tasks(commands.Cog):
    """Cog for running background tasks."""

    def __init__(self, bot: GrazyBot):
        self.bot = bot
        self.event_manager.start()

    def cog_unload(self):
        self.event_manager.cancel()

    @tasks.loop(minutes=1.0)
    async def event_manager(self):
        """
        A background loop that runs every minute to manage the state of various events.
        """
        try:
            async with self.bot.db_pool.acquire() as conn:
                # --- Handle Ended Raffles ---
                ended_raffles = await conn.fetch("SELECT id FROM raffles WHERE ends_at <= NOW() AND winner_id IS NULL")
                if ended_raffles:
                    logger.info(f"Found {len(ended_raffles)} ended raffle(s) to process.")
                    for raffle in ended_raffles:
                        await raffle_utils.draw_raffle_winner(self.bot, raffle['id'])

                # --- Handle Ended SOTW Competitions ---
                ended_sotw_records = await conn.fetch("SELECT id, competition_id FROM active_competitions WHERE ends_at <= NOW()")
                if ended_sotw_records:
                    logger.info(f"Found {len(ended_sotw_records)} ended SOTW competition(s) to process.")
                    for sotw_record in ended_sotw_records:
                        comp_data, error = await wom_utils.get_competition_details(sotw_record['competition_id'])
                        if not error and comp_data:
                            point_values = [100, 50, 25]
                            for i, participant in enumerate(comp_data.get('participations', [])[:3]):
                                osrs_name = participant['player']['displayName']
                                user_data = await conn.fetchrow("SELECT discord_id FROM user_links WHERE osrs_name = $1", osrs_name)
                                if user_data:
                                    member = self.bot.get_guild(config.DEBUG_GUILD_ID).get_member(user_data['discord_id'])
                                    if member:
                                        reason = f"placing #{i+1} in the {comp_data['title']} SOTW"
                                        await clan.award_points(self.bot, member, point_values[i], reason)
                        await conn.execute("DELETE FROM active_competitions WHERE id = $1", sotw_record['id'])

                # --- Handle Ended Giveaways ---
                ended_giveaways = await conn.fetch("SELECT * FROM giveaways WHERE ends_at <= NOW() AND is_active = TRUE")
                if ended_giveaways:
                    logger.info(f"Found {len(ended_giveaways)} ended giveaway(s) to process.")
                    for gw in ended_giveaways:
                        gw_channel = self.bot.get_channel(gw['channel_id'])
                        if not gw_channel:
                            continue

                        entrants = await conn.fetch("SELECT user_id FROM giveaway_entries WHERE giveaway_id = $1", gw['id'])
                        if not entrants:
                            await gw_channel.send(f"The giveaway for **{gw['prize']}** has ended, but no one entered.")
                        else:
                            winner_ids = random.sample([e['user_id'] for e in entrants], k=min(gw['winner_count'], len(entrants)))
                            winners_mention = [f"<@{wid}>" for wid in winner_ids]

                            win_embed = discord.Embed(
                                title="🎉 Giveaway Winners! 🎉",
                                description=f"Congratulations to {', '.join(winners_mention)}! You've won the **{gw['prize']}**!",
                                color=discord.Color.gold()
                            )
                            await gw_channel.send(embed=win_embed)

                            if gw['role_id']:
                                role = gw_channel.guild.get_role(gw['role_id'])
                                if role:
                                    for winner_id in winner_ids:
                                        member = gw_channel.guild.get_member(winner_id)
                                        if member:
                                            await member.add_roles(role)

                        await conn.execute("UPDATE giveaways SET is_active = FALSE WHERE id = $1", gw['id'])

        except Exception as e:
            logger.error(f"Error in event_manager task: {e}", exc_info=True)

    @event_manager.before_loop
    async def before_event_manager(self):
        """Wait until the bot is ready before starting the loop."""
        await self.bot.wait_until_ready()
        logger.info("Event manager task is starting.")

async def setup(bot: GrazyBot):
    await bot.add_cog(Tasks(bot))