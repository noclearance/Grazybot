# bot/cogs/pvm.py
# Contains commands for managing PVM events.

import discord
from discord.commands import SlashCommandGroup
from discord.ext import commands
from datetime import datetime, timezone

from bot.config import PVM_EVENT_CHANNEL_ID
from bot.helpers.ai import generate_announcement_json
from bot.helpers.utils import send_global_announcement
from bot.views import PvmEventView

class PVM(commands.Cog):
    """Cog for PVM event commands."""

    def __init__(self, bot):
        self.bot = bot

    pvm = SlashCommandGroup("pvm", "Commands for PVM events.")

    @pvm.command(name="schedule", description="Schedule a new PVM event.")
    @commands.has_permissions(manage_events=True)
    async def schedule_pvm_event(self, ctx: discord.ApplicationContext,
                                 title: discord.Option(str, "Title of the event."),
                                 description: discord.Option(str, "Description of the event."),
                                 start_time: discord.Option(str, "Start time (e.g., '2024-12-31 20:00 UTC')."),
                                 duration_minutes: discord.Option(int, "Duration in minutes.", default=60)):
        await ctx.defer(ephemeral=True)
        
        try:
            event_start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
        except ValueError:
            return await ctx.respond("Invalid start time format. Use 'YYYY-MM-DD HH:MM UTC'.", ephemeral=True)

        if event_start_dt <= datetime.now(timezone.utc):
            return await ctx.respond("Start time must be in the future.", ephemeral=True)

        pvm_channel = self.bot.get_channel(PVM_EVENT_CHANNEL_ID)
        if not pvm_channel:
            return await ctx.respond("PVM Event Channel ID not configured.", ephemeral=True)
        
        details = {'title': title, 'description': description, 'start_time_unix': int(event_start_dt.timestamp())}
        ai_embed_data = await generate_announcement_json("pvm_event_start", details)
        event_embed = discord.Embed.from_dict(ai_embed_data)
        event_embed.add_field(name="Starts At", value=f"<t:{int(event_start_dt.timestamp())}:F>", inline=False)
        event_embed.add_field(name="Duration", value=f"{duration_minutes} minutes", inline=False)
        event_embed.set_footer(text=f"Event by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        event_message = await pvm_channel.send(embed=event_embed)
        
        async with self.bot.db_pool.acquire() as conn:
            event_id = await conn.fetchval("""
                INSERT INTO pvm_events (title, description, starts_at, duration_minutes, message_id, channel_id, signup_message_id) 
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
            """, title, description, event_start_dt, duration_minutes, event_message.id, pvm_channel.id, event_message.id)

        await event_message.edit(view=PvmEventView(event_id=event_id))
        await ctx.respond(f"PVM event '{title}' scheduled!", ephemeral=True)
        await send_global_announcement(self.bot, "pvm_event_start", details, event_message.jump_url)

    @pvm.command(name="participants", description="View participants for a PVM event.")
    async def view_pvm_participants(self, ctx: discord.ApplicationContext, 
                                      event_id: discord.Option(int, "The ID of the PVM event.")):
        await ctx.defer()

        async with self.bot.db_pool.acquire() as conn:
            event_data = await conn.fetchrow("SELECT * FROM pvm_events WHERE id = $1", event_id)
            if not event_data:
                return await ctx.respond(f"PVM event with ID {event_id} not found.")

            signups = await conn.fetch("SELECT user_id FROM pvm_event_signups WHERE event_id = $1", event_id)

        embed = discord.Embed(title=f"Participants for '{event_data['title']}'",
                              description=f"Total Signed Up: **{len(signups)}**", color=discord.Color.green())
        
        if not signups:
            embed.description += "\n\nNo one has signed up yet."
        else:
            participant_list = []
            for entry in signups:
                member = ctx.guild.get_member(entry['user_id'])
                participant_list.append(member.display_name if member else f"User ID: {entry['user_id']}")
            embed.add_field(name="Signed-Up Warriors", value="\n".join(f"- {p}" for p in participant_list), inline=False)
        
        await ctx.respond(embed=embed)

    @pvm.command(name="cancel", description="Cancel an upcoming PVM event.")
    @commands.has_permissions(manage_events=True)
    async def cancel_pvm_event(self, ctx: discord.ApplicationContext, 
                               event_id: discord.Option(int, "The ID of the PVM event to cancel.")):
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            event_data = await conn.fetchrow("SELECT * FROM pvm_events WHERE id = $1 AND is_active = TRUE", event_id)
            if not event_data:
                return await ctx.respond(f"PVM event with ID {event_id} not found or already inactive.", ephemeral=True)
            
            await conn.execute("UPDATE pvm_events SET is_active = FALSE WHERE id = $1", event_id)
            
            event_channel = self.bot.get_channel(event_data['channel_id'])
            if event_channel:
                try:
                    msg = await event_channel.fetch_message(event_data['message_id'])
                    await msg.edit(content=f"**EVENT CANCELLED: {event_data['title']}**", embed=None, view=None)
                except discord.NotFound:
                    pass
            
            await ctx.respond(f"PVM event '{event_data['title']}' (ID: {event_id}) successfully cancelled.", ephemeral=True)

def setup(bot):
    bot.add_cog(PVM(bot))