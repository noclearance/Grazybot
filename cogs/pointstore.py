# cogs/pointstore.py
# Commands for managing and using the clan point store.

# cogs/pointstore.py
# Commands for managing and using the clan point store.

import discord
import asyncpg
import logging
from discord import app_commands
from discord.ext import commands

from core.bot import GrazyBot

logger = logging.getLogger(__name__)

class PointStore(commands.Cog):
    """Cog for the clan point store."""

    def __init__(self, bot: GrazyBot):
        self.bot = bot

    store_group = app_commands.Group(name="pointstore", description="Manage and redeem clan points.")
    admin_group = app_commands.Group(name="admin", parent=store_group, description="Admin commands for the point store.", default_permissions=discord.Permissions(manage_guild=True))

    @store_group.command(name="rewards", description="View available rewards in the point store.")
    async def view_rewards(self, interaction: discord.Interaction):
        """Displays all active rewards available for purchase with points."""
        await interaction.response.defer()
        try:
            async with self.bot.db_pool.acquire() as conn:
                rewards = await conn.fetch(
                    """
                    SELECT r.reward_name, r.point_cost, r.description, rr.role_id
                    FROM rewards r
                    LEFT JOIN role_rewards rr ON r.id = rr.reward_id
                    WHERE r.is_active = TRUE
                    ORDER BY r.point_cost ASC
                    """
                )
            embed = discord.Embed(title="🎁 Clan Point Store Rewards", color=discord.Color.gold())
            if not rewards:
                embed.description = "There are currently no active rewards in the store."
            else:
                reward_text = []
                for reward in rewards:
                    desc = reward['description'] or 'No description provided.'
                    if reward['role_id']:
                        role = interaction.guild.get_role(reward['role_id'])
                        desc += f"\n**Grants Role:** {role.mention if role else 'Unknown Role'}"
                    reward_text.append(f"### {reward['reward_name']} - `{reward['point_cost']:,}` points\n{desc}")
                embed.description = "\n\n".join(reward_text)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error fetching point store rewards: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching rewards.", ephemeral=True)

    @store_group.command(name="redeem", description="Redeem a reward from the point store.")
    async def redeem_reward(self, interaction: discord.Interaction, reward_name: str):
        """Allows a user to spend their points on a reward."""
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.bot.db_pool.acquire() as conn, conn.transaction():
                reward = await conn.fetchrow("SELECT * FROM rewards WHERE reward_name ILIKE $1 AND is_active = TRUE", reward_name)
                if not reward:
                    return await interaction.followup.send(f"Reward '{reward_name}' not found or is currently inactive.", ephemeral=True)

                user_points = await conn.fetchval("SELECT points FROM clan_points WHERE discord_id = $1", interaction.user.id) or 0
                if user_points < reward['point_cost']:
                    return await interaction.followup.send(f"You need {reward['point_cost']:,} points, but you only have {user_points:,}.", ephemeral=True)

                new_balance = user_points - reward['point_cost']
                await conn.execute("UPDATE clan_points SET points = $1 WHERE discord_id = $2", new_balance, interaction.user.id)
                
                await conn.execute(
                    "INSERT INTO redeem_transactions (user_id, reward_id, reward_name, point_cost) VALUES ($1, $2, $3, $4)",
                    interaction.user.id, reward['id'], reward['reward_name'], reward['point_cost']
                )

                # Handle role rewards
                role_reward = await conn.fetchrow("SELECT role_id FROM role_rewards WHERE reward_id = $1", reward['id'])
                if role_reward and role_reward['role_id']:
                    role = interaction.guild.get_role(role_reward['role_id'])
                    if role:
                        await interaction.user.add_roles(role, reason=f"Redeemed '{reward['reward_name']}' from point store.")
                        await interaction.followup.send(f"You redeemed **{reward['reward_name']}**! The role **{role.name}** has been added. Your new balance is `{new_balance:,}`.", ephemeral=True)
                    else:
                        await interaction.followup.send(f"Redemption successful, but the associated role (ID: {role_reward['role_id']}) was not found.", ephemeral=True)
                else:
                    await interaction.followup.send(f"You redeemed **{reward['reward_name']}**! Your new balance is `{new_balance:,}`. Please contact an admin for fulfillment.", ephemeral=True)

                logger.info(f"{interaction.user} redeemed '{reward['reward_name']}' for {reward['point_cost']} points.")

        except discord.Forbidden:
            await interaction.followup.send("Redemption successful, but I lack permissions to assign the role. Please contact an admin.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error during reward redemption for {interaction.user}: {e}", exc_info=True)
            await interaction.followup.send("An error occurred during the redemption process.", ephemeral=True)

    @admin_group.command(name="add", description="Add a new reward to the point store.")
    async def add_reward(self, interaction: discord.Interaction,
                         name: str,
                         cost: int,
                         description: str = None,
                         role: discord.Role = None):
        """Adds a new reward to the database."""
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.bot.db_pool.acquire() as conn:
                reward_id = await conn.fetchval(
                    "INSERT INTO rewards (reward_name, point_cost, description) VALUES ($1, $2, $3) RETURNING id",
                    name, cost, description
                )
                if role:
                    await conn.execute("INSERT INTO role_rewards (reward_id, role_id) VALUES ($1, $2)", reward_id, role.id)
            await interaction.followup.send(f"Reward '{name}' added to the store.", ephemeral=True)
            logger.info(f"Admin {interaction.user} added new reward: {name}")
        except asyncpg.exceptions.UniqueViolationError:
            await interaction.followup.send(f"A reward named '{name}' already exists.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error adding new reward '{name}': {e}", exc_info=True)
            await interaction.followup.send("An error occurred while adding the reward.", ephemeral=True)

    @admin_group.command(name="remove", description="Permanently remove a reward from the point store.")
    async def remove_reward(self, interaction: discord.Interaction, reward_name: str):
        """Removes a reward from the database."""
        await interaction.response.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            # The database should cascade delete from role_rewards.
            result = await conn.execute("DELETE FROM rewards WHERE reward_name ILIKE $1", reward_name)
            if 'DELETE 1' in result:
                await interaction.followup.send(f"Reward '{reward_name}' has been permanently removed.", ephemeral=True)
                logger.info(f"Admin {interaction.user} removed reward: {reward_name}")
            else:
                await interaction.followup.send(f"Reward '{reward_name}' not found.", ephemeral=True)

    @admin_group.command(name="toggle", description="Activate or deactivate a reward in the store.")
    async def toggle_reward(self, interaction: discord.Interaction, reward_name: str, is_active: bool):
        """Toggles the active status of a reward."""
        await interaction.response.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE rewards SET is_active = $1 WHERE reward_name ILIKE $2",
                is_active, reward_name
            )
            if 'UPDATE 1' in result:
                status = "activated" if is_active else "deactivated"
                await interaction.followup.send(f"Reward '{reward_name}' has been {status}.", ephemeral=True)
                logger.info(f"Admin {interaction.user} {status} reward: {reward_name}")
            else:
                await interaction.followup.send(f"Reward '{reward_name}' not found.", ephemeral=True)

async def setup(bot: GrazyBot):
    await bot.add_cog(PointStore(bot))