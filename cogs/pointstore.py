# bot/cogs/pointstore.py
# Commands for managing and using the clan point store.

import discord
import asyncpg
from discord.commands import SlashCommandGroup, Option
from discord.ext import commands

class PointStore(commands.Cog):
    """Cog for the clan point store."""

    def __init__(self, bot):
        self.bot = bot

    pointstore = SlashCommandGroup("pointstore", "Manage and redeem clan points.")

    @pointstore.command(name="rewards", description="View available rewards in the point store.")
    async def view_rewards(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        async with self.bot.db_pool.acquire() as conn:
            rewards = await conn.fetch("""
                SELECT r.reward_name, r.point_cost, r.description, rr.role_id 
                FROM rewards r
                LEFT JOIN role_rewards rr ON r.id = rr.reward_id
                WHERE r.is_active = TRUE 
                ORDER BY r.point_cost ASC
            """)
            embed = discord.Embed(title="Clan Point Store Rewards", color=discord.Color.gold())
            if not rewards:
                embed.description = "There are currently no active rewards."
            else:
                for reward in rewards:
                    desc = reward['description'] or 'No description provided.'
                    if reward['role_id']:
                        role = ctx.guild.get_role(reward['role_id'])
                        desc += f"\n**Role:** {role.mention if role else 'Not Found'}"
                    embed.add_field(name=f"{reward['reward_name']} ({reward['point_cost']} points)", value=desc, inline=False)
            await ctx.respond(embed=embed)

    @pointstore.command(name="redeem", description="Redeem a reward from the point store.")
    async def redeem_reward(self, ctx: discord.ApplicationContext, reward_name: str):
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn, conn.transaction():
            reward = await conn.fetchrow("SELECT * FROM rewards WHERE reward_name ILIKE $1 AND is_active = TRUE", reward_name)
            if not reward:
                return await ctx.respond(f"Reward '{reward_name}' not found or is inactive.", ephemeral=True)
            
            user_points = await conn.fetchval("SELECT points FROM clan_points WHERE discord_id = $1", ctx.author.id) or 0
            if user_points < reward['point_cost']:
                return await ctx.respond(f"You need {reward['point_cost']} points, but you only have {user_points}.", ephemeral=True)
                
            new_balance = user_points - reward['point_cost']
            await conn.execute("UPDATE clan_points SET points = $1 WHERE discord_id = $2", new_balance, ctx.author.id)
            
            # Log the transaction
            await conn.execute("INSERT INTO redeem_transactions (user_id, reward_id, reward_name, point_cost) VALUES ($1, $2, $3, $4)",
                               ctx.author.id, reward['id'], reward['reward_name'], reward['point_cost'])

            # Handle role rewards
            role_reward = await conn.fetchrow("SELECT role_id FROM role_rewards WHERE reward_id = $1", reward['id'])
            if role_reward:
                role = ctx.guild.get_role(role_reward['role_id'])
                if role:
                    try:
                        await ctx.author.add_roles(role)
                        await ctx.followup.send(f"You redeemed '{reward['reward_name']}'! The role **{role.name}** has been added. New balance: **{new_balance}**.", ephemeral=False)
                    except discord.Forbidden:
                        await ctx.followup.send("Redemption successful, but I lack permissions to assign the role.", ephemeral=False)
                else:
                    await ctx.followup.send("Redemption successful, but the associated role was not found.", ephemeral=False)
            else:
                await ctx.followup.send(f"You redeemed '{reward['reward_name']}'! New balance: **{new_balance}**. Please contact an admin for fulfillment.", ephemeral=False)

    @pointstore.command(name="addreward", description="Add a new reward to the point store.")
    @commands.has_permissions(manage_guild=True)
    async def add_reward(self, ctx: discord.ApplicationContext, name: str, cost: int, 
                         description: Option(str, required=False), role: Option(discord.Role, required=False)):
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            try:
                reward_id = await conn.fetchval("INSERT INTO rewards (reward_name, point_cost, description) VALUES ($1, $2, $3) RETURNING id",
                                                name, cost, description)
                if role:
                    await conn.execute("INSERT INTO role_rewards (reward_id, role_id) VALUES ($1, $2)", reward_id, role.id)
                await ctx.respond(f"Reward '{name}' added.", ephemeral=True)
            except asyncpg.exceptions.UniqueViolationError:
                await ctx.respond(f"A reward named '{name}' already exists.", ephemeral=True)

    @pointstore.command(name="removereward", description="Remove a reward from the point store.")
    @commands.has_permissions(manage_guild=True)
    async def remove_reward(self, ctx: discord.ApplicationContext, reward_name: str):
        await ctx.defer(ephemeral=True)
        async with self.bot.db_pool.acquire() as conn:
            result = await conn.execute("DELETE FROM rewards WHERE reward_name ILIKE $1", reward_name)
            if 'DELETE 1' in result:
                await ctx.respond(f"Reward '{reward_name}' removed.", ephemeral=True)
            else:
                await ctx.respond(f"Reward '{reward_name}' not found.", ephemeral=True)

def setup(bot):
    bot.add_cog(PointStore(bot))