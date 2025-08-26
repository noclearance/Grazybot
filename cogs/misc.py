# cogs/misc.py
import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
import textwrap
import psycopg2
import psycopg2.extras
import aiohttp
import os

from .utils import get_db_connection, award_points

class Misc(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"✅ Cog-based bot {self.bot.user} is online and ready!")

    events = SlashCommandGroup("events", "View all active clan events.")
    @events.command(name="view", description="Shows all currently active competitions and raffles.")
    async def view_events(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM active_competitions ORDER BY ends_at DESC LIMIT 1")
        comp = cursor.fetchone()
        cursor.execute("SELECT * FROM raffles LIMIT 1")
        raf = cursor.fetchone()
        cursor.close(); conn.close()
        
        embed = discord.Embed(title="📅 Clan Event Status", description="Here's a look at all the events currently running.", color=discord.Color.blurple())
        if comp:
            comp_info = (f"**Title:** [{comp['title']}](https://wiseoldman.net/competitions/{comp['id']})\n"
                         f"**Ends:** <t:{int(comp['ends_at'].timestamp())}:R>")
            embed.add_field(name="⚔️ Active Competition", value=comp_info, inline=False)
        else:
            embed.add_field(name="⚔️ Active Competition", value="There is no SOTW competition currently running.", inline=False)
        
        if raf:
            raf_info = (f"**Prize:** {raf['prize']}\n"
                        f"**Ends:** <t:{int(raf['ends_at'].timestamp())}:R>")
            embed.add_field(name="🎟️ Active Raffle", value=raf_info, inline=False)
        else:
            embed.add_field(name="🎟️ Active Raffle", value="There is no raffle currently running.", inline=False)
            
        await ctx.respond(embed=embed)

    osrs = SlashCommandGroup("osrs", "Commands related to your OSRS account.")
    @osrs.command(name="link", description="Link your Discord account to your OSRS username.")
    async def link(self, ctx: discord.ApplicationContext, username: Option(str, "Your in-game RuneScape name.")):
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

    admin = SlashCommandGroup("admin", "Admin-only commands.")
    @admin.command(name="announce", description="Send a message as the bot to a specific channel.")
    @discord.default_permissions(manage_guild=True)
    async def announce(self, ctx: discord.ApplicationContext, message: Option(str, "The message to send."), channel: Option(discord.TextChannel, "The channel to send to."), ping_everyone: Option(bool, "Whether to ping @everyone.", default=False)):
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

    @admin.command(name="award_sotw_winners", description="Manually award points for a past SOTW competition.")
    @discord.default_permissions(manage_guild=True)
    async def award_sotw_winners(self, ctx: discord.ApplicationContext, competition_id: Option(int, "The ID of the competition from Wise Old Man.")):
        await ctx.defer(ephemeral=True)
        details_url = f"https://api.wiseoldman.net/v2/competitions/{competition_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(details_url) as response:
                if response.status != 200:
                    return await ctx.respond(f"Could not fetch details for competition ID {competition_id}.")
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
            return await ctx.respond("No winners could be found or linked for that competition.")
        await ctx.respond("Successfully awarded points to:\n" + "\n".join(awarded_to))
        
    @commands.slash_command(name="help", description="Shows a list of all available commands.")
    async def help(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        embed = discord.Embed(
            title="📜 GrazyBot Command List 📜",
            description="Here are all the commands you can use to manage clan events.",
            color=discord.Color.blurple()
        )
        member_commands = """
        `/sotw view` - View the leaderboard for the current Skill of the Week.
        `/raffle enter` - Get one ticket for the current raffle (max 10).
        `/raffle view_tickets` - See how many tickets everyone has.
        `/bingo board` - Get a link to the current bingo board.
        `/bingo complete` - Submit a task for bingo completion.
        `/points view` - Check your current Clan Point balance.
        `/points leaderboard` - View the Clan Points leaderboard.
        `/pointstore rewards` - See what you can buy with your points.
        `/pointstore redeem` - Spend your points on a reward.
        `/osrs link` - Link your Discord account to your OSRS name.
        `/events view` - See all currently active events.
        """
        admin_commands = """
        `/sotw start` - Manually start a new SOTW competition.
        `/sotw poll` - Start a poll to choose the next SOTW.
        `/giveaway start` - Start a new giveaway with a prize and duration.
        `/raffle start` - Start a new raffle.
        `/raffle give_tickets` - Give raffle tickets to a member.
        `/raffle edit_tickets` - Set a member's total ticket count.
        `/raffle draw_now` - End the raffle and draw a winner immediately.
        `/raffle cancel` - Cancel the current raffle.
        `/bingo start` - Start a new clan bingo event.
        `/bingo submissions` - View and manage pending bingo submissions.
        `/pointstore addreward` - Add a new reward to the store.
        `/pointstore removereward` - Remove a reward from the store.
        `/pointstore togglereward` - Activate or deactivate a reward.
        `/admin announce` - Send a global announcement as the bot.
        `/admin manage_points` - Add or remove Clan Points from a member.
        `/admin award_sotw_winners` - Manually award points for a past SOTW.
        """
        embed.add_field(name="✅ Member Commands", value=textwrap.dedent(member_commands), inline=False)
        embed.add_field(name="👑 Admin Commands", value=textwrap.dedent(admin_commands), inline=False)
        embed.set_footer(text="Let the games begin!")
        await ctx.respond(embed=embed, ephemeral=True)

def setup(bot):
    bot.add_cog(Misc(bot))