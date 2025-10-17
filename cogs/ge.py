# cogs/ge.py
# Contains commands related to the Grand Exchange.

import discord
import aiohttp
import re
import logging
from discord import SlashCommandGroup, Option
from discord.ext import commands

from core.bot import GrazyBot
from utils.time import format_timestamp

logger = logging.getLogger(__name__)

class GrandExchange(commands.Cog):
    """Cog for Grand Exchange commands."""
    
    def __init__(self, bot: GrazyBot):
        self.bot = bot
        self.price_api_url = "https://prices.osrs.cloud/api/v1/latest"
        self.session = aiohttp.ClientSession(headers={'User-Agent': 'GrazyBot/2.0'})

    def cog_unload(self):
        """Close the aiohttp session when the cog is unloaded."""
        self.bot.loop.create_task(self.session.close())

    ge = SlashCommandGroup("ge", "Commands for the Grand Exchange.")

    async def item_autocomplete(self, ctx: discord.AutocompleteContext) -> list[str]:
        """Provides autocomplete suggestions for OSRS items."""
        query = ctx.value.lower()
        if not self.bot.item_mapping:
            return ["Item list is still loading, please wait..."]
        if not query:
            return ["Twisted bow", "Scythe of vitur", "Abyssal whip", "Dragon claws"]
        
        matches = [name.title() for name in self.bot.item_mapping.keys() if name.startswith(query)]
        if len(matches) < 25:
            containing_matches = [
                name.title() for name in self.bot.item_mapping.keys()
                if query in name and name.title() not in matches
            ]
            matches.extend(containing_matches)
            
        return matches[:25]

    @ge.command(name="price", description="Check the Grand Exchange price of an item.")
    async def price(self, ctx: discord.ApplicationContext, 
                    item: Option(str, "The name of the item to check.", autocomplete=item_autocomplete)):
        """Fetches and displays the GE price for a specified item."""
        await ctx.defer()
        
        item_details = self.bot.item_mapping.get(item.lower())
        if not item_details:
            return await ctx.respond("Could not find this item. Please choose one from the list.", ephemeral=True)
            
        item_id = item_details['id']
        try:
            async with self.session.get(f"{self.price_api_url}/item/{item_id}") as response:
                response.raise_for_status()
                price_data = await response.json()

                embed = discord.Embed(title=f"Price Check: {item_details['name']}", color=discord.Color.gold())
                if item_details.get('icon'):
                    embed.set_thumbnail(url=item_details['icon'])
                    
                buy_price = price_data.get('high', 0)
                sell_price = price_data.get('low', 0)
                embed.add_field(name="Buy Price", value=f"{buy_price:,} gp", inline=True)
                embed.add_field(name="Sell Price", value=f"{sell_price:,} gp", inline=True)
                embed.add_field(name="Margin", value=f"{buy_price - sell_price:,} gp", inline=True)

                embed.add_field(name="Last Buy", value=f"Updated {format_timestamp(price_data.get('highTime'))}", inline=True)
                embed.add_field(name="Last Sell", value=f"Updated {format_timestamp(price_data.get('lowTime'))}", inline=True)

                embed.set_footer(text="Price data from osrs.cloud")
                await ctx.respond(embed=embed)
        except aiohttp.ClientError as e:
            logger.error(f"GE price check failed for item '{item}' (ID: {item_id}): {e}")
            await ctx.respond(f"Error fetching price data. The API might be down.", ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error in GE price check for '{item}': {e}", exc_info=True)
            await ctx.respond("An unexpected error occurred. Please try again later.", ephemeral=True)

    @ge.command(name="value", description="Calculate the total GE value of multiple items.")
    async def calculate_value(self, ctx: discord.ApplicationContext,
        item_list: discord.Option(str, "A list of items and quantities (e.g., '10k raw sharks, 1 tbow').")):
        """Parses a string of items and quantities, and calculates their total GE value."""
        await ctx.defer()

        total_value = 0
        valued_items = []
        unmatched_items = []
        
        item_regex = re.compile(r"([\d.,]+[km]?)\s*([a-zA-Z\s'-]+?)(?:,|$|and)")
        matches = item_regex.findall(item_list.lower())

        if not matches:
            return await ctx.respond("Invalid format. Please use a format like '10k raw sharks, 1 twisted bow'.", ephemeral=True)

        for quantity_str, item_name_raw in matches:
            item_name = item_name_raw.strip()
            quantity_str = quantity_str.strip().replace(',', '')

            try:
                if 'k' in quantity_str:
                    quantity = float(quantity_str.replace('k', '')) * 1_000
                elif 'm' in quantity_str:
                    quantity = float(quantity_str.replace('m', '')) * 1_000_000
                else:
                    quantity = float(quantity_str)
            except ValueError:
                unmatched_items.append(f"'{quantity_str} {item_name}' (Invalid quantity)")
                continue

            matched_item = self.bot.item_mapping.get(item_name)
            if not matched_item:
                # Try to find a partial match as a fallback
                for key, val in self.bot.item_mapping.items():
                    if item_name in key:
                        matched_item = val
                        break

            if matched_item:
                try:
                    async with self.session.get(f"{self.price_api_url}/item/{matched_item['id']}") as resp:
                        resp.raise_for_status()
                        price = (await resp.json()).get('high', 0)
                        value = price * quantity
                        total_value += value
                        valued_items.append(f"`{int(quantity):,}` x **{matched_item['name']}** @ `{price:,}` = `{int(value):,}` gp")
                except aiohttp.ClientError:
                    unmatched_items.append(f"**{matched_item['name']}** (Price fetch error)")
            else:
                unmatched_items.append(f"**{item_name.title()}** (Item not found)")

        embed = discord.Embed(title="GE Value Calculator", color=discord.Color.dark_teal())
        if valued_items:
            embed.description = "\n".join(valued_items)
            embed.add_field(name="Total Value", value=f"**{int(total_value):,} gp**", inline=False)
        if unmatched_items:
            embed.add_field(name="Unmatched / Failed Items", value="\n".join(unmatched_items), inline=False)

        await ctx.respond(embed=embed)


async def setup(bot: GrazyBot):
    await bot.add_cog(GrandExchange(bot))