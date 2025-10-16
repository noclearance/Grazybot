# cogs/pvm.py
# Contains commands for managing PVM events.

import discord
import logging
from discord.commands import SlashCommandGroup, Option
from discord.ext import commands
from datetime import datetime, timezone

from core.bot import GrazyBot
from core import config
from utils import clan, ai
from utils.views import PvmEventView

logger = logging.getLogger(__name__)

class PVM(commands.Cog):
    """Cog for PVM event commands."""

    def __init__(self, bot: GrazyBot):
        self.bot = bot

    pvm_group = SlashCommandGroup("pvm", "Commands for PVM events.")

    @pvm_group.command(name="schedule", description="Schedule a new PVM event.")
    @commands.has_permissions(manage_events=True)
    async def schedule_pvm_event(self, ctx: discord.ApplicationContext,
                                 title: Option(str, "Title of the event."),
                                 description: Option(str, "Description of the event."),
                                 start_time: Option(str, "Start time (e.g., 'YYYY-MM-DD HH:MM UTC')."),
                                 duration_minutes: Option(int, "Duration in minutes.", default=60)):
        """Schedules a new PVM event and posts it in the designated channel."""
        await ctx.defer(ephemeral=True)
        
        try:
            event_start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M %Z').replace(tzinfo=timezone.utc)
        except ValueError:
            return await ctx.respond("Invalid start time format. Use 'YYYY-MM-DD HH:MM UTC'.", ephemeral=True)

        if event_start_dt <= datetime.now(timezone.utc):
            return await ctx.respond("Event start time must be in the future.", ephemeral=True)

        pvm_channel = self.bot.get_channel(config.PVM_EVENT_CHANNEL_ID)
        if not pvm_channel:
            return await ctx.respond("PVM Event Channel ID not configured.", ephemeral=True)
        
        details = {'title': title, 'description': description, 'start_time_unix': int(event_start_dt.timestamp())}
        ai_embed_data = await ai.generate_announcement_json("pvm_event_start", details)
        event_embed = discord.Embed.from_dict(ai_embed_data)
        event_embed.add_field(name="⏰ Starts At", value=f"<t:{int(event_start_dt.timestamp())}:F>", inline=False)
        event_embed.add_field(name="⏳ Duration", value=f"{duration_minutes} minutes", inline=False)
        event_embed.set_footer(text=f"Event by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        try:
            event_message = await pvm_channel.send(embed=event_embed)

            async with self.bot.db_pool.acquire() as conn:
                event_id = await conn.fetchval(
                    """
                    INSERT INTO pvm_events (title, description, starts_at, duration_minutes, message_id, channel_id)
                    VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
                    """,
                    title, description, event_start_dt, duration_minutes, event_message.id, pvm_channel.id
                )

            await event_message.edit(view=PvmEventView(event_id=event_id))
            await ctx.respond(f"PVM event '{title}' scheduled in {pvm_channel.mention}!", ephemeral=True)
            await clan.send_global_announcement(self.bot, "pvm_event_start", details, event_message.jump_url)
            logger.info(f"PVM event '{title}' scheduled by {ctx.author}.")
        except Exception as e:
            logger.error(f"Failed to schedule PVM event '{title}': {e}", exc_info=True)
            await ctx.respond("An error occurred while scheduling the event.", ephemeral=True)

    @pvm_group.command(name="participants", description="View participants for a PVM event.")
    async def view_pvm_participants(self, ctx: discord.ApplicationContext, 
                                      event_id: Option(int, "The ID of the PVM event.")):
        """Displays the list of users signed up for a specific PVM event."""
        await ctx.defer()
        try:
            async with self.bot.db_pool.acquire() as conn:
                event_data = await conn.fetchrow("SELECT * FROM pvm_events WHERE id = $1", event_id)
                if not event_data:
                    return await ctx.respond(f"PVM event with ID `{event_id}` not found.", ephemeral=True)

                signups = await conn.fetch("SELECT user_id FROM pvm_event_signups WHERE event_id = $1", event_id)

            embed = discord.Embed(
                title=f"Participants for '{event_data['title']}'",
                description=f"Total Signed Up: **{len(signups)}**",
                color=discord.Color.green()
            )

            if not signups:
                embed.description += "\n\nNo one has signed up yet."
            else:
                participant_list = [ctx.guild.get_member(entry['user_id']) for entry in signups]
                embed.add_field(
                    name="Signed-Up Warriors",
                    value="\n".join(f"• {member.mention}" for member in participant_list if member),
                    inline=False
                )

            await ctx.respond(embed=embed)
        except Exception as e:
            logger.error(f"Failed to get participants for PVM event {event_id}: {e}", exc_info=True)
            await ctx.respond("An error occurred while fetching participants.", ephemeral=True)

    @pvm_group.command(name="cancel", description="Cancel an upcoming PVM event.")
    @commands.has_permissions(manage_events=True)
    async def cancel_pvm_event(self, ctx: discord.ApplicationContext, 
                               event_id: Option(int, "The ID of the PVM event to cancel.")):
        """Cancels a PVM event, deactivating it and updating the original message."""
        await ctx.defer(ephemeral=True)
        try:
            async with self.bot.db_pool.acquire() as conn:
                # Use RETURNING to get the data of the row we're updating
                event_data = await conn.fetchrow(
                    "UPDATE pvm_events SET is_active = FALSE WHERE id = $1 AND is_active = TRUE RETURNING *",
                    event_id
                )
                if not event_data:
                    return await ctx.respond(f"PVM event with ID `{event_id}` not found or already inactive.", ephemeral=True)
            
            event_channel = self.bot.get_channel(event_data['channel_id'])
            if event_channel:
                try:
                    msg = await event_channel.fetch_message(event_data['message_id'])
                    cancelled_embed = discord.Embed(
                        title=f"EVENT CANCELLED: {event_data['title']}",
                        description="This event has been cancelled by an admin.",
                        color=discord.Color.dark_red()
                    )
                    await msg.edit(embed=cancelled_embed, view=None)
                except discord.NotFound:
                    logger.warning(f"Could not find PVM event message {event_data['message_id']} to cancel.")
            
            await ctx.respond(f"PVM event '{event_data['title']}' (ID: {event_id}) successfully cancelled.", ephemeral=True)
            logger.info(f"PVM event {event_id} cancelled by {ctx.author}.")
        except Exception as e:
            logger.error(f"Failed to cancel PVM event {event_id}: {e}", exc_info=True)
            await ctx.respond("An error occurred while cancelling the event.", ephemeral=True)

async def setup(bot: GrazyBot):
    await bot.add_cog(PVM(bot))