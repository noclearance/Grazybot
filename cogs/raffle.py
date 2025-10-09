# bot/cogs/raffle.py
# Contains commands for managing raffles.

import discord
from discord.commands import SlashCommandGroup
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import random

from bot.config import RAFFLE_CHANNEL_ID
from bot.helpers.ai import generate_announcement_json
from bot.helpers.raffle_utils import draw_raffle_winner
from bot.helpers.utils import send_global_announcement

class Raffle(commands.Cog):
    """Cog for all raffle-related commands."""
    
    def __init__(self, bot):
        self.bot = bot

    raffle = SlashCommandGroup("raffle", "Commands for managing raffles.")

    @raffle.command(name="start", description="Start a new raffle.")
    @commands.has_permissions(manage_events=True)
    async def start_raffle(self, ctx: discord.ApplicationContext, 
                           prize: discord.Option(str, "What is the prize?"), 
                           duration_days: discord.Option(float, "How many days will it last?")):
        await ctx.defer(ephemeral=True)
        
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        details = {"prize": prize}
        
        ai_embed_data = await generate_announcement_json("raffle_start", details)
        embed = discord.Embed.from_dict(ai_embed_data)
        embed.add_field(name="How to Enter", value="Use \`/raffle enter\` to get a ticket! (Max 10 per person)", inline=False)
        embed.add_field(name="Raffle Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        
        raffle_channel = self.bot.get_channel(RAFFLE_CHANNEL_ID)
        if not raffle_channel:
            return await ctx.respond("Error: Raffle Channel ID not configured.", ephemeral=True)

        raffle_message = await raffle_channel.send(embed=embed)
        
        async with self.bot.db_pool.acquire() as conn:
            raffle_id = await conn.fetchval("INSERT INTO raffles (prize, ends_at, message_id, channel_id) VALUES ($1, $2, $3, $4) RETURNING id",
                                            prize, ends_at, raffle_message.id, raffle_channel.id)
        
        embed.set_footer(text=f"Raffle ID: {raffle_id} | Started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await raffle_message.edit(embed=embed)
        
        await send_global_announcement(self.bot, "raffle_start", details, raffle_message.jump_url)
        await ctx.respond(f"Raffle (ID: {raffle_id}) for **{prize}** created!", ephemeral=True)

    @raffle.command(name="enter", description="Get tickets for the current raffle.")
    async def enter_raffle(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            active_raffle = await conn.fetchrow("SELECT id, prize FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
            if not active_raffle:
                return await ctx.respond("There is no active raffle to enter.", ephemeral=True)
            
            raffle_id, prize = active_raffle['id'], active_raffle['prize']
            
            # Check max self-entries
            self_entries = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2 AND source = 'self'", ctx.author.id, raffle_id)
            if self_entries >= 10:
                return await ctx.respond(f"You have already claimed your max of 10 tickets for the '{prize}' raffle.", ephemeral=True)
            
            await conn.execute("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES ($1, $2, 'self')", raffle_id, ctx.author.id)
            total_tickets = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", ctx.author.id, raffle_id)
        
        await ctx.respond(f"You have entered the **{prize}** raffle! You now have {total_tickets} ticket(s).", ephemeral=True)

    @raffle.command(name="give_tickets", description="Give raffle tickets to a member.")
    @commands.has_permissions(manage_events=True)
    async def give_tickets(self, ctx: discord.ApplicationContext, 
                           member: discord.Option(discord.Member, "The member to give tickets to."), 
                           amount: discord.Option(int, "How many tickets to give.", min_value=1)):
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            active_raffle = await conn.fetchrow("SELECT id FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
            if not active_raffle:
                return await ctx.respond("There is no active raffle.", ephemeral=True)
            
            raffle_id = active_raffle['id']
            entries = [(raffle_id, member.id, 'admin') for _ in range(amount)]
            await conn.copy_records_to_table('raffle_entries', records=entries, columns=['raffle_id', 'user_id', 'source'])
            
            total_tickets = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", member.id, raffle_id)
            
        await ctx.respond(f"Gave {amount} ticket(s) to {member.display_name}. They now have {total_tickets} ticket(s).", ephemeral=True)

    @raffle.command(name="view_tickets", description="View ticket counts for the active raffle.")
    async def view_tickets(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        async with self.bot.db_pool.acquire() as conn:
            active_raffle = await conn.fetchrow("SELECT id, prize FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
            if not active_raffle:
                return await ctx.respond("There is no active raffle.")
            
            raffle_id, prize = active_raffle['id'], active_raffle['prize']
            entries = await conn.fetch("SELECT user_id, COUNT(user_id) as count FROM raffle_entries WHERE raffle_id = $1 GROUP BY user_id ORDER BY count DESC", raffle_id)

        embed = discord.Embed(title=f"Raffle Tickets for '{prize}'", color=discord.Color.gold())
        if not entries:
            embed.description = "No tickets have been claimed yet."
        else:
            desc = ""
            for entry in entries:
                member = ctx.guild.get_member(entry['user_id'])
                desc += f"**{member.display_name if member else f'ID: {entry['user_id']}'}**: {entry['count']} ticket(s)\n"
            embed.description = desc
            
        await ctx.respond(embed=embed)

    @raffle.command(name="draw_now", description="Immediately ends and draws a raffle winner.")
    @commands.has_permissions(manage_events=True)
    async def draw_now(self, ctx: discord.ApplicationContext, 
                       raffle_id: discord.Option(int, "The ID of the raffle to draw.")):
        await ctx.defer(ephemeral=True)
        channel = self.bot.get_channel(RAFFLE_CHANNEL_ID)
        if not channel:
            return await ctx.respond("Error: Raffle channel not found.", ephemeral=True)

        async with self.bot.db_pool.acquire() as conn:
            # Forcibly end the raffle by setting its end time to now
            result = await conn.execute("UPDATE raffles SET ends_at = NOW() WHERE id = $1 AND winner_id IS NULL", raffle_id)
            if 'UPDATE 0' in result:
                return await ctx.respond(f"Raffle ID {raffle_id} not found or already ended.", ephemeral=True)
        
        # The background task will pick this up on its next run, or we can call it directly
        result_message = await draw_raffle_winner(self.bot, raffle_id) 
        await ctx.respond(f"Forced raffle draw: {result_message}", ephemeral=True)

def setup(bot):
    bot.add_cog(Raffle(bot))