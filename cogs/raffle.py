# cogs/raffle.py
import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
import os
from datetime import datetime, timezone, timedelta

# Import helper functions from our utils file
from .utils import get_db_connection, generate_announcement_json, send_to_announcement_channels, draw_raffle_winner

class Raffle(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.raffle_channel_id = int(os.getenv('RAFFLE_CHANNEL_ID'))

    raffle = SlashCommandGroup("raffle", "Commands for managing raffles.")

    @raffle.command(name="start", description="Start a new raffle.")
    @discord.default_permissions(manage_events=True)
    async def start_raffle(self, ctx: discord.ApplicationContext, prize: Option(str, "What is the prize?"), duration_days: Option(float, "How many days will it last?")):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM raffles"); cursor.execute("DELETE FROM raffle_entries")
        ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        cursor.execute("INSERT INTO raffles (id, prize, ends_at) VALUES (1, %s, %s)", (prize, ends_at.isoformat()))
        conn.commit()
        cursor.close()
        conn.close()
        
        details = {"prize": prize}
        ai_embed_data = await generate_announcement_json("raffle_start", details)
        embed = discord.Embed.from_dict(ai_embed_data)
        
        embed.add_field(name="How to Enter", value="Use `/raffle enter` to get a ticket! (Max 10 per person)", inline=False)
        embed.add_field(name="Raffle Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"Raffle started by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        
        raffle_channel = self.bot.get_channel(self.raffle_channel_id)
        if raffle_channel:
            raffle_message = await raffle_channel.send(embed=embed)
            
            # Use the new helper function to announce in multiple channels
            announce_embed = embed.copy()
            announce_embed.add_field(name="Details", value=f"[Click here to view the raffle!]({raffle_message.jump_url})")
            await send_to_announcement_channels(self.bot, announce_embed)

            await ctx.respond("Raffle created successfully!", ephemeral=True)
        else:
            await ctx.respond("Error: Raffle Channel ID not configured correctly.", ephemeral=True)

    @raffle.command(name="enter", description="Get one ticket for the current raffle (max 10).")
    async def enter_raffle(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT prize FROM raffles LIMIT 1")
        raffle_data = cursor.fetchone()
        if not raffle_data:
            cursor.close(); conn.close()
            return await ctx.respond("There is no active raffle to enter right now.", ephemeral=True)
        
        cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s AND source = 'self'", (ctx.author.id,))
        self_entries = cursor.fetchone()[0]
        if self_entries >= 10:
            cursor.close(); conn.close()
            return await ctx.respond("You have already claimed your maximum of 10 tickets for this raffle!", ephemeral=True)
            
        cursor.execute("INSERT INTO raffle_entries (user_id, source) VALUES (%s, 'self')", (ctx.author.id,))
        conn.commit()
        
        cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s", (ctx.author.id,))
        total_tickets = cursor.fetchone()[0]
        
        cursor.close(); conn.close()
        await ctx.respond(f"You have successfully claimed a ticket for the **{raffle_data[0]}** raffle! You now have a total of {total_tickets} ticket(s).", ephemeral=True)

    @raffle.command(name="give_tickets", description="ADMIN: Give raffle tickets to a member.")
    @discord.default_permissions(manage_events=True)
    async def give_tickets(self, ctx: discord.ApplicationContext, member: Option(discord.Member, "The member to give tickets to."), amount: Option(int, "How many tickets to give.", min_value=1)):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM raffles LIMIT 1")
        if not cursor.fetchone():
            cursor.close(); conn.close()
            return await ctx.respond("There is no active raffle.", ephemeral=True)
        
        entries = [(member.id, 'admin') for _ in range(amount)]
        cursor.executemany("INSERT INTO raffle_entries (user_id, source) VALUES (%s, %s)", entries)
        conn.commit()
        
        cursor.execute("SELECT COUNT(*) FROM raffle_entries WHERE user_id = %s", (member.id,))
        total_tickets = cursor.fetchone()[0]
        
        cursor.close(); conn.close()
        await ctx.respond(f"Successfully gave {amount} ticket(s) to {member.display_name}. They now have {total_tickets} ticket(s).", ephemeral=True)

    @raffle.command(name="edit_tickets", description="ADMIN: Set a member's total ticket count.")
    @discord.default_permissions(manage_events=True)
    async def edit_tickets(self, ctx: discord.ApplicationContext, member: Option(discord.Member, "The member whose tickets you want to edit."), new_total: Option(int, "The new total number of tickets they should have.", min_value=0)):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM raffles LIMIT 1")
        if not cursor.fetchone():
            cursor.close(); conn.close()
            return await ctx.respond("There is no active raffle.", ephemeral=True)
            
        cursor.execute("DELETE FROM raffle_entries WHERE user_id = %s", (member.id,))
        
        if new_total > 0:
            entries = [(member.id, 'admin_edit') for _ in range(new_total)]
            cursor.executemany("INSERT INTO raffle_entries (user_id, source) VALUES (%s, %s)", entries)
        
        conn.commit()
        cursor.close(); conn.close()
        await ctx.respond(f"Successfully set {member.display_name}'s ticket count to {new_total}.", ephemeral=True)

    @raffle.command(name="view_tickets", description="View the current ticket count for all participants.")
    async def view_tickets(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT prize FROM raffles LIMIT 1")
        raffle_data = cursor.fetchone()
        if not raffle_data:
            cursor.close(); conn.close()
            return await ctx.respond("There is no active raffle.")
        
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
                    continue
            embed.description = description
        
        await ctx.respond(embed=embed)

    @raffle.command(name="draw_now", description="ADMIN: Immediately ends the raffle and draws a winner.")
    @discord.default_permissions(manage_events=True)
    async def draw_now(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        result = await draw_raffle_winner(self.bot, self.raffle_channel_id)
        await ctx.respond(f"Successfully triggered winner drawing: {result}")

    @raffle.command(name="cancel", description="ADMIN: Cancels the current raffle without drawing a winner.")
    @discord.default_permissions(manage_events=True)
    async def cancel_raffle(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT prize FROM raffles LIMIT 1")
        raffle_data = cursor.fetchone()
        if not raffle_data:
            cursor.close(); conn.close()
            return await ctx.respond("There is no active raffle to cancel.")
            
        prize = raffle_data[0]
        cursor.execute("DELETE FROM raffles"); cursor.execute("DELETE FROM raffle_entries")
        conn.commit(); cursor.close(); conn.close()
        
        channel = self.bot.get_channel(self.raffle_channel_id)
        if channel:
            await channel.send(f"The raffle for **{prize}** has been cancelled by an admin.")
            
        await ctx.respond("Raffle successfully cancelled.")

def setup(bot):
    bot.add_cog(Raffle(bot))