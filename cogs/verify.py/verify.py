import discord
from discord.ext import commands
import aiohttp
import random
import string

class Verify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pending = {}  # {str(discord_id): {"rsn": str, "code": str}}

    def generate_code(self, length: int = 6) -> str:
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

    @commands.slash_command(name="verify", description="Start RSN verification (WOM bio method)")
    async def verify(self, ctx, rsn: str):
        code = self.generate_code()
        self.pending[str(ctx.author.id)] = {"rsn": rsn, "code": code}
        embed = discord.Embed(
            title="🔐 RSN Verification",
            description=(
                f"1) Add this code to your Wise Old Man bio for **{rsn}**:\n\n**{code}**\n\n"
                "2) Run `/confirm_verify` when done."
            ),
            color=discord.Color.orange()
        )
        await ctx.respond(embed=embed)

    @commands.slash_command(name="confirm_verify", description="Confirm RSN verification")
    async def confirm_verify(self, ctx):
        entry = self.pending.get(str(ctx.author.id))
        if not entry:
            return await ctx.respond("❌ No verification in progress. Use `/verify <rsn>` first.")
        rsn = entry["rsn"]
        code = entry["code"]
        url = f"https://api.wiseoldman.net/v2/players/{rsn}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return await ctx.respond("❌ Could not fetch WOM profile for that RSN.")
                data = await resp.json()
                bio = data.get("bio") or ""
                if code in bio:
                    # persist via storage abstraction
                    try:
                        self.bot.storage.upsert_verified(str(ctx.author.id), rsn)
                    except Exception:
                        return await ctx.respond("❌ Storage error while saving verification.")
                    del self.pending[str(ctx.author.id)]
                    await ctx.respond(f"✅ Verified {ctx.author.mention} as **{rsn}**.")
                else:
                    await ctx.respond("❌ Verification code not found in WOM bio. Make sure you saved changes and try again.")

def setup(bot):
    bot.add_cog(Verify(bot))
