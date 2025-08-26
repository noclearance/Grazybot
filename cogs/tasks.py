# cogs/tasks.py
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import aiohttp
import os
import psycopg2
import psycopg2.extras

# Import helper functions from our utils file
from .utils import (
    get_db_connection,
    generate_recap_text,
    award_points,
    draw_raffle_winner,
    end_giveaway,
    generate_announcement_json,
    ai_model 
)

class BackgroundTasks(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        # Load environment variables within the cog
        self.recap_channel_id = int(os.getenv('RECAP_CHANNEL_ID'))
        self.sotw_channel_id = int(os.getenv('SOTW_CHANNEL_ID'))
        self.raffle_channel_id = int(os.getenv('RAFFLE_CHANNEL_ID'))
        self.announcements_channel_id = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))
        self.wom_clan_id = os.getenv('WOM_CLAN_ID')
        self.debug_guild_id = int(os.getenv('DEBUG_GUILD_ID'))

        # Start the tasks
        self.periodic_event_reminder.start()
        self.event_manager.start()

    def cog_unload(self):
        self.periodic_event_reminder.cancel()
        self.event_manager.cancel()

    @tasks.loop(hours=4)
    async def periodic_event_reminder(self):
        await self.bot.wait_until_ready()
        announcements_channel = self.bot.get_channel(self.announcements_channel_id)
        if not announcements_channel:
            return

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM active_competitions WHERE ends_at > NOW() ORDER BY ends_at DESC LIMIT 1")
        sotw = cursor.fetchone()
        cursor.execute("SELECT * FROM raffles WHERE ends_at > NOW() AND winner_id IS NULL LIMIT 1")
        raffle = cursor.fetchone()
        cursor.execute("SELECT * FROM giveaways WHERE ends_at > NOW() AND is_active = TRUE LIMIT 1")
        giveaway = cursor.fetchone()
        conn.close()

        event_summary = ""
        if sotw: event_summary += f"- A Skill of the Week competition for **{sotw['title']}** is underway!\n"
        if raffle: event_summary += f"- A raffle for the legendary **{raffle['prize']}** is active! Use `/raffle enter`.\n"
        if giveaway: event_summary += f"- A giveaway for **{giveaway['prize']}** is happening now! Find the message and click the button to enter.\n"
        
        if not event_summary:
            return
        
        prompt = f"""
        You are TaskmasterGPT, the wise and ancient lore-keeper for a clan of warriors.
        Your task is to write a bulletin summarizing the clan's active events. Your tone is epic, grand, and encouraging.
        Use the following information to compose your message. Frame it as a call to continue the good fight and remind everyone of the glories to be won.
        Active Events:
        {event_summary}
        Write a compelling summary in a few short paragraphs.
        """
        
        try:
            response = await ai_model.generate_content_async(prompt)
            description = response.text
            embed = discord.Embed(title="📜 The Taskmaster's Bulletin 📜", description=description, color=discord.Color.dark_gold())
            embed.set_footer(text="Seize the day, warriors!")
            await announcements_channel.send(embed=embed)
        except Exception as e:
            print(f"Failed to generate or send periodic reminder: {e}")

    @tasks.loop(minutes=5)
    async def event_manager(self):
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        
        recap_channel = self.bot.get_channel(self.recap_channel_id)
        if recap_channel and now.weekday() == 6 and now.hour == 19 and now.minute < 5:
            url = f"https://api.wiseoldman.net/v2/groups/{self.wom_clan_id}/gained?period=week&metric=overall"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        recap_text = await generate_recap_text(data)
                        embed = discord.Embed(title="📈 Weekly Recap from the Taskmaster", description=recap_text, color=discord.Color.from_rgb(100, 150, 255))
                        embed.set_footer(text=f"Recap for the week ending {now.strftime('%B %d, %Y')}")
                        await recap_channel.send(embed=embed)
        
        sotw_channel = self.bot.get_channel(self.sotw_channel_id)
        if sotw_channel:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT * FROM active_competitions")
            competitions = cursor.fetchall()
            for comp in competitions:
                ends_at = comp['ends_at']
                starts_at = comp['starts_at']
                if now > ends_at and not comp['winners_awarded']:
                    details_url = f"https://api.wiseoldman.net/v2/competitions/{comp['id']}"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(details_url) as response:
                            if response.status == 200:
                                comp_data = await response.json()
                                point_values = [100, 50, 25]
                                for i, participant in enumerate(comp_data.get('participations', [])[:3]):
                                    osrs_name = participant['player']['displayName']
                                    # This part requires another DB connection, which is fine
                                    with get_db_connection() as conn_inner:
                                        with conn_inner.cursor() as cursor_inner:
                                            cursor_inner.execute("SELECT discord_id FROM user_links WHERE osrs_name = %s", (osrs_name,))
                                            user_data = cursor_inner.fetchone()
                                            if user_data:
                                                guild = self.bot.get_guild(self.debug_guild_id)
                                                if guild:
                                                    member = guild.get_member(user_data[0])
                                                    if member:
                                                        await award_points(member, point_values[i], f"placing #{i+1} in the {comp['title']} SOTW")
                    cursor.execute("UPDATE active_competitions SET winners_awarded = TRUE WHERE id = %s", (comp['id'],))

                if not comp['final_ping_sent'] and (ends_at - now) <= timedelta(hours=1):
                    reminder_embed = discord.Embed(title="⏳ Final Hour!", description=f"The **{comp['title']}** competition ends in less than an hour!", color=discord.Color.red(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                    await sotw_channel.send(content="@everyone", embed=reminder_embed)
                    cursor.execute("UPDATE active_competitions SET final_ping_sent = TRUE WHERE id = %s", (comp['id'],))
                elif not comp['midway_ping_sent'] and now >= starts_at + ((ends_at - starts_at) / 2):
                    midway_embed = discord.Embed(title="½ Midway Point Reached!", description=f"The **{comp['title']}** competition is halfway through!", color=discord.Color.yellow(), url=f"https://wiseoldman.net/competitions/{comp['id']}")
                    await sotw_channel.send(embed=midway_embed)
                    cursor.execute("UPDATE active_competitions SET midway_ping_sent = TRUE WHERE id = %s", (comp['id'],))
            conn.commit()
            cursor.close()
            conn.close()

        raffle_channel = self.bot.get_channel(self.raffle_channel_id)
        if raffle_channel:
            with get_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                    cursor.execute("SELECT * FROM raffles WHERE ends_at < %s AND winner_id IS NULL LIMIT 1", (now,))
                    raffle_data = cursor.fetchone()
                    if raffle_data:
                        await draw_raffle_winner(self.bot, self.raffle_channel_id)

        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor_gw:
                cursor_gw.execute("SELECT * FROM giveaways WHERE ends_at < %s AND is_active = TRUE", (now,))
                ended_giveaways = cursor_gw.fetchall()
                for giveaway in ended_giveaways:
                    await end_giveaway(giveaway)
                
                cursor_gw.execute("SELECT message_id, channel_id FROM giveaways WHERE is_active = TRUE")
                active_giveaways = cursor_gw.fetchall()
                for giveaway in active_giveaways:
                    try:
                        cursor_gw.execute("SELECT COUNT(user_id) FROM giveaway_entries WHERE message_id = %s", (giveaway['message_id'],))
                        entry_count = cursor_gw.fetchone()[0]
                        channel = self.bot.get_channel(giveaway['channel_id'])
                        if not channel: continue
                        message = await channel.fetch_message(giveaway['message_id'])
                        embed = message.embeds[0]
                        entry_field_index = -1
                        for i, field in enumerate(embed.fields):
                            if "Entries" in field.name:
                                entry_field_index = i
                                break
                        new_entry_text = f"👥 **Entries:** {entry_count}"
                        if entry_field_index != -1:
                            if embed.fields[entry_field_index].value != new_entry_text:
                                embed.set_field_at(entry_field_index, name="Entries", value=new_entry_text, inline=True)
                                await message.edit(embed=embed)
                        else:
                            if len(embed.fields) < 3:
                                embed.add_field(name="Entries", value=new_entry_text, inline=True)
                                await message.edit(embed=embed)
                    except discord.NotFound:
                        cursor_gw.execute("UPDATE giveaways SET is_active = FALSE WHERE message_id = %s", (giveaway['message_id'],))
                        conn.commit()
                    except Exception as e:
                        print(f"Error updating giveaway entry count: {e}")

def setup(bot):
    bot.add_cog(BackgroundTasks(bot))