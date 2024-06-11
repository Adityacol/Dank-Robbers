import discord
from redbot.core import commands
from redbot.core.data_manager import cog_data_path
import re
import json
from discord.ext import tasks

class RollTracker(commands.Cog):
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
            return "Universe box", 1
        elif 2 <= roll_number <= 68:
            return "Landmine", 3
        elif roll_number == 69:
            return "69,000,000", 1
        elif 70 <= roll_number <= 200:
            return "Pet food", 4
        elif 201 <= roll_number <= 499:
            return "Ammo", 1
        elif roll_number == 500:
            return "Work box", 1
        elif 501 <= roll_number <= 800:
            return "Worms", 3
        elif 801 <= roll_number <= 999:
            return "Cell phone", 2
        elif roll_number == 1000:
            return "Pepe trophy", 1
        elif 1001 <= roll_number <= 1400:
            return "Bank note", 1
        elif 1401 <= roll_number <= 1499:
            return "Dmc", 1
        elif roll_number == 1500:
            return "Cookies", 400
        elif 1501 <= roll_number <= 1800:
            return "Ants", 10
        elif 1801 <= roll_number <= 1999:
            return "Daily box", 2
        elif roll_number == 2000:
            return "Pepe trophy", 2
        elif 2001 <= roll_number <= 2400:
            return "Adventure ticket", 10
        elif 2401 <= roll_number <= 2499:
            return "Bank note", 10
        elif roll_number == 2500:
            return "Robber's wishlist", 30
        elif 2501 <= roll_number <= 2700:
            return "Lucky horseshoe", 10
        elif 2701 <= roll_number <= 2999:
            return "dmc", 5000000
        elif roll_number == 3000:
            return "Pepe trophy", 2
        elif 3001 <= roll_number <= 3400:
            return "Potato", 10
        elif 3401 <= roll_number <= 3499:
            return "Bank notes", 20
        elif roll_number == 3500:
            return "Blue's plane", 1
        elif 3501 <= roll_number <= 3700:
            return "Shredded cheese", 1
        elif 3701 <= roll_number <= 3999:
            return "Dmc", 1
        elif roll_number == 4000:
            return "Pepe trophy", 4
        elif 4001 <= roll_number <= 4400:
            return "Life saver", 2
        elif 4401 <= roll_number <= 4499:
            return "Duct tape", 1
        elif roll_number == 4500:
            return "Tool box", 5
        elif 4501 <= roll_number <= 4700:
            return "Bank note", 1
        elif 4701 <= roll_number <= 4998:
            return "dmc", 5000000
        elif roll_number == 4999:
            return "Bank notes", 200
        elif roll_number == 5000:
            return "Rolls + Free Cosmetic", 2
        else:
            return "Unknown prize", 1

    async def send_winner_message(self, winner_username, roll_number, prize, quantity, message_timestamp):
        target_channel = self.bot.get_channel(self.target_channel_id)
        if target_channel:
            winner_data = self.user_cache.get(winner_username)
            if not winner_data:
                return

            winner_id = winner_data["id"]
            embed = discord.Embed(
                title=" 🎲 Roll Event ",
                description=f"Congratulations **{winner_username}**! You rolled {roll_number} and won {prize}!",
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
                    await self.process_payment(payload.guild_id, message_id, member.id)

    async def process_payment(self, guild_id, message_id, payer_id):
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
    cog = EmbedTracker(bot)
    await bot.add_cog(cog)
