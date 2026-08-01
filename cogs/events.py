# bot/cogs/events.py
# Command to view all active clan events.

import discord
from discord.commands import SlashCommandGroup
from discord.ext import commands
import asyncio

class Events(commands.Cog):
    """Cog for viewing active events."""
    
    def __init__(self, bot):
        self.bot = bot

    events = SlashCommandGroup("events", "View all active clan events.")

    @events.command(name="view", description="Shows all currently active competitions and raffles.")
    async def view_events(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        async with self.bot.db_pool.acquire() as conn:
            # Fetch all event data concurrently for efficiency
            comp_task = conn.fetchrow("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
            raf_task = conn.fetchrow("SELECT * FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
            giveaway_task = conn.fetchrow("SELECT * FROM giveaways WHERE is_active = TRUE AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
            pvm_event_task = conn.fetchrow("SELECT * FROM pvm_events WHERE is_active = TRUE AND starts_at > NOW() ORDER BY starts_at ASC LIMIT 1")
            
            comp, raf, giveaway, pvm_event = await asyncio.gather(comp_task, raf_task, giveaway_task, pvm_event_task)

        embed = discord.Embed(title="Clan Event Status", description="Here's a look at all the events currently running.", color=discord.Color.blurple())
        
        # SOTW
        if comp:
            embed.add_field(name="Active Competition", value=f"**Title:** [{comp['title']}](https://wiseoldman.net/competitions/{comp['id']})\n**Ends:** <t:{int(comp['ends_at'].timestamp())}:R>", inline=False)
        else:
            embed.add_field(name="Active Competition", value="No SOTW competition is running.", inline=False)
        
        # Raffle
        if raf:
            raffle_channel = self.bot.get_channel(raf['channel_id'])
            url = raffle_channel.get_partial_message(raf['message_id']).jump_url if raffle_channel else '#'
            embed.add_field(name="Active Raffle", value=f"**Prize:** {raf['prize']}\n**Ends:** <t:{int(raf['ends_at'].timestamp())}:R>\n[View Raffle]({url})", inline=False)
        else:
            embed.add_field(name="Active Raffle", value="No raffle is running.", inline=False)

        # Giveaway
        if giveaway:
            gw_channel = self.bot.get_channel(giveaway['channel_id'])
            url = gw_channel.get_partial_message(giveaway['message_id']).jump_url if gw_channel else '#'
            embed.add_field(name="Active Giveaway", value=f"**Prize:** {giveaway['prize']}\n**Ends:** <t:{int(giveaway['ends_at'].timestamp())}:R>\n[Enter Here]({url})", inline=False)
        else:
            embed.add_field(name="Active Giveaway", value="No active giveaways.", inline=False)
        
        # PVM Event
        if pvm_event:
            pvm_channel = self.bot.get_channel(pvm_event['channel_id'])
            url = pvm_channel.get_partial_message(pvm_event['message_id']).jump_url if pvm_channel else '#'
            embed.add_field(name="Upcoming PVM Event", value=f"**Event:** {pvm_event['title']}\n**Starts:** <t:{int(pvm_event['starts_at'].timestamp())}:R>\n[View Event]({url})", inline=False)
        else:
            embed.add_field(name="Upcoming PVM Event", value="No PVM events scheduled.", inline=False)

        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.respond(embed=embed)

def setup(bot):
    bot.add_cog(Events(bot))