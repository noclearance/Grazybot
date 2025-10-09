import discord
from discord.ext import commands
import aiohttp
import math

class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # 🧮 Combat level calculator (based on RS Wiki formula)
    @staticmethod
    def calculate_combat(stats):
        def get_level(skill):
            xp = skill.get("experience", 0)
            return math.floor(((math.sqrt(2 * xp + 1) - 1) / 2))

        attack = get_level(stats.get("attack", {}))
        strength = get_level(stats.get("strength", {}))
        defence = get_level(stats.get("defence", {}))
        hitpoints = get_level(stats.get("hitpoints", {}))
        ranged = get_level(stats.get("ranged", {}))
        magic = get_level(stats.get("magic", {}))
        prayer = get_level(stats.get("prayer", {}))

        base = 0.25 * (defence + hitpoints + math.floor(prayer / 2))
        melee = 0.325 * (attack + strength)
        range = 0.325 * (math.floor(ranged * 1.5))
        mage = 0.325 * (math.floor(magic * 1.5))

        return round(base + max(melee, range, mage), 1)

    # ✅ Slash command to fetch full stats
    @commands.slash_command(name="stats", description="Get full OSRS stats from WOM")
    async def stats(self, ctx, username: str):
        await ctx.defer()
        url = f"https://api.wiseoldman.net/v2/players/{username}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return await ctx.respond(f"❌ Could not fetch stats for **{username}**.")

                data = await resp.json()
                snapshot = data.get("latestSnapshot", {}).get("data", {})
                boss_data = {m.get("metric"): m for m in snapshot.values() if m.get("metric") in ["zulrah", "vorkath", "jad"]}
                skill_data = {m.get("metric"): m for m in snapshot.values() if m.get("metric") in [
                    "attack", "strength", "defence", "hitpoints", "ranged", "magic", "prayer"
                ]}

                total_xp = sum(m.get("experience", 0) for m in snapshot.values() if m.get("metric", "").startswith("skill"))
                combat = Stats.calculate_combat(skill_data)

                embed = discord.Embed(
                    title=f"{username}'s Stats",
                    description=f"🧠 Combat Level: **{combat}**\n📈 Total XP: **{total_xp:,}**",
                    color=discord.Color.gold()
                )

                # 🧪 Skill breakdown
                for skill in ["attack", "strength", "defence", "hitpoints", "ranged", "magic", "prayer"]:
                    xp = skill_data.get(skill, {}).get("experience", 0)
                    level = math.floor(((math.sqrt(2 * xp + 1) - 1) / 2))
                    emoji = {
                        "attack": "🗡️", "strength": "💪", "defence": "🛡️",
                        "hitpoints": "❤️", "ranged": "🏹", "magic": "✨", "prayer": "🕊️"
                    }.get(skill, "🔹")
                    embed.add_field(name=f"{emoji} {skill.title()}", value=f"Level: **{level}**\nXP: `{xp:,}`", inline=True)

                # 💀 Boss KC
                for boss in ["zulrah", "vorkath", "jad"]:
                    kc = boss_data.get(boss, {}).get("kills", 0)
                    emoji = {"zulrah": "🐍", "vorkath": "🧊", "jad": "🔥"}.get(boss, "💀")
                    embed.add_field(name=f"{emoji} {boss.title()}", value=f"Kills: `{kc}`", inline=True)

                await ctx.respond(embed=embed)

    # 📊 Slash command to compare two players
    @commands.slash_command(name="compare", description="Compare two players' combat and XP")
    async def compare(self, ctx, user1: str, user2: str):
        await ctx.defer()

        async def fetch_snapshot(username):
            url = f"https://api.wiseoldman.net/v2/players/{username}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    snapshot = data.get("latestSnapshot", {}).get("data", {})
                    skills = {m.get("metric", ""): m for m in snapshot.values() if m.get("metric", "").startswith("skill")}
                    total_xp = sum(m.get("experience", 0) for m in skills.values())
                    combat = Stats.calculate_combat(skills)
                    return {"xp": total_xp, "combat": combat}

        stats1 = await fetch_snapshot(user1)
        stats2 = await fetch_snapshot(user2)

        if not stats1 or not stats2:
            return await ctx.respond("❌ Failed to fetch one or both players.")

        embed = discord.Embed(
            title=f"📊 {user1} vs {user2}",
            description=(
                f"**{user1}**\n🧠 Combat: {stats1['combat']}\n📈 XP: {stats1['xp']:,}\n\n"
                f"**{user2}**\n🧠 Combat: {stats2['combat']}\n📈 XP: {stats2['xp']:,}"
            ),
            color=discord.Color.blue()
        )
        await ctx.respond(embed=embed)

def setup(bot):
    bot.add_cog(Stats(bot))

