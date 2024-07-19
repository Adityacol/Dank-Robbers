import discord
from redbot.core import commands
from redbot.core.data_manager import cog_data_path
import re
import json
from discord.ext import tasks

class RollTrack(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tracked_channel_id = 1249773817484673145  # Replace with the actual channel ID to track
        self.target_channel_id = 1249809341935255553  # Replace with the actual target channel ID
        self.bot_user_id = 235148962103951360  # ID of the bot that sends the roll messages
        self.payment_role_id = 1018578013140566137  # ID of the role that can confirm payment
        self.loading_emoji = '⌛'  # Loading emoji
        self.thumbs_up_emoji = '👍'  # Thumbs up emoji
        self.sent_embeds = {}  # Dictionary to keep track of sent embeds
        self.members_file = cog_data_path(self) / "members.json"
        self.user_cache = {}

        self.update_members_data.start()  # Start the background task

    @commands.Cog.listener()
    async def on_ready(self):
        # Load the member data from the JSON file
        if self.members_file.exists():
            with open(self.members_file, "r") as f:
                self.user_cache = json.load(f)
        else:
            self.user_cache = {}

    @commands.command()
    @commands.is_owner()
    async def fetchmembers(self, ctx):
        await self.update_member_data()
        await ctx.send("Member data has been fetched and stored.")

    @tasks.loop(hours=6)
    async def update_members_data(self):
        await self.update_member_data()

    async def update_member_data(self):
        user_cache = {}
        for guild in self.bot.guilds:
            async for member in guild.fetch_members(limit=None):
                user_cache[member.name] = {
                    "id": member.id,
                    "name": member.name
                }
        self.user_cache = user_cache
        with open(self.members_file, "w") as f:
            json.dump(self.user_cache, f, indent=4)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id == self.tracked_channel_id and message.author.id == self.bot_user_id:
            content = None

            if message.content:
                content = message.content
            elif message.embeds:
                embed = message.embeds[0]
                content = embed.title

            if content:
                roll_number = self.extract_roll_number(content)
                winner_username = self.extract_winner_username(content)
                if roll_number is not None and winner_username:
                    prize, quantity = self.get_prize_and_quantity(roll_number)
                    await self.send_winner_message(winner_username, roll_number, prize, quantity, message.created_at)
                    await self.reply_to_tracked_message(message, winner_username, prize, quantity)

    def extract_roll_number(self, content):
        roll_pattern = r'rolls \*\*(\d{1,5})\*\*'
        match = re.search(roll_pattern, content)
        if match:
            return int(match.group(1))
        return None

    def extract_winner_username(self, content):
        username_pattern = r'\*\*(\S+)\*\* rolls'
        match = re.search(username_pattern, content)
        if match:
            return match.group(1)
        return None

    def get_prize_and_quantity(self, roll_number):
        if roll_number == 1:
            return "Grand Prize - 10 Billion Dmc", 1
        elif 2 <= roll_number <= 499:
            return "Adventure Ticket", 20
        elif roll_number == 500:
            return "Fool's Notif", 3
        elif 501 <= roll_number <= 999:
            return "Cookie", 50
        elif roll_number == 1000:
            return "Daily Box", 3
        elif 1001 <= roll_number <= 1499:
            return "Worm", 10
        elif roll_number == 1500:
            return "Pet Food", 3
        elif 1501 <= roll_number <= 1999:
            return "Metal Pipe", 1
        elif roll_number == 2000:
            return "Pepe Coin", 10
        elif 2001 <= roll_number <= 2499:
            return "Life saver", 15
        elif roll_number == 2500:
            return "Dmc", 2500000
        elif 2501 <= roll_number <= 2999:
            return "Ant", 50
        elif roll_number == 3000:
            return "Coin Bomb", 10
        elif 3001 <= roll_number <= 3332:
            return "Worm", 15
        elif roll_number == 3333:
            return "Dmc", 333333333
        elif 3334 <= roll_number <= 3499:
            return "Bean seed", 15
        elif roll_number == 3500:
            return "Cell phone", 100
        elif 3501 <= roll_number <= 3999:
            return "Adventure Ticket", 20
        elif roll_number == 4000:
            return "Daily Box", 3
        elif 4001 <= roll_number <= 4499:
            return "Cookie", 50
        elif roll_number == 4500:
            return "Ammo", 1
        elif 4501 <= roll_number <= 4999:
            return "Ant", 50
        elif roll_number == 5000:
            return "Pepe Crown", 1
        elif 5001 <= roll_number <= 5499:
            return "New Year Popper", 10
        elif roll_number == 5500:
            return "Pet Food", 3
        elif 5501 <= roll_number <= 5999:
            return "Vote Pack", 5
        elif roll_number == 6000:
            return "Cowboy Boot", 1
        elif 6001 <= roll_number <= 6499:
            return "Cell Phone", 69
        elif roll_number == 6500:
            return "Daily Box", 10
        elif 6501 <= roll_number <= 6968:
            return "Apple", 10
        elif roll_number == 6969:
            return "Lucky horseshoe", 69
        elif 6970 <= roll_number <= 6999:
            return "New Years Popper", 10
        elif roll_number == 7000:
            return "Pepe Coin", 30
        elif 7001 <= roll_number <= 7499:
            return "Worm", 20
        elif roll_number == 7500:
            return "Metal Pipe", 5
        elif 7501 <= roll_number <= 7999:
            return "Padlock", 29
        elif roll_number == 8000:
            return "Pepe Trophy", 1
        elif 8001 <= roll_number <= 8499:
            return "Dmc", 3333333
        elif roll_number == 8500:
            return "Pet Food", 3
        elif 8501 <= roll_number <= 8999:
            return "Adventure Ticket", 25
        elif roll_number == 9000:
            return "Fool's Notif", 1
        elif 9001 <= roll_number <= 9499:
            return "Cookie", 60
        elif roll_number == 9500:
            return "Credit card", 1
        elif 9501 <= roll_number <= 9998:
            return "Ant", 50
        elif roll_number == 9999:
            return "Dank box", 10
        elif roll_number == 10000:
            return "Odd eye", 4
        elif 10001 <= roll_number <= 10499:
            return "New Years Poppers", 20
        elif 10500 <= roll_number <= 10999:
            return "Fertilizer bags", 20
        elif 11000 <= roll_number <= 11000:
            return "Landmine", 69
        elif 11001 <= roll_number <= 11110:
            return "Adventure ticket", 20
        elif roll_number == 11111:
            return "DMC", 11111111
        elif 11112 <= roll_number <= 11499:
            return "Worm", 10
        elif roll_number == 11500:
            return "Apple", 100
        elif 11501 <= roll_number <= 11999:
            return "Rabbit", 20
        elif roll_number == 12000:
            return "Daily Box", 3
        elif 12001 <= roll_number <= 12344:
            return "Pizza Slice", 1
        elif roll_number == 12345:
            return "Robber's Wishlist", 50
        elif 12346 <= roll_number <= 12499:
            return "Bean Seeds", 10
        elif roll_number == 12500:
            return "Pet Food", 3
        elif 12501 <= roll_number <= 12999:
            return "New Years Popper", 10
        elif roll_number == 13000:
            return "Normie box", 5
        elif 13001 <= roll_number <= 13499:
            return "Ant", 50
        elif roll_number == 13500:
            return "Message in a Bottle", 1
        elif 13501 <= roll_number <= 13999:
            return "Vote pack", 3
        elif roll_number == 14000:
            return "Coin Bomb", 10
        elif 14001 <= roll_number <= 14499:
            return "Cookie", 50
        elif roll_number == 14500:
            return " Cell Phone", 100
        elif 14501 <= roll_number <= 14999:
            return "Adventure Ticket", 20
        elif roll_number == 15000:
            return "UNIVERSE BOX", 4
        return "No Prize", 0

    async def send_winner_message(self, winner_username, roll_number, prize, quantity, message_timestamp):
        target_channel = self.bot.get_channel(self.target_channel_id)
        if target_channel:
            winner_data = self.user_cache.get(winner_username)
            if not winner_data:
                return

            winner_id = winner_data["id"]
            embed = discord.Embed(
                title=" 🎲 Roll Event ",
                description=f"Congratulations **{winner_username}**! You rolled {roll_number} and won {quantity} {prize}!",
                color=discord.Color.gold(),
                timestamp=message_timestamp
            )
            embed.add_field(name="Payout Command", value=f"```/serverevents payout user:{winner_id} quantity:{quantity} item:{prize}```")
            embed.set_footer(text="Roll Event • Keep on rolling!")
            message = await target_channel.send(embed=embed)
            await message.add_reaction(self.loading_emoji)
            self.sent_embeds[message.id] = {"winner_username": winner_username, "roll_number": roll_number, "payer_id": None}

    async def reply_to_tracked_message(self, message, winner_username, prize, quantity):
        winner_data = self.user_cache.get(winner_username)
        if not winner_data:
            return

        winner_id = winner_data["id"]
        winner_user = self.bot.get_user(winner_id)
        user_mention = winner_user.mention
        reply_embed = discord.Embed(
            description=f"Congratulations {user_mention} for winning {quantity} {prize}!\n\n",
            color=discord.Color.gold()
        )
        reply_embed.set_footer(text="Roll Event • Keep on rolling!")
        await message.reply(embed=reply_embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.channel_id == self.target_channel_id and str(payload.emoji) == self.loading_emoji:
            message_id = payload.message_id
            if message_id in self.sent_embeds:
                guild = self.bot.get_guild(payload.guild_id)
                member = guild.get_member(payload.user_id)
                if member and discord.utils.get(member.roles, id=self.payment_role_id):
                    await self.process_payment(message_id, member.id)

    async def process_payment(self, message_id, payer_id):
        target_channel = self.bot.get_channel(self.target_channel_id)
        if target_channel:
            embed_info = self.sent_embeds.get(message_id)
            if embed_info:
                winner_username = embed_info["winner_username"]
                prize, quantity = self.get_prize_and_quantity(embed_info["roll_number"])
                payer_user = await self.bot.fetch_user(payer_id)
                embed_message = await target_channel.fetch_message(message_id)
                embed = embed_message.embeds[0]
                embed.title = "Payment Confirmed!"
                embed.description = f"{winner_username} has been paid {quantity} {prize} by {payer_user.mention} for their roll event"
                embed.remove_field(0)  # Remove the payout command field
                embed.set_footer(text="Roll Event • Payment confirmed!")
                await embed_message.edit(embed=embed)
                await embed_message.clear_reaction(self.loading_emoji)
                await embed_message.add_reaction(self.thumbs_up_emoji)
                del self.sent_embeds[message_id]

async def setup(bot):
    cog = RollTrack(bot)
    await bot.add_cog(cog)
