# bot/cogs/ge.py
# Contains commands related to the Grand Exchange.

import discord
import aiohttp
import re
from discord.commands import SlashCommandGroup, Option
from discord.ext import commands
from datetime import datetime, timezone

from bot.helpers.utils import format_price_timestamp

class GrandExchange(commands.Cog):
    """Cog for Grand Exchange commands."""
    
    def __init__(self, bot):
        self.bot = bot

    ge = SlashCommandGroup("ge", "Commands for the Grand Exchange.")

    async def item_autocomplete(self, ctx: discord.AutocompleteContext):
        """Provides autocomplete suggestions for OSRS items."""
        query = ctx.value.lower()
        if not self.bot.item_mapping:
            return ["Item list is loading..."]
        if not query:
            popular_items = ["Twisted bow", "Scythe of vitur", "Abyssal whip", "Dragon claws"]
            return popular_items
        
        # Prioritize matches that start with the query
        matches = [name.title() for name in self.bot.item_mapping.keys() if name.startswith(query)]
        # If not enough matches, find matches that contain the query
        if len(matches) < 25:
            containing_matches = [name.title() for name in self.bot.item_mapping.keys() if query in name and name.title() not in matches]
            matches.extend(containing_matches)
            
        return matches[:25]

    @ge.command(name="price", description="Check the Grand Exchange price of an item.")
    async def price(self, ctx: discord.ApplicationContext, 
                    item: Option(str, "The name of the item to check.", autocomplete=item_autocomplete)):
        if not self.bot.item_mapping:
            return await ctx.respond("Item list is still loading. Please try again in a moment.", ephemeral=True)
        
        await ctx.defer()
        item_name_lower = item.lower()
        
        item_details = self.bot.item_mapping.get(item_name_lower)
        if not item_details:
            return await ctx.respond("Could not find this item. Please choose one from the list.", ephemeral=True)
            
        item_id = item_details['id']
        url = f"https://prices.osrs.cloud/api/v1/latest/item/{item_id}"
        headers = {'User-Agent': 'GrazyBot/1.0'}
        
        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                async with session.get(url) as response:
                    response.raise_for_status()
                    price_data = await response.json()
                    
                    embed = discord.Embed(title=f"Price Check: {item_details['name']}", color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
                    if item_details.get('icon'):
                        embed.set_thumbnail(url=item_details['icon'])
                        
                    buy_price = price_data.get('high', 0)
                    sell_price = price_data.get('low', 0)
                    embed.add_field(name="Buy Price", value=f"{buy_price:,} gp", inline=True)
                    embed.add_field(name="Sell Price", value=f"{sell_price:,} gp", inline=True)
                    embed.add_field(name="Margin", value=f"{buy_price - sell_price:,} gp", inline=True)
                    
                    buy_time = format_price_timestamp(price_data.get('highTime'))
                    sell_time = format_price_timestamp(price_data.get('lowTime'))
                    embed.add_field(name="Last Buy", value=f"Updated {buy_time}", inline=True)
                    embed.add_field(name="Last Sell", value=f"Updated {sell_time}", inline=True)
                    
                    embed.set_footer(text="Price data from osrs.cloud")
                    await ctx.respond(embed=embed)
            except aiohttp.ClientError as e:
                await ctx.respond(f"Error fetching price data (Status: {response.status}).", ephemeral=True)
            except Exception as e:
                await ctx.respond("An unexpected error occurred while fetching price data.", ephemeral=True)

    @ge.command(name="value", description="Calculate the total GE value of multiple items.")
    async def calculate_value(self, ctx: discord.ApplicationContext,
        item_list: discord.Option(str, "List of items and quantities (e.g., '10k raw sharks, 1 twisted bow').")):
      
        if not self.bot.item_mapping:
            return await ctx.respond("Item list is still loading...", ephemeral=True)
        await ctx.defer()

        total_value = 0
        parsed_items_output = []
        unmatched_items = []
        
        # Regex to find quantity (with k/m suffix) and item name
        item_regex = re.compile(r"(\d+(?:\.\d+)?[km]?)\s+([a-zA-Z0-9\s'-]+?)(?:,|$)")
        matches = item_regex.findall(item_list.lower() + ',')

        if not matches:
            return await ctx.respond("Invalid format. Use 'QUANTITY ITEM, ...'.", ephemeral=True)

        async with aiohttp.ClientSession(headers={'User-Agent': 'GrazyBot/1.0'}) as session:
            for quantity_str, item_name_raw in matches:
                item_name = item_name_raw.strip()
                quantity = 0.0
                if 'k' in quantity_str:
                    quantity = float(quantity_str.replace('k', '')) * 1_000
                elif 'm' in quantity_str:
                    quantity = float(quantity_str.replace('m', '')) * 1_000_000
                else:
                    quantity = float(quantity_str)

                matched_item = self.bot.item_mapping.get(item_name)
                if not matched_item:
                    # Try partial match if exact fails
                    best_match = next((k for k in self.bot.item_mapping if item_name in k), None)
                    if best_match: matched_item = self.bot.item_mapping[best_match]

                if matched_item:
                    try:
                        async with session.get(f"https://prices.osrs.cloud/api/v1/latest/item/{matched_item['id']}") as resp:
                            resp.raise_for_status()
                            price_data = await resp.json()
                            price = price_data.get('high', 0)
                            value = price * quantity
                            total_value += value
                            parsed_items_output.append(f"{int(quantity):,} x {matched_item['name']} @ {price:,} gp = {int(value):,} gp")
                    except Exception as e:
                        unmatched_items.append(f"{item_name_raw.title()} (Price fetch error)")
                else:
                    unmatched_items.append(f"{item_name_raw.title()} (Item not found)")

        embed = discord.Embed(title="Grand Exchange Value Calculator", color=discord.Color.dark_teal())
        if parsed_items_output:
            embed.add_field(name="Valued Items", value="\n".join(parsed_items_output), inline=False)
            embed.add_field(name="Total Value", value=f"**{int(total_value):,} gp**", inline=False)
        if unmatched_items:
            embed.add_field(name="Unmatched Items", value="\n".join(unmatched_items), inline=False)
        embed.set_footer(text="Prices from osrs.cloud (instant buy)")
        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(GrandExchange(bot))