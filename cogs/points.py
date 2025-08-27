 cogs/points.py
import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
import os

# Import helper functions from our utils file
from .utils import get_db_connection, award_points

class Points(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        # Load the DEBUG_GUILD_ID when the cog is initialized
        self.debug_guild_id = int(os.getenv('DEBUG_GUILD_ID'))

    # --- Points Command Group ---
    points_group = SlashCommandGroup("points", "Commands related to Clan Points.")

    @points_group.command(name="view", description="Check your current Clan Point balance.")
    async def view_points(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT points FROM clan_points WHERE discord_id = %s", (ctx.author.id,))
        point_data = cursor.fetchone()
        cursor.close()
        conn.close()
        
        current_points = point_data[0] if point_data else 0
        await ctx.respond(f"You currently have **{current_points}** Clan Points.", ephemeral=True)

    @points_group.command(name="leaderboard", description="View the Clan Points leaderboard.")
    async def leaderboard(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT discord_id, points FROM clan_points ORDER BY points DESC LIMIT 10")
        leaders = cursor.fetchall()
        cursor.close()
        conn.close()

        embed = discord.Embed(title="🏆 Clan Points Leaderboard 🏆", color=discord.Color.gold())
        if not leaders:
            embed.description = "No one has earned any points yet."
        else:
            leaderboard_text = ""
            for i, (user_id, points_val) in enumerate(leaders):
                rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i + 1, f"`{i + 1}.`")
                try:
                    member = await ctx.guild.fetch_member(user_id)
                    leaderboard_text += f"{rank_emoji} **{member.display_name}**: {points_val:,} points\n"
                except discord.NotFound:
                    continue
            embed.description = leaderboard_text
        
        await ctx.respond(embed=embed)

    # --- Pointstore Command Group ---
    pointstore = points_group.create_subgroup("store", "Manage and redeem clan points.")

    @pointstore.command(name="rewards", description="View available rewards in the point store.")
    async def view_rewards(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            cursor.execute("SELECT * FROM rewards WHERE is_active = TRUE ORDER BY point_cost ASC")
            rewards = cursor.fetchall()
            embed = discord.Embed(title="🛍️ Clan Point Store Rewards 🛍️", color=discord.Color.gold())
            if not rewards:
                embed.description = "There are currently no active rewards in the point store."
            else:
                for reward in rewards:
                    role_reward_text = ""
                    cursor.execute("SELECT role_id FROM role_rewards WHERE reward_id = %s", (reward['id'],))
                    role_reward_data = cursor.fetchone()
                    if role_reward_data:
                        role = ctx.guild.get_role(role_reward_data['role_id'])
                        if role:
                            role_reward_text = f"\n**Role:** {role.mention}"
                        else:
                            role_reward_text = f"\n**Role ID:** {role_reward_data['role_id']} (Role not found)"
                    embed.add_field(
                        name=f"{reward['reward_name']} ({reward['point_cost']} points)",
                        value=f"{reward['description'] or 'No description provided.'}{role_reward_text}",
                        inline=False
                    )
            embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
            embed.timestamp = datetime.now(timezone.utc)
            await ctx.respond(embed=embed)
        except Exception as e:
            print(f"Error fetching rewards: {e}")
            await ctx.respond("An error occurred while fetching rewards.", ephemeral=True)
        finally:
            if cursor: cursor.close()
            if conn: conn.close()

    @pointstore.command(name="redeem", description="Redeem a reward from the point store.")
    async def redeem_reward(self, ctx: discord.ApplicationContext, reward_name: str):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            cursor.execute("SELECT * FROM rewards WHERE reward_name ILIKE %s AND is_active = TRUE", (reward_name,))
            reward = cursor.fetchone()
            if not reward:
                return await ctx.respond(f"Reward '{reward_name}' not found or is not currently active.", ephemeral=True)
            
            cursor.execute("SELECT points FROM clan_points WHERE discord_id = %s", (ctx.author.id,))
            user_points_data = cursor.fetchone()
            current_points = user_points_data['points'] if user_points_data else 0
            
            if current_points < reward['point_cost']:
                return await ctx.respond(f"You don't have enough points. You need {reward['point_cost']}, you have {current_points}.", ephemeral=True)

            new_balance = current_points - reward['point_cost']
            cursor.execute("UPDATE clan_points SET points = %s WHERE discord_id = %s", (new_balance, ctx.author.id))
            cursor.execute(
                "INSERT INTO redeem_transactions (user_id, reward_id, reward_name, point_cost) VALUES (%s, %s, %s, %s)",
                (ctx.author.id, reward['id'], reward['reward_name'], reward['point_cost'])
            )
            
            cursor.execute("SELECT role_id FROM role_rewards WHERE reward_id = %s", (reward['id'],))
            role_reward_data = cursor.fetchone()
            if role_reward_data:
                role = ctx.guild.get_role(role_reward_data['role_id'])
                if role:
                    await ctx.author.add_roles(role)
                    await ctx.followup.send(f"You have successfully redeemed '{reward['reward_name']}'! The role **{role.name}** has been added. New balance: **{new_balance}**.", ephemeral=False)
                else:
                    await ctx.followup.send(f"You have redeemed '{reward['reward_name']}'! Role not found. Please contact an admin. New balance: **{new_balance}**.", ephemeral=False)
            else:
                await ctx.followup.send(f"You have redeemed '{reward['reward_name']}'! Please contact an admin for fulfillment. New balance: **{new_balance}**.", ephemeral=False)
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Error redeeming reward: {e}")
            await ctx.respond("An error occurred while redeeming the reward.", ephemeral=True)
        finally:
            cursor.close()
            conn.close()
            
    @pointstore.command(name="addreward", description="ADMIN: Add a new reward to the point store.")
    @discord.default_permissions(manage_guild=True)
    async def add_reward(self, ctx: discord.ApplicationContext, name: str, cost: int, description: Option(str, "Optional description.", required=False), role: Option(discord.Role, "Optional role to link.", required=False)):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO rewards (reward_name, point_cost, description) VALUES (%s, %s, %s) RETURNING id",
                (name, cost, description)
            )
            reward_id = cursor.fetchone()[0]
            if role:
                cursor.execute(
                    "INSERT INTO role_rewards (reward_id, role_id) VALUES (%s, %s)",
                    (reward_id, role.id)
                )
                await ctx.respond(f"Reward '{name}' added and linked to role {role.mention}.", ephemeral=True)
            else:
                await ctx.respond(f"Reward '{name}' added with a cost of {cost} points.", ephemeral=True)
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            await ctx.respond(f"A reward with the name '{name}' already exists.", ephemeral=True)
        except Exception as e:
            conn.rollback()
            print(f"Error adding reward: {e}")
            await ctx.respond("An error occurred while adding the reward.", ephemeral=True)
        finally:
            cursor.close()
            conn.close()

    @pointstore.command(name="removereward", description="ADMIN: Remove a reward from the point store.")
    @discord.default_permissions(manage_guild=True)
    async def remove_reward(self, ctx: discord.ApplicationContext, reward_name: str):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM rewards WHERE reward_name ILIKE %s RETURNING id", (reward_name,))
            deleted_reward = cursor.fetchone()
            if deleted_reward:
                conn.commit()
                await ctx.respond(f"Reward '{reward_name}' removed.", ephemeral=True)
            else:
                await ctx.respond(f"Reward '{reward_name}' not found.", ephemeral=True)
        except Exception as e:
            conn.rollback()
            print(f"Error removing reward: {e}")
            await ctx.respond("An error occurred.", ephemeral=True)
        finally:
            cursor.close()
            conn.close()

    @pointstore.command(name="togglereward", description="ADMIN: Toggle the active status of a reward.")
    @discord.default_permissions(manage_guild=True)
    async def toggle_reward(self, ctx: discord.ApplicationContext, reward_name: str):
        await ctx.defer(ephemeral=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, is_active FROM rewards WHERE reward_name ILIKE %s", (reward_name,))
            reward_data = cursor.fetchone()
            if not reward_data:
                return await ctx.respond(f"Reward '{reward_name}' not found.", ephemeral=True)
            new_status = not reward_data[1]
            cursor.execute("UPDATE rewards SET is_active = %s WHERE id = %s", (new_status, reward_data[0]))
            conn.commit()
            status_text = "active" if new_status else "inactive"
            await ctx.respond(f"Reward '{reward_name}' is now **{status_text}**.", ephemeral=True)
        except Exception as e:
            conn.rollback()
            print(f"Error toggling reward: {e}")
            await ctx.respond("An error occurred.", ephemeral=True)
        finally:
            cursor.close()
            conn.close()

# This function is required for the cog to be loaded by the bot
def setup(bot):
    bot.add_cog(Points(bot))