import discord
from discord.ext import commands


class ExampleCog(commands.Cog):
    """Minimal cog scaffold. Move one command from `bot.py` into a cog like this.

    Usage:
      - place file at `cogs/example.py`
      - load with `bot.load_extension('cogs.example')` or add to `main.py` extensions list
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name="ping", description="Ping the bot")
    async def ping(self, ctx):
        await ctx.respond("Pong!")


def setup(bot):
    bot.add_cog(ExampleCog(bot))
