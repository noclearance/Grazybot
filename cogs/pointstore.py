# cogs/pointstore.py
# Commands for managing and using the clan point store.

import discord
import asyncpg
import logging
from discord import SlashCommandGroup, Option
from discord.ext import commands

from core.bot import GrazyBot

logger = logging.getLogger(__name__)

class PointStore(commands.Cog):
    """Cog for the clan point store."""

    def __init__(self, bot: GrazyBot):
        self.bot = bot

    store_group = SlashCommandGroup("pointstore", "Manage and redeem clan points.")
    admin_group = store_group.create_subgroup(
        "admin",
        "Admin commands for the point store.",
        default_member_permissions=discord.Permissions(manage_guild=True)
    )

    @store_group.command(name="rewards", description="View available rewards in the point store.")
    async def view_rewards(self, ctx: discord.ApplicationContext):
        """Displays all active rewards available for purchase with points."""
        await ctx.defer()
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
                        role = ctx.guild.get_role(reward['role_id'])
                        desc += f"\n**Grants Role:** {role.mention if role else 'Unknown Role'}"
                    reward_text.append(f"### {reward['reward_name']} - `{reward['point_cost']:,}` points\n{desc}")
                embed.description = "\n\n".join(reward_text)
            await ctx.respond(embed=embed)
        except Exception as e:
            logger.error(f"Error fetching point store rewards: {e}", exc_info=True)
            await ctx.respond("An error occurred while fetching rewards.", ephemeral=True)

    @store_group.command(name="redeem", description="Redeem a reward from the point store.")
    async def redeem_reward(self, ctx: discord.ApplicationContext, reward_name: str):
        """Allows a user to spend their points on a reward."""
        await ctx.defer(ephemeral=True)
        try:
            async with self.bot.db_pool.acquire() as conn, conn.transaction():
                reward = await conn.fetchrow("SELECT * FROM rewards WHERE reward_name ILIKE $1 AND is_active = TRUE", reward_name)
                if not reward:
                    return await ctx.respond(f"Reward '{reward_name}' not found or is currently inactive.", ephemeral=True)

                user_points = await conn.fetchval("SELECT points FROM clan_points WHERE discord_id = $1", ctx.author.id) or 0
                if user_points < reward['point_cost']:
                    return await ctx.respond(f"You need {reward['point_cost']:,} points, but you only have {user_points:,}.", ephemeral=True)

                new_balance = user_points - reward['point_cost']
                await conn.execute("UPDATE clan_points SET points = $1 WHERE discord_id = $2", new_balance, ctx.author.id)
                
                await conn.execute(
                    "INSERT INTO redeem_transactions (user_id, reward_id, reward_name, point_cost) VALUES ($1, $2, $3, $4)",
                    ctx.author.id, reward['id'], reward['reward_name'], reward['point_cost']
                )

                # Handle role rewards
                role_reward = await conn.fetchrow("SELECT role_id FROM role_rewards WHERE reward_id = $1", reward['id'])
                if role_reward and role_reward['role_id']:
                    role = ctx.guild.get_role(role_reward['role_id'])
                    if role:
                        await ctx.author.add_roles(role, reason=f"Redeemed '{reward['reward_name']}' from point store.")
                        await ctx.followup.send(f"You redeemed **{reward['reward_name']}**! The role **{role.name}** has been added. Your new balance is `{new_balance:,}`.", ephemeral=True)
                    else:
                        await ctx.followup.send(f"Redemption successful, but the associated role (ID: {role_reward['role_id']}) was not found.", ephemeral=True)
                else:
                    await ctx.followup.send(f"You redeemed **{reward['reward_name']}**! Your new balance is `{new_balance:,}`. Please contact an admin for fulfillment.", ephemeral=True)

                logger.info(f"{ctx.author} redeemed '{reward['reward_name']}' for {reward['point_cost']} points.")

        except discord.Forbidden:
            await ctx.followup.send("Redemption successful, but I lack permissions to assign the role. Please contact an admin.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error during reward redemption for {ctx.author}: {e}", exc_info=True)
            await ctx.respond("An error occurred during the redemption process.", ephemeral=True)

    @admin_group.command(name="add", description="Add a new reward to the point store.")
    async def add_reward(self, ctx: discord.ApplicationContext,
                         name: Option(str, "The name of the reward."),
                         cost: Option(int, "How many points it costs."),
                         description: Option(str, "A short description of the reward.", required=False),
                         role: Option(discord.Role, "Optional role to grant upon redemption.", required=False)):
        """Adds a new reward to the database."""
        await ctx.defer(ephemeral=True)
        try:
            async with self.bot.db_pool.acquire() as conn:
                reward_id = await conn.fetchval(
                    "INSERT INTO rewards (reward_name, point_cost, description) VALUES ($1, $2, $3) RETURNING id",
                    name, cost, description
                )
                if role:
                    await conn.execute("INSERT INTO role_rewards (reward_id, role_id) VALUES ($1, $2)", reward_id, role.id)
            await ctx.respond(f"Reward '{name}' added to the store.", ephemeral=True)
            logger.info(f"Admin {ctx.author} added new reward: {name}")
        except asyncpg.exceptions.UniqueViolationError:
            await ctx.respond(f"A reward named '{name}' already exists.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error adding new reward '{name}': {e}", exc_info=True)
            await ctx.respond("An error occurred while adding the reward.", ephemeral=True)

    @admin_group.command(name="remove", description="Permanently remove a reward from the point store.")
    async def remove_reward(self, ctx: discord.ApplicationContext, reward_name: str):
        """Removes a reward from the database."""
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            # The database should cascade delete from role_rewards.
            result = await conn.execute("DELETE FROM rewards WHERE reward_name ILIKE $1", reward_name)
            if 'DELETE 1' in result:
                await ctx.respond(f"Reward '{reward_name}' has been permanently removed.", ephemeral=True)
                logger.info(f"Admin {ctx.author} removed reward: {reward_name}")
            else:
                await ctx.respond(f"Reward '{reward_name}' not found.", ephemeral=True)

    @admin_group.command(name="toggle", description="Activate or deactivate a reward in the store.")
    async def toggle_reward(self, ctx: discord.ApplicationContext, reward_name: str, is_active: bool):
        """Toggles the active status of a reward."""
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE rewards SET is_active = $1 WHERE reward_name ILIKE $2",
                is_active, reward_name
            )
            if 'UPDATE 1' in result:
                status = "activated" if is_active else "deactivated"
                await ctx.respond(f"Reward '{reward_name}' has been {status}.", ephemeral=True)
                logger.info(f"Admin {ctx.author} {status} reward: {reward_name}")
            else:
                await ctx.respond(f"Reward '{reward_name}' not found.", ephemeral=True)

async def setup(bot: GrazyBot):
    await bot.add_cog(PointStore(bot))