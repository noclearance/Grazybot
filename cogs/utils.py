 Add this function to your cogs/utils.py file

async def draw_raffle_winner(bot, raffle_channel_id):
    RAFFLE_CHANNEL_ID = raffle_channel_id
    ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute("SELECT * FROM raffles WHERE winner_id IS NULL LIMIT 1")
    raffle_data = cursor.fetchone()
    
    if not raffle_data:
        cursor.close()
        conn.close()
        return "No active raffle to draw."

    prize = raffle_data['prize']
    cursor.execute("SELECT user_id FROM raffle_entries")
    entries = cursor.fetchall()
    
    raffle_channel = bot.get_channel(RAFFLE_CHANNEL_ID)
    if not raffle_channel:
        print(f"Could not find raffle channel with ID {RAFFLE_CHANNEL_ID}")
        cursor.close()
        conn.close()
        return "Raffle channel not found."

    if not entries:
        await raffle_channel.send(f"The raffle for **{prize}** has ended, but alas, no one entered the contest of fate.")
        cursor.execute("UPDATE raffles SET winner_id = 0 WHERE id = 1") # Use 0 for no winner
    else:
        winner_id = random.choice(entries)['user_id']
        try:
            winner_user = await bot.fetch_user(winner_id)
            await award_points(winner_user, 50, f"winning the raffle for {prize}")

            # Embed for the #raffle channel
            raffle_embed = discord.Embed(title="🎉 Raffle Winner Announcement! 🎉", description=f"The fates have chosen! Congratulations to {winner_user.mention}, you have won the raffle!", color=discord.Color.fuchsia())
            raffle_embed.add_field(name="Prize", value=f"**{prize}**", inline=False)
            raffle_embed.set_footer(text="Thanks to everyone for participating!")
            raffle_embed.set_thumbnail(url=winner_user.display_avatar.url)
            await raffle_channel.send(embed=raffle_embed)
            
            # Special Embed for the #announcements channel
            announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
            if announcements_channel:
                announcement_embed = discord.Embed(title="🏆 A Champion of Fortune! 🏆", description=f"Let the entire clan celebrate! {winner_user.mention} has emerged victorious in the recent test of luck.", color=discord.Color.gold())
                announcement_embed.add_field(name="Prize Claimed", value=f"The grand prize of **{prize}** is now theirs!", inline=False)
                announcement_embed.add_field(name="Bonus Reward", value="For this victory, they have also been granted **50 Clan Points**!", inline=False)
                announcement_embed.set_thumbnail(url=winner_user.display_avatar.url)
                announcement_embed.set_footer(text="May their luck inspire us all.")
                await announcements_channel.send(content=f"@everyone Congratulations to our winner, {winner_user.mention}!", embed=announcement_embed)

            cursor.execute("UPDATE raffles SET winner_id = %s WHERE id = 1", (winner_id,))
        except discord.NotFound:
            print(f"Could not find winner user with ID {winner_id}")
            # Handle the case where the winner left the server
            await raffle_channel.send(f"The winner for the **{prize}** raffle could not be found. A re-draw may be necessary.")

    conn.commit()
    cursor.close()
    conn.close()
    return f"Winner drawn for the '{prize}' raffle."