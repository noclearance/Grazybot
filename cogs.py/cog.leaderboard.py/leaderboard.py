import discord
from discord.ext import commands
import aiohttp
import asyncio
import math

SKILL_METRICS = {
    "attack","strength","defence","hitpoints","ranged","prayer","magic",
    "cooking","woodcutting","fletching","fishing","firemaking","crafting",
    "smithing","mining","herblore","agility","thieving","slayer","farming",
    "runecraft","hunter","construction"
}

BOSS_METRICS = {"zulrah", "vorkath", "jad", "cerberus", "king_black_dragon"}

class Leaderboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        await self.session.close()

    async def fetch_wom_snapshot(self, rsn: str):
        url = f"https://api.wiseoldman.net/v2/players/{rsn}"
        async with self.session.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("latestSnapshot", {}).get("data", {})

    async def compute_xp(self, rsn: str):
        snapshot = await self.fetch_wom_snapshot(rsn)
        if not snapshot:
            return None
        xp = 0
        for item in snapshot.values():
            metric = item.get("metric", "")
            if metric in SKILL_METRICS or metric.startswith("skill"):
                xp += item.get("experience", 0)
        return xp

    async def compute_kc(self, rsn: str):
        snapshot = await self.fetch_wom_snapshot(rsn)
        if not snapshot:
            return None
        kc_total = 0
        for item in snapshot.values():
            metric = item.get("metric", "")
            if metric in BOSS_METRICS:
                kc_total += item.get("kills", 0) or 0
            # some metrics may use different keys; resilient access above
        return kc_total

    @commands.slash_command(name="leaderboard", description="Show leaderboard (xp, kc, points)")
    async def leaderboard(
        self, ctx,
        metric: discord.Option(str, "Metric", choices=["xp", "kc", "points"], required=True),
        top: discord.Option(int, "Top N", required=False, default=10),
        verified_only: discord.Option(bool, "Only verified users", required=False, default=True)
    ):
        await ctx.defer()
        guild = ctx.guild
        if not guild:
            return await ctx.respond("❌ Run this in a server.")

        # use storage abstraction to get verified mapping {discord_id: rsn}
        try:
            verified_map = self.bot.storage.get_all_verified() or {}
        except Exception:
            verified_map = {}

        members_to_check = []
        if verified_only:
            for discord_id, rsn in verified_map.items():
                member = guild.get_member(int(discord_id))
                if member:
                    members_to_check.append((member.display_name, rsn, discord_id))
        else:
            for discord_id, rsn in verified_map.items():
                member = guild.get_member(int(discord_id))
                if member:
                    members_to_check.append((member.display_name, rsn, discord_id))

        if not members_to_check:
            return await ctx.respond("❌ No members found to rank.")

        results = []
        if metric == "points":
            # points should be handled by storage if you add that later; fallback zero
            for name, rsn, sid in members_to_check:
                val = 0
                try:
                    val = int(self.bot.storage.get_points(str(sid))) if hasattr(self.bot.storage, "get_points") else 0
                except Exception:
                    val = 0
                results.append((name, val, sid))
        else:
            jobs = []
            for name, rsn, sid in members_to_check:
                if not rsn:
                    results.append((name, None, sid))
                    continue
                if metric == "xp":
                    jobs.append((name, sid, self.compute_xp(rsn)))
                else:
                    jobs.append((name, sid, self.compute_kc(rsn)))
            coros = [c for (_, _, c) in jobs]
            responses = await asyncio.gather(*coros, return_exceptions=True)
            for idx, resp in enumerate(responses):
                name, sid, _ = jobs[idx]
                if isinstance(resp, Exception) or resp is None:
                    value = None
                else:
                    value = resp
                results.append((name, value, sid))

        ranked = [r for r in results if r[1] is not None]
        ranked.sort(key=lambda x: x[1], reverse=True)
        top_n = ranked[:top]

        embed = discord.Embed(title=f"🏆 Leaderboard — {metric.upper()}", color=discord.Color.blurple())
        if not top_n:
            embed.description = "No data available."
            return await ctx.respond(embed=embed)

        lines = []
        for i, (name, value, sid) in enumerate(top_n, start=1):
            if metric == "xp":
                lines.append(f"**{i}. {name}** — {value:,} XP")
            elif metric == "kc":
                lines.append(f"**{i}. {name}** — {value} kills")
            else:
                lines.append(f"**{i}. {name}** — {value} points")
        embed.description = "\n".join(lines)
        await ctx.respond(embed=embed)

    @commands.slash_command(name="points_add", description="Add points to a user (admin only)")
    @commands.default_permissions(administrator=True)
    async def points_add(self, ctx, member: discord.Member, amount: int):
        if hasattr(self.bot.storage, "add_points"):
            try:
                self.bot.storage.add_points(str(member.id), int(amount))
                await ctx.respond(f"✅ {member.display_name} now has points updated.")
            except Exception:
                await ctx.respond("❌ Error updating points in storage.")
        else:
            await ctx.respond("❌ Storage backend doesn't support points yet.")

def setup(bot):
    bot.add_cog(Leaderboard(bot))
