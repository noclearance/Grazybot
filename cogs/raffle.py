# cogs/raffle.py
# Contains commands for managing raffles.

import discord
import logging
from discord import SlashCommandGroup, Option
from discord.ext import commands
from datetime import datetime, timedelta, timezone

from core.bot import GrazyBot
from core import config
from utils import raffle as raffle_utils, clan, ai

logger = logging.getLogger(__name__)

class Raffle(commands.Cog):
    """Cog for all raffle-related commands."""
    
    def __init__(self, bot: GrazyBot):
        self.bot = bot

    raffle_group = SlashCommandGroup("raffle", "Commands for managing raffles.")
    admin_group = raffle_group.create_subgroup(
        "admin",
        "Admin commands for raffles.",
        default_member_permissions=discord.Permissions(manage_events=True)
    )

    @admin_group.command(name="start", description="Start a new raffle.")
    async def start_raffle(self, ctx: discord.ApplicationContext, 
                           prize: Option(str, "What is the prize?"),
                           duration_days: Option(float, "How many days will it last?")):
        """Starts a new raffle in the designated channel."""
        await ctx.defer(ephemeral=True)
        
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        details = {"prize": prize}
        
        ai_embed_data = await ai.generate_announcement_json("raffle_start", details)
        embed = discord.Embed.from_dict(ai_embed_data)
        embed.add_field(name="How to Enter", value="Use `/raffle enter` to get a ticket! (Max 10 per person)", inline=False)
        embed.add_field(name="Raffle Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        
        raffle_channel = self.bot.get_channel(config.RAFFLE_CHANNEL_ID)
        if not raffle_channel:
            return await ctx.respond("Error: Raffle Channel ID not configured.", ephemeral=True)

        try:
            raffle_message = await raffle_channel.send(embed=embed)

            async with self.bot.db_pool.acquire() as conn:
                raffle_id = await conn.fetchval(
                    "INSERT INTO raffles (prize, ends_at, message_id, channel_id) VALUES ($1, $2, $3, $4) RETURNING id",
                    prize, ends_at, raffle_message.id, raffle_channel.id
                )

            embed.set_footer(text=f"Raffle ID: {raffle_id} | Started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
            await raffle_message.edit(embed=embed)

            await clan.send_global_announcement(self.bot, "raffle_start", details, raffle_message.jump_url)
            await ctx.respond(f"Raffle (ID: {raffle_id}) for **{prize}** created in {raffle_channel.mention}!", ephemeral=True)
            logger.info(f"Raffle {raffle_id} for '{prize}' started by {ctx.author}.")
        except Exception as e:
            logger.error(f"Failed to start raffle for '{prize}': {e}", exc_info=True)
            await ctx.respond("An error occurred while starting the raffle.", ephemeral=True)

    @raffle_group.command(name="enter", description="Get tickets for the current raffle.")
    async def enter_raffle(self, ctx: discord.ApplicationContext):
        """Allows a user to claim up to 10 tickets for the active raffle."""
        await ctx.defer(ephemeral=True)
        try:
            async with self.bot.db_pool.acquire() as conn:
                active_raffle = await conn.fetchrow("SELECT id, prize FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
                if not active_raffle:
                    return await ctx.respond("There is no active raffle to enter.", ephemeral=True)

                raffle_id, prize = active_raffle['id'], active_raffle['prize']

                self_entries = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2 AND source = 'self'", ctx.author.id, raffle_id)
                if self_entries >= 10:
                    return await ctx.respond(f"You have already claimed your max of 10 tickets for the '{prize}' raffle.", ephemeral=True)

                await conn.execute("INSERT INTO raffle_entries (raffle_id, user_id, source) VALUES ($1, $2, 'self')", raffle_id, ctx.author.id)
                total_tickets = await conn.fetchval("SELECT COUNT(*) FROM raffle_entries WHERE user_id = $1 AND raffle_id = $2", ctx.author.id, raffle_id)
            
            await ctx.respond(f"You have entered the **{prize}** raffle! You now have {total_tickets} ticket(s).", ephemeral=True)
        except Exception as e:
            logger.error(f"Error entering raffle for {ctx.author}: {e}", exc_info=True)
            await ctx.respond("An error occurred while entering the raffle.", ephemeral=True)

    @admin_group.command(name="give_tickets", description="Give raffle tickets to a member.")
    async def give_tickets(self, ctx: discord.ApplicationContext, 
                           member: Option(discord.Member, "The member to give tickets to."),
                           amount: Option(int, "How many tickets to give.", min_value=1)):
        """Gives a specified number of raffle tickets to a member."""
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
        logger.info(f"Admin {ctx.author} gave {amount} raffle tickets to {member.display_name}.")

    @raffle_group.command(name="view_tickets", description="View ticket counts for the active raffle.")
    async def view_tickets(self, ctx: discord.ApplicationContext):
        """Displays a list of all participants and their ticket counts for the current raffle."""
        await ctx.defer()
        async with self.bot.db_pool.acquire() as conn:
            active_raffle = await conn.fetchrow("SELECT id, prize FROM raffles WHERE winner_id IS NULL AND ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
            if not active_raffle:
                return await ctx.respond("There is no active raffle.", ephemeral=True)
            
            raffle_id, prize = active_raffle['id'], active_raffle['prize']
            entries = await conn.fetch("SELECT user_id, COUNT(user_id) as count FROM raffle_entries WHERE raffle_id = $1 GROUP BY user_id ORDER BY count DESC", raffle_id)

        embed = discord.Embed(title=f"🎟️ Raffle Tickets for '{prize}'", color=discord.Color.gold())
        if not entries:
            embed.description = "No tickets have been claimed yet."
        else:
            desc = []
            for entry in entries:
                member = ctx.guild.get_member(entry['user_id'])
                desc.append(f"**{member.display_name if member else f'ID: {entry['user_id']}'}**: `{entry['count']}` ticket(s)")
            embed.description = "\n".join(desc)
            
        await ctx.respond(embed=embed)

    @admin_group.command(name="draw", description="Immediately ends and draws a raffle winner.")
    async def draw_now(self, ctx: discord.ApplicationContext, 
                       raffle_id: Option(int, "The ID of the raffle to draw.")):
        """Forces a raffle to end and draws a winner immediately."""
        await ctx.defer(ephemeral=True)

        async with self.bot.db_pool.acquire() as conn:
            result = await conn.execute("UPDATE raffles SET ends_at = NOW() WHERE id = $1 AND winner_id IS NULL", raffle_id)
            if 'UPDATE 0' in result:
                return await ctx.respond(f"Raffle ID {raffle_id} not found or already ended.", ephemeral=True)
        
        result_message = await raffle_utils.draw_raffle_winner(self.bot, raffle_id)
        await ctx.respond(f"Forced raffle draw for ID {raffle_id}. Result: {result_message}", ephemeral=True)
        logger.info(f"Admin {ctx.author} forced a draw for raffle {raffle_id}.")

async def setup(bot: GrazyBot):
    await bot.add_cog(Raffle(bot))