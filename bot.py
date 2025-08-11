 ",
        description="Here are all the commands you can use to manage clan events.",
        color=discord.Color.blurple()
    )

    member_commands = """
    `/sotw view` - View the leaderboard for the current Skill of the Week.
    `/raffle enter` - Get one ticket for the current raffle (max 10).
    `/raffle view_tickets` - See how many tickets everyone has.
    `/bingo board` - Get a link to the current bingo board.
    `/bingo complete` - Submit a task for bingo completion.
    `/points view` - Check your current Clan Point balance.
    `/points leaderboard` - View the Clan Points leaderboard.
    `/osrs link` - Link your Discord account to your OSRS name.
    `/events view` - See all currently active events.
    """
    
    admin_commands = """
    `/sotw start` - Manually start a new SOTW competition.
    `/sotw poll` - Start a poll to choose the next SOTW.
    `/raffle start` - Start a new raffle.
    `/raffle give_tickets` - Give raffle tickets to a member.
    `/raffle edit_tickets` - Set a member's total ticket count.
    `/raffle draw_now` - End the raffle and draw a winner immediately.
    `/raffle cancel` - Cancel the current raffle.
    `/bingo start` - Start a new clan bingo event.
    `/bingo submissions` - View and manage pending bingo submissions.
    `/admin announce` - Send a global announcement as the bot.
    `/admin manage_points` - Add or remove Clan Points from a member.
    `/admin award_sotw_winners` - Manually award points for a past SOTW.
    """
    
    embed.add_field(name="✅ Member Commands", value=textwrap.dedent(member_commands), inline=False)
    embed.add_field(name="👑 Admin Commands", value=textwrap.dedent(admin_commands), inline=False)
    embed.set_footer(text="Let the games begin!")
    
    await ctx.respond(embed=embed, ephemeral=True)

# --- Main Execution Block ---
async def run_bot():
    """A resilient function to start the bot and handle rate limits."""
    while True:
        try:
            await bot.start(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print("BOT is being rate-limited by Discord. Retrying in 5 minutes...")
                await asyncio.sleep(300) # Wait 5 minutes before trying to reconnect
            else:
                print(f"An unexpected HTTP error occurred with the bot: {e}")
                break # Exit on other HTTP errors
        except Exception as e:
            print(f"An unexpected error occurred while running the bot: {e}")
            break # Exit on other errors

async def main():
    web_task = asyncio.create_task(start_web_server())
    bot_task = asyncio.create_task(run_bot())
    await asyncio.gather(web_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())
 