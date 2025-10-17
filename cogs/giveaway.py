# cogs/giveaway.py
# Contains commands for managing giveaways.

# cogs/giveaway.py
# Contains commands for managing giveaways.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import logging

from core.bot import GrazyBot
from core import config
from utils import time as time_utils, ai
from utils.views import GiveawayView

logger = logging.getLogger(__name__)

class Giveaway(commands.Cog):
    """Cog for managing giveaways with buttons."""
    
    def __init__(self, bot: GrazyBot):
        self.bot = bot

    giveaway_group = app_commands.Group(name="giveaway", description="Commands for managing giveaways.")

    @giveaway_group.command(name="start", description="Start a new giveaway.")
    @commands.has_permissions(manage_events=True)
    async def start_giveaway(self, interaction: discord.Interaction,
                             prize: str,
                             duration: str,
                             winners: int = 1,
                             reward_role: discord.Role = None):
        """Starts a new giveaway in the designated channel."""
        await interaction.response.defer(ephemeral=True)

        delta = time_utils.parse_duration(duration)
        if delta is None:
            return await interaction.followup.send("Invalid duration format. Use 'd' for days, 'h' for hours, 'm' for minutes.", ephemeral=True)
        
        ends_at = datetime.now(timezone.utc) + delta
        giveaway_channel = self.bot.get_channel(config.GIVEAWAY_CHANNEL_ID) # Assuming this is in config
        if not giveaway_channel:
            return await interaction.followup.send("Giveaway channel not found. Please configure it.", ephemeral=True)

        details = {"prize": prize, "winner_count": winners}
        ai_embed_data = await ai.generate_announcement_json("giveaway_start", details)
        embed = discord.Embed.from_dict(ai_embed_data)
        embed.add_field(name="Ends In", value=f"<t:{int(ends_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Winners", value=f"**{winners}**", inline=True)
        if reward_role:
            embed.add_field(name="Bonus Reward", value=f"Winner(s) will also receive the {reward_role.mention} role!", inline=False)
        
        try:
            view = GiveawayView(message_id=0, prize=prize) # Placeholder message_id
            giveaway_message = await giveaway_channel.send(embed=embed, view=view)

            # Update view with the actual message_id
            view.message_id = giveaway_message.id
            await giveaway_message.edit(view=view)

            async with self.bot.db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO giveaways (message_id, channel_id, prize, ends_at, winner_count, role_id) VALUES ($1, $2, $3, $4, $5, $6)",
                    giveaway_message.id, giveaway_channel.id, prize, ends_at, winners, reward_role.id if reward_role else None
                )
            await interaction.followup.send(f"Giveaway for **{prize}** has been started in {giveaway_channel.mention}!", ephemeral=True)
            logger.info(f"Giveaway started by {interaction.user}: {prize}")
        except Exception as e:
            logger.error(f"Failed to start giveaway: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while starting the giveaway.", ephemeral=True)

    @giveaway_group.command(name="entries", description="View the list of entrants for a giveaway.")
    @commands.has_permissions(manage_events=True)
    async def view_entries(self, interaction: discord.Interaction, message_id: str):
        """Displays the list of users who have entered a giveaway."""
        await interaction.response.defer(ephemeral=True)
        try:
            msg_id = int(message_id)
        except ValueError:
            return await interaction.followup.send("Invalid message ID format.", ephemeral=True)

        try:
            async with self.bot.db_pool.acquire() as conn:
                giveaway_data = await conn.fetchrow("SELECT * FROM giveaways WHERE message_id = $1", msg_id)
                if not giveaway_data:
                    return await interaction.followup.send("No giveaway found with that message ID.", ephemeral=True)

                entries = await conn.fetch("SELECT user_id FROM giveaway_entries WHERE message_id = $1", msg_id)
            
            embed = discord.Embed(title=f"Entries for '{giveaway_data['prize']}'", description=f"Total Entries: **{len(entries)}**", color=discord.Color.blue())
            
            if entries:
                entrant_list = []
                for entry in entries:
                    member = interaction.guild.get_member(entry['user_id'])
                    entrant_list.append(member.mention if member else f"User ID: {entry['user_id']}")

                # Paginate if the list is too long
                if len(entrant_list) > 50:
                     embed.description += "\n\nShowing first 50 entries."
                     entrant_list = entrant_list[:50]

                embed.description += "\n\n" + "\n".join(f"• {e}" for e in entrant_list)

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to get giveaway entries for message {message_id}: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching entries.", ephemeral=True)

async def setup(bot: GrazyBot):
    await bot.add_cog(Giveaway(bot))